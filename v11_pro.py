import sys
from types import ModuleType

# — CORRECTIF PYTHON 3.14 (MOCK NUMBA) —

if ‘numba’ not in sys.modules:
m = ModuleType(‘numba’)
m.njit = lambda f=None, *a, **k: (lambda x: x) if f is None else f
sys.modules[‘numba’] = m

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
self.send_header(‘Content-type’, ‘text/plain’)
self.end_headers()
self.wfile.write(b”Atlas v14.0 Online”)
def log_message(self, format, *args): return

def run_web_server():
port = int(os.environ.get(“PORT”, 8000))
server = HTTPServer((‘0.0.0.0’, port), SimpleHandler)
server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ============================================================

# CONFIGURATION

# ============================================================

load_dotenv(“key.env”)
BINANCE_KEY     = os.getenv(‘BINANCE_API_KEY’)
GOOGLE_KEYS     = [os.getenv(‘GOOGLE_API_KEY’), os.getenv(‘GOOGLE_API_KEY_2’)]
TELEGRAM_TOKEN  = os.getenv(‘TELEGRAM_TOKEN’)
TELEGRAM_CHAT_ID = os.getenv(‘TELEGRAM_CHAT_ID’)

client_binance = Client(BINANCE_KEY, “”)

# SYMBOLES - tu peux étendre selon ton tableau récap

SYMBOLS = [“BTCUSDT”, “ETHUSDT”, “SOLUSDT”, “AVAXUSDT”, “XRPUSDT”, “NEARUSDT”]

ACTIVE_MODEL = “gemini-2.5-flash”

# Déduplication : mémorise le hash du dernier signal envoyé par symbole

last_signal_hash = {}

# ============================================================

# TELEGRAM

# ============================================================

def envoyer_telegram(message):
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
return
url = f”https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage”
try:
requests.post(url, json={
“chat_id”: TELEGRAM_CHAT_ID,
“text”: message,
“parse_mode”: “Markdown”
}, timeout=10)
except Exception as e:
print(f”[TELEGRAM ERROR] {e}”)

# ============================================================

# COLLECTE DONNÉES + INDICATEURS

# ============================================================

def get_data(symbol):
“””
Retourne un dict d’indicateurs sur 3 timeframes :
- H1  : RSI, MACD, ATR, ADX, Bollinger, Volume ratio
- H4  : RSI, ADX (confirmation tendance moyen terme)
- D1  : EMA200, Pivot classique (high/low/close J-1)
“””
try:
# ── Données brutes ──────────────────────────────────
raw_h1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR,   “200 hours ago UTC”)
raw_h4 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_4HOUR,   “100 days ago UTC”)
raw_d1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY,    “400 days ago UTC”)

```
    cols = ['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig']

    def to_df(raw):
        df = pd.DataFrame(raw, columns=cols)
        for c in ['open','high','low','close','vol']:
            df[c] = pd.to_numeric(df[c])
        return df.copy()

    df_h1 = to_df(raw_h1)
    df_h4 = to_df(raw_h4)
    df_d1 = to_df(raw_d1)

    # ── Indicateurs H1 ──────────────────────────────────
    df_h1.ta.rsi(length=14, append=True)
    df_h1.ta.macd(fast=12, slow=26, signal=9, append=True)
    df_h1.ta.atr(length=14, append=True)
    df_h1.ta.adx(length=14, append=True)
    df_h1.ta.bbands(length=20, std=2, append=True)

    # Volume moyen 20 bougies vs volume actuel
    vol_ma20 = df_h1['vol'].rolling(20).mean().iloc[-1]
    vol_now  = df_h1['vol'].iloc[-1]
    vol_ratio = vol_now / vol_ma20 if vol_ma20 > 0 else 1.0

    # ── Indicateurs H4 (confirmation) ───────────────────
    df_h4.ta.rsi(length=14, append=True)
    df_h4.ta.adx(length=14, append=True)
    rsi_h4 = df_h4['RSI_14'].iloc[-1]
    adx_h4 = df_h4['ADX_14'].iloc[-1]

    # ── EMA200 Daily ─────────────────────────────────────
    ema_series = df_d1.ta.ema(length=200)
    ema200 = float(ema_series.iloc[-1]) if ema_series is not None and not ema_series.empty else None

    # ── Pivot Classique J-1 (méthode correcte) ───────────
    # On prend la bougie fermée d'hier, pas le max de 150h
    prev_high  = df_d1['high'].iloc[-2]
    prev_low   = df_d1['low'].iloc[-2]
    prev_close = df_d1['close'].iloc[-2]
    pivot = (prev_high + prev_low + prev_close) / 3
    r1 = (2 * pivot) - prev_low
    r2 = pivot + (prev_high - prev_low)
    s1 = (2 * pivot) - prev_high
    s2 = pivot - (prev_high - prev_low)

    # ── Résumé H1 dernière bougie ─────────────────────────
    last = df_h1.iloc[-1].to_dict()
    last['vol_ratio']  = round(vol_ratio, 2)
    last['rsi_h4']     = rsi_h4
    last['adx_h4']     = adx_h4
    last['pivot']      = round(pivot, 4)
    last['pivot_r1']   = round(r1, 4)
    last['pivot_r2']   = round(r2, 4)
    last['pivot_s1']   = round(s1, 4)
    last['pivot_s2']   = round(s2, 4)

    return last, ema200

except Exception as e:
    print(f"[DATA ERROR] {symbol}: {e}")
    return None, None
```

