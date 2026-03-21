import sys
from types import ModuleType

# --- 1. CORRECTIF CRITIQUE POUR PYTHON 3.14 (NUMBA MOCK) ---
# Ce bloc doit impérativement rester au début du fichier
if 'numba' not in sys.modules:
    m = ModuleType('numba')
    m.njit = lambda f=None, *a, **k: (lambda x: x) if f is None else f
    sys.modules['numba'] = m

import os
import time
import requests
import json
from datetime import datetime
from dotenv import load_dotenv
import pandas as pd
import pandas_ta as ta
from binance.client import Client

# --- 2. CONFIGURATION & CLÉS ---
load_dotenv("key.env")
BINANCE_KEY = os.getenv('BINANCE_API_KEY')
GOOGLE_KEY = os.getenv('GOOGLE_API_KEY')

# Initialisation Binance (Lecture seule)
client_binance = Client(BINANCE_KEY, "")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"]
HISTORIQUE_FILE = "historique_signaux.csv"

# --- 3. DIAGNOSTIC DES MODÈLES GOOGLE ---
def lister_modeles_valides():
    """Vérifie quels modèles sont autorisés avec ta clé API"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GOOGLE_KEY}"
    try:
        response = requests.get(url)
        res = response.json()
        if 'models' in res:
            # On filtre les modèles capables de générer du contenu
            return [m['name'].split('/')[-1] for m in res['models'] if 'generateContent' in m['supportedGenerationMethods']]
        else:
            return []
    except:
        return []

print("🔍 Vérification de tes droits Google AI...")
MODELES = lister_modeles_valides()

if not MODELES:
    print("⚠️ Aucun modèle IA trouvé. Vérifie ta clé dans key.env.")
    ACTIVE_MODEL = "gemini-1.5-flash" # Valeur par défaut au cas où
else:
    ACTIVE_MODEL = MODELES[0]
    print(f"✅ Succès ! Modèle détecté : {ACTIVE_MODEL}")

# --- 4. FONCTIONS DE CALCUL & IA ---
def get_data(symbol):
    try:
        # Récupère 100h pour le court terme et 300j pour l'EMA 200
        h_data = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "100 hours ago UTC")
        d_data = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "300 days ago UTC")
        
        # DataFrame Court Terme (1h)
        df_h = pd.DataFrame(h_data, columns=['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig'])
        df_h[['close','high','low']] = df_h[['close','high','low']].apply(pd.to_numeric)
        df_h.ta.rsi(length=14, append=True)
        df_h.ta.ema(length=20, append=True)
        
        # DataFrame Long Terme (1j)
        df_d = pd.DataFrame(d_data, columns=['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig'])
        df_d['close'] = pd.to_numeric(df_d['close'])
        df_d.ta.ema(length=200, append=True)
        
        return df_h.iloc[-1], df_d.iloc[-1]
    except Exception as e:
        print(f"❌ Erreur Data {symbol}: {e}")
        return None, None

def demander_ia(symbol, prix, c_stats, f_stats):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{ACTIVE_MODEL}:generateContent?key={GOOGLE_KEY}"
    headers = {'Content-Type': 'application/json'}
    
    prompt = f"""Expert Trader pour Ali G. Analyse {symbol} à {prix}$.
    Court Terme (1h): RSI {c_stats.get('RSI_14'):.1f}, EMA20 {c_stats.get('EMA_20'):.1f}.
    Long Terme (1j): EMA200 {f_stats.get('EMA_200'):.1f}.
    
    Réponds précisément en français :
    1. SCORE : X/100
    2. ACTION : (ACHAT, VENTE ou ATTENTE)
    3. STRATÉGIE : % Portefeuille, Take Profit (prix), Stop Loss (prix)
    4. POURQUOI : 1 phrase simple."""
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        res = response.json()
        return res['candidates'][0]['content']['parts'][0]['text']
    except:
        return "⚠️ L'IA n'a pas pu répondre (Vérifie ton quota ou ta clé)."

# --- 5. BOUCLE DE SCAN ---
print("\n" + "="*50)
print("🚀 BOTG-TRADE : ANALYSE COMPLETE + IA ACTIVE")
print("="*50)

while True:
    print(f"\n📢 SCAN DU {datetime.now().strftime('%H:%M:%S')}")
    
    for symbol in SYMBOLS:
        c, f = get_data(symbol)
        
        if c is not None and f is not None:
            prix = c['close']
            rsi = c.get('RSI_14', 50)
            ema200 = f.get('EMA_200', 0)
            
            print(f"\n💎 --- {symbol} ---")
            
            # --- ANALYSE TECHNIQUE SCRIPT ---
            tend = "📈 BULL" if prix > ema200 else "📉 BEAR"
            print(f"  [TECH] Tendance: {tend} | RSI: {rsi:.1f} | Prix: {prix:,.2f}$")
            
            # --- ANALYSE IA ---
            print(f"  🔍 Consultation de {ACTIVE_MODEL}...")
            verdict_ia = demander_ia(symbol, prix, c.to_dict(), f.to_dict())
            print(f"  🤖 VERDICT IA :\n{verdict_ia}")
            
            # Sauvegarde CSV
            with open(HISTORIQUE_FILE, "a", encoding="utf-8") as file:
                date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                verdict_clean = verdict_ia.replace('\n', ' ').replace('"', "'")
                file.write(f"{date_str},{symbol},{prix},\"{verdict_clean}\"\n")
                
    print(f"\n⏳ Scan terminé. Repos 30 minutes...")
    time.sleep(10800)
