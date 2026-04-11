import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import time
import json
from datetime import datetime, timezone
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Inicializar base de datos solo si las variables están configuradas
db = None
try:
    turso_url = os.environ.get('TURSO_DATABASE_URL')
    turso_token = os.environ.get('TURSO_AUTH_TOKEN')
    if turso_url and turso_token:
        from db_manager import DatabaseManager
        db = DatabaseManager()
        print("✅ Sistema de tracking de BD activado")
    else:
        print("⚠️  Variables Turso no configuradas - Sistema funcionará sin tracking de BD")
except Exception as e:
    print(f"⚠️  No se pudo inicializar BD: {e}")
    print("⚠️  Sistema funcionará sin tracking de BD")
    db = None

# ══════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════
TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_THREAD_ID = int(os.environ.get('THREAD_ID_SWING') or 0) or None

CHECK_INTERVAL = 10 * 60  # cada 10 minutos (balance óptimo para timeframe 1D)

# ══════════════════════════════════════
# PARÁMETROS — ESPECÍFICOS BITCOIN
# ══════════════════════════════════════
SIMBOLOS = {
    'BTCUSD': {
        'ticker_yf':          'BTC-USD',    # Bitcoin en Yahoo Finance
        'zona_resist_high':   88000.0,     # Resistencia overhead post-ATH
        'zona_resist_low':    80000.0,      # Resistencia inmediata
        'zona_soporte_high':  68000.0,      # Soporte actual — precio en zona
        'zona_soporte_low':   63000.0,      # Soporte fuerte — mínimo 1Y: 60k
        'tp1_venta':          72000.0,      # TP1 zona de precio actual
        'tp2_venta':          68000.0,
        'tp3_venta':          63000.0,
        'tp1_compra':         80000.0,
        'tp2_compra':         88000.0,
        'tp3_compra':         95000.0,
        'tolerancia':         2000.0,       # Alta tolerancia por volatilidad BTC
        'limit_offset_pct':   0.5,          # Mayor offset para entradas
        'anticipar_velas':    3,
        'cancelar_dist':      2.5,          # Mayor distancia por volatilidad
        'rsi_length':         14,
        'rsi_min_sell':       60.0,         # RSI más exigente (cripto volátil)
        'rsi_max_buy':        40.0,         # RSI más exigente
        'ema_fast_len':       9,
        'ema_slow_len':       21,
        'ema_trend_len':      200,
        'atr_length':         14,
        'atr_sl_mult':        2.5,          # SL más amplio (volatilidad BTC)
        'vol_mult':           1.5,          # Volumen muy importante en cripto
        'vol_min_mult':       0.5,          # Filtro obligatorio: vol < 50% del promedio bloquea señal
    }
}

# ══════════════════════════════════════
# CONTROL ANTI-SPAM
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
        if TELEGRAM_THREAD_ID:
            payload["message_thread_id"] = TELEGRAM_THREAD_ID
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"✅ Telegram enviado → {r.status_code}")
        else:
            print(f"❌ Error Telegram → Status {r.status_code}")
            print(f"   Respuesta: {r.text}")
            print(f"   Mensaje (primeros 200 chars): {mensaje[:200]}...")
    except Exception as e:
        print(f"❌ Error Telegram (excepción): {e}")

# ══════════════════════════════════════
# INDICADORES TÉCNICOS
# ══════════════════════════════════════
def calcular_rsi(series, length):
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(com=length - 1, min_periods=length).mean()
    avg_l = loss.ewm(com=length - 1, min_periods=length).mean()
    rs    = avg_g / avg_l
    return 100 - (100 / (1 + rs))

def calcular_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calcular_atr(df, length):
    high       = df['High']
    low        = df['Low']
    close_prev = df['Close'].shift(1)
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
    bb_width = (bb_upper - bb_lower) / bb_mid
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
    
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    
    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move
    
    atr = tr.ewm(com=length - 1, min_periods=length).mean()
    plus_di = 100 * (plus_dm.ewm(com=length - 1, min_periods=length).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(com=length - 1, min_periods=length).mean() / atr)
    
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    adx = dx.ewm(com=length - 1, min_periods=length).mean()
    
    return adx, plus_di, minus_di

