import sys
from types import ModuleType

# --- 1. CORRECTIF PYTHON 3.14 ---
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
# SERVEUR WEB & LOGGING
# =============================================================
def log(msg):
    print(msg, flush=True)

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-type', 'text/plain'); self.end_headers()
        self.wfile.write(b"Atlas v15.3 Full-Log Online")
    def log_message(self, format, *args): return

def run_web_server():
    port = int(os.environ.get("PORT", 8000))
    HTTPServer(('0.0.0.0', port), SimpleHandler).serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# =============================================================
# CONFIGURATION
# =============================================================
load_dotenv("key.env")
BINANCE_KEY      = os.getenv('BINANCE_API_KEY')
GOOGLE_KEYS      = [os.getenv('GOOGLE_API_KEY'), os.getenv('GOOGLE_API_KEY_2')]
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

client_binance   = Client(BINANCE_KEY, "")
SYMBOLS          = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT"]
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
            for c in ['open','high','low','close','vol']: df[c] = pd.to_numeric(df[c])
            return df.copy()

        log(f"    -> Collecte données {symbol}...")
        raw_h1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "220 hours ago UTC")
        raw_h4 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_4HOUR, "120 days ago UTC")
        raw_d1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "400 days ago UTC")

        df_h1, df_h4, df_d1 = to_df(raw_h1), to_df(raw_h4), to_df(raw_d1)

        df_h1.ta.rsi(length=14, append=True)
        df_h1.ta.macd(append=True)
        df_h1.ta.adx(append=True)
        df_h1.ta.bbands(append=True)
        df_h1.ta.atr(length=14, append=True)

        atr_col = next((c for c in ['ATRr_14', 'ATR_14'] if c in df_h1.columns), None)
        atr_val = float(df_h1[atr_col].iloc[-1]) if atr_col else 0.0

        vol_ma20 = df_h1['vol'].rolling(20).mean().iloc[-1]
        vol_ratio = df_h1['vol'].iloc[-1] / vol_ma20 if vol_ma20 > 0 else 1.0

        df_h4.ta.rsi(length=14, append=True)
        df_h4.ta.adx(length=14, append=True)
        df_h4.ta.macd(append=True)
        
        ema_series = df_d1.ta.ema(length=200)
        ema200 = float(ema_series.iloc[-1]) if (ema_series is not None and not ema_series.empty) else None

        # Pivots
        ph, pl, pc = df_d1['high'].iloc[-2], df_d1['low'].iloc[-2], df_d1['close'].iloc[-2]
        pivot = (ph + pl + pc) / 3

        res = df_h1.iloc[-1].to_dict()
        res.update({
            'atr_val': atr_val, 'vol_ratio': round(vol_ratio, 2),
            'rsi_h4': round(df_h4['RSI_14'].iloc[-1], 1),
            'adx_h4': round(df_h4['ADX_14'].iloc[-1], 1),
            'macd_h4': round(df_h4['MACDh_12_26_9'].iloc[-1], 6),
            'p_r1': round((2*pivot)-pl, 6), 'p_r2': round(pivot+(ph-pl), 6),
            'p_s1': round((2*pivot)-ph, 6), 'p_s2': round(pivot-(ph-pl), 6)
        })
        log(f"    -> RSI H1={res['RSI_14']:.1f} | ATR={atr_val:.4f} | Vol=x{vol_ratio:.2f}")
        return res, ema200
    except Exception as e:
        log(f"    [DATA ERROR] {symbol}: {e}"); return None, None

# =============================================================
# PRE-FILTRE & IA
# =============================================================
def pre_filter(last):
    vr, adx, rsi = float(last.get('vol_ratio', 1.0)), float(last.get('ADX_14', 0)), float(last.get('RSI_14', 50))
    log(f"    -> Check: Vol=x{vr} | ADX={adx:.1f} | RSI={rsi:.1f}")
    if vr < 0.7: return False, f"Vol faible (x{vr})"
    if adx < 18 and 42 < rsi < 58: return False, f"Range plat"
    return True, "Setup Valide"

def demander_ia_expert(symbol, last, ema200):
    def fv(key, prec=2):
        val = last.get(key)
        try: return "N/A" if (val is None or str(val).lower() == 'nan') else f"{float(val):.{prec}f}"
        except: return "N/A"

    close, atr = float(last['close']), float(last.get('atr_val', 0))
    sl_min = round(atr * 1.5, 6)
    
    prompt = f"""Expert Trader H1. Analyse {symbol} à {close:.6f}$.
    DATA: RSI {fv('RSI_14', 1)} | ADX {fv('ADX_14', 1)} | MACD {fv('MACDh_12_26_9', 6)} | ATR {atr:.6f} | Vol x{fv('vol_ratio')}
    H4: RSI {fv('rsi_h4', 1)} | ADX {fv('adx_h4', 1)} | MACD {fv('macd_h4', 6)}
    D1: EMA200 {fv('ema200', 4)} | R2 {fv('p_r2', 6)} | S2 {fv('p_s2', 6)}
    
    REGLES: SL MIN = {sl_min:.6f}. Ratio G/R selon ADX. 
    ACHAT: DI+ > DI- & MACD UP & RSI H4 > 50. 
    VENTE: DI- > DI+ & MACD DOWN & RSI H4 < 50.
    
    FORMAT:
    SIGNAL    : [ ACHAT | VENTE | ATTENTE ]
    CONFIANCE : X%
    TP : X.XXXXXX | SL : X.XXXXXX
    ANALYSE : (4 phrases techniques)"""

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
# UTILITAIRES & BOUCLE
# =============================================================
def extract_signal(v):
    v_up = v.upper()
    return 'ACHAT' if 'ACHAT' in v_up else ('VENTE' if 'VENTE' in v_up else 'ATTENTE')

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

log("Stabilisation Koyeb (60s)...")
time.sleep(60)

while True:
    log(f"\n{'='*52}\nATLAS v15.3 - Scan {datetime.now().strftime('%H:%M:%S')}\n{'='*52}")
    sent, filtered = 0, 0
    for s in SYMBOLS:
        log(f"\n[{s}] --- Analyse ---")
        last, ema200 = get_data(s)
        if not last: continue
        
        ok, reason = pre_filter(last)
        if not ok: log(f"  [SKIP] {reason}"); continue
            
        verdict = demander_ia_expert(s, last, ema200)
        sig, conf = extract_signal(verdict), extract_confidence(verdict)
        log(f"  [RESULTAT] {sig} ({conf}%)")
        log(f"  [VERDICT BRUT]: {verdict[:100]}...")

        if sig == 'ATTENTE' or conf < CONFIDENCE_MIN or is_duplicate(s, verdict):
            if conf < CONFIDENCE_MIN and sig != 'ATTENTE': filtered += 1
            continue

        msg = f"🟢 *{s}* (Conf: {conf}%)\n💰 Prix: `{last['close']:.6f}`\n```\n{verdict}\n```"
        envoyer_telegram(msg); sent += 1; time.sleep(12)

    done_msg = f"🏁 *Cycle terminé* - Signaux: {sent}/{len(SYMBOLS)} | Filtres: {filtered}"
    envoyer_telegram(done_msg); log(f"\n{done_msg}"); time.sleep(3600)
