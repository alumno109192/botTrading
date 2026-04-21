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

# Inicializar base de datos
db = None
try:
    turso_url = os.environ.get('TURSO_DATABASE_URL')
    turso_token = os.environ.get('TURSO_AUTH_TOKEN')
    if turso_url and turso_token:
        from adapters.database import DatabaseManager
        db = DatabaseManager()
        print("✅ [1D] Sistema de tracking de BD activado")
    else:
        print("⚠️  [1D] Variables Turso no configuradas - sin tracking BD")
except Exception as e:
    print(f"⚠️  [1D] No se pudo inicializar BD: {e}")
    db = None

# ══════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════
TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_THREAD_ID = int(os.environ.get('THREAD_ID_SWING') or 0) or None

CHECK_INTERVAL = 10 * 60  # cada 10 minutos (balance óptimo para timeframe 1D)
                          # mantiene el servidor activo

# ══════════════════════════════════════
# PARÁMETROS — igual que Pine Script
# ══════════════════════════════════════
SIMBOLOS = {
    'XAUUSD': {
        'ticker_yf':          'GC=F',       # Oro en Yahoo Finance
        # Zonas S/R calculadas automáticamente en analizar() — sin mantenimiento manual
        'sr_lookback':        30,           # 30 velas 1D ≈ 6 semanas de historia
        'sr_zone_mult':       0.8,          # ancho de zona = atr × 0.8
        'limit_offset_pct':   0.3,
        'anticipar_velas':    3,
        'cancelar_dist':      1.0,
        'rsi_length':         14,
        'rsi_min_sell':       55.0,
        'rsi_max_buy':        45.0,
        'ema_fast_len':       9,
        'ema_slow_len':       21,
        'ema_trend_len':      200,
        'atr_length':         14,
        'atr_sl_mult':        1.5,
        'atr_tp1_mult':       3.0,    # TP1: 3.0× ATR (~$150-240 desde entry)
        'atr_tp2_mult':       5.0,    # TP2: 5.0× ATR
        'atr_tp3_mult':       8.0,    # TP3: 8.0× ATR (objetivo swing amplio)
        'vol_mult':           1.2,
    }
}

# ══════════════════════════════════════
# CONTROL DE ALERTAS YA ENVIADAS
# (evita spam en la misma vela)
# ══════════════════════════════════════
alertas_enviadas = {}
ultimo_analisis = {}  # Guarda última fecha y scores analizados


# ══════════════════════════════════════
from core.indicators import (
    calcular_rsi, calcular_ema, calcular_atr,
    calcular_bollinger_bands, calcular_macd, calcular_obv, calcular_adx,
    detectar_evening_star, detectar_morning_star,
)

# ══════════════════════════════════════
# ANÁLISIS DE SENTIMIENTO DEL MERCADO
# (Mejora calidad - solo señales con confluencia)
# ══════════════════════════════════════
def calcular_sentimiento_bajista(row, prev, p2, df, params):
    """
    Calcula el score de sentimiento bajista (0-10)
    Returns: (score, factores_detectados)
    """
    factores = []
    score = 0
    
    close = row['Close']
    high = row['High']
    rsi = row['rsi']
    ema_fast = row['ema_fast']
    ema_slow = row['ema_slow']
    ema_trend = row['ema_trend']
    
    # 1. Estructura bajista (2 puntos)
    max_decreciente = (high < float(prev['High'])) and (float(prev['High']) < float(p2['High']))
    min_decreciente = (row['Low'] < float(prev['Low'])) and (float(prev['Low']) < float(p2['Low']))
    if max_decreciente or min_decreciente:
        factores.append("Estructura bajista")
        score += 2
    
    # 2. EMAs bajistas (1 punto cada una)
    if ema_fast < ema_slow:
        factores.append("EMAs bajistas (9<21)")
        score += 1
    if close < ema_trend:
        factores.append("Precio bajo EMA200")
        score += 1
    
    # 3. RSI en zona alta (1 punto)
    if rsi > params['rsi_min_sell']:
        factores.append(f"RSI alto ({rsi:.1f})")
        score += 1
    
    # 4. En zona de resistencia (2 puntos)
    zrh = float(df['High'].iloc[-31:-1].max())
    zrl = zrh - (df['High'] - df['Low']).iloc[-14:-1].mean()
    tol_s = (df['High'] - df['Low']).iloc[-14:-1].mean() * 0.4
    if (high >= zrl - tol_s) and (high <= zrh + tol_s):
        factores.append("En zona resistencia")
        score += 2
    
    # 5. Divergencia bajista (2 puntos)
    try:
        lookback = 5
        if len(df) >= lookback + 3:
            price_new_high = high > float(df['High'].iloc[-lookback-2:-2].max())
            rsi_lower_high = rsi < float(df['rsi'].iloc[-lookback-2:-2].max())
            if price_new_high and rsi_lower_high and rsi > 50:
                factores.append("Divergencia bajista")
                score += 2
    except:
        pass
    
    # 6. Tendencia de largo plazo bajista (1 punto)
    if ema_trend < df['Close'].iloc[-20:].mean():
        factores.append("Tendencia LT bajista")
        score += 1
    
    return score, factores

