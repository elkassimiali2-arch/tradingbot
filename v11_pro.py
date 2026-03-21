import sys
from types import ModuleType

# --- CORRECTIF PYTHON 3.14 (MOCK NUMBA) ---
if 'numba' not in sys.modules:
    m = ModuleType('numba')
    m.njit = lambda f=None, *a, **k: (lambda x: x) if f is None else f
    sys.modules['numba'] = m

import threading
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
import os
import time
import requests
from datetime import datetime
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import pandas_ta as ta
from binance.client import Client

# ============================================================
# SERVEUR WEB (Health Check Koyeb)
# ============================================================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Atlas v14.0 Online")
    def log_message(self, format, *args): return

def run_web_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ============================================================
# CONFIGURATION
# ============================================================
load_dotenv("key.env")
BINANCE_KEY = os.getenv('BINANCE_API_KEY')
GOOGLE_KEYS = [os.getenv('GOOGLE_API_KEY'), os.getenv('GOOGLE_API_KEY_2')]
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

client_binance = Client(BINANCE_KEY, "")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT", "NEARUSDT"]
ACTIVE_MODEL = "gemini-2.5-flash"

last_signal_hash = {}

# ============================================================
# TELEGRAM
# ============================================================
def envoyer_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

# ============================================================
# COLLECTE DONNÉES + INDICATEURS
# ============================================================
def get_data(symbol):
    try:
        raw_h1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "200 hours ago UTC")
        raw_h4 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_4HOUR, "100 days ago UTC")
        raw_d1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "400 days ago UTC")

        cols = ['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig']

        def to_df(raw):
            df = pd.DataFrame(raw, columns=cols)
            for c in ['open','high','low','close','vol']:
                df[c] = pd.to_numeric(df[c])
            return df.copy()

        df_h1, df_h4, df_d1 = to_df(raw_h1), to_df(raw_h4), to_df(raw_d1)

        # Indicateurs H1
        df_h1.ta.rsi(length=14, append=True)
        df_h1.ta.macd(append=True)
        df_h1.ta.atr(append=True)
        df_h1.ta.adx(append=True)
        df_h1.ta.bbands(append=True)

        vol_ma20 = df_h1['vol'].rolling(20).mean().iloc[-1]
        vol_now = df_h1['vol'].iloc[-1]
        vol_ratio = vol_now / vol_ma20 if vol_ma20 > 0 else 1.0

        # Indicateurs H4
        df_h4.ta.rsi(length=14, append=True)
        df_h4.ta.adx(length=14, append=True)
        rsi_h4, adx_h4 = df_h4['RSI_14'].iloc[-1], df_h4['ADX_14'].iloc[-1]

        # EMA200 Daily
        ema_series = df_d1.ta.ema(length=200)
        ema200 = float(ema_series.iloc[-1]) if ema_series is not None and not ema_series.empty else None

        # Pivot J-1
        prev_high, prev_low, prev_close = df_d1['high'].iloc[-2], df_d1['low'].iloc[-2], df_d1['close'].iloc[-2]
        pivot = (prev_high + prev_low + prev_close) / 3
        r1, s1 = (2 * pivot) - prev_low, (2 * pivot) - prev_high

        last = df_h1.iloc[-1].to_dict()
        last.update({'vol_ratio': round(vol_ratio, 2), 'rsi_h4': rsi_h4, 'adx_h4': adx_h4,
                     'pivot': round(pivot, 4), 'pivot_r1': round(r1, 4), 'pivot_s1': round(s1, 4)})
        return last, ema200
    except Exception as e:
        print(f"[DATA ERROR] {symbol}: {e}"); return None, None

# ============================================================
# PRÉ-FILTRE + IA
# ============================================================
def pre_filter(last, ema200):
    vol_r = float(last.get('vol_ratio', 1.0))
    if vol_r < 0.8: return False, "Volume trop faible"
    return True, f"Setup valide | Vol x{vol_r:.2f}"

def demander_ia_expert(symbol, last, ema200):
    def fv(key, prec=2):
        val = last.get(key)
        try: return f"{float(val):.{prec}f}" if val is not None else "N/A"
        except: return "N/A"

    close = float(last['close'])
    above_ema = "AU-DESSUS" if (ema200 and close > ema200) else "EN-DESSOUS"
    
    prompt = f"""Expert Trader. Analyse {symbol} à {close:.4f}$.
    H1: RSI {fv('RSI_14', 1)}, MACD {fv('MACD_12_26_9')}, ADX {fv('ADX_14', 1)}, Vol x{fv('vol_ratio')}.
    H4: RSI {fv('rsi_h4', 1)}, ADX {fv('adx_h4', 1)}.
    D1: EMA200 {fv(None) if ema200 is None else f'{ema200:.2f}'} ({above_ema}), Pivot R1 {fv('pivot_r1')}, S1 {fv('pivot_s1')}.
    
    MISSION: 
    1. Signal ACHAT/VENTE/ATTENTE. 
    2. Ratio G/R flexible 1.2-2.0. 
    3. Justifie selon confluence H1/H4.
    
    Réponds au format :
    SIGNAL : [ACHAT/VENTE/ATTENTE]
    CONFIANCE : X%
    TP : X | SL : X
    ANALYSE : (4 phrases max)"""

    for key in GOOGLE_KEYS:
        if not key: continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{ACTIVE_MODEL}:generateContent?key={key}"
        try:
            res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}}, timeout=20)
            return res.json()['candidates'][0]['content']['parts'][0]['text']
        except: pass
    return "Quotas épuisés."

def signal_hash(symbol, verdict):
    first_line = verdict.strip().split('\n')[0][:80]
    return hashlib.md5(f"{symbol}:{first_line}".encode()).hexdigest()

def is_duplicate(symbol, verdict):
    h = signal_hash(symbol, verdict)
    if last_signal_hash.get(symbol) == h: return True
    last_signal_hash[symbol] = h; return False

# ============================================================
# BOUCLE PRINCIPALE
# ============================================================
print("⏳ Stabilisation Koyeb (60s)...")
time.sleep(60)

while True:
    print(f"\n🚀 Scan Atlas v14.0 - {datetime.now().strftime('%H:%M:%S')}")
    signals_sent = 0
    for s in SYMBOLS:
        last, ema200 = get_data(s)
        if last is None: continue
        
        ok, reason = pre_filter(last, ema200)
        if not ok: continue
        
        verdict = demander_ia_expert(s, last, ema200)
        if "ATTENTE" in verdict.upper() or is_duplicate(s, verdict): continue

        emoji = "🟢" if "ACHAT" in verdict.upper() else "🔴"
        envoyer_telegram(f"{emoji} *{s}*\n`{verdict}`")
        signals_sent += 1
        time.sleep(12)

    envoyer_telegram(f"✅ Cycle terminé. Envoyés: {signals_sent}/{len(SYMBOLS)}. Prochain scan: 0,5h.")
    time.sleep(1800)
