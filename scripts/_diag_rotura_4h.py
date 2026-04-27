"""
Diagnóstico: ¿Se detectaría la posible rotura alcista actual en Gold 4H?
"""
import sys
sys.path.insert(0, '.')
from adapters.data_provider import get_ohlcv
from core.indicators import (
    calcular_rsi, calcular_ema, calcular_atr, calcular_macd, calcular_adx,
    calcular_bollinger_bands, calcular_obv,
    detectar_rotura_alcista, detectar_canal_roto, detectar_precio_en_canal,
    detectar_doble_suelo,
)
from core.base_detector import BaseDetector
import pandas as pd

class TmpDet(BaseDetector):
    def analizar(self, *a, **k): pass

det = TmpDet('XAUUSD', '4H', {}, None)

df, _ = get_ohlcv('GC=F', period='60d', interval='4h')
if hasattr(df.columns, 'get_level_values'):
    df.columns = df.columns.get_level_values(0)

df['atr']        = calcular_atr(df, 28)
df['rsi']        = calcular_rsi(df['Close'], 28)
df['ema_fast']   = calcular_ema(df['Close'], 18)
df['ema_slow']   = calcular_ema(df['Close'], 42)
df['ema_trend']  = calcular_ema(df['Close'], 400)
df['vol_avg']    = df['Volume'].rolling(20).mean()
df['obv']        = calcular_obv(df)
df['obv_ema']    = calcular_ema(df['obv'], 20)
df['adx'], df['di_plus'], df['di_minus'] = calcular_adx(df, 28)
df['macd'], df['macd_signal'], df['macd_hist'] = calcular_macd(df['Close'], 24, 52, 18)
df['bb_upper'], df['bb_mid'], df['bb_lower'], df['bb_width'] = calcular_bollinger_bands(df['Close'], 40)
df['total_range'] = df['High'] - df['Low']
df['is_bullish']  = df['Close'] > df['Open']
df['is_bearish']  = df['Close'] < df['Open']
df['body']        = (df['Close'] - df['Open']).abs()
df['upper_wick']  = df['High'] - df[['Close','Open']].max(axis=1)
df['lower_wick']  = df[['Close','Open']].min(axis=1) - df['Low']

row  = df.iloc[-2]
prev = df.iloc[-3]
p2   = df.iloc[-4]
atr  = float(row['atr'])
close = float(row['Close'])
high  = float(row['High'])
low   = float(row['Low'])
vol   = float(row['Volume'])
vol_avg = float(row['vol_avg'])
rsi   = float(row['rsi'])
adx   = float(row['adx'])
di_plus = float(row['di_plus'])
di_minus = float(row['di_minus'])
macd_hist = float(row['macd_hist'])
macd_hist_prev = float(prev['macd_hist'])
macd = float(row['macd'])
macd_signal = float(row['macd_signal'])
obv   = float(row['obv'])
obv_prev = float(prev['obv'])
obv_ema = float(row['obv_ema'])
bb_upper = float(row['bb_upper'])
bb_lower = float(row['bb_lower'])

fecha = df.index[-2].strftime('%Y-%m-%d %H:%M')

PARAMS = {
    'sr_lookback': 80, 'sr_zone_mult': 0.6, 'vol_mult': 1.2,
    'anticipar_velas': 5,  # nuevo valor
    'cancelar_dist': 1.0, 'limit_offset_pct': 0.3,
    'rsi_length': 28, 'rsi_min_sell': 55.0, 'rsi_max_buy': 45.0,
    'ema_fast_len': 18, 'ema_slow_len': 42, 'ema_trend_len': 400,
    'atr_length': 28, 'atr_sl_mult': 1.2,
    'atr_tp1_mult': 2.0, 'atr_tp2_mult': 3.5, 'atr_tp3_mult': 5.5,
}

zrl, zrh, zsl, zsh = det.calcular_zonas_sr(df, atr, 80, 0.6)
tol = round(atr * 0.75, 2)   # nuevo valor
avg_range = float(df['total_range'].iloc[-6:-1].mean())
av = 5  # nuevo valor
vm = 1.2

print(f"=== ESTADO ACTUAL Gold 4H ===")
print(f"Vela: {fecha}")
print(f"Close={close:.1f} | High={high:.1f} | Low={low:.1f}")
print(f"ATR={atr:.2f}  tol={tol:.2f}  avg_range={avg_range:.2f}")
print()
print(f"--- Zonas S/R ---")
print(f"Resistencia: {zrl:.1f} - {zrh:.1f}")
print(f"Soporte:     {zsl:.1f} - {zsh:.1f}")
print()

# Condiciones de zona
en_zona_resist  = (high >= zrl - tol) and (high <= zrh + tol)
en_zona_soporte = (low  >= zsl - tol) and (low  <= zsh + tol)
dist_to_resist  = zrl - close
dist_to_support = close - zsh
aprox_resist = dist_to_resist > 0 and dist_to_resist < avg_range * av and close > float(df['Close'].iloc[-5])
aprox_sop    = dist_to_support > 0 and dist_to_support < avg_range * av and close < float(df['Close'].iloc[-5])

