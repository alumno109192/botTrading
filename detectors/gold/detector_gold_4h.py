import os
from adapters.data_provider import get_ohlcv

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
import json
from datetime import datetime, timezone
from dotenv import load_dotenv
from services import tf_bias
from services.dxy_bias import get_dxy_bias, ajustar_score_por_dxy
from services.economic_calendar import hay_evento_impacto

# Cargar variables de entorno
load_dotenv()
from adapters.telegram import enviar_telegram as _enviar_telegram_base

def enviar_telegram(mensaje):
    return _enviar_telegram_base(mensaje, TELEGRAM_THREAD_ID)

# Inicializar base de datos solo si las variables están configuradas
db = None
try:
    turso_url = os.environ.get('TURSO_DATABASE_URL')
    turso_token = os.environ.get('TURSO_AUTH_TOKEN')
    if turso_url and turso_token:
        from adapters.database import DatabaseManager
        db = DatabaseManager()
        print("✅ Sistema de tracking de BD activado")
    else:
        print("⚠️  Variables Turso no configuradas - Sistema funcionará sin tracking de BD")
except Exception as e:
    print(f"⚠️  No se pudo inicializar BD: {e}")
    print("⚠️  Sistema funcionará sin tracking de BD")
    db = None

# ══════════════════════════════════════
# CONFIGURACIÓN 4H
# ══════════════════════════════════════
TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_THREAD_ID = int(os.environ.get('THREAD_ID_SWING') or 0) or None

CHECK_INTERVAL = 4 * 60  # cada 4 minutos (balance óptimo para timeframe 4H)

# ══════════════════════════════════════
# PARÁMETROS — ESPECÍFICOS GOLD 4H
# ══════════════════════════════════════
SIMBOLOS = {
    'XAUUSD': {
        'ticker_yf':          'GC=F',       # Oro en Yahoo Finance
        # Zonas S/R calculadas automáticamente en analizar() — sin mantenimiento manual
        'sr_lookback':        80,           # 80 velas 4H ≈ 13 días de historia
        'sr_zone_mult':       0.6,          # ancho de zona = atr × 0.6
        'limit_offset_pct':   0.3,
        'anticipar_velas':    3,
        'cancelar_dist':      1.0,
        'rsi_length':         28,           # 14D × 2 para 4H
        'rsi_min_sell':       55.0,
        'rsi_max_buy':        45.0,
        'ema_fast_len':       18,           # 9D × 2 para 4H
        'ema_slow_len':       42,           # 21D × 2 para 4H
        'ema_trend_len':      400,          # 200D × 2 para 4H
        'atr_length':         28,           # 14D × 2 para 4H
        'atr_sl_mult':        1.2,          # Menos agresivo para 4H (era 1.5)
        'atr_tp1_mult':       2.0,          # TP1: 2.0× ATR 4H (~$60-100 desde entry)
        'atr_tp2_mult':       3.5,          # TP2: 3.5× ATR
        'atr_tp3_mult':       5.5,          # TP3: 5.5× ATR (objetivo swing)
        'vol_mult':           1.2,
    }
}

# ══════════════════════════════════════
# CONTROL ANTI-SPAM
# ══════════════════════════════════════
alertas_enviadas = {}
ultimo_analisis = {}


# ══════════════════════════════════════
# INDICADORES TÉCNICOS
# ══════════════════════════════════════
from core.indicators import (
    calcular_rsi, calcular_ema, calcular_atr,
    calcular_bollinger_bands, calcular_macd, calcular_obv, calcular_adx,
    detectar_evening_star, detectar_morning_star,
)

# ══════════════════════════════════════
# LÓGICA PRINCIPAL
# ══════════════════════════════════════
def en_sesion_activa_4h():
    """Filtro horario 4H: sesión London/NY ampliada (06:00-22:00 UTC).
    Evita señales en la ventana puramente asiática nocturna (22:00-06:00 UTC)
    donde Gold tiene menor liquidez y spreads más amplios."""
    from datetime import timezone as tz
    hora_utc = datetime.now(tz.utc).hour
    return 6 <= hora_utc < 22


def calcular_zonas_sr(df, atr, lookback, zone_mult):
    """
    Detecta automáticamente zonas S/R desde swing highs/lows históricos.
    Returns: (zrl, zrh, zsl, zsh)
    """
    highs = df['High'].iloc[-lookback-1:-1]
    lows  = df['Low'].iloc[-lookback-1:-1]
    
    resist_pivot  = float(highs.max())
    support_pivot = float(lows.min())
    zone_width = atr * zone_mult
    
    zrh = round(resist_pivot + zone_width * 0.25, 2)
    zrl = round(resist_pivot - zone_width * 0.75, 2)
    zsh = round(support_pivot + zone_width * 0.75, 2)
    zsl = round(support_pivot - zone_width * 0.25, 2)
    return zrl, zrh, zsl, zsh


