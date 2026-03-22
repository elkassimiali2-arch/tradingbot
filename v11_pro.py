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
import pandas_ta as ta
from binance.client import Client

# =============================================================

# SERVEUR WEB (Health Check Koyeb)

# =============================================================

class SimpleHandler(BaseHTTPRequestHandler):
def do_GET(self):
self.send_response(200)
self.send_header(‘Content-type’, ‘text/plain’)
self.end_headers()
self.wfile.write(b”Atlas v15.0 Online”)
def log_message(self, format, *args): return

def run_web_server():
port = int(os.environ.get(“PORT”, 8000))
HTTPServer((‘0.0.0.0’, port), SimpleHandler).serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# =============================================================

# CONFIGURATION

# =============================================================

load_dotenv(“key.env”)
BINANCE_KEY      = os.getenv(‘BINANCE_API_KEY’)
GOOGLE_KEYS      = [os.getenv(‘GOOGLE_API_KEY’), os.getenv(‘GOOGLE_API_KEY_2’)]
TELEGRAM_TOKEN   = os.getenv(‘TELEGRAM_TOKEN’)
TELEGRAM_CHAT_ID = os.getenv(‘TELEGRAM_CHAT_ID’)

client_binance = Client(BINANCE_KEY, “”)

SYMBOLS = [“BTCUSDT”, “ETHUSDT”, “SOLUSDT”, “AVAXUSDT”, “XRPUSDT”, “NEARUSDT”]
ACTIVE_MODEL = “gemini-2.5-flash”

last_signal_hash = {}

# =============================================================

# COLLECTE DONNEES + INDICATEURS

# Horizon : H1 (signal), H4 (confirmation), D1 (macro)

# =============================================================

def get_data(symbol):
try:
cols = [‘time’,‘open’,‘high’,‘low’,‘close’,‘vol’,‘ct’,‘q_av’,‘tr’,‘tb’,‘tq’,‘ig’]

```
    def to_df(raw):
        df = pd.DataFrame(raw, columns=cols)
        for c in ['open','high','low','close','vol']:
            df[c] = pd.to_numeric(df[c])
        return df.copy()

    raw_h1 = client_binance.get_historical_klines(
        symbol, Client.KLINE_INTERVAL_1HOUR, "220 hours ago UTC")
    raw_h4 = client_binance.get_historical_klines(
        symbol, Client.KLINE_INTERVAL_4HOUR, "120 days ago UTC")
    raw_d1 = client_binance.get_historical_klines(
        symbol, Client.KLINE_INTERVAL_1DAY, "400 days ago UTC")

    df_h1 = to_df(raw_h1)
    df_h4 = to_df(raw_h4)
    df_d1 = to_df(raw_d1)

    # Indicateurs H1
    df_h1.ta.rsi(length=14, append=True)
    df_h1.ta.macd(fast=12, slow=26, signal=9, append=True)
    df_h1.ta.atr(length=14, append=True)
    df_h1.ta.adx(length=14, append=True)
    df_h1.ta.bbands(length=20, std=2, append=True)
    df_h1.ta.stochrsi(length=14, append=True)

    vol_ma20  = df_h1['vol'].rolling(20).mean().iloc[-1]
    vol_ratio = df_h1['vol'].iloc[-1] / vol_ma20 if vol_ma20 > 0 else 1.0

    # Indicateurs H4
    df_h4.ta.rsi(length=14, append=True)
    df_h4.ta.adx(length=14, append=True)
    df_h4.ta.macd(fast=12, slow=26, signal=9, append=True)
    rsi_h4  = df_h4['RSI_14'].iloc[-1]
    adx_h4  = df_h4['ADX_14'].iloc[-1]
    macd_h4 = df_h4['MACDh_12_26_9'].iloc[-1]

    # EMA200 D1
    ema_series = df_d1.ta.ema(length=200)
    ema200 = float(ema_series.iloc[-1]) if (
        ema_series is not None and not ema_series.empty) else None

    # Pivot classique J-1
    ph = df_d1['high'].iloc[-2]
    pl = df_d1['low'].iloc[-2]
    pc = df_d1['close'].iloc[-2]
    pivot = (ph + pl + pc) / 3
    r1 = (2 * pivot) - pl
    r2 = pivot + (ph - pl)
    s1 = (2 * pivot) - ph
    s2 = pivot - (ph - pl)

    res = df_h1.iloc[-1].to_dict()
    res.update({
        'vol_ratio': round(vol_ratio, 2),
        'rsi_h4':    round(rsi_h4, 1),
        'adx_h4':    round(adx_h4, 1),
        'macd_h4':   round(macd_h4, 4),
        'p_r1': round(r1, 4),
        'p_r2': round(r2, 4),
        'p_s1': round(s1, 4),
        'p_s2': round(s2, 4),
    })
    return res, ema200

except Exception as e:
    print(f"  [DATA ERROR] {symbol}: {e}")
    return None, None
```

