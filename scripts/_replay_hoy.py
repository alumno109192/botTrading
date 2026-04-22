"""
Replay de todas las velas 1H de hoy para ver qué score tuvo cada una
y si SIN el bloqueo del PMI habrían salido señales.
"""
import sys
sys.path.insert(0, '.')

from adapters.data_provider import get_ohlcv
from core.indicators import (
    calcular_rsi, calcular_ema, calcular_atr, calcular_adx,
    calcular_bollinger_bands, calcular_macd, calcular_obv,
    detectar_canal_roto, detectar_evening_star, detectar_morning_star
)
import pandas as pd
from datetime import datetime, timezone

# ── Descargar datos ──────────────────────────────────────────────
df_5m, _ = get_ohlcv('GC=F', period='7d', interval='5m')
df = df_5m.resample('1h').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()

df['rsi']       = calcular_rsi(df['Close'], 14)
df['ema_fast']  = calcular_ema(df['Close'], 9)
df['ema_slow']  = calcular_ema(df['Close'], 21)
df['ema_trend'] = calcular_ema(df['Close'], 200)
df['atr']       = calcular_atr(df, 14)
df['vol_avg']   = df['Volume'].rolling(20).mean()
df['total_range'] = df['High'] - df['Low']
df['adx'], df['di_plus'], df['di_minus'] = calcular_adx(df)
df['bb_upper'], _, df['bb_lower'], _ = calcular_bollinger_bands(df['Close'])
df['macd'], df['macd_signal'], df['macd_hist'] = calcular_macd(df['Close'])
df['obv']     = calcular_obv(df)
df['obv_ema'] = calcular_ema(df['obv'], 20)
df['body']       = (df['Close'] - df['Open']).abs()
df['upper_wick'] = df['High'] - df[['Close','Open']].max(axis=1)
df['lower_wick'] = df[['Close','Open']].min(axis=1) - df['Low']

today_utc = datetime.now(timezone.utc).date()
hoy_idx = [i for i in range(4, len(df)-1) if df.index[i].date() == today_utc]

print(f"Fecha: {today_utc}  |  Velas 1H hoy: {len(hoy_idx)}")
print(f"{'Hora':>5} | {'Close':>7} | {'SELL':>4} | {'BUY':>4} | {'Zona':>5} | {'RSI':>5} | {'ADX':>5} | Señal  | Bloq PMI?")
print("-" * 82)

PMI_UTC = datetime(2026, 4, 22, 14, 0, tzinfo=timezone.utc)

