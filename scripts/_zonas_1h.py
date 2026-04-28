"""Análisis de zonas 1H del oro."""
import warnings; warnings.filterwarnings('ignore')
import yfinance as yf
import pandas as pd
import numpy as np

tk = yf.Ticker('GC=F')
df = tk.history(period='10d', interval='1h')
df.columns = [c.lower() for c in df.columns]

close = df['close']
high  = df['high']
low   = df['low']

precio_actual = close.iloc[-1]
print(f'Precio actual: {precio_actual:.2f}')

# ATR 14
tr = pd.concat([
    high - low,
    (high - close.shift(1)).abs(),
    (low  - close.shift(1)).abs()
], axis=1).max(axis=1)
atr = tr.rolling(14).mean().iloc[-1]
print(f'ATR 1H: {atr:.2f}')
print()

# Mínimos y máximos locales (wing=3)
wing = 3
soportes, resistencias = [], []
for i in range(wing, len(df) - wing):
    if low.iloc[i] == low.iloc[i-wing:i+wing+1].min():
        soportes.append(low.iloc[i])
    if high.iloc[i] == high.iloc[i-wing:i+wing+1].max():
        resistencias.append(high.iloc[i])

# Clustering por tolerancia
def cluster(niveles, tol):
    niveles = sorted(niveles)
    grupos = []
    g = [niveles[0]]
    for n in niveles[1:]:
        if n - g[-1] <= tol:
            g.append(n)
        else:
            grupos.append(np.mean(g))
            g = [n]
    grupos.append(np.mean(g))
    return grupos

tol = atr * 0.6
sop_clust = cluster(soportes, tol)
res_clust = cluster(resistencias, tol)

sop_bajo = [s for s in sop_clust if s < precio_actual]
res_alto = [r for r in res_clust if r > precio_actual]

print('--- SOPORTES (debajo del precio) ---')
for s in sorted(sop_bajo, reverse=True)[:5]:
    dist = precio_actual - s
    pct  = dist / precio_actual * 100
    print(f'  {s:.2f}  (-{dist:.0f} pts | -{pct:.2f}%)')

print()
print('--- RESISTENCIAS (encima del precio) ---')
for r in sorted(res_alto)[:5]:
    dist = r - precio_actual
    pct  = dist / precio_actual * 100
    print(f'  {r:.2f}  (+{dist:.0f} pts | +{pct:.2f}%)')

# Vela actual
v = df.iloc[-1]
cuerpo = float(v['close'] - v['open'])
dir_vela = 'ALCISTA' if cuerpo > 0 else 'BAJISTA'
print()
print(f'Vela 1H actual: O={v["open"]:.2f}  H={v["high"]:.2f}  L={v["low"]:.2f}  C={v["close"]:.2f}')
print(f'Cuerpo: {cuerpo:+.2f}  ({dir_vela})')

# Tendencia 1H: EMA9 vs EMA21
ema9  = close.ewm(span=9).mean()
ema21 = close.ewm(span=21).mean()
tend  = 'ALCISTA' if ema9.iloc[-1] > ema21.iloc[-1] else 'BAJISTA'
print(f'EMA9={ema9.iloc[-1]:.2f}  EMA21={ema21.iloc[-1]:.2f}  -> Tendencia 1H: {tend}')

# RSI 14
delta = close.diff()
gain  = delta.clip(lower=0).rolling(14).mean()
loss  = (-delta.clip(upper=0)).rolling(14).mean()
rsi   = 100 - 100 / (1 + gain / loss)
print(f'RSI 14 (1H): {rsi.iloc[-1]:.1f}')
