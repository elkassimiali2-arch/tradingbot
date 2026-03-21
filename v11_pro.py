import sys
from types import ModuleType

# --- 1. CORRECTIF PYTHON 3.14 ---
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

# --- 2. SERVEUR WEB ROBUSTE (Pour satisfaire Koyeb) ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return # Désactive les logs du serveur web pour ne pas polluer

def run_web_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    print(f"✅ Serveur Health Check prêt sur le port {port}")
    server.serve_forever()

# Lancement du serveur en premier
web_thread = threading.Thread(target=run_web_server, daemon=True)
web_thread.start()

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
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def get_data(symbol):
    try:
        h = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "100 hours ago UTC")
        d = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "300 days ago UTC")
        df_h = pd.DataFrame(h, columns=['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig'])
        df_h[['close','high','low']] = df_h[['close','high','low']].apply(pd.to_numeric)
        
        df_h.ta.rsi(length=14, append=True)
        df_h.ta.ema(length=20, append=True)
        df_h.ta.macd(append=True)
        df_h.ta.bbands(append=True)
        df_h.ta.atr(append=True)
        
        df_d = pd.DataFrame(d, columns=['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig'])
        df_d['close'] = pd.to_numeric(df_d['close'])
        ema200 = df_d.ta.ema(length=200).iloc[-1]
        
        return df_h.iloc[-1], ema200
    except: return None, None

def demander_ia_expert(symbol, prix, c_stats, ema200):
    prompt = f"""Expert Trader pour Ali G. Analyse {symbol} à {prix}$. 
    RSI {c_stats.get('RSI_14'):.1f}, EMA20 {c_stats.get('EMA_20'):.1f}, MACD {c_stats.get('MACD_12_26_9'):.2f}, ATR {c_stats.get('ATR_14'):.2f}, EMA200 {ema200:.2f}.
    Réponds en 4 phrases techniques précises (sans gras). Format: SIGNAL, CONFIANCE, TP, SL, ANALYSE."""

    for i, key in enumerate(GOOGLE_KEYS):
        if not key: continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{ACTIVE_MODEL}:generateContent?key={key}"
        try:
            res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
            data = res.json()
            if 'candidates' in data:
                return data['candidates'][0]['content']['parts'][0]['text']
        except: pass
    return "Quotas IA épuisés."

# --- 5. LOGIQUE DE LANCEMENT ---
print("⏳ Stabilisation Koyeb (60s)...")
time.sleep(60) # Laisse le temps à Koyeb de voir que le serveur est "Healthy"

while True:
    print(f"\n🚀 Lancement du scan : {datetime.now().strftime('%H:%M:%S')}")
    
    for s in SYMBOLS:
        last, ema200 = get_data(s)
        if last is not None:
            print(f"🔍 Analyse de {s}...")
            verdict = demander_ia_expert(s, last['close'], last.to_dict(), ema200)
            
            msg = f"📊 *ATLAS v13.1 : {s}*\n💰 *Prix :* `{last['close']:,.2f}$` \n\n`{verdict}`"
            envoyer_telegram(msg)
            
            # 15 secondes entre chaque crypto (important pour les quotas)
            time.sleep(15) 

    print("✅ Cycle terminé. Mise en veille pour 1 heure...")
    # Le repos d'une heure
    time.sleep(10800)