# ============================================================

# PRÉ-FILTRE QUANTITATIF (avant d’appeler l’IA)

# ============================================================

def pre_filter(last, ema200):
“””
Retourne True si le setup mérite une analyse IA.
But : éviter d’appeler Gemini sur des marchés sans momentum.
“””
try:
rsi   = float(last.get(‘RSI_14’, 50))
adx   = float(last.get(‘ADX_14’, 0))
close = float(last[‘close’])
vol_r = float(last.get(‘vol_ratio’, 1.0))

```
    # Volume minimum : bougie en cours doit avoir un volume >= 80% de la moyenne
    if vol_r < 0.8:
        return False, "Volume trop faible"

    # Marché en range (ADX < 20 ET RSI entre 40-60) → pas de signal fiable
    if adx < 20 and 40 < rsi < 60:
        return False, f"Range plat (ADX={adx:.1f}, RSI={rsi:.1f})"

    # EMA200 : on note la direction mais on ne bloque pas
    above_ema = (ema200 is not None and close > ema200)

    return True, f"Setup valide | Vol x{vol_r:.2f} | EMA_above={above_ema}"

except Exception as e:
    return True, f"Filtre ignoré ({e})"
```

# ============================================================

# PROMPT IA OPTIMISÉ

# ============================================================

def demander_ia_expert(symbol, last, ema200):
def fv(key, prec=2):
val = last.get(key)
try:
if val is None or str(val).lower() == ‘nan’:
return “N/A”
return f”{float(val):.{prec}f}”
except:
return “N/A”

```
close = float(last['close'])
above_ema = "AU-DESSUS" if (ema200 and close > ema200) else "EN-DESSOUS"

# Contexte Bollinger
bb_upper = fv('BBU_20_2.0')
bb_lower = fv('BBL_20_2.0')
bb_mid   = fv('BBM_20_2.0')

prompt = f"""Tu es un trader quantitatif professionnel spécialisé crypto. Analyse {symbol} à {close:.4f}$.
```

=== DONNÉES MULTI-TIMEFRAME ===
TIMEFRAME H1 (court terme) :
RSI14     : {fv(‘RSI_14’, 1)}
MACD      : {fv(‘MACD_12_26_9’)} | Signal : {fv(‘MACDs_12_26_9’)} | Histo : {fv(‘MACDh_12_26_9’)}
ATR14     : {fv(‘ATR_14’)}
ADX14     : {fv(‘ADX_14’, 1)} | DI+ : {fv(‘DMP_14’, 1)} | DI- : {fv(‘DMN_14’, 1)}
Bollinger : Low {bb_lower} | Mid {bb_mid} | High {bb_upper}
Volume    : x{fv(‘vol_ratio’, 2)} par rapport à la moyenne 20 bougies

TIMEFRAME H4 (confirmation) :
RSI14     : {fv(‘rsi_h4’, 1)}
ADX14     : {fv(‘adx_h4’, 1)}

TIMEFRAME D1 (macro) :
EMA200    : {fv(None, 2) if ema200 is None else f’{ema200:.4f}’} → Prix {above_ema} de l’EMA200
Pivot J-1 : P={fv(‘pivot’)} | R1={fv(‘pivot_r1’)} | R2={fv(‘pivot_r2’)} | S1={fv(‘pivot_s1’)} | S2={fv(‘pivot_s2’)}

=== RÈGLES DE DÉCISION ===

1. ACHAT uniquement si : RSI H1 < 55, DI+ > DI-, MACD Histo positif ou croissant, prix > S1.
1. VENTE uniquement si : RSI H1 > 45, DI- > DI+, MACD Histo négatif ou décroissant, prix < R1.
1. Si H1 et H4 sont en désaccord sur la direction → signal ATTENTE.
1. Volume x > 1.5 = confirmation forte. Volume x < 1.0 = prudence.
1. ATR définit le SL minimum. Ne jamais mettre un SL < 0.8 * ATR du prix.
1. Ratio Gain/Risque : ADX > 30 → vise 1.8 à 2.5. ADX 20-30 → vise 1.4 à 1.8. ADX < 20 → 1.2 max.
1. Taille de position : confiance > 75% → 5%. confiance 60-75% → 3%. < 60% → ATTENTE.

