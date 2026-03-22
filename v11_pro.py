import sys
from types import ModuleType

# --- 1. CORRECTIF PYTHON 3.14 (MOCK NUMBA) ---
if 'numba' not in sys.modules:
    m = ModuleType('numba')
    m.njit = lambda f=None, *a, **k: (lambda x: x) if f is None else f
    sys.modules['numba'] = m

import threading, hashlib, os, time, requests
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import pandas as pd
import pandas_ta as ta
from binance.client import Client

# =============================================================
# LOGGING + SERVEUR WEB
# =============================================================
def log(msg):
    print(msg, flush=True)

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Atlas v15.4 Online")
    def log_message(self, format, *args): return

def run_web_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# =============================================================
# CONFIGURATION
# =============================================================
load_dotenv("key.env")
BINANCE_KEY      = os.getenv('BINANCE_API_KEY')
GOOGLE_KEYS      = [os.getenv('GOOGLE_API_KEY'), os.getenv('GOOGLE_API_KEY_2')]
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

client_binance = Client(BINANCE_KEY, "")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT"]
ACTIVE_MODEL     = "gemini-2.5-flash"
CONFIDENCE_MIN   = 75    
last_signal_hash = {}

# =============================================================
# DATA ENGINE
# =============================================================
def get_data(symbol):
    try:
        cols = ['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig']
        def to_df(raw):
            df = pd.DataFrame(raw, columns=cols)
            for c in ['open','high','low','close','vol']:
                df[c] = pd.to_numeric(df[c])
            return df.copy()

        log(f"    -> Collecte {symbol}...")
        raw_h1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "220 hours ago UTC")
        raw_h4 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_4HOUR, "120 days ago UTC")
        raw_d1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "400 days ago UTC")

        df_h1, df_h4, df_d1 = to_df(raw_h1), to_df(raw_h4), to_df(raw_d1)

        # Indicateurs H1
        df_h1.ta.rsi(length=14, append=True)
        df_h1.ta.macd(fast=12, slow=26, signal=9, append=True)
        df_h1.ta.adx(length=14, append=True)
        df_h1.ta.bbands(length=20, std=2, append=True)
        df_h1.ta.atr(length=14, append=True)

        atr_col = next((c for c in ['ATRr_14', 'ATR_14'] if c in df_h1.columns), None)
        atr_val = float(df_h1[atr_col].iloc[-1]) if atr_col else 0.0

        vol_ma20 = df_h1['vol'].rolling(20).mean().iloc[-1]
        vol_ratio = df_h1['vol'].iloc[-1] / vol_ma20 if vol_ma20 > 0 else 1.0

        # Indicateurs H4
        df_h4.ta.rsi(length=14, append=True)
        df_h4.ta.adx(length=14, append=True)
        df_h4.ta.macd(fast=12, slow=26, signal=9, append=True)

        # EMA200 D1
        ema_series = df_d1.ta.ema(length=200)
        ema200 = float(ema_series.iloc[-1]) if (ema_series is not None and not ema_series.empty) else None

        # Pivots J-1
        ph, pl, pc = df_d1['high'].iloc[-2], df_d1['low'].iloc[-2], df_d1['close'].iloc[-2]
        pivot = (ph + pl + pc) / 3

        res = df_h1.iloc[-1].to_dict()
        res.update({
            'atr_val': atr_val,
            'vol_ratio': round(vol_ratio, 2),
            'rsi_h4': round(float(df_h4['RSI_14'].iloc[-1]), 1),
            'adx_h4': round(float(df_h4['ADX_14'].iloc[-1]), 1),
            'macd_h4': round(float(df_h4['MACDh_12_26_9'].iloc[-1]), 6),
            'p_r1': round((2 * pivot) - pl, 6),
            'p_r2': round(pivot + (ph - pl), 6),
            'p_s1': round((2 * pivot) - ph, 6),
            'p_s2': round(pivot - (ph - pl), 6)
        })
        log(f"    -> RSI={res['RSI_14']:.1f} | ATR={atr_val:.4f} | Vol=x{vol_ratio:.2f}")
        return res, ema200
    except Exception as e:
        log(f"    [DATA ERROR] {symbol}: {e}"); return None, None

# =============================================================
# PRE-FILTRE
# =============================================================
def pre_filter(last):
    vr, adx, rsi = float(last.get('vol_ratio', 1.0)), float(last.get('ADX_14', 0)), float(last.get('RSI_14', 50))
    log(f"    -> Filtre: Vol=x{vr:.2f} | ADX={adx:.1f} | RSI={rsi:.1f}")
    if vr < 0.7: return False, f"Volume faible (x{vr:.2f})"
    if adx < 18 and 42 < rsi < 58: return False, f"Range plat (ADX={adx:.1f})"
    return True, "Setup valide"