def analizar(simbolo, params):
    print(f"\n🔍 Analizando {simbolo} [4H]...")

    if not en_sesion_activa_4h():
        print(f"  ⏸️  [4H] Fuera de sesión (06-22 UTC) — análisis saltado")
        return

    # ── Filtro calendario económico ──
    bloqueado, descripcion = hay_evento_impacto(ventana_minutos=60)
    if bloqueado:
        print(f"  🚫 [4H] Señal bloqueada por evento macro: {descripcion}")
        return

    # Descargar datos 4H
    try:
        df, is_delayed = get_ohlcv(params['ticker_yf'], period='60d', interval='4h')
        if is_delayed:
            print("  ⚠️  [4H] Datos con 15 min de delay (yfinance). Configura TWELVE_DATA_API_KEY para tiempo real.")
        if df.empty or len(df) < 200:
            print(f"⚠️ Datos insuficientes para {simbolo}")
            return
    except Exception as e:
        print(f"❌ Error descargando {simbolo}: {e}")
        return

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.copy()

    # Indicadores base (4H ajustados)
    df['rsi']       = calcular_rsi(df['Close'], params['rsi_length'])
    df['ema_fast']  = calcular_ema(df['Close'], params['ema_fast_len'])
    df['ema_slow']  = calcular_ema(df['Close'], params['ema_slow_len'])
    df['ema_trend'] = calcular_ema(df['Close'], params['ema_trend_len'])
    df['atr']       = calcular_atr(df, params['atr_length'])
    df['vol_avg']   = df['Volume'].rolling(20).mean()

    # Nuevos Indicadores (4H ajustados)
    df['bb_upper'], df['bb_mid'], df['bb_lower'], df['bb_width'] = calcular_bollinger_bands(df['Close'], length=40)
    df['macd'], df['macd_signal'], df['macd_hist'] = calcular_macd(df['Close'], fast=24, slow=52, signal=18)
    df['obv'] = calcular_obv(df)
    df['adx'], df['di_plus'], df['di_minus'] = calcular_adx(df, length=28)
    df['obv_ema'] = calcular_ema(df['obv'], 20)

    # Velas
    df['body']        = (df['Close'] - df['Open']).abs()
    df['upper_wick']  = df['High'] - df[['Close','Open']].max(axis=1)
    df['lower_wick']  = df[['Close','Open']].min(axis=1) - df['Low']
    df['total_range'] = df['High'] - df['Low']
    df['is_bearish']  = df['Close'] < df['Open']
    df['is_bullish']  = df['Close'] > df['Open']

    # Última vela completa
    row  = df.iloc[-2]
    prev = df.iloc[-3]
    p2   = df.iloc[-4]

    close = row['Close']
    high  = row['High']
    low   = row['Low']
    open_ = row['Open']
    vol   = row['Volume']

    rsi       = row['rsi']
    rsi_prev  = prev['rsi']
    ema_fast  = row['ema_fast']
    ema_slow  = row['ema_slow']
    ema_trend = row['ema_trend']
    atr       = row['atr']
    vol_avg   = row['vol_avg']

    bb_upper  = row['bb_upper']
    bb_lower  = row['bb_lower']
    bb_mid    = row['bb_mid']
    bb_width  = row['bb_width']
    bb_width_prev = prev['bb_width']
    
    macd      = row['macd']
    macd_signal = row['macd_signal']
    macd_hist = row['macd_hist']
    macd_hist_prev = prev['macd_hist']
    
    obv       = row['obv']
    obv_prev  = prev['obv']
    obv_ema   = row['obv_ema']
    
    adx       = row['adx']
    di_plus   = row['di_plus']
    di_minus  = row['di_minus']

    body        = row['body']
    upper_wick  = row['upper_wick']
    lower_wick  = row['lower_wick']
    total_range = row['total_range']
    is_bearish  = row['is_bearish']
    is_bullish  = row['is_bullish']

    # Parámetros de zona (calculados automáticamente)
    zrl, zrh, zsl, zsh = calcular_zonas_sr(df, atr, params['sr_lookback'], params['sr_zone_mult'])
    tol  = round(atr * 0.4, 2)   # tolerancia dinámica: 40% del ATR
    lop  = params['limit_offset_pct']
    cd   = params['cancelar_dist']
    av   = params['anticipar_velas']
    vm   = params['vol_mult']
    rsms = params['rsi_min_sell']
    rsmb = params['rsi_max_buy']
    asm  = params['atr_sl_mult']
    print(f"  📍 Zonas auto — Resist: ${zrl:.1f}-${zrh:.1f} | Soporte: ${zsl:.1f}-${zsh:.1f}")

    sell_limit = zrl + (zrh - zrl) * (lop / 100 * 10)
    buy_limit  = zsh - (zsh - zsl) * (lop / 100 * 10)

    avg_candle_range = df['total_range'].iloc[-6:-1].mean()
    dist_to_resist   = zrl - close
    dist_to_support  = close - zsh

    aproximando_resistencia = (dist_to_resist > 0 and
                               dist_to_resist < avg_candle_range * av and
                               close > df['Close'].iloc[-5])

    aproximando_soporte     = (dist_to_support > 0 and
                               dist_to_support < avg_candle_range * av and
                               close < df['Close'].iloc[-5])

    en_zona_resist  = (high >= zrl - tol) and (high <= zrh + tol)
    en_zona_soporte = (low  >= zsl - tol) and (low  <= zsh + tol)

    cancelar_sell = close > zrh * (1 + cd / 100)
    cancelar_buy  = close < zsl * (1 - cd / 100)

    # BLOQUE VENTA
    intento_rotura_fallido = (high >= zrl) and (close < zrl)
    shooting_star     = is_bearish and upper_wick > body*2 and lower_wick < body*0.3 and en_zona_resist
    bearish_engulfing = is_bearish and open_ >= float(prev['High']) and close <= float(prev['Low']) and en_zona_resist
    bearish_marubozu  = is_bearish and body > total_range*0.8 and en_zona_resist
    doji_resist       = body < total_range*0.1 and en_zona_resist and upper_wick > body*2
    vela_rechazo      = shooting_star or bearish_engulfing or bearish_marubozu or doji_resist

    rsi_alto_girando = (rsi >= rsms) and (rsi < rsi_prev)
    rsi_sobrecompra  = rsi >= 70

    lookback = 5
    price_new_high      = high > float(df['High'].iloc[-lookback-2:-2].max())
    rsi_lower_high      = rsi  < float(df['rsi'].iloc[-lookback-2:-2].max())
    divergencia_bajista = price_new_high and rsi_lower_high and rsi > 50

    vol_alto_rechazo = vol > vol_avg * vm
    vol_decreciente  = (vol < float(prev['Volume'])) and (float(prev['Volume']) < float(p2['Volume'])) and is_bullish

    emas_bajistas      = ema_fast < ema_slow
    bajo_ema200        = close < ema_trend
    max_decreciente    = (high < float(prev['High'])) and (float(prev['High']) < float(p2['High']))
    min_decreciente    = (low  < float(prev['Low']))  and (float(prev['Low'])  < float(p2['Low']))
    estructura_bajista = max_decreciente or min_decreciente

    bb_toca_superior = close >= bb_upper or high >= bb_upper
    bb_squeeze = bb_width < 0.02
    
    macd_cruce_bajista = (macd < macd_signal) and (macd_hist < 0) and (macd_hist_prev >= 0)
    macd_divergencia_bajista = price_new_high and (macd < float(df['macd'].iloc[-lookback-2:-2].max()))
    macd_negativo = macd < 0
    
    adx_tendencia_fuerte = adx > 25
    adx_bajista = (di_minus > di_plus) and adx_tendencia_fuerte
    adx_lateral = adx < 20
    
    obv_divergencia_bajista = price_new_high and (obv < float(df['obv'].iloc[-lookback-2:-2].max()))
    obv_decreciente = obv < obv_prev and obv < obv_ema
    
    evening_star = detectar_evening_star(df, len(df) - 2)

    score_sell = 0
    score_sell += 2 if en_zona_resist          else 0
    score_sell += 2 if vela_rechazo            else 0
    score_sell += 2 if vol_alto_rechazo        else 0
    score_sell += 1 if rsi_alto_girando        else 0
    score_sell += 1 if rsi_sobrecompra         else 0
    score_sell += 1 if divergencia_bajista     else 0
    score_sell += 1 if emas_bajistas           else 0
    score_sell += 1 if estructura_bajista      else 0
    score_sell += 1 if intento_rotura_fallido  else 0
    score_sell += 1 if vol_decreciente         else 0
    score_sell += 1 if (shooting_star and vol_alto_rechazo)       else 0
    score_sell += 1 if (divergencia_bajista and rsi_sobrecompra)  else 0
    score_sell += 1 if bajo_ema200             else 0
    score_sell += 2 if bb_toca_superior        else 0
    score_sell += 2 if evening_star            else 0
    score_sell += 2 if macd_cruce_bajista      else 0
    score_sell += 2 if adx_bajista             else 0
    score_sell += 1 if macd_divergencia_bajista else 0
    score_sell += 1 if obv_divergencia_bajista else 0
    score_sell += 1 if obv_decreciente         else 0
    score_sell += 1 if macd_negativo           else 0
    
    if adx_lateral:
        score_sell = max(0, score_sell - 3)

    # BLOQUE COMPRA
    intento_caida_fallido = (low <= zsh) and (close > zsh)
    hammer            = is_bullish and lower_wick > body*2 and upper_wick < body*0.3 and en_zona_soporte
    bullish_engulfing = is_bullish and open_ <= float(prev['Low']) and close >= float(prev['High']) and en_zona_soporte
    bullish_marubozu  = is_bullish and body > total_range*0.8 and en_zona_soporte
    doji_soporte      = body < total_range*0.1 and en_zona_soporte and lower_wick > body*2
    vela_rebote       = hammer or bullish_engulfing or bullish_marubozu or doji_soporte

    rsi_bajo_girando = (rsi <= rsmb) and (rsi > rsi_prev)
    rsi_sobreventa   = rsi <= 30

    price_new_low       = low < float(df['Low'].iloc[-lookback-2:-2].min())
    rsi_higher_low      = rsi > float(df['rsi'].iloc[-lookback-2:-2].min())
    divergencia_alcista = price_new_low and rsi_higher_low and rsi < 50

    vol_alto_rebote      = vol > vol_avg * vm
    vol_decreciente_sell = (vol < float(prev['Volume'])) and (float(prev['Volume']) < float(p2['Volume'])) and is_bearish

    emas_alcistas      = ema_fast > ema_slow
    sobre_ema200       = close > ema_trend
    max_creciente      = (high > float(prev['High'])) and (float(prev['High']) > float(p2['High']))
    min_creciente      = (low  > float(prev['Low']))  and (float(prev['Low'])  > float(p2['Low']))
    estructura_alcista = max_creciente or min_creciente

    bb_toca_inferior = close <= bb_lower or low <= bb_lower
    
    macd_cruce_alcista = (macd > macd_signal) and (macd_hist > 0) and (macd_hist_prev <= 0)
    macd_divergencia_alcista = price_new_low and (macd > float(df['macd'].iloc[-lookback-2:-2].min()))
    macd_positivo = macd > 0
    
    adx_alcista = (di_plus > di_minus) and adx_tendencia_fuerte
    
    obv_divergencia_alcista = price_new_low and (obv > float(df['obv'].iloc[-lookback-2:-2].min()))
    obv_creciente = obv > obv_prev and obv > obv_ema
    
    morning_star = detectar_morning_star(df, len(df) - 2)

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
    score_buy += 1 if vol_decreciente_sell     else 0
    score_buy += 1 if (hammer and vol_alto_rebote)             else 0
    score_buy += 1 if (divergencia_alcista and rsi_sobreventa) else 0
    score_buy += 1 if sobre_ema200             else 0
    score_buy += 2 if bb_toca_inferior        else 0
    score_buy += 2 if morning_star            else 0
    score_buy += 2 if macd_cruce_alcista      else 0
    score_buy += 2 if adx_alcista             else 0
    score_buy += 1 if macd_divergencia_alcista else 0
    score_buy += 1 if obv_divergencia_alcista else 0
    score_buy += 1 if obv_creciente           else 0
    score_buy += 1 if macd_positivo           else 0
    
    if adx_lateral:
        score_buy = max(0, score_buy - 3)

    # ── Ajuste por sesgo DXY (correlación inversa Gold/USD) ──
    dxy_bias = get_dxy_bias()
    score_buy, score_sell = ajustar_score_por_dxy(score_buy, score_sell, dxy_bias)

    # NIVELES DE SEÑAL 4H (MÁS ESTRICTOS)
    senal_sell_maxima = score_sell >= 14
    senal_sell_fuerte = score_sell >= 12
    senal_sell_media  = score_sell >= 9
    senal_sell_alerta = score_sell >= 5
    senal_buy_maxima  = score_buy  >= 14
    senal_buy_fuerte  = score_buy  >= 12
    senal_buy_media   = score_buy  >= 9
    senal_buy_alerta  = score_buy  >= 5

    sl_venta  = round(sell_limit + atr * asm, 2)
    sl_compra = round(buy_limit  - atr * asm, 2)

    # TPs dinámicos basados en ATR (se adaptan automáticamente al rango de precio)
    tp1_v = round(sell_limit - atr * params['atr_tp1_mult'], 2)
    tp2_v = round(sell_limit - atr * params['atr_tp2_mult'], 2)
    tp3_v = round(sell_limit - atr * params['atr_tp3_mult'], 2)
    tp1_c = round(buy_limit  + atr * params['atr_tp1_mult'], 2)
    tp2_c = round(buy_limit  + atr * params['atr_tp2_mult'], 2)
    tp3_c = round(buy_limit  + atr * params['atr_tp3_mult'], 2)

    def rr(limit, sl, tp):
        return round(abs(tp - limit) / abs(sl - limit), 1) if abs(sl - limit) > 0 else 0

    # ── FILTRO R:R MÍNIMO 1.2 ──
    rr_sell_tp1 = rr(sell_limit, sl_venta, tp1_v)
    rr_buy_tp1  = rr(buy_limit,  sl_compra, tp1_c)
    if rr_sell_tp1 < 1.2:
        print(f"  ⛔ SELL bloqueada: R:R TP1={rr_sell_tp1} < 1.2")
    if rr_buy_tp1 < 1.2:
        print(f"  ⛔ BUY bloqueada: R:R TP1={rr_buy_tp1} < 1.2")

    fecha = df.index[-2].strftime('%Y-%m-%d %H:%M')
    
    # VERIFICAR SI YA SE ANALIZÓ ESTA VELA
    clave_simbolo = f"{simbolo}_4H"
    
    if clave_simbolo in ultimo_analisis:
        ultima_fecha = ultimo_analisis[clave_simbolo]['fecha']
        ultimo_score_sell = ultimo_analisis[clave_simbolo]['score_sell']
        ultimo_score_buy = ultimo_analisis[clave_simbolo]['score_buy']
        
        if (ultima_fecha == fecha and 
            abs(ultimo_score_sell - score_sell) <= 1 and 
            abs(ultimo_score_buy - score_buy) <= 1):
            print(f"  ℹ️  Vela {fecha} ya analizada - Sin cambios significativos")
            return
    
    ultimo_analisis[clave_simbolo] = {
        'fecha': fecha,
        'score_sell': score_sell,
        'score_buy': score_buy
    }
    
    print(f"  📅 Vela:  {fecha}")
    print(f"  💰 Close: {round(close, 2)}")
    print(f"  📊 Score SELL: {score_sell}/15 | Score BUY: {score_buy}/15")
    print(f"  🔴 SELL → Alerta:{senal_sell_alerta} Media:{senal_sell_media} Fuerte:{senal_sell_fuerte} Máxima:{senal_sell_maxima}")
    print(f"  🟢 BUY  → Alerta:{senal_buy_alerta}  Media:{senal_buy_media}  Fuerte:{senal_buy_fuerte}  Máxima:{senal_buy_maxima}")

    clave_vela = f"{simbolo}_4H_{fecha}"

    def ya_enviada(tipo):
        return alertas_enviadas.get(f"{clave_vela}_{tipo}", 0) > time.time() - 172800  # 48h TTL

    def marcar_enviada(tipo):
        alertas_enviadas[f"{clave_vela}_{tipo}"] = time.time()
        if len(alertas_enviadas) > 500:
            _c = time.time() - 172800
            for _k in [k for k in list(alertas_enviadas) if alertas_enviadas[k] < _c]:
                del alertas_enviadas[_k]

    # ── FILTRO PROXIMIDAD: solo operar cerca de zona ──
    cerca_resistencia = en_zona_resist or aproximando_resistencia
    cerca_soporte     = en_zona_soporte or aproximando_soporte
    if not cerca_resistencia:
        if senal_sell_alerta: print(f"  ⏳ SELL ignorada: precio lejos de resistencia")
        senal_sell_maxima = senal_sell_fuerte = senal_sell_media = senal_sell_alerta = False
    if not cerca_soporte:
        if senal_buy_alerta: print(f"  ⏳ BUY ignorada: precio lejos de soporte")
        senal_buy_maxima = senal_buy_fuerte = senal_buy_media = senal_buy_alerta = False

    # ── EXCLUSIÓN MUTUA: una sola dirección por vela ──
    if senal_sell_alerta and senal_buy_alerta:
        if score_sell >= score_buy:
            senal_buy_alerta = False
            print(f"  ⚖️ Exclusión mutua: BUY suprimida (SELL {score_sell} >= BUY {score_buy})")
        else:
            senal_sell_alerta = False
            print(f"  ⚖️ Exclusión mutua: SELL suprimida (BUY {score_buy} > SELL {score_sell})")

    # ── PUBLICAR + FILTRO CONFLUENCIA MULTI-TF (GOLD 4H) ──
    _sesgo_dir = tf_bias.BIAS_BEARISH if score_sell > score_buy else tf_bias.BIAS_BULLISH if score_buy > score_sell else tf_bias.BIAS_NEUTRAL
    tf_bias.publicar_sesgo(simbolo, '4H', _sesgo_dir, max(score_sell, score_buy))
    _conf_sell = ""; _conf_buy = ""
    if senal_sell_alerta:
        _ok, _desc = tf_bias.verificar_confluencia(simbolo, '4H', tf_bias.BIAS_BEARISH)
        if not _ok:
            print(f"  🚫 SELL bloqueada por TF superior: {_desc[:80]}")
            senal_sell_maxima = senal_sell_fuerte = senal_sell_media = senal_sell_alerta = False
        else:
            _conf_sell = _desc
    if senal_buy_alerta:
        _ok, _desc = tf_bias.verificar_confluencia(simbolo, '4H', tf_bias.BIAS_BULLISH)
        if not _ok:
            print(f"  🚫 BUY bloqueada por TF superior: {_desc[:80]}")
            senal_buy_maxima = senal_buy_fuerte = senal_buy_media = senal_buy_alerta = False
        else:
            _conf_buy = _desc

    simbolo_db = f"{simbolo}_4H"

    # ── SEÑAL ACCIONABLE (antes de entrar en zona — pon la orden limit ahora) ──
    if aproximando_resistencia and not en_zona_resist and not cancelar_sell and senal_sell_alerta and not ya_enviada('PREP_SELL'):
        nv = ("🔥 SELL MÁXIMA" if senal_sell_maxima else
              "🔴 SELL FUERTE" if senal_sell_fuerte else
              "⚡ SELL MEDIA"  if senal_sell_media  else
              "👀 SELL ALERTA")
        msg = (f"⚡ <b>SEÑAL SELL — ORO (XAUUSD) 4H</b> | PON ORDEN LIMIT AHORA\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>Nivel:</b> {nv}\n"
               f"💰 <b>Precio actual:</b> ${round(close, 2)}\n"
               f"📌 <b>SELL LIMIT:</b>   ${round(sell_limit, 2)}  ← pon la orden aquí\n"
               f"🛑 <b>Stop Loss:</b>    ${round(sl_venta, 2)}  (-${round(sl_venta - sell_limit, 2)})\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"🎯 <b>TP1:</b> ${tp1_v}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1  (+${round(sell_limit - tp1_v, 2)})\n"
               f"🎯 <b>TP2:</b> ${tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1  (+${round(sell_limit - tp2_v, 2)})\n"
               f"🎯 <b>TP3:</b> ${tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1  (+${round(sell_limit - tp3_v, 2)})\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
               f"⏱️ <b>TF:</b> 4H  📅 {fecha}  🔒 SWING")
        if db and not db.existe_senal_reciente(simbolo_db, "VENTA", horas=2):
            try:
                db.guardar_senal({
                    'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                    'direccion': 'VENTA', 'precio_entrada': sell_limit,
                    'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                    'score': score_sell,
                    'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 2),
                                               'macd': round(macd, 2), 'atr': round(atr, 2)}),
                    'patron_velas': f"Evening Star:{evening_star}, Shooting Star:{shooting_star}",
                    'version_detector': 'GOLD 4H-v2.0'
                })
            except Exception as e:
                print(f"  ⚠️ Error BD: {e}")
        enviar_telegram(msg); marcar_enviada('PREP_SELL')

    if aproximando_soporte and not en_zona_soporte and not cancelar_buy and senal_buy_alerta and not ya_enviada('PREP_BUY'):
        nv = ("🔥 BUY MÁXIMA"  if senal_buy_maxima else
              "🟢 BUY FUERTE"  if senal_buy_fuerte else
              "⚡ BUY MEDIA"   if senal_buy_media  else
              "👀 BUY ALERTA")
        msg = (f"⚡ <b>SEÑAL BUY — ORO (XAUUSD) 4H</b> | PON ORDEN LIMIT AHORA\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>Nivel:</b> {nv}\n"
               f"💰 <b>Precio actual:</b> ${round(close, 2)}\n"
               f"📌 <b>BUY LIMIT:</b>    ${round(buy_limit, 2)}  ← pon la orden aquí\n"
               f"🛑 <b>Stop Loss:</b>    ${round(sl_compra, 2)}  (-${round(buy_limit - sl_compra, 2)})\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"🎯 <b>TP1:</b> ${tp1_c}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1  (+${round(tp1_c - buy_limit, 2)})\n"
               f"🎯 <b>TP2:</b> ${tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1  (+${round(tp2_c - buy_limit, 2)})\n"
               f"🎯 <b>TP3:</b> ${tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1  (+${round(tp3_c - buy_limit, 2)})\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
               f"⏱️ <b>TF:</b> 4H  📅 {fecha}  🔒 SWING")
        if db and not db.existe_senal_reciente(simbolo_db, "COMPRA", horas=2):
            try:
                db.guardar_senal({
                    'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                    'direccion': 'COMPRA', 'precio_entrada': buy_limit,
                    'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                    'score': score_buy,
                    'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 2),
                                               'macd': round(macd, 2), 'atr': round(atr, 2)}),
                    'patron_velas': f"Morning Star:{morning_star}, Hammer:{hammer}",
                    'version_detector': 'GOLD 4H-v2.0'
                })
            except Exception as e:
                print(f"  ⚠️ Error BD: {e}")
        enviar_telegram(msg); marcar_enviada('PREP_BUY')

    # ── SEÑALES EN ZONA (confirmación si ya hubo señal accionable) ──
    if senal_sell_alerta and not cancelar_sell and rr_sell_tp1 >= 1.2:
        if ya_enviada('PREP_SELL') and not (senal_sell_fuerte or senal_sell_maxima):
            print(f"  ℹ️  SELL ALERTA/MEDIA ignorada: señal accionable ya enviada")
        else:
            nivel = ("🔥 SELL MÁXIMA (4H)" if senal_sell_maxima else
                     "🔴 SELL FUERTE (4H)" if senal_sell_fuerte else
                     "⚠️ SELL MEDIA (4H)"  if senal_sell_media  else
                     "👀 SELL ALERTA (4H)")
            tipo_clave = ("SELL_MAX" if senal_sell_maxima else
                          "SELL_FUE" if senal_sell_fuerte else
                          "SELL_MED" if senal_sell_media  else "SELL_ALE")
            if not ya_enviada(tipo_clave):
                if ya_enviada('PREP_SELL'):
                    msg = (f"✅ <b>CONFIRMACIÓN SELL — ORO 4H</b>\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"{nivel} — precio ahora en zona\n"
                           f"💰 <b>Precio:</b> ${round(close, 2)}\n"
                           f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                           f"⏱️ <b>TF:</b> 4H  📅 {fecha}")
                    enviar_telegram(msg); marcar_enviada(tipo_clave)
                else:
                    if db and db.existe_senal_reciente(simbolo_db, "VENTA", horas=2):
                        print(f"  ℹ️  Señal VENTA duplicada - No se guarda"); return
                    msg = (f"{nivel} — <b>ORO (XAUUSD) 🔒 SWING</b>\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"💰 <b>Precio:</b>     ${round(close, 2)}\n"
                           f"📌 <b>SELL LIMIT:</b> ${round(sell_limit, 2)}\n"
                           f"🛑 <b>Stop Loss:</b>  ${round(sl_venta, 2)}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"🎯 <b>TP1:</b> ${tp1_v}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                           f"🎯 <b>TP2:</b> ${tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                           f"🎯 <b>TP3:</b> ${tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                           f"⏱️ <b>TF:</b> 4H  📅 {fecha}")
                    if _conf_sell:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_sell}"
                    if db:
                        try:
                            db.guardar_senal({
                                'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                                'direccion': 'VENTA', 'precio_entrada': sell_limit,
                                'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                                'score': score_sell,
                                'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 2),
                                                           'macd': round(macd, 2), 'atr': round(atr, 2)}),
                                'patron_velas': f"Evening Star:{evening_star}, Shooting Star:{shooting_star}",
                                'version_detector': 'GOLD 4H-v2.0'
                            })
                        except Exception as e:
                            print(f"  ⚠️ Error guardando en BD: {e}")
                    enviar_telegram(msg); marcar_enviada(tipo_clave)

    if senal_buy_alerta and not cancelar_buy and rr_buy_tp1 >= 1.2:
        if ya_enviada('PREP_BUY') and not (senal_buy_fuerte or senal_buy_maxima):
            print(f"  ℹ️  BUY ALERTA/MEDIA ignorada: señal accionable ya enviada")
        else:
            nivel = ("🔥 BUY MÁXIMA (4H)"  if senal_buy_maxima else
                     "🟢 BUY FUERTE (4H)"  if senal_buy_fuerte else
                     "⚠️ BUY MEDIA (4H)"   if senal_buy_media  else
                     "👀 BUY ALERTA (4H)")
            tipo_clave = ("BUY_MAX" if senal_buy_maxima else
                          "BUY_FUE" if senal_buy_fuerte else
                          "BUY_MED" if senal_buy_media  else "BUY_ALE")
            if not ya_enviada(tipo_clave):
                if ya_enviada('PREP_BUY'):
                    msg = (f"✅ <b>CONFIRMACIÓN BUY — ORO 4H</b>\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"{nivel} — precio ahora en zona\n"
                           f"💰 <b>Precio:</b> ${round(close, 2)}\n"
                           f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                           f"⏱️ <b>TF:</b> 4H  📅 {fecha}")
                    enviar_telegram(msg); marcar_enviada(tipo_clave)
                else:
                    if db and db.existe_senal_reciente(simbolo_db, "COMPRA", horas=2):
                        print(f"  ℹ️  Señal COMPRA duplicada - No se guarda"); return
                    msg = (f"{nivel} — <b>ORO (XAUUSD) 🔒 SWING</b>\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"💰 <b>Precio:</b>    ${round(close, 2)}\n"
                           f"📌 <b>BUY LIMIT:</b> ${round(buy_limit, 2)}\n"
                           f"🛑 <b>Stop Loss:</b> ${round(sl_compra, 2)}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"🎯 <b>TP1:</b> ${tp1_c}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                           f"🎯 <b>TP2:</b> ${tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                           f"🎯 <b>TP3:</b> ${tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                           f"⏱️ <b>TF:</b> 4H  📅 {fecha}")
                    if _conf_buy:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_buy}"
                    if db:
                        try:
                            db.guardar_senal({
                                'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                                'direccion': 'COMPRA', 'precio_entrada': buy_limit,
                                'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                                'score': score_buy,
                                'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 2),
                                                           'macd': round(macd, 2), 'atr': round(atr, 2)}),
                                'patron_velas': f"Morning Star:{morning_star}, Hammer:{hammer}",
                                'version_detector': 'GOLD 4H-v2.0'
                            })
                        except Exception as e:
                            print(f"  ⚠️ Error guardando en BD: {e}")
                    enviar_telegram(msg); marcar_enviada(tipo_clave)

