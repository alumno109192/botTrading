from adapters.data_provider import get_ohlcv
from core.indicators import calcular_rsi, calcular_ema, calcular_atr, calcular_macd, calcular_adx
import pandas as pd

df_5m, _ = get_ohlcv('GC=F', period='7d', interval='5m')
df = df_5m.resample('1h').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
print(f'Velas 1H: {len(df)}')

df['rsi']       = calcular_rsi(df['Close'], 14)
df['ema_fast']  = calcular_ema(df['Close'], 9)
df['ema_slow']  = calcular_ema(df['Close'], 21)
df['ema_trend'] = calcular_ema(df['Close'], 200)
df['atr']       = calcular_atr(df, 14)
df['total_range'] = df['High'] - df['Low']

row = df.iloc[-2]
atr = float(row['atr'])
close = float(row['Close'])
high  = float(row['High'])

# S/R con lookback=150 (actual)
lookback = 150
highs = df['High'].iloc[-lookback-1:-1]
lows  = df['Low'].iloc[-lookback-1:-1]
resist_pivot  = float(highs.max())
support_pivot = float(lows.min())
zone_width = atr * 0.8
zrh = round(resist_pivot + zone_width * 0.25, 2)
zrl = round(resist_pivot - zone_width * 0.75, 2)
zsh = round(support_pivot + zone_width * 0.75, 2)
zsl = round(support_pivot - zone_width * 0.25, 2)
tol = atr * 0.4
avg_range = float(df['total_range'].iloc[-6:-1].mean())

print(f'\n--- ULTIMA VELA 1H ({df.index[-2]}) ---')
print(f'Close: {close:.1f} | High: {high:.1f} | ATR: {atr:.1f}')
print(f'RSI: {float(row["rsi"]):.1f} | EMA9: {float(row["ema_fast"]):.1f} | EMA21: {float(row["ema_slow"]):.1f}')

print(f'\n--- ZONAS S/R (lookback={lookback}) ---')
print(f'Resistencia: {zrl:.1f} - {zrh:.1f}  (pivot ATH = {resist_pivot:.1f})')
print(f'Soporte:     {zsl:.1f} - {zsh:.1f}  (pivot ATL = {support_pivot:.1f})')
print(f'→ Distancia a resistencia: {zrl - close:.1f} pts  (avg_range×3 = {avg_range*3:.1f})')
print(f'→ Precio en tierra de nadie: SELL bloqueada por filtro de proximidad')

# Fallo de continuacion en 1H (lkb=20)
lkb = 20
ath_20 = float(df['High'].iloc[-lkb-1:-1].max())
atl_20 = float(df['Low'].iloc[-lkb-1:-1].min())
en_ath = high >= ath_20 * 0.995
is_bearish = float(row['Close']) < float(row['Open'])
fallo_cont_bajista = (en_ath and is_bearish and
                      close < float(row['ema_fast']) and
                      abs(float(row['Close']) - float(row['Open'])) > atr * 0.4)
print(f'\n--- FALLO DE CONTINUACION 1H ---')
print(f'ATH 20 velas: {ath_20:.1f} | ATL 20 velas: {atl_20:.1f}')
print(f'fallo_continuacion_bajista: {fallo_cont_bajista}')
print(f'  en_ath={en_ath} (high={high:.1f} vs ath_20={ath_20:.1f})')

# ¿El nivel 4796-4806 (soporte roto) está cubierto?
nivel_broken = 4796.0
dist_broken = nivel_broken - close
print(f'\n--- NIVEL ROTO (4796 soporte→resistencia) ---')
print(f'Distancia de close ({close:.1f}) a nivel roto ({nivel_broken:.1f}): {dist_broken:.1f} pts')
print(f'→ La lógica actual NO detecta "soporte roto como resistencia"')
print(f'→ Con fallo de continuacion en 1H: el detector buscaría velas en ATH-20h, aprox {ath_20:.1f}')
