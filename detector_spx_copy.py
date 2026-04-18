import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import time
from datetime import datetime
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()
from telegram_utils import enviar_telegram

# ══════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════
TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

CHECK_INTERVAL = 14 * 60  # cada 14 minutos (mantiene servidor activo)

# ══════════════════════════════════════
# PARÁMETROS — ESPECÍFICOS SPX500
# ══════════════════════════════════════
SIMBOLOS = {
    'SPX500': {
        'ticker_yf':          '^GSPC',      # SP500 en Yahoo Finance
        'zona_resist_high':   6100.0,
        'zona_resist_low':    5800.0,
        'zona_soporte_high':  5200.0,
        'zona_soporte_low':   4800.0,
        'tp1_venta':          4941.0,
        'tp2_venta':          4700.0,
        'tp3_venta':          4400.0,
        'tp1_compra':         5800.0,
        'tp2_compra':         6000.0,
        'tp3_compra':         6100.0,
        'tolerancia':         50.0,         # Mayor que ORO
        'limit_offset_pct':   0.3,
        'anticipar_velas':    3,
        'cancelar_dist':      1.5,          # Mayor que ORO
        'rsi_length':         14,
        'rsi_min_sell':       60.0,         # Mayor que ORO
        'rsi_max_buy':        40.0,
        'ema_fast_len':       9,
        'ema_slow_len':       21,
        'ema_trend_len':      200,
        'atr_length':         14,
        'atr_sl_mult':        2.0,          # Mayor que ORO
        'vol_mult':           1.3,          # Mayor que ORO
    }
}

