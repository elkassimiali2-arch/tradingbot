import sys
from types import ModuleType

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

# =============================================================
# LOGGING + SERVEUR WEB
# =============================================================

def log(msg):
    print(msg, flush=True)

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Atlas v17.0 Online")
    def log_message(self, format, *args): return

def run_web_server():
    port = int(os.environ.get("PORT", 8000))
    HTTPServer(('0.0.0.0', port), SimpleHandler).serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# =============================================================
# CONFIGURATION
# =============================================================

load_dotenv("key.env")
BINANCE_KEY      = os.getenv('BINANCE_API_KEY')
GOOGLE_KEYS      = [os.getenv('GOOGLE_API_KEY'), os.getenv('GOOGLE_API_KEY_2')]
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

client_binance   = Client(BINANCE_KEY, "")
SYMBOLS          = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT"]
ACTIVE_MODEL     = "gemini-2.5-flash"
last_signal_hash = {}

# =============================================================
# V17 : PARAMETRES SCORE PONDERE + ADAPTATIF
# =============================================================

WEIGHTS = {
    'C1': 2.0, 'C2': 1.5, 'C3': 2.0,
    'C4': 1.0, 'C5': 1.0, 'C6': 0.5,
    'C7': 1.5, 'C8': 1.0,
}
SCORE_MAX = sum(WEIGHTS.values())   # 10.5

# =============================================================
# DIAGNOSTIC TELEGRAM AU DEMARRAGE
# =============================================================

def diagnostic_telegram():
    log("\n=== DIAGNOSTIC TELEGRAM ===")
    if not TELEGRAM_TOKEN:
        log("  [ERREUR] TELEGRAM_TOKEN absent de key.env")
        return False
    if not TELEGRAM_CHAT_ID:
        log("  [ERREUR] TELEGRAM_CHAT_ID absent de key.env")
        return False
    log(f"  TOKEN   : OK ({TELEGRAM_TOKEN[:10]}...)")
    log(f"  CHAT_ID : {TELEGRAM_CHAT_ID}")
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=10)
        data = r.json()
        if data.get('ok'):
            log(f"  Bot valide : @{data['result']['username']}")
        else:
            log(f"  [ERREUR] Token invalide : {data.get('description')}")
            return False
    except Exception as e:
        log(f"  [ERREUR] getMe failed : {e}")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": "🚀 *ATLAS v17.0 demarre*\nScore pondere + Adaptatif + Funding + Fear&Greed\nPremier scan dans 60s.",
                "parse_mode": "Markdown"
            }, timeout=10)
        data = r.json()
        if data.get('ok'):
            log(f"  Message demarrage envoye OK (id={data['result']['message_id']})")
            return True
        else:
            err  = data.get('error_code')
            desc = data.get('description', '')
            log(f"  [ERREUR] sendMessage : code={err} | {desc}")
            return False
    except Exception as e:
        log(f"  [ERREUR] sendMessage exception : {e}")
        return False

# =============================================================
# FUNDING RATE & FEAR & GREED
# =============================================================

def get_funding_rate(symbol):
    try:
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        r   = requests.get(url, params={"symbol": symbol}, timeout=8)
        data = r.json()
        if isinstance(data, dict) and 'lastFundingRate' in data:
            return float(data['lastFundingRate'])
        return None
    except Exception as e:
        log(f"    [FUNDING ERROR] {symbol}: {e}")
        return None

_fear_greed_cache = {'value': None, 'ts': 0}

def get_fear_greed():
    global _fear_greed_cache
    if time.time() - _fear_greed_cache['ts'] < 3600 and _fear_greed_cache['value'] is not None:
        return _fear_greed_cache['value']
    try:
        r    = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        data = r.json()
        val  = int(data['data'][0]['value'])
        label = data['data'][0]['value_classification']
        _fear_greed_cache = {'value': val, 'ts': time.time(), 'label': label}
        log(f"    -> Fear&Greed : {val} ({label})")
        return val
    except Exception as e:
        log(f"    [FEAR&GREED ERROR] {e}")
        return None

# =============================================================
# DATA ENGINE
# =============================================================