def calcular_sentimiento_alcista(row, prev, p2, df, params):
    """
    Calcula el score de sentimiento alcista (0-10)
    Returns: (score, factores_detectados)
    """
    factores = []
    score = 0
    
    close = row['Close']
    low = row['Low']
    rsi = row['rsi']
    ema_fast = row['ema_fast']
    ema_slow = row['ema_slow']
    ema_trend = row['ema_trend']
    
    # 1. Estructura alcista (2 puntos)
    max_creciente = (row['High'] > float(prev['High'])) and (float(prev['High']) > float(p2['High']))
    min_creciente = (low > float(prev['Low'])) and (float(prev['Low']) > float(p2['Low']))
    if max_creciente or min_creciente:
        factores.append("Estructura alcista")
        score += 2
    
    # 2. EMAs alcistas (1 punto cada una)
    if ema_fast > ema_slow:
        factores.append("EMAs alcistas (9>21)")
        score += 1
    if close > ema_trend:
        factores.append("Precio sobre EMA200")
        score += 1
    
    # 3. RSI en zona baja (1 punto)
    if rsi < params['rsi_max_buy']:
        factores.append(f"RSI bajo ({rsi:.1f})")
        score += 1
    
    # 4. En zona de soporte (2 puntos) — dinámica (mirrors calcular_zonas_sr logic)
    zsl = float(df['Low'].iloc[-31:-1].min())
    zsh = zsl + (df['High'] - df['Low']).iloc[-14:-1].mean()
    tol = (df['High'] - df['Low']).iloc[-14:-1].mean() * 0.4
    if (low >= zsl - tol) and (low <= zsh + tol):
        factores.append("En zona soporte")
        score += 2
    
    # 5. Divergencia alcista (2 puntos)
    try:
        lookback = 5
        if len(df) >= lookback + 3:
            price_new_low = low < float(df['Low'].iloc[-lookback-2:-2].min())
            rsi_higher_low = rsi > float(df['rsi'].iloc[-lookback-2:-2].min())
            if price_new_low and rsi_higher_low and rsi < 50:
                factores.append("Divergencia alcista")
                score += 2
    except:
        pass
    
    # 6. Tendencia de largo plazo alcista (1 punto)
    if ema_trend > df['Close'].iloc[-20:].mean():
        factores.append("Tendencia LT alcista")
        score += 1
    
    return score, factores

# ══════════════════════════════════════
# LÓGICA PRINCIPAL — replica Pine Script
# ══════════════════════════════════════
def calcular_zonas_sr(df, atr, lookback, zone_mult):
    """
    Detecta zonas S/R usando swing highs/lows locales (no solo máx/mín absoluto).
    Principio: soporte roto = nueva resistencia; resistencia rota = nuevo soporte.
    Selecciona el pivote más cercano al precio en cada dirección.

    Returns: (zrl, zrh, zsl, zsh)
        zrl/zrh = límites bajo/alto de la zona de resistencia
        zsl/zsh = límites bajo/alto de la zona de soporte
    """
    close      = float(df['Close'].iloc[-1])
    zone_width = atr * zone_mult
    wing       = 3
    highs = df['High'].iloc[-lookback-1:-1]
    lows  = df['Low'].iloc[-lookback-1:-1]

    swing_highs = []
    for i in range(wing, len(highs) - wing):
        val = float(highs.iloc[i])
        if all(val >= float(highs.iloc[i-j]) for j in range(1, wing+1)) and \
           all(val >= float(highs.iloc[i+j]) for j in range(1, wing+1)):
            swing_highs.append(val)

    swing_lows = []
    for i in range(wing, len(lows) - wing):
        val = float(lows.iloc[i])
        if all(val <= float(lows.iloc[i-j]) for j in range(1, wing+1)) and \
           all(val <= float(lows.iloc[i+j]) for j in range(1, wing+1)):
            swing_lows.append(val)

    if not swing_highs: swing_highs = [float(highs.max())]
    if not swing_lows:  swing_lows  = [float(lows.min())]

    min_dist = atr * 0.3
    candidatos_resist = [v for v in set(swing_highs + swing_lows) if v > close + min_dist]
    candidatos_sop    = [v for v in set(swing_lows + swing_highs) if v < close - min_dist]

    resist_pivot  = min(candidatos_resist) if candidatos_resist else float(highs.max())
    support_pivot = max(candidatos_sop)    if candidatos_sop    else float(lows.min())

    # Resistencia: zona centrada en el swing high (más banda bajo el pivot, donde venden)
    zrh = round(resist_pivot + zone_width * 0.25, 2)
    zrl = round(resist_pivot - zone_width * 0.75, 2)
    # Soporte: zona centrada en el swing low (más banda sobre el pivot, donde compran)
    zsh = round(support_pivot + zone_width * 0.75, 2)
    zsl = round(support_pivot - zone_width * 0.25, 2)
    return zrl, zrh, zsl, zsh


