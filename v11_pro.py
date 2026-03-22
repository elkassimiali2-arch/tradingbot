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
import numpy as np
import pandas_ta as ta
from binance.client import Client

# =============================================================
# SERVEUR WEB (Health Check)
# =============================================================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Atlas v15.2 Online")
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

# Liste mise à jour : Ajout de AVAXUSDT
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "BNBUSDT", "DOGEUSDT", "LINKUSDT"]

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

        raw_h1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "220 hours ago UTC")
        raw_h4 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_4HOUR, "120 days ago UTC")
        raw_d1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "400 days ago UTC")

        df_h1 = to_df(raw_h1)
        df_h4 = to_df(raw_h4)
        df_d1 = to_df(raw_d1)

        # --- Indicateurs H1 ---
        df_h1.ta.rsi(length=14, append=True)
        df_h1.ta.macd(fast=12, slow=26, signal=9, append=True)
        df_h1.ta.adx(length=14, append=True)
        df_h1.ta.bbands(length=20, std=2, append=True)
        df_h1.ta.atr(length=14, append=True)
        
        atr_val = float(df_h1['ATRr_14'].iloc[-1]) if 'ATRr_14' in df_h1.columns else \
                  float(df_h1['ATR_14'].iloc[-1])  if 'ATR_14'  in df_h1.columns else 0.0

        vol_ma20  = df_h1['vol'].rolling(20).mean().iloc[-1]
        vol_ratio = df_h1['vol'].iloc[-1] / vol_ma20 if vol_ma20 > 0 else 1.0

        # --- Indicateurs H4 ---
        df_h4.ta.rsi(length=14, append=True)
        df_h4.ta.adx(length=14, append=True)
        df_h4.ta.macd(fast=12, slow=26, signal=9, append=True)
        rsi_h4  = float(df_h4['RSI_14'].iloc[-1])
        adx_h4  = float(df_h4['ADX_14'].iloc[-1])
        macd_h4 = float(df_h4['MACDh_12_26_9'].iloc[-1])

        # --- EMA200 D1 ---
        ema_series = df_d1.ta.ema(length=200)
        ema200 = float(ema_series.iloc[-1]) if (ema_series is not None and not ema_series.empty) else None

        # --- Pivot J-1 ---
        ph, pl, pc = df_d1['high'].iloc[-2], df_d1['low'].iloc[-2], df_d1['close'].iloc[-2]
        pivot = (ph + pl + pc) / 3
        res = df_h1.iloc[-1].to_dict()
        res.update({
            'atr_val': atr_val, 'vol_ratio': round(vol_ratio, 2),
            'rsi_h4': round(rsi_h4, 1), 'adx_h4': round(adx_h4, 1), 'macd_h4': round(macd_h4, 6),
            'p_r1': round((2 * pivot) - pl, 6), 'p_r2': round(pivot + (ph - pl), 6),
            'p_s1': round((2 * pivot) - ph, 6), 'p_s2': round(pivot - (ph - pl), 6)
        })
        return res, ema200
    except Exception as e:
        print(f"  [DATA ERROR] {symbol}: {e}"); return None, None

# =============================================================
# PRE-FILTRE QUANTITATIF
# =============================================================
def pre_filter(last):
    vol_ratio = float(last.get('vol_ratio', 1.0))
    adx_h1    = float(last.get('ADX_14', 0))
    rsi_h1    = float(last.get('RSI_14', 50))

    if vol_ratio < 0.7:
        return False, f"Volume faible (x{vol_ratio:.2f})"
    if adx_h1 < 18 and 42 < rsi_h1 < 58:
        return False, f"Range plat (ADX={adx_h1:.1f}, RSI={rsi_h1:.1f})"
    return True, f"Setup valide (ADX={adx_h1:.1f}, Vol=x{vol_ratio:.2f})"

