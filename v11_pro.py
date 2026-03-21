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

# --- 2. SERVEUR WEB (Indispensable pour Koyeb Health Check) ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Atlas Bot v13.3 Online")
    def log_message(self, format, *args): return

def run_web_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    print(f"✅ Serveur Health Check prêt sur le port {port}")
    server.serve_forever()

# Lancement immédiat du serveur pour que Koyeb voit le bot "Healthy"
threading.Thread(target=run_web_server, daemon=True).start()

# --- 3. CONFIGURATION & CLÉS ---
load_dotenv("key.env")
BINANCE_KEY = os.getenv('BINANCE_API_KEY')
GOOGLE_KEYS = [os.getenv('GOOGLE_API_KEY'), os.getenv('GOOGLE_API_KEY_2')]
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

client_binance = Client(BINANCE_KEY, "")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"]
ACTIVE_MODEL = "gemini-2.5-flash" 

# --- 4. FONCTIONS DE COMMUNICATION ---
def envoyer_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=10)
    except: pass

# --- 5. ANALYSE TECHNIQUE ---
def get_data(symbol):
    try:
        # On demande 150h et 400j pour assurer le calcul des moyennes mobiles
        h = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "150 hours ago UTC")
        d = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "400 days ago UTC")
        
        # Court terme (1h)
        df_h = pd.DataFrame(h, columns=['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig'])
        df_h[['close','high','low']] = df_h[['close','high','low']].apply(pd.to_numeric)
        
        df_h.ta.rsi(length=14, append=True)
        df_h.ta.ema(length=20, append=True)
        df_h.ta.macd(append=True)
        df_h.ta.bbands(append=True)
        df_h.ta.atr(append=True)
        
        # Long terme (1j)
        df_d = pd.DataFrame(d, columns=['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig'])
        df_d['close'] = pd.to_numeric(df_d['close'])
        ema_val = df_d.ta.ema(length=200)
        
        ema200 = None
        if ema_val is not None and not ema_val.empty:
            ema200 = ema_val.iloc[-1]
        
        return df_h.iloc[-1], ema200
    except Exception as e:
        print(f"❌ Erreur Data {symbol}: {e}")
        return None, None

def demander_ia_expert(symbol, prix, c_stats, ema200):
    # --- BOUCLIER ANTI-CRASH (Transforme None en "Indisponible") ---
    def f_val(val, prec=2):
        try:
            if val is None or str(val).lower() == "nan":
                return "Indisponible"
            return f"{float(val):.{prec}f}"
        except:
            return "Indisponible"

    rsi_txt = f_val(c_stats.get('RSI_14'), 1)
    ema20_txt = f_val(c_stats.get('EMA_20'), 1)
    macd_txt = f_val(c_stats.get('MACD_12_26_9'), 2)
    atr_txt = f_val(c_stats.get('ATR_14'), 2)
    e200_txt = f_val(ema200, 2)

    prompt = f"""Salut Trader ! Agis en tant qu'expert trader, respecte le format ci dessous et sois concis. Analyse {symbol} à {prix}$.
    Données techniques : RSI {rsi_txt}, EMA20 {ema20_txt}, MACD {macd_txt}, ATR {atr_txt}, EMA200 {e200_txt}.
    
    Réponds EXCLUSIVEMENT sous ce format texte (pas de gras) :
    ======================================
    SIGNAL    : [ ACHAT, VENTE ou ATTENTE ]
    CONFIANCE : X%
    --------------------------------------
    TP: X | SL: X
    TAILLE    : X%
    ANALYSE   : (Explique ta décision en utilisant les indicateurs fournis en 4 phrases précises).
    ======================================"""

    for i, key in enumerate(GOOGLE_KEYS):
        if not key: continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{ACTIVE_MODEL}:generateContent?key={key}"
        try:
            res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
            data = res.json()
            if 'candidates' in data:
                return data['candidates'][0]['content']['parts'][0]['text']
        except: pass
    
    return "SIGNAL : [ ERREUR ]\nANALYSE : Quotas épuisés ou erreur technique."

# --- 6. BOUCLE PRINCIPALE ---
print("⏳ Stabilisation Koyeb (60s) pour valider le Health Check...")
time.sleep(60)

while True:
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"\n🚀 LANCEMENT DU SCAN : {ts}")
    
    for s in SYMBOLS:
        last, ema200 = get_data(s)
        if last is not None:
            print(f"🔍 Consultation IA pour {s}...")
            prix_actuel = last['close']
            verdict = demander_ia_expert(s, prix_actuel, last.to_dict(), ema200)
            
            header = f"📊 *ATLAS Recap : {s}*\n💰 *Prix :* `{prix_actuel:,.2f}$`"
            msg = f"{header}\n\n`{verdict}`"
            envoyer_telegram(msg)
            
            # Pause de 15s pour éviter de saturer les clés Google
            time.sleep(15) 

    print(f"✅ Cycle terminé. Repos 1 heure...")
    time.sleep(10800)