def detectar_evening_star(df, idx):
    """
    Evening Star: Patrón de reversión bajista (3 velas)
    """
    if idx < 2:
        return False
    
    v1 = df.iloc[idx - 2]
    v2 = df.iloc[idx - 1]
    v3 = df.iloc[idx]
    
    v1_bullish = v1['Close'] > v1['Open']
    v1_body = abs(v1['Close'] - v1['Open'])
    v1_range = v1['High'] - v1['Low']
    v1_large_body = v1_body > v1_range * 0.6
    
    v2_body = abs(v2['Close'] - v2['Open'])
    v2_range = v2['High'] - v2['Low']
    v2_small = v2_body < v2_range * 0.3
    v2_gap_up = v2['Open'] > v1['Close']
    
    v3_bearish = v3['Close'] < v3['Open']
    v3_body = abs(v3['Close'] - v3['Open'])
    v3_range = v3['High'] - v3['Low']
    v3_large_body = v3_body > v3_range * 0.6
    v3_closes_in_v1 = v3['Close'] < (v1['Open'] + v1['Close']) / 2
    
    return v1_bullish and v1_large_body and v2_small and v2_gap_up and v3_bearish and v3_large_body and v3_closes_in_v1

def detectar_morning_star(df, idx):
    """
    Morning Star: Patrón de reversión alcista (3 velas)
    """
    if idx < 2:
        return False
    
    v1 = df.iloc[idx - 2]
    v2 = df.iloc[idx - 1]
    v3 = df.iloc[idx]
    
    v1_bearish = v1['Close'] < v1['Open']
    v1_body = abs(v1['Close'] - v1['Open'])
    v1_range = v1['High'] - v1['Low']
    v1_large_body = v1_body > v1_range * 0.6
    
    v2_body = abs(v2['Close'] - v2['Open'])
    v2_range = v2['High'] - v2['Low']
    v2_small = v2_body < v2_range * 0.3
    v2_gap_down = v2['Open'] < v1['Close']
    
    v3_bullish = v3['Close'] > v3['Open']
    v3_body = abs(v3['Close'] - v3['Open'])
    v3_range = v3['High'] - v3['Low']
    v3_large_body = v3_body > v3_range * 0.6
    v3_closes_in_v1 = v3['Close'] > (v1['Open'] + v1['Close']) / 2
    
    return v1_bearish and v1_large_body and v2_small and v2_gap_down and v3_bullish and v3_large_body and v3_closes_in_v1

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
    zrh = params['zona_resist_high']
    zrl = params['zona_resist_low']
    tol = params['tolerancia']
    if (high >= zrl - tol) and (high <= zrh + tol):
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
    
    # 4. En zona de soporte (2 puntos)
    zsh = params['zona_soporte_high']
    zsl = params['zona_soporte_low']
    tol = params['tolerancia']
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
# LÓGICA PRINCIPAL
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
    df['obv_ema'] = calcular_ema(df['obv'], 20)

    # ── Velas ──
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

    # ── Nuevas señales VENTA (indicadores alta prioridad) ──
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
    
    # Nuevos puntos (indicadores alta prioridad)
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

    # ══════════════════════════════════
    # BLOQUE COMPRA
    # ══════════════════════════════════
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

    # ── Nuevas señales COMPRA (indicadores alta prioridad) ──
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
    
    # Nuevos puntos (indicadores alta prioridad)
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
    # ALERTA: Score técnico ≥4 normal, O sentimiento FUERTE (≥6) con score mínimo ≥2
    senal_sell_alerta = (score_sell >= 4 and not senal_contradictoria_sell) or (sentimiento_bajista_score >= 6 and score_sell >= 2)
    
    # BUY - Solo con confluencia o muy alto score técnico
    senal_buy_maxima  = score_buy >= 10 and sentimiento_alcista_score >= 6
    senal_buy_fuerte  = score_buy >= 8 and sentimiento_alcista_score >= 4
    senal_buy_media   = score_buy >= 6 and sentimiento_alcista_score >= 3
    # ALERTA: Score técnico ≥4 normal, O sentimiento FUERTE (≥6) con score mínimo ≥2
    senal_buy_alerta  = (score_buy >= 4 and not senal_contradictoria_buy) or (sentimiento_alcista_score >= 6 and score_buy >= 2)

    # ── SL y TP ──
    sl_venta  = round(sell_limit + atr * asm, 2)
    sl_compra = round(buy_limit  - atr * asm, 2)

    tp1_v = params['tp1_venta']
    tp2_v = params['tp2_venta']
    tp3_v = params['tp3_venta']
    tp1_c = params['tp1_compra']
    tp2_c = params['tp2_compra']
    tp3_c = params['tp3_compra']

    def rr(limit, sl, tp):
        return round(abs(tp - limit) / abs(sl - limit), 1) if abs(sl - limit) > 0 else 0

    # ── FILTRO R:R MÍNIMO 1.5 ──
    rr_sell_tp1 = rr(sell_limit, sl_venta, tp1_v)
    rr_buy_tp1  = rr(buy_limit,  sl_compra, tp1_c)
    if rr_sell_tp1 < 1.5:
        print(f"  ⛔ SELL bloqueada: R:R TP1={rr_sell_tp1} < 1.5")
    if rr_buy_tp1 < 1.5:
        print(f"  ⛔ BUY bloqueada: R:R TP1={rr_buy_tp1} < 1.5")

    # ── Log consola ──
    fecha = df.index[-2].strftime('%Y-%m-%d')
    
    # ══════════════════════════════════════
    # VERIFICAR SI YA SE ANALIZÓ ESTA VELA
    # ══════════════════════════════════════
    clave_simbolo = simbolo
    
    if clave_simbolo in ultimo_analisis:
        ultima_fecha = ultimo_analisis[clave_simbolo]['fecha']
        ultimo_score_sell = int(ultimo_analisis[clave_simbolo]['score_sell'])
        ultimo_score_buy = int(ultimo_analisis[clave_simbolo]['score_buy'])
        
        # Si es la misma fecha y scores similares, ya fue analizado
        if (ultima_fecha == fecha and 
            abs(ultimo_score_sell - score_sell) <= 1 and 
            abs(ultimo_score_buy - score_buy) <= 1):
            print(f"  ℹ️  Vela {fecha} ya analizada - Sin cambios significativos")
            return  # No enviar alertas repetidas
    
    # Actualizar último análisis
    ultimo_analisis[clave_simbolo] = {
        'fecha': fecha,
        'score_sell': score_sell,
        'score_buy': score_buy
    }
    
    print(f"  📅 Vela:  {fecha}")
    print(f"  💰 Close: {round(close, 2)}")
    print(f"  📊 Score SELL: {score_sell}/21 | Score BUY: {score_buy}/21")
    print(f"  {emoji_sentimiento} Sentimiento: {sentimiento_general} (Bajista:{sentimiento_bajista_score}/10 | Alcista:{sentimiento_alcista_score}/10)")
    print(f"  🔴 SELL → Alerta:{senal_sell_alerta} Media:{senal_sell_media} Fuerte:{senal_sell_fuerte} Máxima:{senal_sell_maxima}")
    print(f"  🟢 BUY  → Alerta:{senal_buy_alerta}  Media:{senal_buy_media}  Fuerte:{senal_buy_fuerte}  Máxima:{senal_buy_maxima}")
    if senal_contradictoria_sell:
        print(f"  ⚠️  ADVERTENCIA: Señal SELL contradice sentimiento alcista")
    if senal_contradictoria_buy:
        print(f"  ⚠️  ADVERTENCIA: Señal BUY contradice sentimiento bajista")
    # Informar cuando se activa por sentimiento dominante
    if senal_sell_alerta and score_sell < 4 and sentimiento_bajista_score >= 6:
        print(f"  📣 ALERTA SELL activada por SENTIMIENTO BAJISTA FUERTE ({sentimiento_bajista_score}/10) - Score técnico bajo ({score_sell}/21)")
    if senal_buy_alerta and score_buy < 4 and sentimiento_alcista_score >= 6:
        print(f"  📣 ALERTA BUY activada por SENTIMIENTO ALCISTA FUERTE ({sentimiento_alcista_score}/10) - Score técnico bajo ({score_buy}/21)")

    # ══════════════════════════════════
    # FILTRO DE LIQUIDEZ OBLIGATORIO (BTC)
    # ══════════════════════════════════
    vol_min_mult = params.get('vol_min_mult', 0.5)
    if vol < vol_avg * vol_min_mult:
        print(f"  ⚠️  Volumen insuficiente ({round(vol):,} < {round(vol_avg * vol_min_mult):,}) — señal bloqueada")
        return

    # ══════════════════════════════════
    # ANTI-SPAM
    # ══════════════════════════════════
    clave_vela = f"{simbolo}_{fecha}"

    def ya_enviada(tipo):
        return alertas_enviadas.get(f"{clave_vela}_{tipo}", False)

    def marcar_enviada(tipo):
        alertas_enviadas[f"{clave_vela}_{tipo}"] = True

    # ══════════════════════════════════
    # ENVIAR ALERTAS
    # ══════════════════════════════════

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

    # ── APROXIMACIÓN RESISTENCIA ──
    if aproximando_resistencia and not en_zona_resist and not cancelar_sell and not senal_buy_alerta:
        if not ya_enviada('PREP_SELL'):
            msg = (f"🔔 <b>PREPARAR SELL LIMIT — BITCOIN</b> 🔔\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📢 Precio aproximándose a resistencia\n"
                   f"💰 <b>Precio:</b>     ${round(close, 0):,}\n"
                   f"📌 <b>SELL LIMIT:</b> ${round(sell_limit, 0):,}\n"
                   f"🛑 <b>Stop Loss:</b>  ${round(sl_venta, 0):,}\n"
                   f"🎯 <b>TP1:</b> ${tp1_v:,}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                   f"🎯 <b>TP2:</b> ${tp2_v:,}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                   f"🎯 <b>TP3:</b> ${tp3_v:,}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b> {score_sell}/15  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                   f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
            enviar_telegram(msg)
            marcar_enviada('PREP_SELL')

    # ── APROXIMACIÓN SOPORTE ──
    if aproximando_soporte and not en_zona_soporte and not cancelar_buy and not senal_sell_alerta:
        if not ya_enviada('PREP_BUY'):
            msg = (f"🔔 <b>PREPARAR BUY LIMIT — BITCOIN</b> 🔔\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📢 Precio aproximándose a soporte\n"
                   f"💰 <b>Precio:</b>    ${round(close, 0):,}\n"
                   f"📌 <b>BUY LIMIT:</b> ${round(buy_limit, 0):,}\n"
                   f"🛑 <b>Stop Loss:</b> ${round(sl_compra, 0):,}\n"
                   f"🎯 <b>TP1:</b> ${tp1_c:,}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                   f"🎯 <b>TP2:</b> ${tp2_c:,}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                   f"🎯 <b>TP3:</b> ${tp3_c:,}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b> {score_buy}/15  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                   f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
            enviar_telegram(msg)
            marcar_enviada('PREP_BUY')

    # ── SEÑALES VENTA ──
    if senal_sell_alerta and not cancelar_sell and rr_sell_tp1 >= 1.5:
        # Determinar nivel y agregar marcador de confluencia
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
        if not ya_enviada(tipo_clave):
            # Verificar si ya existe señal reciente para evitar duplicados (solo si BD activa)
            if db and db.existe_senal_reciente(simbolo, 'VENTA', horas=2):
                print(f"  ℹ️  Señal VENTA duplicada - No se guarda")
                return
            
            msg = (f"{nivel} — <b>BITCOIN</b>\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"💰 <b>Precio:</b>     ${round(close, 0):,}\n"
                   f"📌 <b>SELL LIMIT:</b> ${round(sell_limit, 0):,}\n"
                   f"🛑 <b>Stop Loss:</b>  ${round(sl_venta, 0):,}\n"
                   f"🎯 <b>TP1:</b> ${tp1_v:,}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                   f"🎯 <b>TP2:</b> ${tp2_v:,}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                   f"🎯 <b>TP3:</b> ${tp3_v:,}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score técnico:</b> {score_sell}/21\n"
                   f"{emoji_sentimiento} <b>Sentimiento:</b> {sentimiento_general} ({sentimiento_bajista_score}/10)\n"
                   f"🎯 <b>Calidad:</b> {calidad}\n"
                   f"📉 <b>RSI:</b> {round(rsi, 1)}\n"
                   f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
            
            # Agregar advertencia si hay contradicción
            if senal_contradictoria_sell:
                msg += f"\n\n⚠️ <b>ADVERTENCIA:</b> Sentimiento alcista ({sentimiento_alcista_score}/10) contradice señal SELL"
            
            # Guardar señal en base de datos (solo si está disponible)
            if db:
                senal_data = {
                    'timestamp': datetime.now(timezone.utc),
                    'simbolo': simbolo,
                    'direccion': 'VENTA',
                    'precio_entrada': sell_limit,
                    'tp1': tp1_v,
                    'tp2': tp2_v,
                    'tp3': tp3_v,
                    'sl': sl_venta,
                    'score': score_sell,
                    'indicadores': json.dumps({
                        'rsi': round(rsi, 1),
                        'ema_fast': round(ema_fast, 2),
                        'ema_slow': round(ema_slow, 2),
                        'atr': round(atr, 2),
                        'bb_upper': round(bb_upper, 2),
                        'bb_lower': round(bb_lower, 2),
                        'macd': round(macd, 2),
                        'adx': round(adx, 2)
                    }),
                    'patron_velas': f"Evening Star: {evening_star}, Shooting Star: {shooting_star}",
                    'version_detector': '2.0'
                }
                
                try:
                    senal_id = db.guardar_senal(senal_data)
                    print(f"  💾 Señal VENTA guardada en DB con ID: {senal_id}")
                except Exception as e:
                    print(f"  ⚠️ Error guardando señal en DB: {e}")
            
            enviar_telegram(msg)
            marcar_enviada(tipo_clave)

    # ── SEÑALES COMPRA ──
    if senal_buy_alerta and not cancelar_buy and rr_buy_tp1 >= 1.5:
        # Determinar nivel y agregar marcador de confluencia
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
        if not ya_enviada(tipo_clave):
            # Verificar si ya existe señal reciente para evitar duplicados (solo si BD activa)
            if db and db.existe_senal_reciente(simbolo, 'COMPRA', horas=2):
                print(f"  ℹ️  Señal COMPRA duplicada - No se guarda")
                return
            
            msg = (f"{nivel} — <b>BITCOIN</b>\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"💰 <b>Precio:</b>    ${round(close, 0):,}\n"
                   f"📌 <b>BUY LIMIT:</b> ${round(buy_limit, 0):,}\n"
                   f"🛑 <b>Stop Loss:</b> ${round(sl_compra, 0):,}\n"
                   f"🎯 <b>TP1:</b> ${tp1_c:,}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                   f"🎯 <b>TP2:</b> ${tp2_c:,}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                   f"🎯 <b>TP3:</b> ${tp3_c:,}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score técnico:</b> {score_buy}/21\n"
                   f"{emoji_sentimiento} <b>Sentimiento:</b> {sentimiento_general} ({sentimiento_alcista_score}/10)\n"
                   f"🎯 <b>Calidad:</b> {calidad}\n"
                   f"📉 <b>RSI:</b> {round(rsi, 1)}\n"
                   f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
            
            # Agregar advertencia si hay contradicción
            if senal_contradictoria_buy:
                msg += f"\n\n⚠️ <b>ADVERTENCIA:</b> Sentimiento bajista ({sentimiento_bajista_score}/10) contradice señal BUY"
            
            # Guardar señal en base de datos (solo si está disponible)
            if db:
                senal_data = {
                    'timestamp': datetime.now(timezone.utc),
                    'simbolo': simbolo,
                    'direccion': 'COMPRA',
                    'precio_entrada': buy_limit,
                    'tp1': tp1_c,
                    'tp2': tp2_c,
                    'tp3': tp3_c,
                    'sl': sl_compra,
                    'score': score_buy,
                    'indicadores': json.dumps({
                        'rsi': round(rsi, 1),
                        'ema_fast': round(ema_fast, 2),
                        'ema_slow': round(ema_slow, 2),
                        'atr': round(atr, 2),
                        'bb_upper': round(bb_upper, 2),
                        'bb_lower': round(bb_lower, 2),
                        'macd': round(macd, 2),
                        'adx': round(adx, 2)
                    }),
                    'patron_velas': f"Morning Star: {morning_star}, Hammer: {hammer}",
                    'version_detector': '2.0'
                }
                
                try:
                    senal_id = db.guardar_senal(senal_data)
                    print(f"  💾 Señal COMPRA guardada en DB con ID: {senal_id}")
                except Exception as e:
                    print(f"  ⚠️ Error guardando señal en DB: {e}")
            
            enviar_telegram(msg)
            marcar_enviada(tipo_clave)

    # ── CANCELACIONES ──
    if cancelar_sell and not ya_enviada('CANCEL_SELL'):
        msg = (f"❌ <b>CANCELAR SELL LIMIT — BITCOIN</b> ❌\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"💰 <b>Precio:</b> ${round(close, 0):,}\n"
               f"⚠️ Precio rompió la resistencia (${zrh:,})\n"
               f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
        enviar_telegram(msg)
        marcar_enviada('CANCEL_SELL')

    if cancelar_buy and not ya_enviada('CANCEL_BUY'):
        msg = (f"❌ <b>CANCELAR BUY LIMIT — BITCOIN</b> ❌\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"💰 <b>Precio:</b> ${round(close, 0):,}\n"
               f"⚠️ Precio rompió el soporte (${zsl:,})\n"
               f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
        enviar_telegram(msg)
        marcar_enviada('CANCEL_BUY')

# ══════════════════════════════════════
# BUCLE PRINCIPAL
# ══════════════════════════════════════
def main():
    print("🚀 Detector BITCOIN iniciado")
    print(f"⏱️  Revisando cada {CHECK_INTERVAL//60} minutos")

    enviar_telegram("🚀 <b>Detector BITCOIN iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "📊 Monitorizando: BTCUSD\n"
                    "⏱️ Timeframe: 1D\n"
                    "🔄 Revisión cada 14 minutos\n"
                    "💚 Mantiene el servidor activo\n"
                    "✅ Solo alertas en velas nuevas o cambios significativos\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔴 Resistencia: $95,000 - $105,000\n"
                    f"🟢 Soporte:     $65,000 - $75,000")

    ciclo = 0
    while True:
        ciclo += 1
        ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n{'='*60}")
        print(f"[{ahora}] 🔄 CICLO #{ciclo} - Iniciando análisis BITCOIN")
        print(f"{'='*60}")
        
        for simbolo, params in SIMBOLOS.items():
            print(f"\n📊 Analizando {simbolo}...")
            analizar(simbolo, params)
        
        print(f"\n{'='*60}")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Ciclo #{ciclo} completado")
        print(f"⏳ Esperando {CHECK_INTERVAL//60} minutos hasta el próximo análisis...")
        print(f"{'='*60}\n")
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()