# =============================================================
# PROMPT IA
# =============================================================
def demander_ia_expert(symbol, last, ema200):
    def fv(key, prec=2):
        val = last.get(key)
        try: return "N/A" if (val is None or str(val).lower() == 'nan') else f"{float(val):.{prec}f}"
        except: return "N/A"

    close, atr, adx_h1 = float(last['close']), float(last.get('atr_val', 0)), float(last.get('ADX_14', 0))
    ema_txt = f"{ema200:.4f}" if ema200 is not None else "N/A"
    above_ema = "AU-DESSUS" if (ema200 and close > ema200) else "EN-DESSOUS"
    sl_min = round(atr * 1.5, 6)
    ratio_cible = "2.0 a 2.5" if adx_h1 >= 30 else ("1.5 a 2.0" if adx_h1 >= 20 else "1.2 a 1.5")

    prompt = f"""Tu es un trader quantitatif crypto visant des signaux haute probabilite uniquement.
Analyse {symbol} a {close:.6f} $.

=== DONNEES MULTI-TIMEFRAME ===
H1: RSI {fv('RSI_14', 1)} | MACD Hist {fv('MACDh_12_26_9', 6)} | ADX {fv('ADX_14', 1)} | ATR {atr:.6f} | Vol x{fv('vol_ratio')}
H4: RSI {fv('rsi_h4', 1)} | ADX {fv('adx_h4', 1)} | MACD Hist {fv('macd_h4', 6)}
D1: EMA 200 {ema_txt} ({above_ema}) | Pivot R2 {fv('p_r2', 6)} | S2 {fv('p_s2', 6)}

=== REGLES STRICTES ===
1. SL MINIMUM = {sl_min:.6f} (1.5 x ATR). 
2. Ratio Risk/Reward : {ratio_cible}.
3. ACHAT : DI+ > DI-, MACD Histo H1 croissant, RSI H4 > 50.
4. VENTE : DI- > DI+, MACD Histo H1 decroissant, RSI H4 < 50.
5. TAILLE : confiance >= 75% -> 5% cap | 60-74% -> 3% | < 60% -> ATTENTE.

=== FORMAT (STRICT) ===
SIGNAL    : [ ACHAT | VENTE | ATTENTE ]
CONFIANCE : X%
TP        : X.XXXXXX
SL        : X.XXXXXX
RATIO G/R : X.X
TAILLE    : X% du capital
H4 STATUS : [ CONFIRME | MIXTE ]
ANALYSE   : (4 phrases précises)"""

    for key in GOOGLE_KEYS:
        if not key: continue
        try:
            res = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{ACTIVE_MODEL}:generateContent?key={key}",
                json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.1}}, timeout=25)
            return res.json()['candidates'][0]['content']['parts'][0]['text']
        except: pass
    return "SIGNAL : ATTENTE\nCONFIANCE : 0%\nANALYSE : Quotas IA epuises."

# =============================================================
# UTILITAIRES & BOUCLE
# =============================================================
def extract_signal(v):
    v_up = v.upper()
    return 'ACHAT' if 'ACHAT' in v_up else ('VENTE' if 'VENTE' in v_up else 'ATTENTE')

def extract_confidence(v):
    for line in v.split('\n'):
        if 'CONFIANCE' in line.upper():
            try: return int(''.join(filter(str.isdigit, line)))
            except: pass
    return 0

def is_duplicate(symbol, verdict):
    h = hashlib.md5(f"{symbol}:{verdict[:80]}".encode()).hexdigest()
    if last_signal_hash.get(symbol) == h: return True
    last_signal_hash[symbol] = h
    return False

def envoyer_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e: print(f"  [TG ERROR] {e}")

print("Stabilisation Koyeb (60s)...")
time.sleep(60)

while True:
    print(f"\n{'='*30}\nATLAS v15.2 - Scan {datetime.now().strftime('%H:%M:%S')}\n{'='*30}")
    sent, filtered = 0, 0
    for s in SYMBOLS:
        last, ema200 = get_data(s)
        if not last: continue
        ok, reason = pre_filter(last)
        if not ok: continue
        
        verdict = demander_ia_expert(s, last, ema200)
        sig, conf = extract_signal(verdict), extract_confidence(verdict)
        
        if sig == 'ATTENTE' or conf < CONFIDENCE_MIN or is_duplicate(s, verdict):
            if conf < CONFIDENCE_MIN and sig != 'ATTENTE': filtered += 1
            continue

        emoji = "🟢" if sig == 'ACHAT' else "🔴"
        msg = f"{emoji} *ATLAS v15.2 - {s}*\n💰 Prix : `{last['close']:.6f} $` | Conf : *{conf}%*\n```\n{verdict}\n```"
        envoyer_telegram(msg)
        sent += 1; time.sleep(12)

    done_msg = f"🏁 *Cycle termine* - Signaux: *{sent}/{len(SYMBOLS)}* | Filtres: {filtered}"
    envoyer_telegram(done_msg); print(f"\n{done_msg}"); time.sleep(3600)