def get_data(symbol):
    try:
        cols = ['time','open','high','low','close','vol','ct','q_av','tr','tb','tq','ig']

        def to_df(raw):
            df = pd.DataFrame(raw, columns=cols)
            for c in ['open','high','low','close','vol']:
                df[c] = pd.to_numeric(df[c])
            return df.copy()

        log(f"    -> Collecte {symbol}...")
        raw_h1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "220 hours ago UTC")
        raw_h4 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_4HOUR, "120 days ago UTC")
        raw_d1 = client_binance.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "400 days ago UTC")

        df_h1, df_h4, df_d1 = to_df(raw_h1), to_df(raw_h4), to_df(raw_d1)

        df_h1.ta.rsi(length=14, append=True)
        df_h1.ta.macd(fast=12, slow=26, signal=9, append=True)
        df_h1.ta.adx(length=14, append=True)
        df_h1.ta.bbands(length=20, std=2, append=True)
        df_h1.ta.atr(length=14, append=True)
        df_h1.ta.stochrsi(length=14, rsi_length=14, k=3, d=3, append=True)

        ema50_s  = df_h1.ta.ema(length=50)
        ema50_h1 = float(ema50_s.iloc[-1]) if (ema50_s is not None and not ema50_s.empty) else None

        atr_col = next((c for c in ['ATRr_14', 'ATR_14'] if c in df_h1.columns), None)
        atr_val = float(df_h1[atr_col].iloc[-1]) if atr_col else 0.0

        bbu_col = next((c for c in df_h1.columns if c.startswith('BBU_')), None)
        bbl_col = next((c for c in df_h1.columns if c.startswith('BBL_')), None)
        bbm_col = next((c for c in df_h1.columns if c.startswith('BBM_')), None)
        bbu = float(df_h1[bbu_col].iloc[-1]) if bbu_col else None
        bbl = float(df_h1[bbl_col].iloc[-1]) if bbl_col else None
        bbm = float(df_h1[bbm_col].iloc[-1]) if bbm_col else None
        bb_width = round(((bbu-bbl)/bbm)*100, 4) if (bbu and bbl and bbm and bbm > 0) else None

        stoch_k_col = next((c for c in df_h1.columns if 'STOCHRSIk' in c), None)
        stoch_k = float(df_h1[stoch_k_col].iloc[-1]) if stoch_k_col else None

        vol_abs   = float(df_h1['vol'].iloc[-1])
        vol_ma20  = df_h1['vol'].rolling(20).mean().iloc[-1]
        vol_ratio = vol_abs / vol_ma20 if vol_ma20 > 0 else 1.0

        df_h4.ta.rsi(length=14, append=True)
        df_h4.ta.adx(length=14, append=True)
        df_h4.ta.macd(fast=12, slow=26, signal=9, append=True)
        rsi_h4   = float(df_h4['RSI_14'].iloc[-1])
        adx_h4   = float(df_h4['ADX_14'].iloc[-1])

        ema200_s = df_d1.ta.ema(length=200)
        ema200 = float(ema200_s.iloc[-1]) if (ema200_s is not None and not ema200_s.empty) else None

        ph = df_d1['high'].iloc[-2]
        pl = df_d1['low'].iloc[-2]
        pc = df_d1['close'].iloc[-2]
        pivot = (ph + pl + pc) / 3

        res = df_h1.iloc[-1].to_dict()
        res.update({
            'atr_val':   atr_val,
            'vol_ratio': round(vol_ratio, 2),
            'vol_abs':   round(vol_abs, 2),
            'bb_width':  bb_width,
            'stoch_k':   round(stoch_k, 2) if stoch_k is not None else None,
            'ema50_h1':  round(ema50_h1, 6) if ema50_h1 is not None else None,
            'rsi_h4':    round(rsi_h4, 1),
            'adx_h4':    round(adx_h4, 1),
            'p_r1': round((2*pivot)-pl, 6),
            'p_r2': round(pivot+(ph-pl), 6),
            'p_s1': round((2*pivot)-ph, 6),
            'p_s2': round(pivot-(ph-pl), 6),
        })

        log(f"    -> OK | Close={res['close']:.4f} | RSI={res['RSI_14']:.1f} | ADX={res['ADX_14']:.1f}")
        return res, ema200

    except Exception as e:
        log(f"    [DATA ERROR] {symbol}: {e}")
        return None, None