print(f"--- Proximidad a zonas ---")
print(f"en_zona_resist:  {en_zona_resist}   (high={high:.1f} | rango zona: {zrl-tol:.1f} - {zrh+tol:.1f})")
print(f"en_zona_soporte: {en_zona_soporte}  (low={low:.1f} | rango zona: {zsl-tol:.1f} - {zsh+tol:.1f})")
print(f"aprox_resist:    {aprox_resist}   dist={dist_to_resist:.1f} vs avg*{av}={avg_range*av:.1f}")
print(f"aprox_soporte:   {aprox_sop}  dist={dist_to_support:.1f} vs avg*{av}={avg_range*av:.1f}")
print()

# Rotura alcista
rotura_alcista = detectar_rotura_alcista(df, zrh, atr, vm)
rotura_bajista_f = False  # no importa para BUY

# Canal
canal_alcista_roto, canal_bajista_roto, linea_sop_canal, linea_res_canal = detectar_canal_roto(
    df, atr, lookback=80, wing=3)
en_resist_canal, en_sop_canal, linea_res_precio, linea_sop_precio = detectar_precio_en_canal(
    df, atr, lookback=80, wing=3)

# Doble suelo
_dt_lookback = min(80, 40)
ds_detectado, ds_nivel_suelo, ds_neckline = detectar_doble_suelo(df, atr, lookback=_dt_lookback, tol_mult=0.7)

print(f"--- Patrones de rotura / canal ---")
print(f"rotura_alcista:          {rotura_alcista}")
print(f"canal_bajista_roto:      {canal_bajista_roto}  (linea_resist={linea_res_canal:.1f})")
print(f"en_soporte_canal_alcist: {en_sop_canal}  (linea_sop={linea_sop_precio:.1f})")
print(f"en_resist_canal_bajista: {en_resist_canal} (linea_res={linea_res_precio:.1f})")
print(f"doble_suelo:             {ds_detectado}")
print()

# Indicadores para score BUY
ema_fast = float(row['ema_fast']); ema_slow = float(row['ema_slow']); ema_trend = float(row['ema_trend'])
is_bullish = bool(row['is_bullish']); is_bearish = bool(row['is_bearish'])
body = float(row['body']); total_range = float(row['total_range'])
lower_wick = float(row['lower_wick']); upper_wick = float(row['upper_wick'])
rsi_prev = float(prev['rsi'])
lookback = 5

price_new_low = low < float(df['Low'].iloc[-lookback-2:-2].min())
rsi_higher_low = rsi > float(df['rsi'].iloc[-lookback-2:-2].min())
divergencia_alcista = price_new_low and rsi_higher_low and rsi < 50
rsi_bajo_girando = (rsi <= 45.0) and (rsi > rsi_prev)
rsi_sobreventa = rsi <= 30
vol_alto_rebote = vol > vol_avg * vm
emas_alcistas = ema_fast > ema_slow
sobre_ema200 = close > ema_trend
max_creciente = (high > float(prev['High'])) and (float(prev['High']) > float(p2['High']))
min_creciente = (low  > float(prev['Low']))  and (float(prev['Low'])  > float(p2['Low']))
estructura_alcista = max_creciente or min_creciente
hammer = is_bullish and lower_wick > body*2 and upper_wick < body*0.3 and en_zona_soporte
bullish_engulfing = is_bullish and float(row['Open']) <= float(prev['Low']) and close >= float(prev['High']) and en_zona_soporte
bullish_marubozu = is_bullish and body > total_range*0.8 and en_zona_soporte
doji_sop = body < total_range*0.1 and en_zona_soporte and lower_wick > body*2
vela_rebote = hammer or bullish_engulfing or bullish_marubozu or doji_sop
intento_caida_fallido = (low <= zsh) and (close > zsh)
bb_toca_inferior = close <= float(row['bb_lower']) or low <= float(row['bb_lower'])
macd_cruce_alcista = (macd > macd_signal) and (macd_hist > 0) and (macd_hist_prev <= 0)
macd_positivo = macd > 0
macd_div_alcista = price_new_low and (macd > float(df['macd'].iloc[-lookback-2:-2].min()))
adx_tendencia_fuerte = adx > 25
adx_alcista = (di_plus > di_minus) and adx_tendencia_fuerte
adx_lateral = adx < 20
obv_div_alcista = price_new_low and (obv > float(df['obv'].iloc[-lookback-2:-2].min()))
obv_creciente = obv > obv_prev and obv > obv_ema
vol_dec_sell = (vol < float(prev['Volume'])) and (float(prev['Volume']) < float(p2['Volume'])) and is_bearish