# ══════════════════════════════════════
# CONTROL ANTI-SPAM
# ══════════════════════════════════════
alertas_enviadas = {}
ultimo_analisis = {}  # Guarda última fecha y scores analizados


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
# ANÁLISIS DE SENTIMIENTO DEL MERCADO
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

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.copy()

    # ── Indicadores ──
    df['rsi']       = calcular_rsi(df['Close'], params['rsi_length'])
    df['ema_fast']  = calcular_ema(df['Close'], params['ema_fast_len'])
    df['ema_slow']  = calcular_ema(df['Close'], params['ema_slow_len'])
    df['ema_trend'] = calcular_ema(df['Close'], params['ema_trend_len'])
    df['atr']       = calcular_atr(df, params['atr_length'])
    df['vol_avg']   = df['Volume'].rolling(20).mean()

    # ── Velas ──
    df['body']        = (df['Close'] - df['Open']).abs()
    df['upper_wick']  = df['High'] - df[['Close','Open']].max(axis=1)
    df['lower_wick']  = df[['Close','Open']].min(axis=1) - df['Low']
    df['total_range'] = df['High'] - df['Low']
    df['is_bearish']  = df['Close'] < df['Open']
    df['is_bullish']  = df['Close'] > df['Open']

    # Última vela cerrada
    row  = df.iloc[-2]
    prev = df.iloc[-3]
    p2   = df.iloc[-4]

    close = float(row['Close'])
    high  = float(row['High'])
    low   = float(row['Low'])
    open_ = float(row['Open'])
    vol   = float(row['Volume'])

    rsi       = float(row['rsi'])
    rsi_prev  = float(prev['rsi'])
    ema_fast  = float(row['ema_fast'])
    ema_slow  = float(row['ema_slow'])
    ema_trend = float(row['ema_trend'])
    atr       = float(row['atr'])
    vol_avg   = float(row['vol_avg'])

    body        = float(row['body'])
    upper_wick  = float(row['upper_wick'])
    lower_wick  = float(row['lower_wick'])
    total_range = float(row['total_range'])
    is_bearish  = bool(row['is_bearish'])
    is_bullish  = bool(row['is_bullish'])

    # ── Parámetros ──
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

    # ── Proximidad ──
    avg_candle_range = float(df['total_range'].iloc[-6:-1].mean())
    dist_to_resist   = zrl - close
    dist_to_support  = close - zsh

    aproximando_resistencia = (dist_to_resist  > 0 and
                               dist_to_resist  < avg_candle_range * av and
                               close > float(df['Close'].iloc[-5]))

    aproximando_soporte     = (dist_to_support > 0 and
                               dist_to_support < avg_candle_range * av and
                               close < float(df['Close'].iloc[-5]))

    velas_para_resist  = round(dist_to_resist  / avg_candle_range, 1) if avg_candle_range > 0 else 0
    velas_para_support = round(dist_to_support / avg_candle_range, 1) if avg_candle_range > 0 else 0

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
    score_sell += 1 if (shooting_star and vol_alto_rechazo)      else 0
    score_sell += 1 if (divergencia_bajista and rsi_sobrecompra) else 0
    score_sell += 1 if bajo_ema200             else 0

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
    # ANÁLISIS DE SENTIMIENTO DEL MERCADO
    # ══════════════════════════════════
    sentimiento_bajista_score, factores_bajistas = calcular_sentimiento_bajista(row, prev, p2, df, params)
    sentimiento_alcista_score, factores_alcistas = calcular_sentimiento_alcista(row, prev, p2, df, params)
    
    # Determinar sentimiento dominante
    if sentimiento_bajista_score >= 6:
        sentimiento_general = "🔴 BAJISTA FUERTE"
    elif sentimiento_alcista_score >= 6:
        sentimiento_general = "🟢 ALCISTA FUERTE"
    elif sentimiento_bajista_score >= 4:
        sentimiento_general = "⚠️ BAJISTA MODERADO"
    elif sentimiento_alcista_score >= 4:
        sentimiento_general = "⚠️ ALCISTA MODERADO"
    else:
        sentimiento_general = "⚪ NEUTRAL/MIXTO"
    
    # Validar confluencia (señal más fiable si sentimiento la apoya)
    confluencia_sell = sentimiento_bajista_score >= 4 and score_sell >= 6
    confluencia_buy = sentimiento_alcista_score >= 4 and score_buy >= 6
    
    # Señal contradictoria (advertencia)
    senal_contradictoria_sell = score_sell >= 6 and sentimiento_alcista_score > sentimiento_bajista_score
    senal_contradictoria_buy = score_buy >= 6 and sentimiento_bajista_score > sentimiento_alcista_score

    # ── SL y TP ──
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

    # ── Log consola ──
    fecha = df.index[-2].strftime('%Y-%m-%d')
    
    # ══════════════════════════════════════
    # VERIFICAR SI YA SE ANALIZÓ ESTA VELA
    # ══════════════════════════════════════
    clave_simbolo = simbolo
    
    if clave_simbolo in ultimo_analisis:
        ultima_fecha = ultimo_analisis[clave_simbolo]['fecha']
        ultimo_score_sell = ultimo_analisis[clave_simbolo]['score_sell']
        ultimo_score_buy = ultimo_analisis[clave_simbolo]['score_buy']
        
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
    print(f"  📊 Score SELL: {score_sell}/15 | Score BUY: {score_buy}/15")
    print(f"  🔴 SELL → Alerta:{senal_sell_alerta} Media:{senal_sell_media} Fuerte:{senal_sell_fuerte} Máxima:{senal_sell_maxima}")
    print(f"  🟢 BUY  → Alerta:{senal_buy_alerta}  Media:{senal_buy_media}  Fuerte:{senal_buy_fuerte}  Máxima:{senal_buy_maxima}")
    
    # Mostrar sentimiento del mercado
    print(f"\n  📊 SENTIMIENTO MERCADO: {sentimiento_general}")
    if sentimiento_bajista_score >= 4:
        print(f"     🔴 Bajista ({sentimiento_bajista_score}/10): {', '.join(factores_bajistas[:3])}")
    if sentimiento_alcista_score >= 4:
        print(f"     🟢 Alcista ({sentimiento_alcista_score}/10): {', '.join(factores_alcistas[:3])}")
    
    # Advertencias de confluencia
    if confluencia_sell:
        print(f"  ✅ CONFLUENCIA SELL: Señal + Sentimiento alineados")
    elif senal_contradictoria_sell:
        print(f"  ⚠️ ADVERTENCIA: Señal SELL pero sentimiento alcista - Precaución")
    
    if confluencia_buy:
        print(f"  ✅ CONFLUENCIA BUY: Señal + Sentimiento alineados")
    elif senal_contradictoria_buy:
        print(f"  ⚠️ ADVERTENCIA: Señal BUY pero sentimiento bajista - Precaución")

    # ══════════════════════════════════
    # ANTI-SPAM
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
    # ══════════════════════════════════

    # ── APROXIMACIÓN RESISTENCIA ──
    if aproximando_resistencia and not en_zona_resist and not cancelar_sell:
        if not ya_enviada('PREP_SELL'):
            # Añadir advertencia si sentimiento es contradictorio
            advertencia = ""
            if sentimiento_alcista_score > sentimiento_bajista_score:
                advertencia = f"\n⚠️ <b>NOTA:</b> Sentimiento alcista ({sentimiento_alcista_score}/10) - Esperar confirmación"
            elif sentimiento_bajista_score >= 4:
                advertencia = f"\n✅ <b>FAVORABLE:</b> Sentimiento bajista ({sentimiento_bajista_score}/10)"
            
            msg = (f"🔔 <b>PREPARAR SELL LIMIT — SPX500</b> 🔔\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📢 Precio aproximándose a resistencia\n"
                   f"💰 <b>Precio:</b>     {round(close, 2)}\n"
                   f"📐 <b>Faltan:</b>     ~{velas_para_resist} velas\n"
                   f"📌 <b>SELL LIMIT:</b> {round(sell_limit, 2)}\n"
                   f"🛑 <b>Stop Loss:</b>  {round(sl_venta, 2)}\n"
                   f"🎯 <b>TP1:</b> {tp1_v}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                   f"🎯 <b>TP2:</b> {tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                   f"🎯 <b>TP3:</b> {tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                   f"{advertencia}\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b> {score_sell}/15  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                   f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
            enviar_telegram(msg)
            marcar_enviada('PREP_SELL')

    # ── APROXIMACIÓN SOPORTE ──
    if aproximando_soporte and not en_zona_soporte and not cancelar_buy:
        if not ya_enviada('PREP_BUY'):
            # Añadir advertencia si sentimiento es contradictorio
            advertencia = ""
            if sentimiento_bajista_score > sentimiento_alcista_score:
                advertencia = f"\n⚠️ <b>NOTA:</b> Sentimiento bajista ({sentimiento_bajista_score}/10) - Esperar confirmación"
            elif sentimiento_alcista_score >= 4:
                advertencia = f"\n✅ <b>FAVORABLE:</b> Sentimiento alcista ({sentimiento_alcista_score}/10)"
            
            msg = (f"🔔 <b>PREPARAR BUY LIMIT — SPX500</b> 🔔\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📢 Precio aproximándose a soporte\n"
                   f"💰 <b>Precio:</b>    {round(close, 2)}\n"
                   f"📐 <b>Faltan:</b>    ~{velas_para_support} velas\n"
                   f"📌 <b>BUY LIMIT:</b> {round(buy_limit, 2)}\n"
                   f"🛑 <b>Stop Loss:</b> {round(sl_compra, 2)}\n"
                   f"🎯 <b>TP1:</b> {tp1_c}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                   f"🎯 <b>TP2:</b> {tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                   f"🎯 <b>TP3:</b> {tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                   f"{advertencia}\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b> {score_buy}/15  📉 <b>RSI:</b> {round(rsi, 1)}\n"
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
        
        # Determinar contexto de sentimiento
        if confluencia_sell:
            contexto = f"\n🎯 <b>CONFLUENCIA:</b> Sentimiento bajista confirmado ({sentimiento_bajista_score}/10)"
            fiabilidad = "⭐⭐⭐ ALTA"
        elif sentimiento_bajista_score >= 3:
            contexto = f"\n📊 <b>SENTIMIENTO:</b> Moderado bajista ({sentimiento_bajista_score}/10)"
            fiabilidad = "⭐⭐ MEDIA"
        elif senal_contradictoria_sell:
            contexto = f"\n⚠️ <b>PRECAUCIÓN:</b> Sentimiento alcista ({sentimiento_alcista_score}/10) - Operar con cautela"
            fiabilidad = "⭐ BAJA (señal mixta)"
        else:
            contexto = "\n⚪ <b>SENTIMIENTO:</b> Neutral"
            fiabilidad = "⭐⭐ MEDIA"
        
        if not ya_enviada(tipo_clave):
            msg = (f"{nivel} — <b>SPX500</b> {nivel.split()[0]}\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"💰 <b>Precio:</b>     {round(close, 2)}\n"
                   f"📌 <b>SELL LIMIT:</b> {round(sell_limit, 2)}\n"
                   f"🛑 <b>Stop Loss:</b>  {round(sl_venta, 2)}\n"
                   f"🎯 <b>TP1:</b> {tp1_v}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                   f"🎯 <b>TP2:</b> {tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                   f"🎯 <b>TP3:</b> {tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b> {score_sell}/15  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                   f"🔍 <b>Fiabilidad:</b> {fiabilidad}"
                   f"{contexto}\n"
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
        
        # Determinar contexto de sentimiento
        if confluencia_buy:
            contexto = f"\n🎯 <b>CONFLUENCIA:</b> Sentimiento alcista confirmado ({sentimiento_alcista_score}/10)"
            fiabilidad = "⭐⭐⭐ ALTA"
        elif sentimiento_alcista_score >= 3:
            contexto = f"\n📊 <b>SENTIMIENTO:</b> Moderado alcista ({sentimiento_alcista_score}/10)"
            fiabilidad = "⭐⭐ MEDIA"
        elif senal_contradictoria_buy:
            contexto = f"\n⚠️ <b>PRECAUCIÓN:</b> Sentimiento bajista ({sentimiento_bajista_score}/10) - Operar con cautela"
            fiabilidad = "⭐ BAJA (señal mixta)"
        else:
            contexto = "\n⚪ <b>SENTIMIENTO:</b> Neutral"
            fiabilidad = "⭐⭐ MEDIA"
        
        if not ya_enviada(tipo_clave):
            msg = (f"{nivel} — <b>SPX500</b> {nivel.split()[0]}\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"💰 <b>Precio:</b>    {round(close, 2)}\n"
                   f"📌 <b>BUY LIMIT:</b> {round(buy_limit, 2)}\n"
                   f"🛑 <b>Stop Loss:</b> {round(sl_compra, 2)}\n"
                   f"🎯 <b>TP1:</b> {tp1_c}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                   f"🎯 <b>TP2:</b> {tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                   f"🎯 <b>TP3:</b> {tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b> {score_buy}/15  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                   f"🔍 <b>Fiabilidad:</b> {fiabilidad}"
                   f"{contexto}\n"
                   f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
            enviar_telegram(msg)
            marcar_enviada(tipo_clave)

    # ── CANCELACIONES ──
    if cancelar_sell and not ya_enviada('CANCEL_SELL'):
        msg = (f"❌ <b>CANCELAR SELL LIMIT — SPX500</b> ❌\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"💰 <b>Precio:</b> {round(close, 2)}\n"
               f"⚠️ Precio rompió la resistencia ({zrh})\n"
               f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
        enviar_telegram(msg)
        marcar_enviada('CANCEL_SELL')

    if cancelar_buy and not ya_enviada('CANCEL_BUY'):
        msg = (f"❌ <b>CANCELAR BUY LIMIT — SPX500</b> ❌\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"💰 <b>Precio:</b> {round(close, 2)}\n"
               f"⚠️ Precio rompió el soporte ({zsl})\n"
               f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
        enviar_telegram(msg)
        marcar_enviada('CANCEL_BUY')

# ══════════════════════════════════════
# BUCLE PRINCIPAL
# ══════════════════════════════════════
def main():
    print("🚀 Detector SPX500 iniciado")
    print(f"⏱️  Revisando cada {CHECK_INTERVAL//60} minutos")

    enviar_telegram("🚀 <b>Detector SPX500 iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "📊 Monitorizando: SPX500\n"
                    "⏱️ Timeframe: 1D\n"
                    "🔄 Revisión cada 14 minutos\n"
                    "💚 Mantiene el servidor activo\n"
                    "✅ Solo alertas en velas nuevas o cambios significativos\n"
                    "🧠 <b>NUEVO:</b> Análisis de sentimiento del mercado\n"
                    "🎯 Validación de confluencia automática\n"
                    "⚠️ Advertencias sobre señales contradictorias\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔴 Resistencia: 5800 - 6100\n"
                    f"🟢 Soporte:     4800 - 5200")

    while True:
        for simbolo, params in SIMBOLOS.items():
            analizar(simbolo, params)
        print(f"\n⏳ Esperando {CHECK_INTERVAL//60} minutos...\n")
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()