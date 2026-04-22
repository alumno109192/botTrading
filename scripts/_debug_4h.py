from adapters.data_provider import get_ohlcv
from core.indicators import calcular_rsi, calcular_ema, calcular_atr, calcular_macd, calcular_adx
import pandas as pd

df, delayed = get_ohlcv('GC=F', period='60d', interval='4h')
print(f'Velas 4H: {len(df)} | delayed: {delayed}')
print(f'Rango precios: {df["Low"].min():.1f} - {df["High"].max():.1f}')

df['rsi']       = calcular_rsi(df['Close'], 28)
df['ema_fast']  = calcular_ema(df['Close'], 18)
df['ema_slow']  = calcular_ema(df['Close'], 42)
df['ema_trend'] = calcular_ema(df['Close'], 400)
df['atr']       = calcular_atr(df, 28)
df['macd'], df['macd_signal'], df['macd_hist'] = calcular_macd(df['Close'], 24, 52, 18)
df['adx'], df['di_plus'], df['di_minus'] = calcular_adx(df, 28)
df['total_range'] = df['High'] - df['Low']

row  = df.iloc[-2]
prev = df.iloc[-3]
p2   = df.iloc[-4]
atr  = float(row['atr'])

lookback = 80
highs = df['High'].iloc[-lookback-1:-1]
lows  = df['Low'].iloc[-lookback-1:-1]
resist_pivot  = float(highs.max())
support_pivot = float(lows.min())
zone_width = atr * 0.6
zrh = round(resist_pivot + zone_width * 0.25, 2)
zrl = round(resist_pivot - zone_width * 0.75, 2)
zsh = round(support_pivot + zone_width * 0.75, 2)
zsl = round(support_pivot - zone_width * 0.25, 2)

close = float(row['Close'])
high  = float(row['High'])
low   = float(row['Low'])
tol   = atr * 0.4

print(f'\n--- ULTIMA VELA ({df.index[-2]}) ---')
print(f'Close: {close:.1f} | High: {high:.1f} | Low: {low:.1f}')
print(f'RSI: {float(row["rsi"]):.1f} | EMA_fast: {float(row["ema_fast"]):.1f} | EMA_slow: {float(row["ema_slow"]):.1f}')
print(f'MACD hist: {float(row["macd_hist"]):.2f} | ADX: {float(row["adx"]):.1f} | DI+: {float(row["di_plus"]):.1f} | DI-: {float(row["di_minus"]):.1f}')

print(f'\n--- ZONAS S/R ---')
print(f'Resistencia: {zrl:.1f} - {zrh:.1f}  (pivot={resist_pivot:.1f})')
print(f'Soporte:     {zsl:.1f} - {zsh:.1f}  (pivot={support_pivot:.1f})')
print(f'Tolerancia ATR: {tol:.1f}')

en_zona_resist  = (high >= zrl - tol) and (high <= zrh + tol)
en_zona_soporte = (low  >= zsl - tol) and (low  <= zsh + tol)
avg_range = float(df['total_range'].iloc[-6:-1].mean())
av = 3
dist_to_resist  = zrl - close
dist_to_support = close - zsh
aprox_resist = (dist_to_resist > 0) and (dist_to_resist < avg_range * av) and (close > float(df['Close'].iloc[-5]))
aprox_sop    = (dist_to_support > 0) and (dist_to_support < avg_range * av) and (close < float(df['Close'].iloc[-5]))

print(f'\nen_zona_resist: {en_zona_resist}  (high={high:.1f} vs zona {zrl:.1f}-{zrh:.1f} +-{tol:.1f})')
print(f'en_zona_soporte: {en_zona_soporte}  (low={low:.1f} vs zona {zsl:.1f}-{zsh:.1f} +-{tol:.1f})')
print(f'dist_to_resist: {dist_to_resist:.1f}  avg_range*{av}: {avg_range*av:.1f}')
print(f'dist_to_support: {dist_to_support:.1f}')
print(f'aprox_resistencia: {aprox_resist}')
print(f'aprox_soporte: {aprox_sop}')

# tf_bias
from services import tf_bias
bias = tf_bias.get_sesgo_actual('XAUUSD', '1D')
print(f'\n--- TF BIAS ---')
print(f'Sesgo 1D: {bias}')
ok_sell, desc_sell = tf_bias.verificar_confluencia('XAUUSD', '4H', tf_bias.BIAS_BEARISH)
ok_buy,  desc_buy  = tf_bias.verificar_confluencia('XAUUSD', '4H', tf_bias.BIAS_BULLISH)
print(f'Confluencia SELL ok: {ok_sell} — {desc_sell[:120]}')
print(f'Confluencia BUY  ok: {ok_buy}  — {desc_buy[:120]}')
