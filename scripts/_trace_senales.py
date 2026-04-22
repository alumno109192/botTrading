"""
Simula el flujo REAL del detector 1H para ver exactamente por qué no hubo señales.
Muestra cada punto de decisión (R:R, zona, PREP, ya_enviada, etc.)
"""
import sys
sys.path.insert(0, '.')

from adapters.data_provider import get_ohlcv
from core.indicators import (
    calcular_rsi, calcular_ema, calcular_atr, calcular_adx,
    calcular_bollinger_bands, calcular_macd, calcular_obv,
    detectar_canal_roto, detectar_evening_star, detectar_morning_star,
    calcular_sr_multiples
)
import pandas as pd
from datetime import datetime, timezone

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

params = {
    'ticker_yf': 'GC=F', 'sr_lookback': 150, 'sr_zone_mult': 0.8,
    'limit_offset_pct': 0.3, 'anticipar_velas': 3, 'cancelar_dist': 1.0,
    'rsi_min_sell': 55.0, 'rsi_max_buy': 45.0, 'atr_sl_mult': 1.0,
    'atr_tp1_mult': 1.5, 'atr_tp2_mult': 2.5, 'atr_tp3_mult': 4.0,
    'vol_mult': 1.2,
}

today_utc = datetime.now(timezone.utc).date()
hoy_idx = [i for i in range(4, len(df)-1) if df.index[i].date() == today_utc]

print(f"{'Hora':>5} | {'Close':>7} | {'SELL':>4} | {'Zona?':>7} | {'R:R':>5} | {'TP1':>7} | {'SL':>7} | {'Límite':>7} | Bloqueo")
print("-" * 95)

for vi in hoy_idx:
    row   = df.iloc[vi]
    prev  = df.iloc[vi-1]
    p2    = df.iloc[vi-2]
    atr   = float(row['atr'])
    close = float(row['Close'])
    high  = float(row['High'])
    low   = float(row['Low'])
    open_ = float(row['Open'])
    rsi   = float(row['rsi'])
    adx   = float(row['adx'])
    di_p  = float(row['di_plus'])
    di_m  = float(row['di_minus'])
    vol   = float(row['Volume'])
    vavg  = float(row['vol_avg'])
    hora  = df.index[vi].strftime('%H:%M')
    is_bear = close < open_
    is_bull = close > open_
    body  = float(row['body'])
    uwi   = float(row['upper_wick'])
    trng  = float(row['total_range'])

    # S/R zones
    lkb = 150; wing = 3
    highs_sr = df['High'].iloc[max(0,vi-lkb-1):vi]
    lows_sr  = df['Low'].iloc[max(0,vi-lkb-1):vi]
    sh, sl = [], []
    for i in range(wing, len(highs_sr)-wing):
        v = float(highs_sr.iloc[i])
        if all(v >= float(highs_sr.iloc[i-j]) for j in range(1,wing+1)) and \
           all(v >= float(highs_sr.iloc[i+j]) for j in range(1,wing+1)): sh.append(v)
    for i in range(wing, len(lows_sr)-wing):
        v = float(lows_sr.iloc[i])
        if all(v <= float(lows_sr.iloc[i-j]) for j in range(1,wing+1)) and \
           all(v <= float(lows_sr.iloc[i+j]) for j in range(1,wing+1)): sl.append(v)
    if not sh: sh = [float(highs_sr.max())]
    if not sl: sl = [float(lows_sr.min())]
    zw = atr*0.8; md = atr*0.3
    cr = sorted([v for v in set(sh+sl) if v > close+md])
    cs = sorted([v for v in set(sl+sh) if v < close-md], reverse=True)
    rp = cr[0] if cr else float(highs_sr.max())
    zrh = rp + zw*0.25; zrl = rp - zw*0.75
    tol = atr*0.4
    en_r = (high >= zrl-tol) and (high <= zrh+tol)
    avg_rng = float(df['total_range'].iloc[max(0,vi-6):vi].mean())
    aprox_r = (zrl-close > 0 and zrl-close < avg_rng*3 and close > float(df['Close'].iloc[vi-4]))

    # sell_limit
    lop = params['limit_offset_pct']
    sell_limit = zrl + (zrh - zrl) * (lop / 100 * 10)

    # SL estructural
    swing_wing = 3
    sub_sl = df.iloc[max(0,vi-30):vi+1]
    swing_h_vals = []
    for i in range(swing_wing, len(sub_sl)-swing_wing):
        h = float(sub_sl['High'].iloc[i])
        if all(h >= float(sub_sl['High'].iloc[i-j]) for j in range(1,swing_wing+1)) and \
           all(h >= float(sub_sl['High'].iloc[i+j]) for j in range(1,swing_wing+1)):
            swing_h_vals.append(h)
    sl_candidates = [v for v in swing_h_vals if v > sell_limit]
    sl_venta = round(min(sl_candidates) + atr*0.3, 1) if sl_candidates else round(sell_limit + atr, 1)

    # TPs desde S/R multiples — usa la misma lógica del fix (_tp1_viable_sell)
    soportes_sr, resistencias_sr = calcular_sr_multiples(df.iloc[:vi+1], atr, lkb, 0.8, 5)
    dist_sl = abs(sl_venta - sell_limit)
    tp1_v = round(sell_limit - atr*1.5, 1)  # fallback
    if dist_sl > 0:
        for nivel in soportes_sr:  # nearest first
            if abs(nivel - sell_limit) / dist_sl >= 1.2:
                tp1_v = round(nivel, 1)
                break

    # R:R
    def rr(limit, sl, tp):
        return round(abs(tp-limit)/abs(sl-limit), 2) if abs(sl-limit)>0 else 0
    rr_tp1 = rr(sell_limit, sl_venta, tp1_v)

    cancelar_rr = rr_tp1 < 1.2
    cancelar_sell = close > zrh * 1.01

    # Score mínimo
    adx_lat = adx < 20
    sh_star = is_bear and uwi > body*2
    en_r_score = en_r
    emas_baj = float(row['ema_fast']) < float(row['ema_slow'])
    b200 = close < float(row['ema_trend'])
    vol_ar = vol > vavg * 1.2
    ss_base = (2 if en_r else 0) + (1 if emas_baj else 0) + (1 if b200 else 0) + (2 if vol_ar else 0)
    if adx_lat: ss_base = max(0, ss_base-3)

    # Determinar bloqueo
    bloqueos = []
    if not en_r and not aprox_r:
        bloqueos.append("FUERA_ZONA")
    if cancelar_rr:
        bloqueos.append(f"RR={rr_tp1}<1.2")
    if cancelar_sell:
        bloqueos.append("CANCEL_DIST")
    if ss_base < 5:
        bloqueos.append(f"SCORE_BAJO({ss_base})")

    bloqueo_str = " | ".join(bloqueos) if bloqueos else "OK (deberia enviar)"
    zona_str = "RESIST" if en_r else ("~RESIST" if aprox_r else "FUERA")

    print(f"  {hora} | {close:>7.1f} | {ss_base:>4} | {zona_str:>7} | {rr_tp1:>5.2f} | {tp1_v:>7.1f} | {sl_venta:>7.1f} | {sell_limit:>7.1f} | {bloqueo_str}")
