import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import time
import json
from datetime import datetime
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# ══════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════
TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

CHECK_INTERVAL = 10 * 60  # cada 10 minutos (balance óptimo para timeframe 1D)
                          # mantiene el servidor activo

# ══════════════════════════════════════
# PARÁMETROS — igual que Pine Script
# ══════════════════════════════════════
SIMBOLOS = {
    'XAUUSD': {
        'ticker_yf':          'GC=F',       # Oro en Yahoo Finance
        'zona_resist_high':   4900.0,
        'zona_resist_low':    4750.0,
        'zona_soporte_high':  4400.0,
        'zona_soporte_low':   4200.0,
        'tp1_venta':          4627.0,
        'tp2_venta':          4374.0,
        'tp3_venta':          4099.0,
        'tp1_compra':         4900.0,
        'tp2_compra':         5100.0,
        'tp3_compra':         5300.0,
        'tolerancia':         30.0,
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
# TELEGRAM
# ══════════════════════════════════════
def enviar_telegram(mensaje):
    try:
        url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       mensaje,
            "parse_mode": "HTML"
        }
        r = requests.post(url, json=payload, timeout=10)
        print(f"✅ Telegram enviado → {r.status_code}")
    except Exception as e:
        print(f"❌ Error Telegram: {e}")

# ══════════════════════════════════════
# INDICADORES TÉCNICOS
# ══════════════════════════════════════
def calcular_rsi(series, length):
    delta  = series.diff()
    gain   = delta.clip(lower=0)
    loss   = -delta.clip(upper=0)
    avg_g  = gain.ewm(com=length - 1, min_periods=length).mean()
    avg_l  = loss.ewm(com=length - 1, min_periods=length).mean()
    rs     = avg_g / avg_l
    return 100 - (100 / (1 + rs))

def calcular_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calcular_atr(df, length):
    high, low, close_prev = df['High'], df['Low'], df['Close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low  - close_prev).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(com=length - 1, min_periods=length).mean()

# ══════════════════════════════════════
# NUEVOS INDICADORES — ALTA PRIORIDAD
# ══════════════════════════════════════

def calcular_bollinger_bands(series, length=20, std_dev=2):
    """
    Bandas de Bollinger
    Retorna: (bb_upper, bb_mid, bb_lower, bb_width)
    """
    bb_mid = series.rolling(window=length).mean()
    std = series.rolling(window=length).std()
    bb_upper = bb_mid + (std * std_dev)
    bb_lower = bb_mid - (std * std_dev)
    bb_width = (bb_upper - bb_lower) / bb_mid  # Ancho normalizado
    return bb_upper, bb_mid, bb_lower, bb_width

def calcular_macd(series, fast=12, slow=26, signal=9):
    """
    MACD (Moving Average Convergence Divergence)
    Retorna: (macd_line, signal_line, histogram)
    """
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calcular_obv(df):
    """
    On-Balance Volume
    Acumula volumen según dirección del precio
    """
    obv = pd.Series(index=df.index, dtype=float)
    obv.iloc[0] = df['Volume'].iloc[0]
    
    for i in range(1, len(df)):
        if df['Close'].iloc[i] > df['Close'].iloc[i-1]:
            obv.iloc[i] = obv.iloc[i-1] + df['Volume'].iloc[i]
        elif df['Close'].iloc[i] < df['Close'].iloc[i-1]:
            obv.iloc[i] = obv.iloc[i-1] - df['Volume'].iloc[i]
        else:
            obv.iloc[i] = obv.iloc[i-1]
    
    return obv

def calcular_adx(df, length=14):
    """
    ADX (Average Directional Index)
    Retorna: (adx, di_plus, di_minus)
    """
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    # True Range
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    
    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    
    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move
    
    # Smoothed TR y DM
    atr = tr.ewm(com=length - 1, min_periods=length).mean()
    plus_di = 100 * (plus_dm.ewm(com=length - 1, min_periods=length).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(com=length - 1, min_periods=length).mean() / atr)
    
    # ADX
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    adx = dx.ewm(com=length - 1, min_periods=length).mean()
    
    return adx, plus_di, minus_di

def detectar_evening_star(df, idx):
    """
    Evening Star: Patrón de reversión bajista (3 velas)
    Vela 1: Alcista grande
    Vela 2: Pequeña (indecisión) - gap al alza
    Vela 3: Bajista grande - cierra dentro de vela 1
    """
    if idx < 2:
        return False
    
    v1 = df.iloc[idx - 2]  # Primera vela
    v2 = df.iloc[idx - 1]  # Vela estrella
    v3 = df.iloc[idx]      # Última vela
    
    # Vela 1: Alcista con cuerpo grande
    v1_bullish = v1['Close'] > v1['Open']
    v1_body = abs(v1['Close'] - v1['Open'])
    v1_range = v1['High'] - v1['Low']
    v1_large_body = v1_body > v1_range * 0.6
    
    # Vela 2: Pequeña (doji o cuerpo pequeño)
    v2_body = abs(v2['Close'] - v2['Open'])
    v2_range = v2['High'] - v2['Low']
    v2_small = v2_body < v2_range * 0.3
    v2_gap_up = v2['Open'] > v1['Close']
    
    # Vela 3: Bajista con cuerpo grande
    v3_bearish = v3['Close'] < v3['Open']
    v3_body = abs(v3['Close'] - v3['Open'])
    v3_range = v3['High'] - v3['Low']
    v3_large_body = v3_body > v3_range * 0.6
    v3_closes_in_v1 = v3['Close'] < (v1['Open'] + v1['Close']) / 2
    
    return v1_bullish and v1_large_body and v2_small and v2_gap_up and v3_bearish and v3_large_body and v3_closes_in_v1

def detectar_morning_star(df, idx):
    """
    Morning Star: Patrón de reversión alcista (3 velas)
    Vela 1: Bajista grande
    Vela 2: Pequeña (indecisión) - gap a la baja
    Vela 3: Alcista grande - cierra dentro de vela 1
    """
    if idx < 2:
        return False
    
    v1 = df.iloc[idx - 2]  # Primera vela
    v2 = df.iloc[idx - 1]  # Vela estrella
    v3 = df.iloc[idx]      # Última vela
    
    # Vela 1: Bajista con cuerpo grande
    v1_bearish = v1['Close'] < v1['Open']
    v1_body = abs(v1['Close'] - v1['Open'])
    v1_range = v1['High'] - v1['Low']
    v1_large_body = v1_body > v1_range * 0.6
    
    # Vela 2: Pequeña (doji o cuerpo pequeño)
    v2_body = abs(v2['Close'] - v2['Open'])
    v2_range = v2['High'] - v2['Low']
    v2_small = v2_body < v2_range * 0.3
    v2_gap_down = v2['Open'] < v1['Close']
    
    # Vela 3: Alcista con cuerpo grande
    v3_bullish = v3['Close'] > v3['Open']
    v3_body = abs(v3['Close'] - v3['Open'])
    v3_range = v3['High'] - v3['Low']
    v3_large_body = v3_body > v3_range * 0.6
    v3_closes_in_v1 = v3['Close'] > (v1['Open'] + v1['Close']) / 2
    
    return v1_bearish and v1_large_body and v2_small and v2_gap_down and v3_bullish and v3_large_body and v3_closes_in_v1

# ══════════════════════════════════════
# LÓGICA PRINCIPAL — replica Pine Script
# ══════════════════════════════════════
def analizar(simbolo, params):
    print(f"\n🔍 Analizando {simbolo}...")

    # ── Descargar datos ──
    try:
        df = yf.download(params['ticker_yf'], period='2y', interval='1d', progress=False)
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

    # ── Parámetros de zona ──
    zrh  = params['zona_resist_high']
    zrl  = params['zona_resist_low']
    zsh  = params['zona_soporte_high']
    zsl  = params['zona_soporte_low']
    tol  = params['tolerancia']
    lop  = params['limit_offset_pct']
    cd   = params['cancelar_dist']
    av   = params['anticipar_velas']
    vm   = params['vol_mult']
    rsms = params['rsi_min_sell']
    rsmb = params['rsi_max_buy']
    asm  = params['atr_sl_mult']

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

    # ══════════════════════════════════
    # NIVELES DE SEÑAL
    # ══════════════════════════════════
    senal_sell_maxima = score_sell >= 10
    senal_sell_fuerte = score_sell >= 8
    senal_sell_media  = score_sell >= 6
    senal_sell_alerta = score_sell >= 4

    senal_buy_maxima  = score_buy  >= 10
    senal_buy_fuerte  = score_buy  >= 8
    senal_buy_media   = score_buy  >= 6
    senal_buy_alerta  = score_buy  >= 4

    # ══════════════════════════════════
    # SL Y TP
    # ══════════════════════════════════
    sl_venta  = max(zrh, close + atr * asm)
    sl_compra = min(zsl, close - atr * asm)

    tp1_v = params['tp1_venta']
    tp2_v = params['tp2_venta']
    tp3_v = params['tp3_venta']
    tp1_c = params['tp1_compra']
    tp2_c = params['tp2_compra']
    tp3_c = params['tp3_compra']

    def rr(limit, sl, tp):
        return round(abs(tp - limit) / abs(sl - limit), 1) if abs(sl - limit) > 0 else 0

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
    print(f"  📊 Score SELL: {score_sell}/15 | Score BUY: {score_buy}/15")
    print(f"  🔴 SELL → Alerta:{senal_sell_alerta} Media:{senal_sell_media} Fuerte:{senal_sell_fuerte} Máxima:{senal_sell_maxima}")
    print(f"  🟢 BUY  → Alerta:{senal_buy_alerta}  Media:{senal_buy_media}  Fuerte:{senal_buy_fuerte}  Máxima:{senal_buy_maxima}")

    # ══════════════════════════════════
    # CONTROL ANTI-SPAM
    # No enviar la misma señal dos veces
    # en la misma vela
    # ══════════════════════════════════
    clave_vela = f"{simbolo}_{fecha}"

    def ya_enviada(tipo):
        return alertas_enviadas.get(f"{clave_vela}_{tipo}", False)

    def marcar_enviada(tipo):
        alertas_enviadas[f"{clave_vela}_{tipo}"] = True

    # ══════════════════════════════════
    # ENVIAR ALERTAS
    # ═══════════════════════════════���══

    # ── APROXIMACIÓN RESISTENCIA ──
    if aproximando_resistencia and not en_zona_resist and not cancelar_sell:
        if not ya_enviada('PREP_SELL'):
            msg = (f"🔔 <b>PREPARAR SELL LIMIT</b> 🔔\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📢 Precio aproximándose a resistencia\n"
                   f"📈 <b>Símbolo:</b>  {simbolo}\n"
                   f"💰 <b>Precio:</b>   {round(close, 2)}\n"
                   f"📌 <b>SELL LIMIT:</b> {round(sell_limit, 2)}\n"
                   f"🛑 <b>Stop Loss:</b>  {round(sl_venta, 2)}\n"
                   f"🎯 <b>TP1:</b> {tp1_v}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                   f"🎯 <b>TP2:</b> {tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                   f"🎯 <b>TP3:</b> {tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b> {score_sell}/15\n"
                   f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
            enviar_telegram(msg)
            marcar_enviada('PREP_SELL')

    # ── APROXIMACIÓN SOPORTE ──
    if aproximando_soporte and not en_zona_soporte and not cancelar_buy:
        if not ya_enviada('PREP_BUY'):
            msg = (f"🔔 <b>PREPARAR BUY LIMIT</b> 🔔\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📢 Precio aproximándose a soporte\n"
                   f"📈 <b>Símbolo:</b>  {simbolo}\n"
                   f"💰 <b>Precio:</b>   {round(close, 2)}\n"
                   f"📌 <b>BUY LIMIT:</b>  {round(buy_limit, 2)}\n"
                   f"🛑 <b>Stop Loss:</b>  {round(sl_compra, 2)}\n"
                   f"🎯 <b>TP1:</b> {tp1_c}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                   f"🎯 <b>TP2:</b> {tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                   f"🎯 <b>TP3:</b> {tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b> {score_buy}/15\n"
                   f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
            enviar_telegram(msg)
            marcar_enviada('PREP_BUY')

    # ── SEÑALES VENTA ──
    if senal_sell_alerta and not cancelar_sell:
        nivel = ("⚡ SELL MÁXIMA" if senal_sell_maxima else
                 "🔴 SELL FUERTE" if senal_sell_fuerte else
                 "⚠️ SELL MEDIA"  if senal_sell_media  else
                 "👀 SELL ALERTA")
        tipo_clave = ("SELL_MAX" if senal_sell_maxima else
                      "SELL_FUE" if senal_sell_fuerte else
                      "SELL_MED" if senal_sell_media  else
                      "SELL_ALE")
        if not ya_enviada(tipo_clave):
            msg = (f"{nivel} {nivel.split()[0]}\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📈 <b>Símbolo:</b>    {simbolo}\n"
                   f"💰 <b>Precio:</b>     {round(close, 2)}\n"
                   f"📌 <b>SELL LIMIT:</b> {round(sell_limit, 2)}\n"
                   f"🛑 <b>Stop Loss:</b>  {round(sl_venta, 2)}\n"
                   f"🎯 <b>TP1:</b> {tp1_v}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                   f"🎯 <b>TP2:</b> {tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                   f"🎯 <b>TP3:</b> {tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b>      {score_sell}/15\n"
                   f"📉 <b>RSI:</b>        {round(rsi, 1)}\n"
                   f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
            enviar_telegram(msg)
            marcar_enviada(tipo_clave)

    # ── SEÑALES COMPRA ──
    if senal_buy_alerta and not cancelar_buy:
        nivel = ("⚡ BUY MÁXIMA"  if senal_buy_maxima else
                 "🟢 BUY FUERTE"  if senal_buy_fuerte else
                 "⚠️ BUY MEDIA"   if senal_buy_media  else
                 "👀 BUY ALERTA")
        tipo_clave = ("BUY_MAX" if senal_buy_maxima else
                      "BUY_FUE" if senal_buy_fuerte else
                      "BUY_MED" if senal_buy_media  else
                      "BUY_ALE")
        if not ya_enviada(tipo_clave):
            msg = (f"{nivel} {nivel.split()[0]}\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📈 <b>Símbolo:</b>   {simbolo}\n"
                   f"💰 <b>Precio:</b>    {round(close, 2)}\n"
                   f"📌 <b>BUY LIMIT:</b> {round(buy_limit, 2)}\n"
                   f"🛑 <b>Stop Loss:</b> {round(sl_compra, 2)}\n"
                   f"🎯 <b>TP1:</b> {tp1_c}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                   f"🎯 <b>TP2:</b> {tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                   f"🎯 <b>TP3:</b> {tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b>     {score_buy}/15\n"
                   f"📉 <b>RSI:</b>       {round(rsi, 1)}\n"
                   f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
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
                    "🔄 Revisión cada 14 minutos\n"
                    "💚 Mantiene el servidor activo\n"
                    "✅ Solo alertas en velas nuevas o cambios significativos")

    while True:
        for simbolo, params in SIMBOLOS.items():
            analizar(simbolo, params)
        print(f"\n⏳ Esperando {CHECK_INTERVAL//60} minutos...\n")
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()