def analizar(simbolo, params):
    print(f"\n🔍 Analizando {simbolo}...")

    # ── Filtro calendario económico ──
    bloqueado, descripcion = hay_evento_impacto(ventana_minutos=60)
    if bloqueado:
        print(f"  🚫 [1D] Señal bloqueada por evento macro: {descripcion}")
        return

    # ── Descargar datos ──
    try:
        df, is_delayed = get_ohlcv(params['ticker_yf'], period='2y', interval='1d')
        if is_delayed:
            print("  ⚠️  [1D] Datos con delay (yfinance). Configura TWELVE_DATA_API_KEY para tiempo real.")
        if df.empty or len(df) < 210:
            print(f"⚠️ Datos insuficientes para {simbolo}")
            return
    except Exception as e:
        print(f"❌ Error descargando {simbolo}: {e}")
        return

    # Limpiar columnas multi-index si existen
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.copy()

    # ── Indicadores base ──
    df['rsi']       = calcular_rsi(df['Close'], params['rsi_length'])
    df['ema_fast']  = calcular_ema(df['Close'], params['ema_fast_len'])
    df['ema_slow']  = calcular_ema(df['Close'], params['ema_slow_len'])
    df['ema_trend'] = calcular_ema(df['Close'], params['ema_trend_len'])
    df['atr']       = calcular_atr(df, params['atr_length'])
    df['vol_avg']   = df['Volume'].rolling(20).mean()

    # ── Nuevos Indicadores (Alta Prioridad) ──
    df['bb_upper'], df['bb_mid'], df['bb_lower'], df['bb_width'] = calcular_bollinger_bands(df['Close'])
    df['macd'], df['macd_signal'], df['macd_hist'] = calcular_macd(df['Close'])
    df['obv'] = calcular_obv(df)
    df['adx'], df['di_plus'], df['di_minus'] = calcular_adx(df)
    
    # OBV promedio para divergencias
    df['obv_ema'] = calcular_ema(df['obv'], 20)

    # ── Velas ──
    df['body']        = (df['Close'] - df['Open']).abs()
    df['upper_wick']  = df['High'] - df[['Close','Open']].max(axis=1)
    df['lower_wick']  = df[['Close','Open']].min(axis=1) - df['Low']
    df['total_range'] = df['High'] - df['Low']
    df['is_bearish']  = df['Close'] < df['Open']
    df['is_bullish']  = df['Close'] > df['Open']

    # Última vela completa (la de ayer, no la actual)
    row  = df.iloc[-2]   # vela cerrada
    prev = df.iloc[-3]   # vela anterior
    p2   = df.iloc[-4]   # dos velas atrás

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

    # Nuevos indicadores
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

    # ── Parámetros de zona (calculados automáticamente) ──
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

    # ── Precios límite ──
    sell_limit = zrl + (zrh - zrl) * (lop / 100 * 10)
    buy_limit  = zsh - (zsh - zsl) * (lop / 100 * 10)

    # ── Proximidad a zonas ──
    avg_candle_range = df['total_range'].iloc[-6:-1].mean()
    dist_to_resist   = zrl - close
    dist_to_support  = close - zsh

    aproximando_resistencia = (dist_to_resist > 0 and
                               dist_to_resist < avg_candle_range * av and
                               close > df['Close'].iloc[-5])

    aproximando_soporte     = (dist_to_support > 0 and
                               dist_to_support < avg_candle_range * av and
                               close < df['Close'].iloc[-5])

    # ── Zonas alcanzadas ──
    en_zona_resist  = (high >= zrl - tol) and (high <= zrh + tol)
    en_zona_soporte = (low  >= zsl - tol) and (low  <= zsh + tol)

    # ── Cancelación ──
    cancelar_sell = close > zrh * (1 + cd / 100)
    cancelar_buy  = close < zsl * (1 - cd / 100)

    # ══════════════════════════════════
    # BLOQUE VENTA
    # ══════════════════════════════════
    intento_rotura_fallido = (high >= zrl) and (close < zrl)
    shooting_star     = is_bearish and upper_wick > body*2 and lower_wick < body*0.3 and en_zona_resist
    bearish_engulfing = is_bearish and open_ >= prev['High'] and close <= prev['Low'] and en_zona_resist
    bearish_marubozu  = is_bearish and body > total_range*0.8 and en_zona_resist
    doji_resist       = body < total_range*0.1 and en_zona_resist and upper_wick > body*2
    vela_rechazo      = shooting_star or bearish_engulfing or bearish_marubozu or doji_resist

    rsi_alto_girando = (rsi >= rsms) and (rsi < rsi_prev)
    rsi_sobrecompra  = rsi >= 70

    lookback = 5
    price_new_high      = high > df['High'].iloc[-lookback-2:-2].max()
    rsi_lower_high      = rsi  < df['rsi'].iloc[-lookback-2:-2].max()
    divergencia_bajista = price_new_high and rsi_lower_high and rsi > 50

    vol_alto_rechazo = vol > vol_avg * vm
    vol_decreciente  = (vol < prev['Volume']) and (prev['Volume'] < p2['Volume']) and is_bullish

    emas_bajistas      = ema_fast < ema_slow
    bajo_ema200        = close < ema_trend
    max_decreciente    = (high < prev['High']) and (prev['High'] < p2['High'])
    min_decreciente    = (low  < prev['Low'])  and (prev['Low']  < p2['Low'])
    estructura_bajista = max_decreciente or min_decreciente

    # ── Nuevas señales VENTA (indicadores alta prioridad) ──
    # Bollinger Bands
    bb_toca_superior = close >= bb_upper or high >= bb_upper
    bb_squeeze = bb_width < 0.02  # Squeeze detectado (próxima explosión)
    
    # MACD
    macd_cruce_bajista = (macd < macd_signal) and (macd_hist < 0) and (macd_hist_prev >= 0)
    macd_divergencia_bajista = price_new_high and (macd < df['macd'].iloc[-lookback-2:-2].max())
    macd_negativo = macd < 0
    
    # ADX
    adx_tendencia_fuerte = adx > 25  # Tendencia confirmada
    adx_bajista = (di_minus > di_plus) and adx_tendencia_fuerte
    adx_lateral = adx < 20  # Evitar señales en lateral
    
    # OBV
    obv_divergencia_bajista = price_new_high and (obv < df['obv'].iloc[-lookback-2:-2].max())
    obv_decreciente = obv < obv_prev and obv < obv_ema
    
    # Evening Star
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
    
    # Nuevos puntos (indicadores alta prioridad)
    score_sell += 2 if bb_toca_superior        else 0  # Bollinger superior
    score_sell += 2 if evening_star            else 0  # Patrón Evening Star
    score_sell += 2 if macd_cruce_bajista      else 0  # MACD cruce
    score_sell += 2 if adx_bajista             else 0  # ADX confirma tendencia bajista
    score_sell += 1 if macd_divergencia_bajista else 0  # MACD divergencia
    score_sell += 1 if obv_divergencia_bajista else 0  # OBV divergencia
    score_sell += 1 if obv_decreciente         else 0  # OBV cayendo
    score_sell += 1 if macd_negativo           else 0  # MACD bajo cero
    
    # Penalización si mercado lateral (ADX bajo)
    if adx_lateral:
        score_sell = max(0, score_sell - 3)  # Reducir score en mercados laterales

    # ══════════════════════════════════
    # BLOQUE COMPRA
    # ══════════════════════════════════
    intento_caida_fallido = (low <= zsh) and (close > zsh)
    hammer            = is_bullish and lower_wick > body*2 and upper_wick < body*0.3 and en_zona_soporte
    bullish_engulfing = is_bullish and open_ <= prev['Low'] and close >= prev['High'] and en_zona_soporte
    bullish_marubozu  = is_bullish and body > total_range*0.8 and en_zona_soporte
    doji_soporte      = body < total_range*0.1 and en_zona_soporte and lower_wick > body*2
    vela_rebote       = hammer or bullish_engulfing or bullish_marubozu or doji_soporte

    rsi_bajo_girando = (rsi <= rsmb) and (rsi > rsi_prev)
    rsi_sobreventa   = rsi <= 30

    price_new_low       = low < df['Low'].iloc[-lookback-2:-2].min()
    rsi_higher_low      = rsi > df['rsi'].iloc[-lookback-2:-2].min()
    divergencia_alcista = price_new_low and rsi_higher_low and rsi < 50

    vol_alto_rebote      = vol > vol_avg * vm
    vol_decreciente_sell = (vol < prev['Volume']) and (prev['Volume'] < p2['Volume']) and is_bearish

    emas_alcistas      = ema_fast > ema_slow
    sobre_ema200       = close > ema_trend
    max_creciente      = (high > prev['High']) and (prev['High'] > p2['High'])
    min_creciente      = (low  > prev['Low'])  and (prev['Low']  > p2['Low'])
    estructura_alcista = max_creciente or min_creciente

    # ── Nuevas señales COMPRA (indicadores alta prioridad) ──
    # Bollinger Bands
    bb_toca_inferior = close <= bb_lower or low <= bb_lower
    
    # MACD
    macd_cruce_alcista = (macd > macd_signal) and (macd_hist > 0) and (macd_hist_prev <= 0)
    macd_divergencia_alcista = price_new_low and (macd > df['macd'].iloc[-lookback-2:-2].min())
    macd_positivo = macd > 0
    
    # ADX
    adx_alcista = (di_plus > di_minus) and adx_tendencia_fuerte
    
    # OBV
    obv_divergencia_alcista = price_new_low and (obv > df['obv'].iloc[-lookback-2:-2].min())
    obv_creciente = obv > obv_prev and obv > obv_ema
    
    # Morning Star
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
    score_buy += 1 if (hammer and vol_alto_rebote)              else 0
    score_buy += 1 if (divergencia_alcista and rsi_sobreventa)  else 0
    score_buy += 1 if sobre_ema200             else 0
    
    # Nuevos puntos (indicadores alta prioridad)
    score_buy += 2 if bb_toca_inferior        else 0  # Bollinger inferior
    score_buy += 2 if morning_star            else 0  # Patrón Morning Star
    score_buy += 2 if macd_cruce_alcista      else 0  # MACD cruce
    score_buy += 2 if adx_alcista             else 0  # ADX confirma tendencia alcista
    score_buy += 1 if macd_divergencia_alcista else 0  # MACD divergencia
    score_buy += 1 if obv_divergencia_alcista else 0  # OBV divergencia
    score_buy += 1 if obv_creciente           else 0  # OBV subiendo
    score_buy += 1 if macd_positivo           else 0  # MACD sobre cero
    
    # Penalización si mercado lateral (ADX bajo)
    if adx_lateral:
        score_buy = max(0, score_buy - 3)  # Reducir score en mercados laterales

    # ── Ajuste por sesgo DXY (correlación inversa Gold/USD) ──
    dxy_bias = get_dxy_bias()
    score_buy, score_sell = ajustar_score_por_dxy(score_buy, score_sell, dxy_bias)

    # ══════════════════════════════════
    # ANÁLISIS DE SENTIMIENTO DEL MERCADO
    # (Validación cruzada para señales de alta calidad)
    # ══════════════════════════════════
    sentimiento_bajista_score, factores_bajistas = calcular_sentimiento_bajista(row, prev, p2, df, params)
    sentimiento_alcista_score, factores_alcistas = calcular_sentimiento_alcista(row, prev, p2, df, params)
    
    # Determinar sentimiento dominante
    if sentimiento_bajista_score >= 6:
        sentimiento_general = "🔴 BAJISTA FUERTE"
        emoji_sentimiento = "🔴"
    elif sentimiento_alcista_score >= 6:
        sentimiento_general = "🟢 ALCISTA FUERTE"
        emoji_sentimiento = "🟢"
    elif sentimiento_bajista_score >= 4:
        sentimiento_general = "⚠️ BAJISTA MODERADO"
        emoji_sentimiento = "🟠"
    elif sentimiento_alcista_score >= 4:
        sentimiento_general = "⚠️ ALCISTA MODERADO"
        emoji_sentimiento = "🟡"
    else:
        sentimiento_general = "⚪ NEUTRAL/MIXTO"
        emoji_sentimiento = "⚪"
    
    # ══════════════════════════════════
    # SISTEMA DE CONFLUENCIA
    # Solo señales con confirmación del sentimiento
    # ══════════════════════════════════
    
    # Validar confluencia (señal más fiable si sentimiento la apoya)
    confluencia_sell = sentimiento_bajista_score >= 4 and score_sell >= 6
    confluencia_buy = sentimiento_alcista_score >= 4 and score_buy >= 6
    
    # Señal contradictoria (advertencia - calidad reducida)
    senal_contradictoria_sell = score_sell >= 6 and sentimiento_alcista_score > sentimiento_bajista_score
    senal_contradictoria_buy = score_buy >= 6 and sentimiento_bajista_score > sentimiento_alcista_score
    
    # ══════════════════════════════════
    # NIVELES DE SEÑAL CON FILTRO DE CALIDAD
    # ══════════════════════════════════
    
    # SELL - Solo con confluencia o muy alto score técnico
    senal_sell_maxima = score_sell >= 10 and sentimiento_bajista_score >= 6
    senal_sell_fuerte = score_sell >= 8 and sentimiento_bajista_score >= 4
    senal_sell_media  = score_sell >= 6 and sentimiento_bajista_score >= 3
    # ALERTA: Score técnico ≥6 normal, O sentimiento FUERTE (≥6) con score mínimo ≥3
    senal_sell_alerta = (score_sell >= 6 and not senal_contradictoria_sell) or (sentimiento_bajista_score >= 6 and score_sell >= 3)
    
    # BUY - Solo con confluencia o muy alto score técnico
    senal_buy_maxima  = score_buy >= 10 and sentimiento_alcista_score >= 6
    senal_buy_fuerte  = score_buy >= 8 and sentimiento_alcista_score >= 4
    senal_buy_media   = score_buy >= 6 and sentimiento_alcista_score >= 3
    # ALERTA: Score técnico ≥6 normal, O sentimiento FUERTE (≥6) con score mínimo ≥3
    senal_buy_alerta  = (score_buy >= 6 and not senal_contradictoria_buy) or (sentimiento_alcista_score >= 6 and score_buy >= 3)

    # ══════════════════════════════════
    # SL Y TP
    # ══════════════════════════════════
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

    # ══════════════════════════════════
    # LOG EN CONSOLA
    # ══════════════════════════════════
    fecha = df.index[-2].strftime('%Y-%m-%d')    
    # ══════════════════════════════════════
    # VERIFICAR SI YA SE ANALIZÓ ESTA VELA
    # ══════════════════════════════════════
    clave_simbolo = simbolo
    ya_analizado = False
    
    if clave_simbolo in ultimo_analisis:
        ultima_fecha = ultimo_analisis[clave_simbolo]['fecha']
        ultimo_score_sell = ultimo_analisis[clave_simbolo]['score_sell']
        ultimo_score_buy = ultimo_analisis[clave_simbolo]['score_buy']
        
        # Si es la misma fecha y scores similares, ya fue analizado
        if (ultima_fecha == fecha and 
            abs(ultimo_score_sell - score_sell) <= 1 and 
            abs(ultimo_score_buy - score_buy) <= 1):
            ya_analizado = True
            print(f"  ℹ️  Vela {fecha} ya analizada - Sin cambios significativos")
            return  # No enviar alertas repetidas
    
    # Actualizar último análisis
    ultimo_analisis[clave_simbolo] = {
        'fecha': fecha,
        'score_sell': score_sell,
        'score_buy': score_buy
    }
    
    print(f"  📅 Vela: {fecha}")
    print(f"  💰 Precio cierre: {round(close, 2)}")
    print(f"  📊 Score SELL: {score_sell}/21 | Score BUY: {score_buy}/21")
    print(f"  {emoji_sentimiento} Sentimiento: {sentimiento_general} (Bajista:{sentimiento_bajista_score}/10 | Alcista:{sentimiento_alcista_score}/10)")
    print(f"  🔴 SELL → Alerta:{senal_sell_alerta} Media:{senal_sell_media} Fuerte:{senal_sell_fuerte} Máxima:{senal_sell_maxima}")
    print(f"  🟢 BUY  → Alerta:{senal_buy_alerta}  Media:{senal_buy_media}  Fuerte:{senal_buy_fuerte}  Máxima:{senal_buy_maxima}")
    if senal_contradictoria_sell:
        print(f"  ⚠️  ADVERTENCIA: Señal SELL contradice sentimiento alcista")
    if senal_contradictoria_buy:
        print(f"  ⚠️  ADVERTENCIA: Señal BUY contradice sentimiento bajista")
    # Informar cuando se activa por sentimiento dominante
    if senal_sell_alerta and score_sell < 6 and sentimiento_bajista_score >= 6:
        print(f"  📣 ALERTA SELL activada por SENTIMIENTO BAJISTA FUERTE ({sentimiento_bajista_score}/10) - Score técnico bajo ({score_sell}/21)")
    if senal_buy_alerta and score_buy < 6 and sentimiento_alcista_score >= 6:
        print(f"  📣 ALERTA BUY activada por SENTIMIENTO ALCISTA FUERTE ({sentimiento_alcista_score}/10) - Score técnico bajo ({score_buy}/21)")

    # ══════════════════════════════════
    # CONTROL ANTI-SPAM
    # No enviar la misma señal dos veces
    # en la misma vela
    # ══════════════════════════════════
    clave_vela = f"{simbolo}_{fecha}"

    def ya_enviada(tipo):
        return alertas_enviadas.get(f"{clave_vela}_{tipo}", 0) > time.time() - 172800  # 48h TTL

    def marcar_enviada(tipo):
        alertas_enviadas[f"{clave_vela}_{tipo}"] = time.time()
        if len(alertas_enviadas) > 500:
            _c = time.time() - 172800
            for _k in [k for k in list(alertas_enviadas) if alertas_enviadas[k] < _c]:
                del alertas_enviadas[_k]

    # ══════════════════════════════════
    # ENVIAR ALERTAS
    # ═══════════════════════════════���══

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

    # ── PUBLICAR SESGO MULTI-TF (GOLD 1D) ──
    _sesgo_dir = tf_bias.BIAS_BEARISH if score_sell > score_buy else tf_bias.BIAS_BULLISH if score_buy > score_sell else tf_bias.BIAS_NEUTRAL
    tf_bias.publicar_sesgo(simbolo, '1D', _sesgo_dir, max(score_sell, score_buy))
    print(f"  📡 Sesgo GOLD 1D publicado: {_sesgo_dir} (sell={score_sell} buy={score_buy})")

    # ── APROXIMACIÓN RESISTENCIA — SEÑAL ACCIONABLE (pon la orden limit ahora) ──
    if aproximando_resistencia and not en_zona_resist and not cancelar_sell and not senal_buy_alerta:
        if not ya_enviada('PREP_SELL'):
            nv = ("🔥 SELL MÁXIMA" if senal_sell_maxima else
                  "🔴 SELL FUERTE" if senal_sell_fuerte else
                  "⚡ SELL MEDIA"  if senal_sell_media  else
                  "👀 SELL ALERTA")
            msg = (f"⚡ <b>SEÑAL SELL — ORO 1D</b> | PON ORDEN LIMIT AHORA\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Nivel:</b> {nv}\n"
                   f"💰 <b>Precio actual:</b> {round(close, 2)}\n"
                   f"📌 <b>SELL LIMIT:</b>   {round(sell_limit, 2)}  ← pon la orden aquí\n"
                   f"🛑 <b>Stop Loss:</b>   {round(sl_venta, 2)}\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"🎯 <b>TP1:</b> {tp1_v}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                   f"🎯 <b>TP2:</b> {tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                   f"🎯 <b>TP3:</b> {tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                   f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
            simbolo_db = f"{simbolo}_1D"
            if db and not db.existe_senal_reciente(simbolo_db, "VENTA", horas=4):
                try:
                    db.guardar_senal({
                        'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                        'direccion': 'VENTA', 'precio_entrada': sell_limit,
                        'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                        'score': score_sell,
                        'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 1), 'atr': round(atr, 2)}),
                        'patron_velas': f"Evening Star:{evening_star}, Shooting Star:{shooting_star}",
                        'version_detector': 'GOLD 1D-v2.0'
                    })
                except Exception as e:
                    print(f"  ⚠️ Error BD: {e}")
            enviar_telegram(msg)
            marcar_enviada('PREP_SELL')

    # ── APROXIMACIÓN SOPORTE — SEÑAL ACCIONABLE (pon la orden limit ahora) ──
    if aproximando_soporte and not en_zona_soporte and not cancelar_buy and not senal_sell_alerta:
        if not ya_enviada('PREP_BUY'):
            nv = ("🔥 BUY MÁXIMA" if senal_buy_maxima else
                  "🟢 BUY FUERTE" if senal_buy_fuerte else
                  "⚡ BUY MEDIA"  if senal_buy_media  else
                  "👀 BUY ALERTA")
            msg = (f"⚡ <b>SEÑAL BUY — ORO 1D</b> | PON ORDEN LIMIT AHORA\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Nivel:</b> {nv}\n"
                   f"💰 <b>Precio actual:</b> {round(close, 2)}\n"
                   f"📌 <b>BUY LIMIT:</b>    {round(buy_limit, 2)}  ← pon la orden aquí\n"
                   f"🛑 <b>Stop Loss:</b>   {round(sl_compra, 2)}\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"🎯 <b>TP1:</b> {tp1_c}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                   f"🎯 <b>TP2:</b> {tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                   f"🎯 <b>TP3:</b> {tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                   f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
            simbolo_db = f"{simbolo}_1D"
            if db and not db.existe_senal_reciente(simbolo_db, "COMPRA", horas=4):
                try:
                    db.guardar_senal({
                        'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                        'direccion': 'COMPRA', 'precio_entrada': buy_limit,
                        'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                        'score': score_buy,
                        'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 1), 'atr': round(atr, 2)}),
                        'patron_velas': f"Morning Star:{morning_star}, Hammer:{hammer}",
                        'version_detector': 'GOLD 1D-v2.0'
                    })
                except Exception as e:
                    print(f"  ⚠️ Error BD: {e}")
            enviar_telegram(msg)
            marcar_enviada('PREP_BUY')

    # ── SEÑALES VENTA (en zona) — confirmación si ya se mandó pre-alerta ──
    if senal_sell_alerta and not cancelar_sell and rr_sell_tp1 >= 1.2:
        # Si ya avisamos antes de zona (PREP_SELL), solo confirmar si es FUERTE o MÁXIMA
        if ya_enviada('PREP_SELL') and not (senal_sell_fuerte or senal_sell_maxima):
            print(f"  ℹ️  SELL ALERTA/MEDIA ignorada: pre-alerta ya enviada")
        else:
            if senal_sell_maxima:
                nivel = "🔥 SELL MÁXIMA - CONFLUENCIA CONFIRMADA 🔥"
                calidad = "✅ ALTA CALIDAD"
            elif senal_sell_fuerte:
                nivel = "🔴 SELL FUERTE"
                calidad = "✅ BUENA CALIDAD" if confluencia_sell else "⚠️ CALIDAD MEDIA"
            elif senal_sell_media:
                nivel = "⚠️ SELL MEDIA"
                calidad = "⚠️ PRECAUCIÓN" if not senal_contradictoria_sell else "❌ SEÑAL DUDOSA"
            else:
                nivel = "👀 SELL ALERTA"
                calidad = "ℹ️ MONITOREAR"

            tipo_clave = ("SELL_MAX" if senal_sell_maxima else
                          "SELL_FUE" if senal_sell_fuerte else
                          "SELL_MED" if senal_sell_media  else
                          "SELL_ALE")

            simbolo_db = f"{simbolo}_1D"
            if not ya_enviada(tipo_clave):
                if db and db.existe_senal_reciente(simbolo_db, "VENTA", horas=4):
                    print(f"  ℹ️  Señal VENTA duplicada - No se guarda")
                    return
                # Si ya hay pre-alerta → confirmar brevemente sin guardar otra vez en BD
                if ya_enviada('PREP_SELL'):
                    msg = (f"✅ <b>CONFIRMACIÓN SELL — ORO 1D</b>\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"{nivel} — precio ahora en zona\n"
                           f"💰 <b>Precio:</b> {round(close, 2)}\n"
                           f"📊 <b>Score:</b> {score_sell}/21  {calidad}\n"
                           f"📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                           f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
                else:
                    msg = (f"{nivel}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"📈 <b>Símbolo:</b>    {simbolo}\n"
                           f"💰 <b>Precio:</b>     {round(close, 2)}\n"
                           f"📌 <b>SELL LIMIT:</b> {round(sell_limit, 2)}\n"
                           f"🛑 <b>Stop Loss:</b>  {round(sl_venta, 2)}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"🎯 <b>TP1:</b> {tp1_v}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                           f"🎯 <b>TP2:</b> {tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                           f"🎯 <b>TP3:</b> {tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"📊 <b>Score técnico:</b> {score_sell}/21\n"
                           f"{emoji_sentimiento} <b>Sentimiento:</b> {sentimiento_general} ({sentimiento_bajista_score}/10)\n"
                           f"🎯 <b>Calidad:</b> {calidad}\n"
                           f"📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                           f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
                    if db:
                        try:
                            db.guardar_senal({
                                'timestamp': datetime.now(timezone.utc),
                                'simbolo': simbolo_db, 'direccion': 'VENTA',
                                'precio_entrada': sell_limit,
                                'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                                'score': score_sell,
                                'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 1),
                                                           'ema_fast': round(ema_fast, 2), 'atr': round(atr, 2)}),
                                'patron_velas': f"Evening Star:{evening_star}, Shooting Star:{shooting_star}",
                                'version_detector': 'GOLD 1D-v2.0'
                            })
                        except Exception as e:
                            print(f"  ⚠️ Error guardando en BD: {e}")
                enviar_telegram(msg)
                marcar_enviada(tipo_clave)

    # ── SEÑALES COMPRA (en zona) — confirmación si ya se mandó pre-alerta ──
    if senal_buy_alerta and not cancelar_buy and rr_buy_tp1 >= 1.2:
        if ya_enviada('PREP_BUY') and not (senal_buy_fuerte or senal_buy_maxima):
            print(f"  ℹ️  BUY ALERTA/MEDIA ignorada: pre-alerta ya enviada")
        else:
            if senal_buy_maxima:
                nivel = "🔥 BUY MÁXIMA - CONFLUENCIA CONFIRMADA 🔥"
                calidad = "✅ ALTA CALIDAD"
            elif senal_buy_fuerte:
                nivel = "🟢 BUY FUERTE"
                calidad = "✅ BUENA CALIDAD" if confluencia_buy else "⚠️ CALIDAD MEDIA"
            elif senal_buy_media:
                nivel = "⚠️ BUY MEDIA"
                calidad = "⚠️ PRECAUCIÓN" if not senal_contradictoria_buy else "❌ SEÑAL DUDOSA"
            else:
                nivel = "👀 BUY ALERTA"
                calidad = "ℹ️ MONITOREAR"

            tipo_clave = ("BUY_MAX" if senal_buy_maxima else
                          "BUY_FUE" if senal_buy_fuerte else
                          "BUY_MED" if senal_buy_media  else
                          "BUY_ALE")

            simbolo_db = f"{simbolo}_1D"
            if not ya_enviada(tipo_clave):
                if db and db.existe_senal_reciente(simbolo_db, "COMPRA", horas=4):
                    print(f"  ℹ️  Señal COMPRA duplicada - No se guarda")
                    return
                if ya_enviada('PREP_BUY'):
                    msg = (f"✅ <b>CONFIRMACIÓN BUY — ORO 1D</b>\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"{nivel} — precio ahora en zona\n"
                           f"💰 <b>Precio:</b> {round(close, 2)}\n"
                           f"📊 <b>Score:</b> {score_buy}/21  {calidad}\n"
                           f"📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                           f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
                else:
                    msg = (f"{nivel}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"📈 <b>Símbolo:</b>   {simbolo}\n"
                           f"💰 <b>Precio:</b>    {round(close, 2)}\n"
                           f"📌 <b>BUY LIMIT:</b> {round(buy_limit, 2)}\n"
                           f"🛑 <b>Stop Loss:</b> {round(sl_compra, 2)}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"🎯 <b>TP1:</b> {tp1_c}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                           f"🎯 <b>TP2:</b> {tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                           f"🎯 <b>TP3:</b> {tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"📊 <b>Score técnico:</b> {score_buy}/21\n"
                           f"{emoji_sentimiento} <b>Sentimiento:</b> {sentimiento_general} ({sentimiento_alcista_score}/10)\n"
                           f"🎯 <b>Calidad:</b> {calidad}\n"
                           f"📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                           f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
                    if db:
                        try:
                            db.guardar_senal({
                                'timestamp': datetime.now(timezone.utc),
                                'simbolo': simbolo_db, 'direccion': 'COMPRA',
                                'precio_entrada': buy_limit,
                                'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                                'score': score_buy,
                                'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 1),
                                                           'ema_fast': round(ema_fast, 2), 'atr': round(atr, 2)}),
                                'patron_velas': f"Morning Star:{morning_star}, Hammer:{hammer}",
                                'version_detector': 'GOLD 1D-v2.0'
                            })
                        except Exception as e:
                            print(f"  ⚠️ Error guardando en BD: {e}")
                enviar_telegram(msg)
                marcar_enviada(tipo_clave)

    # ── CANCELACIONES ──
    if cancelar_sell and not ya_enviada('CANCEL_SELL'):
        msg = (f"❌ <b>CANCELAR SELL LIMIT</b> ❌\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"📈 <b>Símbolo:</b> {simbolo}\n"
               f"💰 <b>Precio:</b>  {round(close, 2)}\n"
               f"⚠️ Precio rompió la resistencia\n"
               f"📅 {fecha}")
        enviar_telegram(msg)
        marcar_enviada('CANCEL_SELL')

    if cancelar_buy and not ya_enviada('CANCEL_BUY'):
        msg = (f"❌ <b>CANCELAR BUY LIMIT</b> ❌\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"📈 <b>Símbolo:</b> {simbolo}\n"
               f"💰 <b>Precio:</b>  {round(close, 2)}\n"
               f"⚠️ Precio rompió el soporte\n"
               f"📅 {fecha}")
        enviar_telegram(msg)
        marcar_enviada('CANCEL_BUY')

# ══════════════════════════════════════
# BUCLE PRINCIPAL
# ══════════════════════════════════════
def main():
    print("🚀 Detector de señales iniciado")
    print(f"⏱️  Revisando cada {CHECK_INTERVAL//60} minutos")
    print(f"📊 Símbolos: {list(SIMBOLOS.keys())}")

    # Enviar mensaje de arranque
    enviar_telegram("🚀 <b>Detector de señales iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "📊 Monitorizando: XAUUSD\n"
                    "⏱️ Timeframe: 1D\n"
                    "🔄 Revisión cada 10 minutos\n"
                    "🎯 Sistema con confluencia técnico + sentimiento\n"
                    "✅ Solo señales de ALTA CALIDAD\n"
                    "💎 Calidad > Cantidad")

    while True:
        # ── Fin de semana: los mercados de futuros cierran → no analizar ──
        ahora_utc = datetime.now(timezone.utc)
        if ahora_utc.weekday() == 5:  # 5=Sábado únicamente
            from datetime import timedelta
            proximo_domingo_18 = (ahora_utc + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
            segundos_espera = min((proximo_domingo_18 - ahora_utc).total_seconds(), 3600)
            print(f"[{ahora_utc.strftime('%Y-%m-%d %H:%M')} UTC] 💤 Sábado — mercado cerrado. Próxima apertura Domingo 18:00 UTC. Revisando en {int(segundos_espera//60)} min...")
            time.sleep(segundos_espera)
            continue

        for simbolo, params in SIMBOLOS.items():
            analizar(simbolo, params)
        print(f"\n⏳ Esperando {CHECK_INTERVAL//60} minutos...\n")
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()