_atl = float(df['Low'].iloc[-20-1:-1].min())
_en_atl = low <= _atl * 1.005
fallo_cont_alcista = _en_atl and is_bullish and close > ema_fast and body > atr*0.4 and vol > vol_avg*1.3

print(f"--- Indicadores BUY ---")
print(f"RSI={rsi:.1f} | rsi_bajo_girando={rsi_bajo_girando} | rsi_sobreventa={rsi_sobreventa}")
print(f"emas_alcistas={emas_alcistas} | sobre_ema200={sobre_ema200}")
print(f"estructura_alcista={estructura_alcista} (max_crec={max_creciente} min_crec={min_creciente})")
print(f"vela_rebote={vela_rebote} | intento_caida_fallido={intento_caida_fallido}")
print(f"vol_alto_rebote={vol_alto_rebote} ({vol:.0f} vs {vol_avg*vm:.0f})")
print(f"divergencia_alcista={divergencia_alcista}")
print(f"bb_toca_inferior={bb_toca_inferior}")
print(f"macd_cruce_alcista={macd_cruce_alcista} | macd_positivo={macd_positivo}")
print(f"adx_alcista={adx_alcista} | adx_lateral={adx_lateral}")
print(f"obv_creciente={obv_creciente}")
print(f"fallo_cont_alcista={fallo_cont_alcista}")
print()

score_buy = 0
score_buy += 2 if en_zona_soporte          else 0
score_buy += 2 if vela_rebote              else 0
score_buy += 2 if vol_alto_rebote          else 0
score_buy += 1 if rsi_bajo_girando         else 0
score_buy += 1 if rsi_sobreventa           else 0
score_buy += 1 if divergencia_alcista      else 0
score_buy += 1 if emas_alcistas            else 0
score_buy += 1 if estructura_alcista       else 0
score_buy += 1 if intento_caida_fallido    else 0
score_buy += 1 if vol_dec_sell             else 0
score_buy += 1 if (hammer and vol_alto_rebote) else 0
score_buy += 1 if (divergencia_alcista and rsi_sobreventa) else 0
score_buy += 1 if sobre_ema200             else 0
score_buy += 2 if bb_toca_inferior         else 0
score_buy += 2 if macd_cruce_alcista       else 0
score_buy += 2 if adx_alcista              else 0
score_buy += 1 if macd_div_alcista         else 0
score_buy += 1 if obv_div_alcista          else 0
score_buy += 1 if obv_creciente            else 0
score_buy += 1 if macd_positivo            else 0
score_buy += 3 if fallo_cont_alcista       else 0
score_buy += 4 if rotura_alcista           else 0
score_buy += 3 if ds_detectado             else 0
score_buy += 2 if canal_bajista_roto       else 0
score_buy += 3 if en_sop_canal             else 0

if adx_lateral:
    score_buy = max(0, score_buy - 3)

atr_media = float(df['atr'].rolling(20).mean().iloc[-2])
ratio = atr / atr_media if atr_media > 0 else 1.0
bump = max(0, min(4, int((ratio - 1.0) * 5)))
umbral_ale = 5 + bump
umbral_med = 9 + bump
umbral_fue = 12 + bump
umbral_max = 14 + bump

cerca_soporte = en_zona_soporte or aprox_sop or fallo_cont_alcista or en_sop_canal
cancelar_buy  = close < zsl * (1 - 1.0/100)

print(f"--- RESULTADO ---")
print(f"score_buy = {score_buy}  (umbrales: ale={umbral_ale} med={umbral_med} fue={umbral_fue} max={umbral_max})")
print(f"cerca_soporte = {cerca_soporte}")
print(f"cancelar_buy  = {cancelar_buy}")
print(f"senal_buy_alerta = {score_buy >= umbral_ale}")
print(f"senal_buy_media  = {score_buy >= umbral_med}")
print(f"senal_buy_fuerte = {score_buy >= umbral_fue}")
print()
if cerca_soporte and score_buy >= umbral_ale and not cancelar_buy:
    print(f">>> SEÑAL BUY SERIA EMITIDA <<<")
elif rotura_alcista:
    print(f">>> ROTURA ALCISTA detectada — señal BREAK_BUY activa <<<")
elif canal_bajista_roto:
    print(f">>> CANAL BAJISTA ROTO — se sumaría al score (+2) <<<")
elif en_resist_canal and score_buy >= umbral_ale:
    print(f">>> EN DIRECTRIZ BAJISTA — señal BUY en resistencia canal <<<")
else:
    print(f">>> NO se emitiria señal — razón:")
    if not cerca_soporte:
        print(f"    - cerca_soporte=False (no en zona ni aproximando)")
    if score_buy < umbral_ale:
        print(f"    - score_buy={score_buy} < umbral_alerta={umbral_ale}")
    if cancelar_buy:
        print(f"    - cancelar_buy=True")