# =============================================================
# SCORE V17 : PONDERE + ADAPTATIF + FUNDING + FEAR&GREED
# =============================================================

def compute_score(last, ema200, funding_rate, fear_greed):
    close     = float(last.get('close', 0))
    adx_h1    = float(last.get('ADX_14', 0))
    di_plus   = float(last.get('DMP_14', 0))
    di_minus  = float(last.get('DMN_14', 0))
    macd_h1   = float(last.get('MACDh_12_26_9', 0))
    rsi_h4    = float(last.get('rsi_h4', 50))
    stoch_k   = last.get('stoch_k')
    ema50     = last.get('ema50_h1')
    bb_width  = last.get('bb_width')
    vol_ratio = float(last.get('vol_ratio', 1.0))
    atr       = float(last.get('atr_val', 0))

    if vol_ratio < 0.7:
        return None, 0.0, 0.0, {'SKIP_BLOQUANT': f"Volume faible x{vol_ratio:.2f}"}, None, None

    if adx_h1 < 15:
        return None, 0.0, 0.0, {'SKIP_BLOQUANT': f"ADX trop faible {adx_h1:.1f}"}, None, None

    if di_plus > di_minus: direction = 'ACHAT'
    elif di_minus > di_plus: direction = 'VENTE'
    else: return None, 0.0, 0.0, {'SKIP_BLOQUANT': "DI neutre"}, None, None

    h4_ok = (direction == 'ACHAT' and rsi_h4 > 50) or (direction == 'VENTE' and rsi_h4 < 50)
    if not h4_ok: return None, 0.0, 0.0, {'SKIP_BLOQUANT': f"H4 non confirme RSI={rsi_h4}"}, None, None

    if direction == 'ACHAT':
        c7_ok = (funding_rate is None) or (funding_rate < 0.10)
        c8_ok = (fear_greed is None) or (fear_greed < 75)
        conds = [
            ('C1 DI+ > DI-', di_plus > di_minus, WEIGHTS['C1'], f"DI+={di_plus:.1f}"),
            ('C2 MACD > 0', macd_h1 > 0, WEIGHTS['C2'], f"{macd_h1:.6f}"),
            ('C3 RSI H4 > 50', rsi_h4 > 50, WEIGHTS['C3'], f"{rsi_h4}"),
            ('C4 StochK < 80', stoch_k is not None and stoch_k < 80, WEIGHTS['C4'], f"{stoch_k}"),
            ('C5 Close > EMA50', ema50 is not None and close > ema50, WEIGHTS['C5'], f"ema={ema50}"),
            ('C6 BB Width > 2%', bb_width is not None and bb_width>2.0, WEIGHTS['C6'], f"{bb_width}"),
            ('C7 Funding < 0.10', c7_ok, WEIGHTS['C7'], f"{funding_rate}"),
            ('C8 F&G < 75', c8_ok, WEIGHTS['C8'], f"{fear_greed}"),
        ]
    else:
        c7_ok = (funding_rate is None) or (funding_rate > -0.10)
        c8_ok = (fear_greed is None) or (fear_greed > 25)
        conds = [
            ('C1 DI- > DI+', di_minus > di_plus, WEIGHTS['C1'], f"DI-={di_minus:.1f}"),
            ('C2 MACD < 0', macd_h1 < 0, WEIGHTS['C2'], f"{macd_h1:.6f}"),
            ('C3 RSI H4 < 50', rsi_h4 < 50, WEIGHTS['C3'], f"{rsi_h4}"),
            ('C4 StochK > 20', stoch_k is not None and stoch_k > 20, WEIGHTS['C4'], f"{stoch_k}"),
            ('C5 Close < EMA50', ema50 is not None and close < ema50, WEIGHTS['C5'], f"ema={ema50}"),
            ('C6 BB Width > 2%', bb_width is not None and bb_width>2.0, WEIGHTS['C6'], f"{bb_width}"),
            ('C7 Funding > -0.10', c7_ok, WEIGHTS['C7'], f"{funding_rate}"),
            ('C8 F&G > 25', c8_ok, WEIGHTS['C8'], f"{fear_greed}"),
        ]

    score_weighted = sum(w for _, ok, w, _ in conds if ok)
    detail = {name: f"[{'OK' if ok else 'FAIL'}] w={w} | {val}" for name, ok, w, val in conds}

    if adx_h1 > 28: threshold = SCORE_MAX * 0.50
    elif adx_h1 >= 18: threshold = SCORE_MAX * 0.60
    else: threshold = SCORE_MAX * 0.70

    sl_dist = atr * 1.5
    adx_ratio = 2.2 if adx_h1 >= 30 else (1.7 if adx_h1 >= 20 else 1.3)
    tp_dist = sl_dist * adx_ratio
    sl = round(close - sl_dist if direction == 'ACHAT' else close + sl_dist, 6)
    tp = round(close + tp_dist if direction == 'ACHAT' else close - tp_dist, 6)

    return direction, score_weighted, threshold, detail, sl, tp

