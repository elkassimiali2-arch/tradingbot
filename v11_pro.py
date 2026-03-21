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

# --- 2. SERVEUR FANTÔME (Indispensable pour Koyeb) ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b"Atlas Bot v12 (Gemini 2.5) is Active")

def run_web_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# --- 3. CONFIGURATION & CLÉS ---
load_dotenv("key.env")
BINANCE_KEY = os.getenv('BINANCE_API_KEY')
GOOGLE_KEYS = [os.getenv('GOOGLE_API_KEY'), os.getenv('GOOGLE_API_KEY_2')]
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

client_binance = Client(BINANCE_KEY, "")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"]
ACTIVE_MODEL = "gemini-2.5-flash"  # Ton modèle champion

# --- 4. FONCTIONS TECHNIQUES ---
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
        df_d = pd.DataFrame(d, columns=['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig'])
        df_d['close'] = pd.to_numeric(df_d['close'])
        ema200 = df_d.ta.ema(length=200).iloc[-1]
        return df_h.iloc[-1], ema200
    except: return None, None

def demander_ia_expert(symbol, prix, c_stats, ema200):
    # Prompt personnalisé pour Ali G avec demande d'analyse longue
    prompt = f"""Salut Trader ! Agis en tant qu'expert trader. Analyse {symbol} à {prix}$.
    Données techniques : RSI {c_stats.get('RSI_14'):.1f}, EMA20 {c_stats.get('EMA_20'):.1f}, EMA200 {ema200:.2f}.
    
    Réponds EXCLUSIVEMENT sous ce format texte (sans gras, sans astérisques) :
    ======================================
    SIGNAL    : [ ACHAT, VENTE ou ATTENTE ]
    CONFIANCE : X%
    --------------------------------------
    TP: X | SL: X
    TAILLE    : X%
    ANALYSE   : (Rédige une analyse technique détaillée d'environ 4 phrases expliquant la structure du marché, les supports/résistances et le momentum actuel).
    ======================================"""

    for i, key in enumerate(GOOGLE_KEYS):
        if not key: continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{ACTIVE_MODEL}:generateContent?key={key}"
        try:
            res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
            data = res.json()
            if 'candidates' in data:
                return data['candidates'][0]['content']['parts'][0]['text']
            elif 'error' in data:
                print(f"⚠️ Clé {i+1} : {data['error'].get('message')}")
        except: pass
    return "SIGNAL : [ ERREUR ]\nANALYSE : Quotas épuisés sur toutes les clés Gemini 2.5."

# --- 5. BOUCLE DE SCAN ---
print(f"🚀 ATLAS v12 (Moteur {ACTIVE_MODEL}) ACTIF")

while True:
    ts = datetime.now().strftime('%H:%M:%S')
    for s in SYMBOLS:
        last, ema200 = get_data(s)
        if last is not None:
            prix = last['close']
            print(f"[{ts}] Analyse de {s} avec {ACTIVE_MODEL}...")
            verdict = demander_ia_expert(s, prix, last.to_dict(), ema200)
            
            header = f"📊 *BOT-TRADE : {s}*\n💰 *Prix :* `{prix:,.2f}$`"
            msg = f"{header}\n\n`{verdict}`"
            envoyer_telegram(msg)
            time.sleep(5) 

    print(f"⏳ Scan terminé. Repos 1 heure...")
    time.sleep(10800)
