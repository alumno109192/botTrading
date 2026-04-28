"""Diagnóstico: ¿saltará alerta BUY en 1H si el precio toca soporte?"""
import warnings; warnings.filterwarnings('ignore')
from adapters.data_provider import get_ohlcv
from core.indicators import calcular_rsi, calcular_atr, calcular_adx, calcular_sr_multiples

df, delayed = get_ohlcv('GC=F', period='30d', interval='1h')
print(f'Velas 1H: {len(df)} | delayed: {delayed}')

df['rsi'] = calcular_rsi(df['Close'], 14)
df['atr'] = calcular_atr(df, 14)
df['adx'], df['dip'], df['dim'] = calcular_adx(df, 14)

row      = df.iloc[-2]   # última cerrada
row_live = df.iloc[-1]   # vela viva
atr      = float(row['atr'])
atr_media = float(df['atr'].rolling(20).mean().iloc[-2])

close = float(row['Close'])
print(f'\nVela cerrada: {df.index[-2]}')
print(f'Close={close:.2f}  High={float(row["High"]):.2f}  Low={float(row["Low"]):.2f}')
print(f'RSI={float(row["rsi"]):.1f}  ADX={float(row["adx"]):.1f}  DI+={float(row["dip"]):.1f}  DI-={float(row["dim"]):.1f}')
print(f'ATR={atr:.2f}  ATR_media={atr_media:.2f}')

print(f'\nVela VIVA: {df.index[-1]}')
close_live = float(row_live['Close'])
low_live   = float(row_live['Low'])
open_live  = float(row_live['Open'])
high_live  = float(row_live['High'])
print(f'Close={close_live:.2f}  High={high_live:.2f}  Low={low_live:.2f}  Open={open_live:.2f}')

# === ZONAS S/R (mismo algoritmo que base_detector.calcular_zonas_sr) ===
_df_cerrado = df.iloc[:-1]
lookback = 150
wing = 3
n_h = len(_df_cerrado)
lows  = _df_cerrado['Low'].iloc[-lookback:]
highs = _df_cerrado['High'].iloc[-lookback:]
n = len(lows)

candidatos_sop = []
for i in range(wing, n - wing):
    if all(lows.iloc[i] <= lows.iloc[i-j] for j in range(1, wing+1)) and \
       all(lows.iloc[i] <= lows.iloc[i+j] for j in range(1, wing+1)):
        candidatos_sop.append(float(lows.iloc[i]))

candidatos_res = []
for i in range(wing, n - wing):
    if all(highs.iloc[i] >= highs.iloc[i-j] for j in range(1, wing+1)) and \
       all(highs.iloc[i] >= highs.iloc[i+j] for j in range(1, wing+1)):
        candidatos_res.append(float(highs.iloc[i]))

sop_debajo = [s for s in candidatos_sop if s <= close]
res_encima = [r for r in candidatos_res if r >= close]
support_pivot = max(sop_debajo) if sop_debajo else float(lows.min())
resist_pivot  = min(res_encima) if res_encima else float(highs.max())

zone_mult = 0.5
zsh = round(support_pivot + atr * zone_mult * 0.75, 2)
zsl = round(support_pivot - atr * zone_mult * 0.25, 2)
zrh = round(resist_pivot  + atr * zone_mult * 0.25, 2)
zrl = round(resist_pivot  - atr * zone_mult * 0.75, 2)
tol = atr * 0.8   # sr_zone_mult del detector 1H

print(f'\n--- ZONAS S/R 1H ---')
print(f'Soporte pivot:    {support_pivot:.2f}')
print(f'Zona soporte:     {zsl:.2f} - {zsh:.2f}   (tol±{tol:.1f})')
print(f'  → precio entraría en zona si low llega a: {zsh + tol:.2f}  (desde arriba)')
print(f'  → precio mínimo sin cancelar:             {zsl * 0.995:.2f}')
print(f'Resistencia pivot:{resist_pivot:.2f}')
print(f'Zona resist:      {zrl:.2f} - {zrh:.2f}')

# SR intermedios wing=2
sops, ress = calcular_sr_multiples(_df_cerrado, atr, lookback=150, zone_mult=0.5, n_niveles=8, wing=2)
print(f'\n--- SR INTERMEDIOS (wing=2) ---')
print(f'Soportes cercanos: {[round(s,1) for s in sorted(sops, reverse=True)[:5]]}')
print(f'Resists cercanas:  {[round(r,1) for r in sorted(ress)[:5]]}')

# === DETECCIÓN REBOTE EN VELA VIVA ===
rebote_soporte_live = (
    (low_live  >= zsl - tol) and (low_live <= zsh + tol) and
    close_live > open_live and
    close_live > zsh - tol and
    low_live   < zsh
)
en_zona_soporte_cerrada = (float(row['Low']) >= zsl - tol) and (float(row['Low']) <= zsh + tol)
aproximando_soporte = (close > zsh) and (close - zsh < atr * 2) and (close < float(df['Close'].iloc[-5]))
cancelar_buy = close < zsl * 0.995

print(f'\n--- ESTADO ACTUAL ---')
print(f'en_zona_soporte (vela cerrada):  {en_zona_soporte_cerrada}')
print(f'aproximando_soporte:             {aproximando_soporte}  (dist={close - zsh:.1f})')
print(f'rebote_soporte_live (vela viva): {rebote_soporte_live}')
print(f'  low_live({low_live:.2f}) en [{zsl-tol:.1f} - {zsh+tol:.1f}]: {(low_live >= zsl-tol) and (low_live <= zsh+tol)}')
print(f'  alcista? close({close_live:.2f}) > open({open_live:.2f}): {close_live > open_live}')
print(f'  sobre zona? close({close_live:.2f}) > {zsh-tol:.2f}: {close_live > zsh - tol}')
print(f'cancelar_buy: {cancelar_buy}')

# === UMBRALES ===
def umbral_adapt(base, atr, atr_media):
    ratio = atr / atr_media if atr_media > 0 else 1.0
    if ratio > 1.5: return base + 2
    if ratio > 1.2: return base + 1
    return base

u_ale = umbral_adapt(5, atr, atr_media)
u_med = umbral_adapt(6, atr, atr_media)
u_fue = umbral_adapt(9, atr, atr_media)
print(f'\n--- UMBRALES SEÑAL (ATR_ratio={atr/atr_media:.2f}) ---')
print(f'ALERTA:  score_buy >= {u_ale}')
print(f'MEDIA:   score_buy >= {u_med}')
print(f'FUERTE:  score_buy >= {u_fue}')

# === RESUMEN ===
print(f'\n{"="*50}')
print(f'RESUMEN: precio actual {close:.2f}, soporte en {zsl:.0f}-{zsh:.0f}')
dist = close - zsh
if dist > 0:
    print(f'→ Precio está {dist:.1f} pts POR ENCIMA del soporte (zona empieza en {zsh:.2f})')
    print(f'→ Para activar rebote_live, precio debe caer {dist:.1f} pts más hasta {zsh:.2f}')
else:
    print(f'→ Precio está {-dist:.1f} pts DENTRO de la zona de soporte')
print(f'→ Con rebote confirmado y score_buy >= {u_ale}, se dispara ALERTA en Telegram')
print(f'→ Con score_buy >= {u_med}, MEDIA en Telegram')
print(f'→ Con score_buy >= {u_fue}, FUERTE en Telegram')
