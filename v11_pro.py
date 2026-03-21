import sys
from types import ModuleType

# --- 1. CORRECTIF PYTHON 3.14 (MOCK NUMBA) ---
if 'numba' not in sys.modules:
    m = ModuleType('numba')
    m.njit = lambda f=None, *a, **k: (lambda x: x) if f is None else f
    sys.modules['numba'] = m

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import os
import time
import requests
from datetime import datetime
from dotenv import load_dotenv
import pandas as pd
import pandas_ta as ta
from binance.client import Client

# --- 2. SERVEUR WEB (Koyeb Health Check) ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-type', 'text/plain'); self.end_headers()
        self.wfile.write(b"Atlas v13.5 Flex Online")
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
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"]
ACTIVE_MODEL = "gemini-2.5-flash" 

# --- 4. FONCTIONS ---
def envoyer_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def get_data(symbol):
    try:
        h = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "150 hours ago UTC")
        d = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "400 days ago UTC")
        
        df_h = pd.DataFrame(h, columns=['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig'])
        df_h[['close','high','low']] = df_h[['close','high','low']].apply(pd.to_numeric)
        
        # INDICATEURS
        df_h.ta.rsi(length=14, append=True)
        df_h.ta.macd(append=True)
        df_h.ta.atr(length=14, append=True)
        df_h.ta.adx(length=14, append=True)
        
        # STRUCTURE PIVOTS
        high_p, low_p, close_p = df_h['high'].max(), df_h['low'].min(), df_h['close'].iloc[-1]
        pivot = (high_p + low_p + close_p) / 3
        r1, s1 = (2 * pivot) - low_p, (2 * pivot) - high_p

        # TENDANCE LONG TERME
        df_d = pd.DataFrame(d, columns=['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig'])
        df_d['close'] = pd.to_numeric(df_d['close'])
        ema_val = df_d.ta.ema(length=200)
        ema200 = ema_val.iloc[-1] if ema_val is not None and not ema_val.empty else None
        
        res = df_h.iloc[-1].to_dict()
        res['pivot_r1'], res['pivot_s1'] = r1, s1
        return res, ema200
    except: return None, None

def demander_ia_expert(symbol, prix, c_stats, ema200):
    def f_val(val, prec=2):
        try:
            if val is None or str(val).lower() == "nan": return "Indisponible"
            return f"{float(val):.{prec}f}"
        except: return "Indisponible"

    rsi_txt, macd_txt = f_val(c_stats.get('RSI_14'), 1), f_val(c_stats.get('MACD_12_26_9'), 2)
    atr_txt, adx_txt = f_val(c_stats.get('ATR_14'), 2), f_val(c_stats.get('ADX_14'), 1)
    r1_txt, s1_txt, e200_txt = f_val(c_stats.get('pivot_r1')), f_val(c_stats.get('pivot_s1')), f_val(ema200)

    prompt = f"""Agis en tant qu'expert Trader. Analyse {symbol} à {prix}$.
    STATS : RSI {rsi_txt}, MACD {macd_txt}, ATR {atr_txt}, ADX {adx_txt}.
    STRUCTURE : Pivot R1 {r1_txt}, Pivot S1 {s1_txt}, EMA200 {e200_txt}.
    
    MISSION :
    1. Propose un ratio Gain/Risque réaliste entre 1.2 et 2.0.
    2. Si ADX > 30 (tendance forte), vise un ratio élevé (1.5+). Si ADX < 25 (marché mou), un ratio de 1.2 est acceptable.
    3. Le SL doit être cohérent avec l'ATR pour éviter les faux signaux.
    
    Format texte strict (pas de gras) :
    ======================================
    SIGNAL    : [ ACHAT, VENTE ou ATTENTE ]
    CONFIANCE : X%
    --------------------------------------
    TP: X | SL: X (Ratio Flexible)
    TAILLE    : X%
    ANALYSE   : (Justifie le ratio choisi selon l'ADX et les Pivots en 4 phrases).
    ======================================"""

    for key in GOOGLE_KEYS:
        if not key: continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{ACTIVE_MODEL}:generateContent?key={key}"
        try:
            res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
            data = res.json()
            if 'candidates' in data: return data['candidates'][0]['content']['parts'][0]['text']
        except: pass
    return "Quotas IA épuisés."

# --- 5. BOUCLE PRINCIPALE ---
print("⏳ Stabilisation Koyeb (60s)...")
time.sleep(60)

while True:
    print(f"\n🚀 Scan Flex : {datetime.now().strftime('%H:%M:%S')}")
    for s in SYMBOLS:
        last, ema200 = get_data(s)
        if last is not None:
            print(f"🔍 Analyse Trader pour {s}...")
            verdict = demander_ia_expert(s, last['close'], last, ema200)
            msg = f"📊 *ATLAS v13.5 : {s}*\n💰 *Prix :* `{last['close']:,.2f}$` \n\n`{verdict}`"
            envoyer_telegram(msg)
            time.sleep(15) 

    print(f"✅ Cycle terminé ({datetime.now().strftime('%H:%M:%S')}). Repos 3 heures...")
    time.sleep(14400) # Repos de 4 heures
