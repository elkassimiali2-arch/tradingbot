import sys
from types import ModuleType

# --- 1. CORRECTIF PYTHON 3.14 ---
if 'numba' not in sys.modules:
    m = ModuleType('numba')
    m.njit = lambda f=None, *a, **k: (lambda x: x) if f is None else f
    sys.modules['numba'] = m

import os
import time
import requests
from datetime import datetime
from dotenv import load_dotenv
import pandas as pd
import pandas_ta as ta
from binance.client import Client

# --- 2. CONFIGURATION & CLÉS ---
load_dotenv("key.env")
BINANCE_KEY = os.getenv('BINANCE_API_KEY')
# On récupère les deux clés depuis Koyeb
GOOGLE_KEYS = [os.getenv('GOOGLE_API_KEY'), os.getenv('GOOGLE_API_KEY_2')]

client_binance = Client(BINANCE_KEY, "")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"]
HISTORIQUE_FILE = "historique_signaux.csv"

# --- 3. FONCTIONS DATA & IA ---
def get_data(symbol):
    try:
        h_data = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "100 hours ago UTC")
        d_data = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "300 days ago UTC")
        
        df_h = pd.DataFrame(h_data, columns=['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig'])
        df_h[['close','high','low']] = df_h[['close','high','low']].apply(pd.to_numeric)
        df_h.ta.rsi(length=14, append=True)
        df_h.ta.ema(length=20, append=True)
        
        df_d = pd.DataFrame(d_data, columns=['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig'])
        df_d['close'] = pd.to_numeric(df_d['close'])
        ema200 = df_d.ta.ema(length=200).iloc[-1]
        
        return df_h.iloc[-1], ema200
    except Exception as e:
        print(f"❌ Erreur Data {symbol}: {e}")
        return None, None

def demander_ia_detaillee(symbol, prix, c_stats, ema200):
    prompt = f"""Expert Trader. Analyse {symbol} à {prix}$.
    Court Terme (1h): RSI {c_stats.get('RSI_14'):.1f}, EMA20 {c_stats.get('EMA_20'):.1f}.
    Long Terme (1j): EMA200 {ema200:.2f}.
    
    Réponds en français sous ce format :
    1. SCORE : X/100
    2. ACTION : (ACHAT, VENTE ou ATTENTE)
    3. STRATÉGIE : % Portefeuille, TP et SL précis.
    4. ANALYSE : (Fournis une analyse technique détaillée d'au moins 3 ou 4 phrases sur la structure du marché, les supports et les résistances)."""

    # Boucle sur tes deux clés pour éviter le quota plein
    for i, key in enumerate(GOOGLE_KEYS):
        if not key: continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
        try:
            res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
            data = res.json()
            if 'candidates' in data:
                return data['candidates'][0]['content']['parts'][0]['text']
            else:
                print(f"⚠️ Clé {i+1} : {data.get('error', {}).get('message', 'Erreur inconnue')}")
        except:
            print(f"⚠️ Erreur technique avec la clé {i+1}")
            
    return "⚠️ Quotas IA épuisés sur TOUTES les clés. Attends le reset."

# --- 4. BOUCLE DE SCAN ---
print("\n" + "="*50)
print("🚀 BOTG-TRADE : DOUBLE CLÉ + ANALYSE DÉTAILLÉE")
print("="*50)

while True:
    print(f"\n📢 SCAN DU {datetime.now().strftime('%H:%M:%S')}")
    
    for symbol in SYMBOLS:
        c, ema200 = get_data(symbol)
        
        if c is not None:
            prix = c['close']
            print(f"\n💎 --- {symbol} ---")
            
            # --- ANALYSE IA ---
            print(f"  🔍 Consultation de l'IA (Système Multi-clés)...")
            verdict_ia = demander_ia_detaillee(symbol, prix, c.to_dict(), ema200)
            print(f"  🤖 VERDICT IA :\n{verdict_ia}")
            
            # Sauvegarde CSV
            with open(HISTORIQUE_FILE, "a", encoding="utf-8") as file:
                date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                verdict_clean = verdict_ia.replace('\n', ' ').replace('"', "'")
                file.write(f"{date_str},{symbol},{prix},\"{verdict_clean}\"\n")
                
    print(f"\n⏳ Scan terminé. Repos 3 heures pour préserver les quotas...")
    time.sleep(10800) # 3 heures