# === FORMAT RÉPONSE (respecte strictement ce bloc, sans gras ni markdown) ===

## SIGNAL    : [ ACHAT | VENTE | ATTENTE ]
CONFIANCE : X%

## TP        : X.XXXX
SL        : X.XXXX
RATIO G/R : X.X
TAILLE    : X% du portefeuille

## CONFLUENCE H1/H4 : [ ALIGNÉS | MIXTES ]
VOLUME    : [ FORT | NORMAL | FAIBLE ]

ANALYSE   : (4 phrases max. Justifie le signal avec les indicateurs ci-dessus.
Mentionne explicitement EMA200, pivots, et si H4 confirme.)
======================================”””

```
for key in GOOGLE_KEYS:
    if not key:
        continue
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{ACTIVE_MODEL}:generateContent?key={key}"
    try:
        res = requests.post(url, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}   # température basse = réponses plus stables
        }, timeout=20)
        data = res.json()
        if 'candidates' in data:
            return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"[IA ERROR] {key[:8]}... : {e}")
return "Quotas IA épuisés."
```

# ============================================================

# DÉDUPLICATION DU SIGNAL

# ============================================================

def signal_hash(symbol, verdict):
“”“Hash basé sur le symbole + première ligne du verdict (le SIGNAL).”””
first_line = verdict.strip().split(’\n’)[0][:80]
return hashlib.md5(f”{symbol}:{first_line}”.encode()).hexdigest()

def is_duplicate(symbol, verdict):
h = signal_hash(symbol, verdict)
if last_signal_hash.get(symbol) == h:
return True
last_signal_hash[symbol] = h
return False

# ============================================================

# EXTRACTION DU TYPE DE SIGNAL (filtre ATTENTE post-IA)

# ============================================================

def extract_signal_type(verdict):
for line in verdict.split(’\n’):
if ‘SIGNAL’ in line.upper():
if ‘ACHAT’ in line.upper():
return ‘ACHAT’
elif ‘VENTE’ in line.upper():
return ‘VENTE’
return ‘ATTENTE’

# ============================================================

# BOUCLE PRINCIPALE

# ============================================================

print(“⏳ Stabilisation Koyeb (60s)…”)
time.sleep(60)

CYCLE_WAIT = 3 * 3600   # 3h entre chaque cycle complet
INTER_WAIT = 12         # délai entre chaque symbole (évite rate-limit Gemini)

while True:
now = datetime.now().strftime(’%H:%M:%S’)
print(f”\n{’=’*50}”)
print(f”🚀 Atlas v14.0 - Scan {now}”)
print(f”{’=’*50}”)

```
signals_sent = 0

for s in SYMBOLS:
    print(f"\n🔍 {s} - collecte données...")
    last, ema200 = get_data(s)

    if last is None:
        print(f"  ⚠️  Données indisponibles pour {s}")
        continue

    # Pré-filtre quantitatif
    ok, reason = pre_filter(last, ema200)
    if not ok:
        print(f"  ⏭️  {s} filtré : {reason}")
        continue

    print(f"  ✅ {reason}")
    print(f"  🤖 Analyse IA en cours...")

    verdict = demander_ia_expert(s, last, ema200)
    signal_type = extract_signal_type(verdict)

    # On n'envoie pas les ATTENTE
    if signal_type == 'ATTENTE':
        print(f"  💤 Signal ATTENTE - pas d'envoi Telegram")
        time.sleep(INTER_WAIT)
        continue

    # Déduplication
    if is_duplicate(s, verdict):
        print(f"  🔁 Signal identique au précédent - ignoré")
        time.sleep(INTER_WAIT)
        continue

    # Construction du message Telegram
    emoji = "🟢" if signal_type == 'ACHAT' else "🔴"
    close_price = float(last['close'])
    vol_ratio   = float(last.get('vol_ratio', 1.0))
    vol_emoji   = "🔥" if vol_ratio >= 1.5 else ("📊" if vol_ratio >= 1.0 else "🔇")

    msg = (
        f"{emoji} *ATLAS v14.0 - {s}* {emoji}\n"
        f"💰 Prix : `{close_price:,.4f} $`\n"
        f"{vol_emoji} Volume : `x{vol_ratio:.2f}` vs moy 20h\n"
        f"📡 Signal : *{signal_type}*\n\n"
        f"`{verdict}`"
    )

    envoyer_telegram(msg)
    signals_sent += 1
    print(f"  📨 Signal {signal_type} envoyé pour {s}")
    time.sleep(INTER_WAIT)

summary = (
    f"✅ *Cycle terminé* - {datetime.now().strftime('%H:%M:%S')}\n"
    f"📬 Signaux envoyés : *{signals_sent}/{len(SYMBOLS)}*\n"
    f"⏳ Prochain scan dans 3 heures."
)
envoyer_telegram(summary)
print(f"\n{summary.replace('*','')}")
time.sleep(CYCLE_WAIT)
```