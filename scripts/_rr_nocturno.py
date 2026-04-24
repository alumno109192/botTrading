"""
Analisis R:R nocturno (23 abril) con ambos fixes aplicados.
"""
import sys
sys.path.insert(0, '.')

from adapters.data_provider import get_ohlcv
from core.indicators import calcular_atr, calcular_sr_multiples
import pandas as pd

df_5m, _ = get_ohlcv('GC=F', period='7d', interval='5m')
df = df_5m.resample('1h').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
df['atr'] = calcular_atr(df, 14)

today = df.index[-1].date()
hoy_idx = [i for i in range(4, len(df)-1) if df.index[i].date() == today]

print(f"Fecha: {today}")
print(f"{'Hora':>5} | {'Close':>7} | {'ATR':>5} | {'Límite':>7} | {'SL':>7} | {'TP1':>7} | {'R:R':>5} | Estado")
print("-" * 85)

for vi in hoy_idx:
    # vela cerrada = iloc[-2] del slice hasta vi+1
    row  = df.iloc[vi-1]
    atr  = float(df['atr'].iloc[vi-1])
    close = float(row['Close'])
    hora  = df.index[vi-1].strftime('%H:%M')

    # S/R con velas cerradas (fix)
    _df_c = df.iloc[:vi]
    soportes, resistencias = calcular_sr_multiples(_df_c, atr, 150, 0.8, 8)

    # Resistencia más cercana sobre precio
    cr = sorted([v for v in resistencias if v > close + atr * 0.3])
    if not cr:
        print(f"  {hora} | {close:>7.1f} |  sin resistencia")
        continue
    resist    = cr[0]
    zrl       = resist - atr * 0.8 * 0.75
    zrh       = resist + atr * 0.8 * 0.25
    sell_limit = round(zrl + (zrh - zrl) * 0.3, 1)

    # SL: swing high más cercano sobre sell_limit + 0.3×ATR buffer
    sub = df.iloc[max(0, vi-30):vi]
    swing_h = []
    for i in range(3, len(sub)-3):
        h = float(sub['High'].iloc[i])
        if (all(h >= float(sub['High'].iloc[i-j]) for j in range(1, 4)) and
                all(h >= float(sub['High'].iloc[i+j]) for j in range(1, 4))):
            swing_h.append(h)
    cands = [v for v in swing_h if v > sell_limit]
    sl = round(min(cands) + atr * 0.3, 1) if cands else round(sell_limit + atr, 1)

    dist_sl = abs(sl - sell_limit)

    # _tp1_viable_sell: primer soporte con R:R >= 1.2, fallback ATR
    tp1 = round(sell_limit - atr * 1.5, 1)
    for nivel in soportes:
        if dist_sl > 0 and abs(nivel - sell_limit) / dist_sl >= 1.2:
            tp1 = round(nivel, 1)
            break

    rr  = round(abs(tp1 - sell_limit) / dist_sl, 2) if dist_sl > 0 else 0
    ok  = "PASA" if rr >= 1.2 else "BLOQ"
    en_zona = (close >= zrl - atr * 0.4) and (close <= zrh + atr * 0.4)
    zona = "EN_ZONA" if en_zona else "FUERA  "
    print(f"  {hora} | {close:>7.1f} | {atr:>5.1f} | {sell_limit:>7.1f} | {sl:>7.1f} | {tp1:>7.1f} | {rr:>5.2f} | {ok} {zona}")
