import sys
from types import ModuleType

# --- 1. CORRECTIF PYTHON 3.14 ---
if 'numba' not in sys.modules:
    m = ModuleType('numba')
    m.njit = lambda f=None, *a, **k: (lambda x: x) if f is None else f
    sys.modules['numba'] = m

import threading, hashlib, os, time, requests
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import pandas as pd
import pandas_ta as ta
from binance.client import Client

# --- 2. SERVEUR WEB ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-type', 'text/plain'); self.end_headers()
        self.wfile.write(b"Atlas v14.7 ATR-Fixed Online")
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

# --- 4. DATA ENGINE (ROBUSTE) ---
def get_data(symbol):
    try:
        raw_h1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "200 hours ago UTC")
        raw_h4 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_4HOUR, "100 days ago UTC")
        raw_d1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "400 days ago UTC")
        
        def to_df(raw):
            df = pd.DataFrame(raw, columns=['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig'])
            for c in ['open','high','low','close','vol']: df[c] = pd.to_numeric(df[c])
            return df
            
        df_h1, df_h4, df_d1 = to_df(raw_h1), to_df(raw_h4), to_df(raw_d1)
        
        # Indicateurs H1
        df_h1.ta.rsi(append=True)
        df_h1.ta.macd(append=True)
        df_h1.ta.adx(append=True)
        df_h1.ta.bbands(append=True)
        
        # ATR avec récupération dynamique du nom de colonne
        atr_df = df_h1.ta.atr(length=14, append=True)
        atr_col = atr_df.name if hasattr(atr_df, 'name') else 'ATR_14'
        
        vol_ma20 = df_h1['vol'].rolling(20).mean().iloc[-1]
        vol_ratio = df_h1['vol'].iloc[-1] / vol_ma20 if vol_ma20 > 0 else 1.0
        
        rsi_h4 = df_h4.ta.rsi().iloc[-1]
        ema_series = df_d1.ta.ema(length=200)
        ema200 = float(ema_series.iloc[-1]) if (ema_series is not None and not ema_series.empty) else None
        
        ph, pl, pc = df_d1['high'].iloc[-2], df_d1['low'].iloc[-2], df_d1['close'].iloc[-2]
        pivot = (ph + pl + pc) / 3
        r1, s1 = (2 * pivot) - pl, (2 * pivot) - ph
        
        res = df_h1.iloc[-1].to_dict()
        res.update({
            'vol_ratio': round(vol_ratio, 2), 
            'rsi_h4': rsi_h4, 
            'p_r1': r1, 'p_s1': s1,
            'atr_val': df_h1[atr_col].iloc[-1] # On utilise le nom dynamique
        })
        return res, ema200
    except Exception as e:
        print(f"  ❌ Erreur Data {symbol}: {e}"); return None, None

# --- 5. EXPERT IA ---
def demander_ia_expert(symbol, last, ema200):
    def fv(key, prec=2):
        val = last.get(key)
        try: return f"{float(val):.{prec}f}" if val is not None else "N/A"
        except: return "N/A"
    
    ema_txt = f"{ema200:.2f}" if ema200 is not None else "N/A"
    atr = float(last.get('atr_val', 0))
    
    prompt = f"""Expert Trader H1. Analyse {symbol} à {last['close']:.2f}$.
    STATS H1: RSI {fv('RSI_14', 1)}, ADX {fv('ADX_14', 1)}, Vol x{fv('vol_ratio')}, ATR {atr:.2f}.
    BOLLINGER H1: Low {fv('BBL_20_2.0')}, Mid {fv('BBM_20_2.0')}, High {fv('BBU_20_2.0')}.
    CONTEXTE: RSI H4 {fv('rsi_h4', 1)}, EMA200 D1 {ema_txt}, Pivot R1 {fv('p_r1')}, S1 {fv('p_s1')}.
    
    MISSION: Signal ACHAT/VENTE/ATTENTE.
    RÈGLES SL/TP: 
    - Le SL doit être placé à MINIMUM 1.5x l'ATR ({atr:.2f}) du prix actuel.
    - Ratio Risk/Reward entre 1.5 et 2.5.
    
    FORMAT:
    SIGNAL: [ACHAT/VENTE/ATTENTE]
    CONFIANCE: [X]%
    PRIX ACTUEL: {last['close']:.2f}
    TP: [X] | SL: [X]
    ANALYSE: [Justifie techniquement avec RSI H4, ATR et BB en 3 phrases]."""

    for key in GOOGLE_KEYS:
        if not key: continue
        try:
            res = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{ACTIVE_MODEL}:generateContent?key={key}", 
                json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.1}}, timeout=20)
            return res.json()['candidates'][0]['content']['parts'][0]['text']
        except: pass
    return "SIGNAL: ATTENTE (Quotas)"

# --- 6. MAIN LOOP ---
print("⏳ Stabilisation Koyeb (60s)...")
time.sleep(60)

while True:
    now_str = datetime.now().strftime('%H:%M:%S')
    print(f"\n🚀 SCAN ATLAS v14.7 - {now_str}")
    signals_sent = 0
    
    for s in SYMBOLS:
        print(f"🔍 {s}..."); last, ema200 = get_data(s)
        if last is None: continue
        if float(last.get('vol_ratio', 1.0)) < 0.5: continue
            
        verdict = demander_ia_expert(s, last, ema200)
        print(f"  🤖 Verdict :\n{verdict}\n")
        
        if "SIGNAL: ATTENTE" in verdict.upper() or "SIGNAL:ATTENTE" in verdict.upper(): continue
            
        h = hashlib.md5(f"{s}:{verdict.strip().splitlines()[0]}".encode()).hexdigest()
        if last_signal_hash.get(s) == h: continue
        last_signal_hash[s] = h
        
        emoji = "🟢" if "ACHAT" in verdict.upper() else "🔴"
        msg = f"{emoji} *{s}*\n{verdict}"
        try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        except: pass
        signals_sent += 1; time.sleep(12)
        
    done_msg = f"🏁 *Cycle terminé* ({datetime.now().strftime('%H:%M')})\nSignaux: {signals_sent}/{len(SYMBOLS)}\nRepos: 1 heure."
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": done_msg, "parse_mode": "Markdown"})
    except: pass
    
    print(f"✅ {done_msg.replace('*','')}")
    time.sleep(3600)
