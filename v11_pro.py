import sys
from types import ModuleType

# --- 1. CORRECTIF PYTHON 3.14 (Priorité Haute) ---
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

# --- 2. SERVEUR FANTÔME POUR RENDER (Port 10000) ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b"Atlas Bot v11 is live")

def run_web_server():
    server = HTTPServer(('0.0.0.0', 10000), SimpleHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# --- 3. CONFIGURATION (Chargement depuis key.env) ---
load_dotenv("key.env")
BINANCE_KEY = os.getenv('BINANCE_API_KEY')
# Système multi-clés pour Google Gemini
GOOGLE_KEYS = [os.getenv('GOOGLE_API_KEY'), os.getenv('GOOGLE_API_KEY_2')]
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

client_binance = Client(BINANCE_KEY, "")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"]

# --- 4. FONCTIONS DE TRAITEMENT ---
def envoyer_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def get_expert_data(symbol):
    try:
        cols = ['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig']
        h = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "100 hours ago UTC")
        df = pd.DataFrame(h, columns=cols)
        df[['close','high','low','open']] = df[['close','high','low','open']].apply(pd.to_numeric)
        
        # Calcul des indicateurs techniques (Python)
        df.ta.rsi(length=14, append=True)
        df.ta.ema(length=20, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        
        # Récupération de l'EMA200 sur 1 jour
        d = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "250 days ago UTC")
        df_d = pd.DataFrame(d, columns=cols)
        df_d['close'] = pd.to_numeric(df_d['close'])
        ema200 = df_d.ta.ema(length=200).iloc[-1]
        
        return df.iloc[-1], ema200
    except: return None, None

def demander_ia_expert(symbol, prix, stats, ema200):
    # Prompt de la v9 (Format strict sans gras)
    prompt = f"""Expert Trading Crypto. Analyse {symbol} à {prix}$.
    Stats: RSI {stats.get('RSI_14',0):.1f}, MACD {stats.get('MACDH_12_26_9',0):.2f}, EMA200 {ema200:.2f}.
    
    Réponds EXCLUSIVEMENT sous ce format texte (sans gras, sans astérisques) :
    ======================================
    SIGNAL    : [ ACHAT, VENTE ou ATTENTE ]
    CONFIANCE : X%
    --------------------------------------
    TP: X | SL: X
    TAILLE    : X%
    ANALYSE   : (Une phrase courte d'explication)
    ======================================"""

    # Système de bascule multi-clés (Failover)
    for i, key in enumerate(GOOGLE_KEYS):
        if not key: continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
        try:
            res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
            data = res.json()
            if 'candidates' in data:
                return data['candidates'][0]['content']['parts'][0]['text']
        except: print(f"⚠️ Clé Gemini {i+1} en erreur.")

    # Si toutes les clés ont échoué
    return "======================================\nSIGNAL    : [ ERREUR ]\nANALYSE   : Quotas IA épuisés sur toutes les clés.\n======================================"

# --- 5. BOUCLE PRINCIPALE (INTÉGRATION DU VISUEL) ---
print("="*50)
print("🚀 ATLAS v11 PRO : INTÉGRATION VISUELLE & DOUBLE CLÉ")
print("="*50)

while True:
    ts = datetime.now().strftime('%H:%M:%S')
    
    for s in SYMBOLS:
        last, ema200 = get_expert_data(s)
        
        if last is not None:
            prix = last['close']
            
            # --- CALCUL DE LA TENDANCE (Image 3) ---
            tendance = "Bull 📈" if prix > ema200 else "Bear 📉"
            
            # Construction du Header d'Analyse Complète
            # On ajoute des étoiles pour le gras sur la première ligne
            header_integre = (
                f"📊 *ANALYSE TECHNIQUE : {s}*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💰 *Prix :* `{prix:,.2f}$` \n"
                f"📈 *Tendance :* `{tendance}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
            )
            
            # Affichage console (Look Atlas v11)
            print(f"\n📢 SCAN DU {ts}")
            print(header_integre.replace('*','')) # Affiche proprement dans la console
            print(f"🔍 Consultation de l'IA (Format Atlas)...")
            
            # Consultation IA pour le Dashboard
            verdict = demander_ia_expert(s, prix, last.to_dict(), ema200)
            print(verdict)
            
            # Envoi Telegram : Tendance + Verdict aligné
            # On utilise les backticks uniquement autour du verdict pour garder l'alignement
            msg_telegram = f"{header_integre}\n🔍 *Verdict IA :*\n`{verdict}`"
            envoyer_telegram(msg_telegram)
            
            time.sleep(5) 

    print("\n⏳ Scan terminé. Repos 6 heures...")
    time.sleep(21600)
