import sys
from types import ModuleType

# --- 1. CORRECTIF PYTHON 3.14 (MOCK NUMBA) ---
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

# --- 2. SERVEUR WEB (Health Check Koyeb) ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Atlas v14.1 Full Logs Online")
    def log_message(self, format, *args): return

def run_web_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# --- 3. CONFIGURATION ---
load_dotenv("key.env")
BINANCE_KEY = os.getenv('BINANCE_API_KEY')
GOOGLE_KEYS = [os.getenv('GOOGLE_API_KEY'), os.getenv('GOOGLE_API_KEY_2')]
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

client_binance = Client(BINANCE_KEY, "")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT", "NEARUSDT"]
ACTIVE_MODEL = "gemini-2.5-flash"
last_signal_hash = {}

# --- 4. FONCTIONS DE RÉCUPÉRATION ---
def get_data(symbol):
    try:
        raw_h1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "200 hours ago UTC")
        raw_h4 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_4HOUR, "100 days ago UTC")
        raw_d1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "400 days ago UTC")
        
        cols = ['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig']
        def to_df(raw):
            df = pd.DataFrame(raw, columns=cols)
            for c in ['open','high','low','close','vol']: df[c] = pd.to_numeric(df[c])
            return df
            
        df_h1, df_h4, df_d1 = to_df(raw_h1), to_df(raw_h4), to_df(raw_d1)
        
        df_h1.ta.rsi(append=True); df_h1.ta.macd(append=True); df_h1.ta.atr(append=True); df_h1.ta.adx(append=True)
        df_h4.ta.rsi(append=True); df_h4.ta.adx(append=True)
        
        vol_ma20 = df_h1['vol'].rolling(20).mean().iloc[-1]
        vol_ratio = df_h1['vol'].iloc[-1] / vol_ma20 if vol_ma20 > 0 else 1.0
        
        ema_series = df_d1.ta.ema(length=200)
        ema200 = float(ema_series.iloc[-1]) if ema_series is not None else None
        
        ph, pl, pc = df_d1['high'].iloc[-2], df_d1['low'].iloc[-2], df_d1['close'].iloc[-2]
        pivot = (ph + pl + pc) / 3
        r1, s1 = (2 * pivot) - pl, (2 * pivot) - ph
        
        res = df_h1.iloc[-1].to_dict()
        res.update({
            'vol_ratio': round(vol_ratio, 2), 
            'rsi_h4': df_h4['RSI_14'].iloc[-1], 
            'adx_h4': df_h4['ADX_14'].iloc[-1], 
            'p_r1': r1, 'p_s1': s1
        })
        return res, ema200
    except Exception as e:
        print(f"  ❌ Erreur Data {symbol}: {e}")
        return None, None

def pre_filter(last):
    vol_r = float(last.get('vol_ratio', 1.0))
    if vol_r < 0.7: return False, f"Volume faible (x{vol_r})"
    return True, f"Volume OK (x{vol_r})"

# --- 5. LOGIQUE IA ---
def demander_ia_expert(symbol, last, ema200):
    def fv(key, prec=2):
        val = last.get(key)
        return f"{float(val):.{prec}f}" if val is not None else "N/A"
        
    prompt = f"""Agis en tant qu'Expert Trader. Analyse {symbol} à {last['close']:.2f}$. 
    H1: RSI {fv('RSI_14', 1)}, Vol x{fv('vol_ratio')}, ADX {fv('ADX_14', 1)}. 
    H4: RSI {fv('rsi_h4', 1)}. 
    D1: EMA200 {ema200:.2f if ema200 else 'N/A'}, Pivot R1 {fv('p_r1')}, S1 {fv('p_s1')}. 
    
    MISSION: Signal ACHAT/VENTE/ATTENTE. Ratio 1.2-2.0. 
    Format: SIGNAL: [X], TP: [X], SL: [X], ANALYSE: [Justification technique courte]."""

    for key in GOOGLE_KEYS:
        if not key: continue
        try:
            res = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{ACTIVE_MODEL}:generateContent?key={key}", 
                json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}}, 
                timeout=20
            )
            return res.json()['candidates'][0]['content']['parts'][0]['text']
        except: pass
    return "ATTENTE (Quotas épuisés)"

def is_duplicate(symbol, verdict):
    first_line = verdict.strip().splitlines()[0] if verdict.strip() else ""
    h = hashlib.md5(f"{symbol}:{first_line}".encode()).hexdigest()
    if last_signal_hash.get(symbol) == h: return True
    last_signal_hash[symbol] = h
    return False

# --- 6. BOUCLE PRINCIPALE ---
print("⏳ Stabilisation Koyeb (60s)...")
time.sleep(60)

while True:
    print(f"\n🚀 SCAN ATLAS v14.1 - {datetime.now().strftime('%H:%M:%S')}")
    signals_sent = 0
    
    for s in SYMBOLS:
        print(f"🔍 {s} : Analyse en cours...")
        last, ema200 = get_data(s)
        if last is None: continue
        
        ok, reason = pre_filter(last)
        if not ok:
            print(f"  ⏭️ Ignoré : {reason}")
            continue
            
        verdict = demander_ia_expert(s, last, ema200)
        print(f"  🤖 Verdict IA :\n{verdict}\n") # Affiche tout dans Koyeb
        
        if "ATTENTE" in verdict.upper() or is_duplicate(s, verdict):
            continue
            
        emoji = "🟢" if "ACHAT" in verdict.upper() else "🔴"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        msg = f"{emoji} *{s}*\n`{verdict}`"
        try: requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        except: pass
        
        signals_sent += 1
        time.sleep(12) # Anti-spam
        
    print(f"✅ Cycle terminé. Envoyés: {signals_sent}/{len(SYMBOLS)}. Repos 0,5h.")
    time.sleep(1800)