# ══════════════════════════════════════
# BUCLE PRINCIPAL
# ══════════════════════════════════════
def main():
    print("🚀 Detector GOLD 4H iniciado")
    print(f"⏱️  Revisando cada {CHECK_INTERVAL//60} minutos")

    enviar_telegram("🚀 <b>Detector GOLD 4H iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "📊 Monitorizando: XAUUSD (Gold)\n"
                    "⏱️ Timeframe: 4H\n"
                    "🔄 Revisión cada 7 minutos\n"
                    "💚 Filtros más estrictos que 1D\n"
                    "✅ Score mínimo: 5 (alerta), 9 (media), 12 (fuerte), 14 (máxima)\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔴 Resistencia: $4,750 - $4,900\n"
                    f"🟢 Soporte:     $4,200 - $4,400")

    ciclo = 0
    while True:
        ciclo += 1
        ahora_utc = datetime.now(timezone.utc)

        # ── Fin de semana: los mercados de futuros cierran → no analizar ──
        if ahora_utc.weekday() >= 5:  # 5=Sábado, 6=Domingo
            from datetime import timedelta
            dias_hasta_lunes = 7 - ahora_utc.weekday()
            proximo_lunes = (ahora_utc + timedelta(days=dias_hasta_lunes)).replace(hour=0, minute=0, second=0, microsecond=0)
            segundos_espera = min((proximo_lunes - ahora_utc).total_seconds(), 3600)
            print(f"[{ahora_utc.strftime('%Y-%m-%d %H:%M')} UTC] 💤 Fin de semana — mercado cerrado. Revisando en {int(segundos_espera//60)} min...")
            time.sleep(segundos_espera)
            continue

        ahora = ahora_utc.strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n{'='*60}")
        print(f"[{ahora}] 🔄 CICLO #{ciclo} - Iniciando análisis GOLD 4H")
        print(f"{'='*60}")
        
        for simbolo, params in SIMBOLOS.items():
            print(f"\n📊 Analizando {simbolo} [4H]...")
            analizar(simbolo, params)
        
        print(f"\n{'='*60}")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Ciclo #{ciclo} completado")
        print(f"⏳ Esperando {CHECK_INTERVAL//60} minutos hasta el próximo análisis...")
        print(f"{'='*60}\n")
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()
