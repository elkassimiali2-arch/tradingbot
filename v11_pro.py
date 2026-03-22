import sys
from types import ModuleType

# Correctif Numba pour Python récent
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
        self.wfile.write(b"Atlas v16.0 Online")
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
SCORE_MIN        = 4    # conditions minimum sur 6 pour valider un setup
last_signal_hash = {}

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

        # Indicateurs H1
        df_h1.ta.rsi(length=14, append=True)
        df_h1.ta.macd(fast=12, slow=26, signal=9, append=True)
        df_h1.ta.adx(length=14, append=True)
        df_h1.ta.bbands(length=20, std=2, append=True)
        df_h1.ta.atr(length=14, append=True)
        df_h1.ta.stochrsi(length=14, rsi_length=14, k=3, d=3, append=True)

        ema50_s = df_h1.ta.ema(length=50)
        ema50_h1 = float(ema50_s.iloc[-1]) if (ema50_s is not None and not ema50_s.empty) else None

        atr_col = next((c for c in ['ATRr_14', 'ATR_14'] if c in df_h1.columns), None)
        atr_val = float(df_h1[atr_col].iloc[-1]) if atr_col else 0.0

        bbu_col = next((c for c in df_h1.columns if c.startswith('BBU_')), None)
        bbl_col = next((c for c in df_h1.columns if c.startswith('BBL_')), None)
        bbm_col = next((c for c in df_h1.columns if c.startswith('BBM_')), None)
        bbu = float(df_h1[bbu_col].iloc[-1]) if bbu_col else None
        bbl = float(df_h1[bbl_col].iloc[-1]) if bbl_col else None
        bbm = float(df_h1[bbm_col].iloc[-1]) if bbm_col else None
        bb_width = round(((bbu - bbl) / bbm) * 100, 4) if (bbu and bbl and bbm and bbm > 0) else None

        stoch_k_col = next((c for c in df_h1.columns if 'STOCHRSIk' in c), None)
        stoch_d_col = next((c for c in df_h1.columns if 'STOCHRSId' in c), None)
        stoch_k = float(df_h1[stoch_k_col].iloc[-1]) if stoch_k_col else None
        stoch_d = float(df_h1[stoch_d_col].iloc[-1]) if stoch_d_col else None

        vol_abs   = float(df_h1['vol'].iloc[-1])
        vol_ma20  = df_h1['vol'].rolling(20).mean().iloc[-1]
        vol_ratio = vol_abs / vol_ma20 if vol_ma20 > 0 else 1.0

        # Indicateurs H4
        df_h4.ta.rsi(length=14, append=True)
        df_h4.ta.adx(length=14, append=True)
        df_h4.ta.macd(fast=12, slow=26, signal=9, append=True)

        rsi_h4   = float(df_h4['RSI_14'].iloc[-1])
        adx_h4   = float(df_h4['ADX_14'].iloc[-1])
        macd_h4  = float(df_h4['MACDh_12_26_9'].iloc[-1])
        macds_h4 = float(df_h4['MACDs_12_26_9'].iloc[-1])

        # EMA200 D1
        ema200_s = df_d1.ta.ema(length=200)
        ema200 = float(ema200_s.iloc[-1]) if (ema200_s is not None and not ema200_s.empty) else None

        # Pivots J-1
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
            'bbu':       bbu,
            'bbl':       bbl,
            'bbm':       bbm,
            'stoch_k':   round(stoch_k, 2) if stoch_k is not None else None,
            'stoch_d':   round(stoch_d, 2) if stoch_d is not None else None,
            'ema50_h1':  round(ema50_h1, 6) if ema50_h1 is not None else None,
            'rsi_h4':    round(rsi_h4, 1),
            'adx_h4':    round(adx_h4, 1),
            'macd_h4':   round(macd_h4, 6),
            'macds_h4':  round(macds_h4, 6),
            'p_r1': round((2 * pivot) - pl, 6),
            'p_r2': round(pivot + (ph - pl), 6),
            'p_s1': round((2 * pivot) - ph, 6),
            'p_s2': round(pivot - (ph - pl), 6),
        })

        log(f"    -> OK | Close={res['close']:.4f} | RSI={res['RSI_14']:.1f} | ADX={res['ADX_14']:.1f}")
        return res, ema200

    except Exception as e:
        log(f"    [DATA ERROR] {symbol}: {e}")
        return None, None

# =============================================================
# SCORE PYTHON DETERMINISTE
# =============================================================