# =============================================================
# LOG SCORE, IA & TELEGRAM
# =============================================================

def log_score_detail(symbol, direction, score_w, threshold, detail, sl, tp, close):
    if 'SKIP_BLOQUANT' in detail:
        log(f"  [SKIP] {detail['SKIP_BLOQUANT']}")
        return
    log(f"  [SCORE] {direction} | {score_w:.1f}/{SCORE_MAX} | seuil={threshold:.2f}")

def demander_analyse_ia(symbol, last, ema200, direction, score_w, threshold, sl, tp, funding_rate, fear_greed):
    close = float(last['close'])
    prompt = f"Analyse breve signal {direction} sur {symbol} score {score_w:.1f}/{SCORE_MAX}. 4 phrases."
    for key in GOOGLE_KEYS:
        if not key: continue
        try:
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{ACTIVE_MODEL}:generateContent?key={key}", 
                             json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}}, timeout=25)
            return r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        except: pass
    return "Analyse IA indisponible."

def is_duplicate(symbol, direction, score_w):
    key = f"{symbol}:{direction}:{score_w:.1f}"
    h   = hashlib.md5(key.encode()).hexdigest()
    if last_signal_hash.get(symbol) == h: return True
    last_signal_hash[symbol] = h
    return False

def envoyer_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                         json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        return r.json().get('ok')
    except: return False

# =============================================================
# BOUCLE PRINCIPALE
# =============================================================

CYCLE_WAIT, INTER_DELAY = 3600, 12
log("Stabilisation Koyeb (60s)...")
time.sleep(60)
diagnostic_telegram()

while True:
    log(f"\nATLAS v17.0 - Scan {datetime.now().strftime('%H:%M:%S')}")
    fear_greed = get_fear_greed()
    sent = 0

    for s in SYMBOLS:
        last, ema200 = get_data(s)
        if last is None: continue
        
        funding_rate = get_funding_rate(s)
        direction, score_w, threshold, detail, sl, tp = compute_score(last, ema200, funding_rate, fear_greed)
        log_score_detail(s, direction, score_w, threshold, detail, sl, tp, float(last['close']))

        if direction and score_w >= threshold and not is_duplicate(s, direction, score_w):
            analyse = demander_analyse_ia(s, last, ema200, direction, score_w, threshold, sl, tp, funding_rate, fear_greed)
            emoji = "🟢" if direction == 'ACHAT' else "🔴"
            msg = f"{emoji} *ATLAS v17.0 - {s}* {emoji}\nScore: `{score_w:.1f}/{SCORE_MAX}`\nTP: `{tp:.6f}` | SL: `{sl:.6f}`\n\n{analyse}"
            if envoyer_telegram(msg): sent += 1
            time.sleep(INTER_DELAY)
    
    log(f"Cycle fini. Signaux: {sent}")
    time.sleep(CYCLE_WAIT)