def calc_scores(df, vi):
    row   = df.iloc[vi]
    prev  = df.iloc[vi-1]
    p2    = df.iloc[vi-2]
    atr   = float(row['atr'])
    close = float(row['Close'])
    high  = float(row['High'])
    low   = float(row['Low'])
    open_ = float(row['Open'])
    rsi   = float(row['rsi'])
    rsi_p = float(prev['rsi'])
    adx   = float(row['adx'])
    di_p  = float(row['di_plus'])
    di_m  = float(row['di_minus'])
    vol   = float(row['Volume'])
    vavg  = float(row['vol_avg'])
    ef    = float(row['ema_fast'])
    es    = float(row['ema_slow'])
    et    = float(row['ema_trend'])
    macd  = float(row['macd'])
    msig  = float(row['macd_signal'])
    mh    = float(row['macd_hist'])
    mhp   = float(prev['macd_hist'])
    obv   = float(row['obv'])
    obvp  = float(prev['obv'])
    obve  = float(row['obv_ema'])
    bbu   = float(row['bb_upper'])
    bbl   = float(row['bb_lower'])
    body  = float(row['body'])
    uwi   = float(row['upper_wick'])
    lwi   = float(row['lower_wick'])
    trng  = float(row['total_range'])
    bear  = close < open_
    bull  = close > open_

    # Zonas S/R
    lkb = 150; wing = 3
    highs = df['High'].iloc[max(0,vi-lkb-1):vi]
    lows  = df['Low'].iloc[max(0,vi-lkb-1):vi]
    sh, sl = [], []
    for i in range(wing, len(highs)-wing):
        v = float(highs.iloc[i])
        if all(v >= float(highs.iloc[i-j]) for j in range(1,wing+1)) and \
           all(v >= float(highs.iloc[i+j]) for j in range(1,wing+1)): sh.append(v)
    for i in range(wing, len(lows)-wing):
        v = float(lows.iloc[i])
        if all(v <= float(lows.iloc[i-j]) for j in range(1,wing+1)) and \
           all(v <= float(lows.iloc[i+j]) for j in range(1,wing+1)): sl.append(v)
    if not sh: sh = [float(highs.max())]
    if not sl: sl = [float(lows.min())]
    zw = atr*0.8; md = atr*0.3
    cr = sorted([v for v in set(sh+sl) if v > close+md])
    cs = sorted([v for v in set(sl+sh) if v < close-md], reverse=True)
    rp = cr[0] if cr else float(highs.max())
    sp = cs[0] if cs else float(lows.min())
    zrh = rp + zw*0.25; zrl = rp - zw*0.75
    zsh = sp + zw*0.75; zsl = sp - zw*0.25
    tol = atr*0.4
    en_r = (high >= zrl-tol) and (high <= zrh+tol)
    en_s = (low  >= zsl-tol) and (low  <= zsh+tol)
    avg_rng = float(df['total_range'].iloc[max(0,vi-6):vi].mean())
    aprox_r = (zrl-close > 0 and zrl-close < avg_rng*3 and close > float(df['Close'].iloc[vi-4]))
    aprox_s = (close-zsh > 0 and close-zsh < avg_rng*3 and close < float(df['Close'].iloc[vi-4]))
    cancel_s = close > zrh*(1+1/100)
    cancel_b = close < zsl*(1-1/100)

    # Canal
    ca, cb, _, _ = detectar_canal_roto(df.iloc[:vi+1], atr, lookback=40)

    # Indicadores
    lk5 = 5
    phi  = high  > float(df['High'].iloc[max(0,vi-lk5-2):vi-1].max()) if vi > lk5+2 else False
    pnl  = low   < float(df['Low'].iloc[max(0,vi-lk5-2):vi-1].min())  if vi > lk5+2 else False
    rlh  = rsi   < float(df['rsi'].iloc[max(0,vi-lk5-2):vi-1].max())  if vi > lk5+2 else False
    rhl  = rsi   > float(df['rsi'].iloc[max(0,vi-lk5-2):vi-1].min())  if vi > lk5+2 else False
    div_b = phi and rlh and rsi > 50
    div_a = pnl and rhl and rsi < 50
    adx_t = adx > 25; adx_l = adx < 20
    adx_b = (di_m > di_p) and adx_t
    adx_a = (di_p > di_m) and adx_t
    shoot = bear and uwi > body*2 and lwi < body*0.3 and en_r
    be    = bear and open_ >= float(prev['High']) and close <= float(prev['Low']) and en_r
    vr    = shoot or be or (bear and body > trng*0.8 and en_r) or (body < trng*0.1 and en_r and uwi > body*2)
    hamm  = bull and lwi > body*2 and uwi < body*0.3 and en_s
    bue   = bull and open_ <= float(prev['Low']) and close >= float(prev['High']) and en_s
    vreb  = hamm or bue or (bull and body > trng*0.8 and en_s) or (body < trng*0.1 and en_s and lwi > body*2)
    rsi_ag = (rsi >= 55) and (rsi < rsi_p)
    rsi_so = rsi >= 70
    rsi_bg = (rsi <= 45) and (rsi > rsi_p)
    rsi_sv = rsi <= 30
    eve   = detectar_evening_star(df, vi)
    mor   = detectar_morning_star(df, vi)
    mcb   = (macd < msig) and (mh < 0) and (mhp >= 0)
    mca   = (macd > msig) and (mh > 0) and (mhp <= 0)
    bbs   = close >= bbu or high >= bbu
    bbi   = close <= bbl or low  <= bbl
    eb    = ef < es; ea = ef > es
    b200  = close < et; s200 = close > et
    stu_b = ((high < float(prev['High']) and float(prev['High']) < float(p2['High'])) or
             (low  < float(prev['Low'])  and float(prev['Low'])  < float(p2['Low'])))
    stu_a = ((high > float(prev['High']) and float(prev['High']) > float(p2['High'])) or
             (low  > float(prev['Low'])  and float(prev['Low'])  > float(p2['Low'])))
    obd   = obv < obvp and obv < obve
    obc   = obv > obvp and obv > obve
    vol_a = vol > vavg*1.2

    ss = (
        (2 if en_r else 0) + (2 if vr else 0) + (2 if vol_a else 0) +
        (1 if rsi_ag else 0) + (1 if rsi_so else 0) + (1 if div_b else 0) +
        (1 if eb else 0) + (1 if stu_b else 0) + (1 if b200 else 0) +
        (2 if bbs else 0) + (2 if eve else 0) + (2 if mcb else 0) +
        (2 if adx_b else 0) + (1 if obd else 0) + (2 if ca else 0)
    )
    sb = (
        (2 if en_s else 0) + (2 if vreb else 0) + (2 if vol_a else 0) +
        (1 if rsi_bg else 0) + (1 if rsi_sv else 0) + (1 if div_a else 0) +
        (1 if ea else 0) + (1 if stu_a else 0) + (1 if s200 else 0) +
        (2 if bbi else 0) + (2 if mor else 0) + (2 if mca else 0) +
        (2 if adx_a else 0) + (1 if obc else 0) + (2 if cb else 0)
    )
    if adx_l: ss = max(0, ss-3); sb = max(0, sb-3)

    zona = "RESIST" if en_r else ("SOPORT" if en_s else ("~R" if aprox_r else ("~S" if aprox_s else "  ---  ")))
    return ss, sb, zona, rsi, adx, close, cancel_s, cancel_b, aprox_r, aprox_s, en_r, en_s

for vi in hoy_idx:
    ss, sb, zona, rsi, adx, close, cs, cb, aprx, aprs, en_r, en_s = calc_scores(df, vi)
    hora = df.index[vi].strftime('%H:%M')
    hora_dt = df.index[vi].to_pydatetime()
    bloq = " ⏸️PMI" if abs((hora_dt - PMI_UTC).total_seconds()) <= 3600 else ""
    senal = ""
    if ss >= 9 and not cs:   senal = "🔴SELL FUERTE"
    elif ss >= 6 and not cs: senal = "⚡SELL MEDIA"
    elif ss >= 5 and not cs: senal = "👀SELL ALERTA"
    elif sb >= 9 and not cb:  senal = "🟢BUY FUERTE"
    elif sb >= 6 and not cb:  senal = "⚡BUY MEDIA"
    elif sb >= 5 and not cb:  senal = "👀BUY ALERTA"
    elif aprx and ss >= 5:    senal = "⏳PREP SELL"
    elif aprs and sb >= 5:    senal = "⏳PREP BUY"
    print(f"  {hora} | {close:>7.1f} | {ss:>4} | {sb:>4} | {zona:>6} | {rsi:>5.1f} | {adx:>5.1f} | {senal:<14} {bloq}")

print()
print("Nota: 'Bloq PMI' = hora dentro de ±60min del PMI Flash 14:00 UTC")
print("Umbrales: ALERTA≥5 | MEDIA≥6 | FUERTE≥9 | MAX≥12")