def compute_score(last, ema200):
    close    = float(last.get('close', 0))
    rsi_h1   = float(last.get('RSI_14', 50))
    adx_h1   = float(last.get('ADX_14', 0))
    di_plus  = float(last.get('DMP_14', 0))
    di_minus = float(last.get('DMN_14', 0))
    macd_h1  = float(last.get('MACDh_12_26_9', 0))
    rsi_h4   = float(last.get('rsi_h4', 50))
    stoch_k  = last.get('stoch_k')
    ema50    = last.get('ema50_h1')
    bb_width = last.get('bb_width')
    vol_ratio = float(last.get('vol_ratio', 1.0))
    atr      = float(last.get('atr_val', 0))

    if vol_ratio < 0.7:
        return None, 0, {'SKIP_BLOQUANT': f"Volume faible x{vol_ratio:.2f}"}, None, None

    if adx_h1 < 15:
        return None, 0, {'SKIP_BLOQUANT': f"ADX H1 trop faible ({adx_h1:.1f})"}, None, None

    if di_plus > di_minus:
        direction = 'ACHAT'
    elif di_minus > di_plus:
        direction = 'VENTE'
    else:
        return None, 0, {'SKIP_BLOQUANT': "DI neutre"}, None, None

    h4_confirme = (direction == 'ACHAT' and rsi_h4 > 50) or (direction == 'VENTE' and rsi_h4 < 50)
    if not h4_confirme:
        return None, 0, {'SKIP_BLOQUANT': f"H4 non confirme (RSI H4={rsi_h4:.1f})"}, None, None

    if direction == 'ACHAT':
        c1 = di_plus > di_minus
        c2 = macd_h1 > 0
        c3 = rsi_h4 > 50
        c4 = (stoch_k is not None and stoch_k < 80)
        c5 = (ema50 is not None and close > ema50)
        c6 = (bb_width is not None and bb_width > 2.0)
        labels = [('C1 DI+ > DI-', c1, f"{di_plus:.1f}"), ('C2 MACD > 0', c2, f"{macd_h1:.6f}"), ('C3 RSI H4 > 50', c3, f"{rsi_h4}"), ('C4 Stoch < 80', c4, f"{stoch_k}"), ('C5 Prix > EMA50', c5, f"{ema50}"), ('C6 BB > 2%', c6, f"{bb_width}")]
    else:
        c1 = di_minus > di_plus
        c2 = macd_h1 < 0
        c3 = rsi_h4 < 50
        c4 = (stoch_k is not None and stoch_k > 20)
        c5 = (ema50 is not None and close < ema50)
        c6 = (bb_width is not None and bb_width > 2.0)
        labels = [('C1 DI- > DI+', c1, f"{di_minus:.1f}"), ('C2 MACD < 0', c2, f"{macd_h1:.6f}"), ('C3 RSI H4 < 50', c3, f"{rsi_h4}"), ('C4 Stoch > 20', c4, f"{stoch_k}"), ('C5 Prix < EMA50', c5, f"{ema50}"), ('C6 BB > 2%', c6, f"{bb_width}")]

    score = sum([c1, c2, c3, c4, c5, c6])
    detail = {name: f"[{'OK' if passed else 'FAIL'}] {val}" for name, passed, val in labels}

    sl_dist = atr * 1.5
    adx_ratio = 2.2 if adx_h1 >= 30 else (1.7 if adx_h1 >= 20 else 1.3)
    tp_dist = sl_dist * adx_ratio

    sl = round(close - sl_dist if direction == 'ACHAT' else close + sl_dist, 6)
    tp = round(close + tp_dist if direction == 'ACHAT' else close - tp_dist, 6)

    return direction, score, detail, sl, tp

def log_score_detail(symbol, direction, score, detail, sl, tp, close):
    if 'SKIP_BLOQUANT' in detail:
        log(f"  [SKIP] {detail['SKIP_BLOQUANT']}")
        return
    log(f"  [SCORE] {direction} | {score}/6")
    if score >= SCORE_MIN:
        log(f"  [VALIDE] TP={tp:.6f} | SL={sl:.6f}")

# =============================================================
# GEMINI ANALYSE
# =============================================================

def demander_analyse_ia(symbol, last, ema200, direction, score, sl, tp):
    def fv(key, prec=2):
        val = last.get(key)
        try: return "N/A" if (val is None or str(val).lower() == 'nan') else f"{float(val):.{prec}f}"
        except: return "N/A"

    close = float(last['close'])
    prompt = f"Explique brievement pourquoi le signal {direction} ({score}/6) sur {symbol} est valide techniquement (MACD, RSI, EMA, ATR). 4 phrases max."

    for key in GOOGLE_KEYS:
        if not key: continue
        try:
            res = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{ACTIVE_MODEL}:generateContent?key={key}", 
                               json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}}, timeout=25)
            return res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        except: pass
    return "Analyse IA indisponible."

# =============================================================
# TELEGRAM + BOUCLE
# =============================================================

def is_duplicate(symbol, direction, score):
    h = hashlib.md5(f"{symbol}:{direction}:{score}".encode()).hexdigest()
    if last_signal_hash.get(symbol) == h: return True
    last_signal_hash[symbol] = h
    return False

def envoyer_telegram(msg):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except: pass

CYCLE_WAIT, INTER_DELAY = 3600, 12
log("Stabilisation Koyeb (60s)...")
time.sleep(60)

while True:
    log(f"\nScan {datetime.now().strftime('%H:%M:%S')}")
    sent = 0
    for s in SYMBOLS:
        last, ema200 = get_data(s)
        if not last: continue
        
        direction, score, detail, sl, tp = compute_score(last, ema200)
        log_score_detail(s, direction, score, detail, sl, tp, float(last['close']))

        if direction and score >= SCORE_MIN and not is_duplicate(s, direction, score):
            analyse = demander_analyse_ia(s, last, ema200, direction, score, sl, tp)
            emoji = "🟢" if direction == 'ACHAT' else "🔴"
            msg = f"{emoji} *{s}* {emoji}\nScore: `{score}/6` | Prix: `{last['close']}`\nTP: `{tp}` | SL: `{sl}`\n\n{analyse}"
            envoyer_telegram(msg)
            sent += 1
            time.sleep(INTER_DELAY)
    
    log(f"Cycle fini. Signaux: {sent}")
    time.sleep(CYCLE_WAIT)