# =============================================================

# PRE-FILTRE QUANTITATIF

# =============================================================

def pre_filter(last):
vol_ratio = float(last.get(‘vol_ratio’, 1.0))
adx_h1    = float(last.get(‘ADX_14’, 0))
rsi_h1    = float(last.get(‘RSI_14’, 50))

```
if vol_ratio < 0.7:
    return False, f"Volume faible (x{vol_ratio:.2f})"

if adx_h1 < 18 and 42 < rsi_h1 < 58:
    return False, f"Range plat (ADX={adx_h1:.1f}, RSI={rsi_h1:.1f})"

return True, f"Setup valide (ADX={adx_h1:.1f}, Vol=x{vol_ratio:.2f})"
```

# =============================================================

# PROMPT IA OPTIMISE

# =============================================================

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
close     = float(last['close'])
atr_h1    = float(last.get('ATR_14', 0))
adx_h1    = float(last.get('ADX_14', 0))
ema_txt   = f"{ema200:.4f}" if ema200 is not None else "N/A"
above_ema = "AU-DESSUS" if (ema200 and close > ema200) else "EN-DESSOUS"
sl_min    = round(atr_h1 * 1.5, 4)

if adx_h1 >= 30:
    ratio_cible = "2.0 a 2.5 (tendance forte)"
elif adx_h1 >= 20:
    ratio_cible = "1.5 a 2.0 (tendance moderee)"
else:
    ratio_cible = "1.2 a 1.5 (marche en range)"