# =============================================================
# PROMPT IA
# =============================================================
def demander_ia_expert(symbol, last, ema200):
    def fv(key, prec=2):
        val = last.get(key)
        try: return "N/A" if (val is None or str(val).lower() == 'nan') else f"{float(val):.{prec}f}"
        except: return "N/A"

    close, atr, adx_h1 = float(last['close']), float(last.get('atr_val', 0)), float(last.get('ADX_14', 0))
    sl_min = round(atr * 1.5, 6)
    ema_txt = f"{ema200:.4f}" if ema200 is not None else "N/A"
    above_ema = "AU-DESSUS" if (ema200 and close > ema200) else "EN-DESSOUS"
    ratio_cible = "2.0 a 2.5 (forte)" if adx_h1 >= 30 else ("1.5 a 2.0 (moderee)" if adx_h1 >= 20 else "1.2 a 1.5 (range)")

    prompt = f"""Tu es un trader quantitatif crypto. Signaux haute probabilite uniquement.
Analyse {symbol} a {close:.6f} $.

H1 (signal principal) :
RSI 14     : {fv('RSI_14', 1)} | MACD Histo : {fv('MACDh_12_26_9', 6)}
ADX 14     : {fv('ADX_14', 1)} | ATR 14 : {atr:.6f}
Bollinger  : Low {fv('BBL_20_2.0', 4)} | Mid {fv('BBM_20_2.0', 4)} | High {fv('BBU_20_2.0', 4)}
Volume     : x{fv('vol_ratio')} vs moy 20 bougies

H4 (confirmation) :
RSI 14 : {fv('rsi_h4', 1)} | ADX 14 : {fv('adx_h4', 1)} | MACD Histo : {fv('macd_h4', 6)}

D1 (macro) :
EMA 200 : {ema_txt} ({above_ema})
R2 {fv('p_r2', 6)} | R1 {fv('p_r1', 6)} | S1 {fv('p_s1', 6)} | S2 {fv('p_s2', 6)}

REGLES :
1. SL minimum = {sl_min:.6f} (1.5 x ATR).
2. Ratio G/R cible : {ratio_cible}.
3. ACHAT : DI+ > DI-, MACD Histo croissant, RSI H4 > 50.
4. VENTE : DI- > DI+, MACD Histo decroissant, RSI H4 < 50.
5. Confiance >= 75% -> 5% capital | 60-74% -> 3% | < 60% -> ATTENTE.

FORMAT (STRICT) :
SIGNAL    : [ ACHAT | VENTE | ATTENTE ]
CONFIANCE : X%
TP : X.XXXXXX | SL : X.XXXXXX
RATIO G/R : X.X | TAILLE : X%
H4 STATUS : [ CONFIRME | MIXTE ]
ANALYSE : (4 phrases precises)"""

    for key in GOOGLE_KEYS:
        if not key: continue
        try:
            log(f"    -> Appel Gemini...")
            res = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{ACTIVE_MODEL}:generateContent?key={key}",
                json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.1}}, timeout=25)
            return res.json()['candidates'][0]['content']['parts'][0]['text']
        except: pass
    return "SIGNAL : ATTENTE\nCONFIANCE : 0%\nANALYSE : Quotas epuises."

# =============================================================
# UTILITAIRES
# =============================================================
def extract_signal(verdict):
    for line in verdict.split('\n'):
        if 'SIGNAL' in line.upper() and ':' in line:
            content = line.split(':', 1)[1].upper()
            if 'ACHAT' in content: return 'ACHAT'
            if 'VENTE' in content: return 'VENTE'
            if 'ATTENTE' in content: return 'ATTENTE'
    return 'ATTENTE'

def extract_confidence(v):
    for line in v.split('\n'):
        if 'CONFIANCE' in line.upper():
            try: return int(''.join(filter(str.isdigit, line))[:3])
            except: pass
    return 0

def is_duplicate(symbol, verdict):
    h = hashlib.md5(f"{symbol}:{verdict[:80]}".encode()).hexdigest()
    if last_signal_hash.get(symbol) == h: return True
    last_signal_hash[symbol] = h
    return False

def envoyer_telegram(msg):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except: log("  [TELEGRAM ERROR]")

# =============================================================
# BOUCLE PRINCIPALE
# =============================================================
CYCLE_WAIT, INTER_DELAY = 3600, 12
log("Stabilisation Koyeb (60s)...")
time.sleep(60)

while True:
    log(f"\n{'='*52}\nATLAS v15.4 - Scan {datetime.now().strftime('%H:%M:%S')}\n{'='*52}")
    sent, filtered = 0, 0
    for s in SYMBOLS:
        log(f"\n[{s}] --- Analyse ---")
        last, ema200 = get_data(s)
        if not last: continue
        ok, reason = pre_filter(last)
        if not ok: log(f"  [SKIP] {reason}"); continue
            
        verdict = demander_ia_expert(s, last, ema200)
        sig, conf = extract_signal(verdict), extract_confidence(verdict)
        log(f"  [SIGNAL] {sig} ({conf}%)")

        if sig == 'ATTENTE' or conf < CONFIDENCE_MIN or is_duplicate(s, verdict):
            if conf < CONFIDENCE_MIN and sig != 'ATTENTE': filtered += 1
            continue

        emoji = "🟢" if sig == 'ACHAT' else "🔴"
        v_r = float(last.get('vol_ratio', 1.0))
        v_icon = "🔥" if v_r >= 1.5 else ("📊" if v_r >= 1.0 else "🔇")
        msg = f"{emoji} *ATLAS v15.4 - {s}* {emoji}\n💰 Prix : `{float(last['close']):.6f} $`\n{v_icon} Vol: `x{v_r:.2f}` | 📐 ATR: `{last['atr_val']:.4f}`\n🎯 Conf: *{conf}%* | Signal: *{sig}*\n```\n{verdict}\n```"
        envoyer_telegram(msg); sent += 1; time.sleep(INTER_DELAY)

    done_msg = f"🏁 *Cycle termine* ({datetime.now().strftime('%H:%M')})\nSignaux: *{sent}/{len(SYMBOLS)}* | Filtres: {filtered}"
    envoyer_telegram(done_msg); log(f"\n{done_msg}"); time.sleep(CYCLE_WAIT)