prompt = f"""Tu es un trader quantitatif professionnel specialise crypto. Analyse {symbol}.
```

=== DONNEES MARCHE ===
Prix actuel : {close:.4f} $
Position vs EMA200 D1 : {above_ema} ({ema_txt})

TIMEFRAME H1 (signal de trading - horizon 4h a 24h) :
RSI 14        : {fv(‘RSI_14’, 1)}
MACD Histo    : {fv(‘MACDh_12_26_9’)}  |  Signal : {fv(‘MACDs_12_26_9’)}
ADX 14        : {fv(‘ADX_14’, 1)}  |  DI+ : {fv(‘DMP_14’, 1)}  |  DI- : {fv(‘DMN_14’, 1)}
ATR 14        : {fv(‘ATR_14’)}  (volatilite par bougie H1)
Bollinger Low : {fv(‘BBL_20_2.0’)}  |  Mid : {fv(‘BBM_20_2.0’)}  |  High : {fv(‘BBU_20_2.0’)}
StochRSI      : {fv(‘STOCHRSIk_14_14_3_3’, 1)}
Volume ratio  : x{fv(‘vol_ratio’)} vs moyenne 20 bougies

TIMEFRAME H4 (confirmation tendance) :
RSI 14        : {fv(‘rsi_h4’, 1)}
ADX 14        : {fv(‘adx_h4’, 1)}
MACD Histo    : {fv(‘macd_h4’)}

NIVEAUX CLES D1 :
Pivot R2 : {fv(‘p_r2’)}  |  R1 : {fv(‘p_r1’)}
Pivot S1 : {fv(‘p_s1’)}  |  S2 : {fv(‘p_s2’)}

=== REGLES STRICTES ===

1. ATR H1 = {atr_h1:.4f}. SL MINIMUM = {sl_min:.4f} (1.5 x ATR). Ne jamais mettre un SL plus serre.
1. Ratio Gain/Risque cible : {ratio_cible}.
1. ACHAT valide si : DI+ > DI-, MACD Histo positif OU croissant, H4 confirme (RSI H4 > 50).
1. VENTE valide si : DI- > DI+, MACD Histo negatif OU decroissant, H4 confirme (RSI H4 < 50).
1. Si H1 et H4 sont en desaccord : ATTENTE obligatoire.
1. Volume x < 1.0 : reduire la taille de position de moitie.
1. StochRSI > 80 en ACHAT : attendre pullback, baisser la confiance.
1. StochRSI < 20 en VENTE : attendre rebond, baisser la confiance.
1. Taille position : confiance >= 75% -> 5% | 60-74% -> 3% | < 60% -> ATTENTE.

=== FORMAT DE REPONSE (respecte exactement ce bloc) ===
SIGNAL    : [ ACHAT | VENTE | ATTENTE ]
CONFIANCE : X%
TP        : X.XXXX
SL        : X.XXXX
RATIO G/R : X.X
TAILLE    : X% du portefeuille
H4 STATUS : [ CONFIRME | MIXTE ]
ANALYSE   : (4 phrases. Cite l’ATR, les pivots, le H4 et le volume pour justifier le signal et les niveaux TP/SL.)”””

```
for key in GOOGLE_KEYS:
    if not key:
        continue
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{ACTIVE_MODEL}:generateContent?key={key}")
    try:
        res = requests.post(url, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}
        }, timeout=25)
        data = res.json()
        if 'candidates' in data:
            return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"  [IA ERROR] {e}")

return "SIGNAL    : ATTENTE\nCONFIANCE : 0%\nANALYSE   : Quotas IA epuises."
```

# =============================================================

# EXTRACTION SIGNAL + DEDUPLICATION

# =============================================================

def extract_signal(verdict):
for line in verdict.split(’\n’):
u = line.upper()
if ‘SIGNAL’ in u:
if ‘ACHAT’ in u:  return ‘ACHAT’
if ‘VENTE’ in u:  return ‘VENTE’
return ‘ATTENTE’

def is_duplicate(symbol, verdict):
key = verdict.strip().splitlines()[0][:80]
h   = hashlib.md5(f”{symbol}:{key}”.encode()).hexdigest()
if last_signal_hash.get(symbol) == h:
return True
last_signal_hash[symbol] = h
return False

# =============================================================

# ENVOI TELEGRAM

# =============================================================

def envoyer_telegram(msg):
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
return
try:
requests.post(
f”https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage”,
json={“chat_id”: TELEGRAM_CHAT_ID, “text”: msg, “parse_mode”: “Markdown”},
timeout=10
)
except Exception as e:
print(f”  [TELEGRAM ERROR] {e}”)

# =============================================================

# BOUCLE PRINCIPALE

# Cycle toutes les 60 minutes (coherent avec H1)

# =============================================================

CYCLE_WAIT  = 3600
INTER_DELAY = 12

print(“Stabilisation Koyeb (60s)…”)
time.sleep(60)

while True:
now_str = datetime.now().strftime(’%H:%M:%S’)
print(f”\n{’=’*52}”)
print(f”ATLAS v15.0 - Scan {now_str}”)
print(f”{’=’*52}”)

```
signals_sent = 0

for s in SYMBOLS:
    print(f"\n[{s}] Collecte donnees...")
    last, ema200 = get_data(s)

    if last is None:
        print(f"  Donnees indisponibles.")
        continue

    ok, reason = pre_filter(last)
    if not ok:
        print(f"  Filtre: {reason}")
        continue
    print(f"  {reason}")

    print(f"  Appel IA...")
    verdict     = demander_ia_expert(s, last, ema200)
    signal_type = extract_signal(verdict)
    print(f"  Signal : {signal_type}")

    if signal_type == 'ATTENTE':
        print(f"  Ignore (ATTENTE).")
        time.sleep(INTER_DELAY)
        continue

    if is_duplicate(s, verdict):
        print(f"  Ignore (doublon).")
        time.sleep(INTER_DELAY)
        continue

    emoji     = "🟢" if signal_type == 'ACHAT' else "🔴"
    vol_ratio = float(last.get('vol_ratio', 1.0))
    vol_icon  = "🔥" if vol_ratio >= 1.5 else ("📊" if vol_ratio >= 1.0 else "🔇")
    try:
        atr_str = f"{float(last.get('ATR_14', 0)):.4f}"
    except:
        atr_str = "N/A"

    msg = (
        f"{emoji} *ATLAS v15 - {s}* {emoji}\n"
        f"💰 Prix : `{float(last['close']):.4f} $`\n"
        f"{vol_icon} Volume : `x{vol_ratio:.2f}`\n"
        f"📐 ATR H1 : `{atr_str}`\n"
        f"📡 Signal : *{signal_type}*\n\n"
        f"```\n{verdict}\n```"
    )

    envoyer_telegram(msg)
    signals_sent += 1
    print(f"  Signal envoye sur Telegram.")
    time.sleep(INTER_DELAY)

done_msg = (
    f"🏁 *Cycle termine* ({datetime.now().strftime('%H:%M')})\n"
    f"Signaux envoyes : *{signals_sent}/{len(SYMBOLS)}*\n"
    f"Prochain scan dans 60 min."
)
envoyer_telegram(done_msg)
print(f"\n{done_msg.replace('*', '')}")
time.sleep(CYCLE_WAIT)
```