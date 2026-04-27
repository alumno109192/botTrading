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
from services.economic_calendar import obtener_aviso_macro

# Cargar variables de entorno
load_dotenv()
from adapters.telegram import enviar_telegram as _enviar_telegram_base

_aviso_macro = ""

def enviar_telegram(mensaje):
    sufijo = f"\n⚠️ <b>Evento macro próximo:</b> {_aviso_macro}" if _aviso_macro else ""
    return _enviar_telegram_base(mensaje + sufijo, TELEGRAM_THREAD_ID)

from adapters.database import get_db
from core.base_detector import BaseDetector
import logging
logger = logging.getLogger('bottrading')

db = get_db()

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
        'anticipar_velas':    5,          # aumentado de 3→5 para detectar aproximaciones antes
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
    detectar_rotura_alcista, detectar_rotura_bajista,
    detectar_doble_techo, detectar_doble_suelo,
    detectar_canal_roto, calcular_sr_multiples,
    detectar_precio_en_canal,
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





class GoldDetector4H(BaseDetector):
    def analizar(self, simbolo, params):
        logger.info(f"\n🔍 Analizando {simbolo} [4H]...")

        # ── Aviso calendario económico (no bloquea, solo advierte en el mensaje) ──
        self.aviso_macro = obtener_aviso_macro(60, '4H', simbolo)

        # Descargar datos 4H
        try:
            df, is_delayed = get_ohlcv(params['ticker_yf'], period='60d', interval='4h')
            if is_delayed:
                logger.warning("  ⚠️  [4H] Datos con 15 min de delay (yfinance). Configura TWELVE_DATA_API_KEY para tiempo real.")
            if df.empty or len(df) < 200:
                logger.warning(f"⚠️ Datos insuficientes para {simbolo}")
                return
        except Exception as e:
            logger.error(f"❌ Error descargando {simbolo}: {e}")
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
        zrl, zrh, zsl, zsh = self.calcular_zonas_sr(df, atr, params['sr_lookback'], params['sr_zone_mult'])
        tol  = round(atr * 0.75, 2)  # tolerancia dinámica: 75% del ATR (aumentado de 0.4 para capturar rebotes en canal)
        lop  = params['limit_offset_pct']
        cd   = params['cancelar_dist']
        av   = params['anticipar_velas']
        vm   = params['vol_mult']
        rsms = params['rsi_min_sell']
        rsmb = params['rsi_max_buy']
        asm  = params['atr_sl_mult']
        logger.info(f"  📍 Zonas auto — Resist: ${zrl:.1f}-${zrh:.1f} | Soporte: ${zsl:.1f}-${zsh:.1f}")

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

        # ── PATRÓN: FALLO DE CONTINUACIÓN BAJISTA ──
        # Precio formó ATH reciente pero no pudo sostenerlo → breakout fracasado
        _lkb_fallo = 20
        _ath_reciente = float(df['High'].iloc[-_lkb_fallo-1:-1].max())
        _en_ath = high >= _ath_reciente * 0.995          # vela tocó el ATH (margen 0.5%)
        fallo_continuacion_bajista = (
            _en_ath and                                  # estuvo en ATH
            is_bearish and                               # vela bajista
            close < ema_fast and                         # cerró bajo EMA rápida
            body > atr * 0.4 and                         # cuerpo significativo
            vol > vol_avg * 1.3                          # volumen institucional
        )
        if fallo_continuacion_bajista:
            logger.warning(f"  ⚠️ [4H] FALLO DE CONTINUACIÓN BAJISTA detectado (ATH={_ath_reciente:.1f})")

        # ── ROTURA DE NIVEL Y DOBLES TECHOS/SUELOS ──────────────────────────
        # Rotura alcista: precio cerró por encima de resistencia con impulso y volumen
        rotura_alcista = detectar_rotura_alcista(df, zrh, atr, params['vol_mult'])
        # Rotura bajista: precio cerró por debajo de soporte con impulso y volumen
        rotura_bajista = detectar_rotura_bajista(df, zsl, atr, params['vol_mult'])

        # Parámetro de tolerancia para dobles patrones: más ajustado en 4H
        _dt_lookback = min(params['sr_lookback'], 40)
        dt_detectado, dt_nivel_techo, dt_neckline = detectar_doble_techo(
            df, atr, lookback=_dt_lookback, tol_mult=0.7)
        ds_detectado, ds_nivel_suelo, ds_neckline = detectar_doble_suelo(
            df, atr, lookback=_dt_lookback, tol_mult=0.7)

        if rotura_alcista:
            logger.info(f"  🚀 [4H] ROTURA ALCISTA detectada — cerró sobre resistencia ${zrh:.1f}")
        if rotura_bajista:
            logger.info(f"  📉 [4H] ROTURA BAJISTA detectada — cerró bajo soporte ${zsl:.1f}")
        if dt_detectado:
            logger.info(f"  🔻 [4H] DOBLE TECHO detectado — techo=${dt_nivel_techo:.1f} cuello=${dt_neckline:.1f}")
        if ds_detectado:
            logger.info(f"  🔺 [4H] DOBLE SUELO detectado — suelo=${ds_nivel_suelo:.1f} cuello=${ds_neckline:.1f}")

        # ── Detección de canal roto 4H ─────────────────────────────────────────────
        canal_alcista_roto_4h, canal_bajista_roto_4h, \
            linea_soporte_canal_4h, linea_resist_canal_4h = detectar_canal_roto(
                df, atr, lookback=params.get('sr_lookback', 80), wing=3)
        soportes_sr_4h, resistencias_sr_4h = calcular_sr_multiples(
            df, atr, lookback=params.get('sr_lookback', 80),
            zone_mult=params.get('sr_zone_mult', 0.6))
        if canal_alcista_roto_4h:
            logger.info(f"  🔻 [4H] CANAL ALCISTA ROTO — línea soporte ${linea_soporte_canal_4h:.2f}")
        if canal_bajista_roto_4h:
            logger.info(f"  🔺 [4H] CANAL BAJISTA ROTO — línea resist ${linea_resist_canal_4h:.2f}")

        en_resist_canal_bajista, en_soporte_canal_alcista, \
            linea_resist_canal_precio_4h, linea_soporte_canal_precio_4h = detectar_precio_en_canal(
                df, atr, lookback=params.get('sr_lookback', 80), wing=3)
        if en_resist_canal_bajista:
            logger.info(f"  📐 [4H] PRECIO EN DIRECTRIZ BAJISTA — resist ${linea_resist_canal_precio_4h:.2f}")
        if en_soporte_canal_alcista:
            logger.info(f"  📐 [4H] PRECIO EN DIRECTRIZ ALCISTA — soporte ${linea_soporte_canal_precio_4h:.2f}")


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
        score_sell += 3 if fallo_continuacion_bajista else 0
        score_sell += 4 if rotura_bajista          else 0  # rotura con impulso+volumen
        score_sell += 3 if dt_detectado            else 0  # doble techo confirmado
        score_sell += 2 if canal_alcista_roto_4h   else 0  # canal alcista roto → sesgo bajista
        score_sell += 3 if en_resist_canal_bajista  else 0  # precio tocando directriz bajista

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

        # ── PATRÓN: FALLO DE CONTINUACIÓN ALCISTA ──
        _atl_reciente = float(df['Low'].iloc[-_lkb_fallo-1:-1].min())
        _en_atl = low <= _atl_reciente * 1.005
        fallo_continuacion_alcista = (
            _en_atl and
            is_bullish and
            close > ema_fast and
            body > atr * 0.4 and
            vol > vol_avg * 1.3
        )
        if fallo_continuacion_alcista:
            logger.warning(f"  ⚠️ [4H] FALLO DE CONTINUACIÓN ALCISTA detectado (ATL={_atl_reciente:.1f})")

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
        score_buy  += 3 if fallo_continuacion_alcista else 0
        score_buy  += 4 if rotura_alcista          else 0  # rotura con impulso+volumen
        score_buy  += 3 if ds_detectado            else 0  # doble suelo confirmado
        score_buy  += 2 if canal_bajista_roto_4h   else 0  # canal bajista roto → sesgo alcista
        score_buy  += 3 if en_soporte_canal_alcista else 0  # precio tocando directriz alcista

        if adx_lateral:
            score_buy = max(0, score_buy - 3)

        # ── Ajuste por sesgo DXY (correlación inversa Gold/USD) ──
        dxy_bias = get_dxy_bias()
        score_buy, score_sell = ajustar_score_por_dxy(score_buy, score_sell, dxy_bias)

        # ── Filtro de volumen: penalizar señales en velas de bajo volumen ──
        score_sell, score_buy, _vol_bajo = self.ajustar_scores_por_volumen(
            score_sell, score_buy, vol, vol_avg, vm)
        if _vol_bajo:
            logger.info(f"  ⚠️ [4H] Volumen bajo ({vol:.0f} < {vol_avg * vm:.0f}) — scores penalizados -3")

        # ── Umbral adaptativo: elevar umbrales si ATR está en alta volatilidad ──
        atr_media = float(df['atr'].rolling(20).mean().iloc[-2])
        _umbral_max = self.umbral_adaptativo(14, atr, atr_media)
        _umbral_fue = self.umbral_adaptativo(12, atr, atr_media)
        _umbral_med = self.umbral_adaptativo(9,  atr, atr_media)
        _umbral_ale = self.umbral_adaptativo(5,  atr, atr_media)

        # NIVELES DE SEÑAL 4H (MÁS ESTRICTOS)
        senal_sell_maxima = score_sell >= _umbral_max
        senal_sell_fuerte = score_sell >= _umbral_fue
        senal_sell_media  = score_sell >= _umbral_med
        senal_sell_alerta = score_sell >= _umbral_ale
        senal_buy_maxima  = score_buy  >= _umbral_max
        senal_buy_fuerte  = score_buy  >= _umbral_fue
        senal_buy_media   = score_buy  >= _umbral_med
        senal_buy_alerta  = score_buy  >= _umbral_ale

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
            logger.warning(f"  ⛔ SELL bloqueada: R:R TP1={rr_sell_tp1} < 1.2")
        if rr_buy_tp1 < 1.2:
            logger.warning(f"  ⛔ BUY bloqueada: R:R TP1={rr_buy_tp1} < 1.2")

        fecha = df.index[-2].strftime('%Y-%m-%d %H:%M')
    
        # VERIFICAR SI YA SE ANALIZÓ ESTA VELA
        clave_simbolo = f"{simbolo}_4H"
    
        if clave_simbolo in self.ultimo_analisis:
            ultima_fecha = self.ultimo_analisis[clave_simbolo]['fecha']
            ultimo_score_sell = self.ultimo_analisis[clave_simbolo]['score_sell']
            ultimo_score_buy = self.ultimo_analisis[clave_simbolo]['score_buy']
        
            if (ultima_fecha == fecha and 
                abs(ultimo_score_sell - score_sell) <= 1 and 
                abs(ultimo_score_buy - score_buy) <= 1):
                logger.info(f"  ℹ️  Vela {fecha} ya analizada - Sin cambios significativos")
                return
    
        self.ultimo_analisis[clave_simbolo] = {
            'fecha': fecha,
            'score_sell': score_sell,
            'score_buy': score_buy
        }
    
        logger.info(f"  📅 Vela:  {fecha}")
        logger.info(f"  💰 Close: {round(close, 2)}")
        logger.info(f"  📊 Score SELL: {score_sell}/15 | Score BUY: {score_buy}/15")
        logger.info(f"  🔴 SELL → Alerta:{senal_sell_alerta} Media:{senal_sell_media} Fuerte:{senal_sell_fuerte} Máxima:{senal_sell_maxima}")
        logger.info(f"  🟢 BUY  → Alerta:{senal_buy_alerta}  Media:{senal_buy_media}  Fuerte:{senal_buy_fuerte}  Máxima:{senal_buy_maxima}")

        clave_vela = f"{simbolo}_4H_{fecha}"


        cerca_resistencia = en_zona_resist or aproximando_resistencia or fallo_continuacion_bajista or en_resist_canal_bajista
        cerca_soporte     = en_zona_soporte or aproximando_soporte     or fallo_continuacion_alcista or en_soporte_canal_alcista
        if not cerca_resistencia:
            if senal_sell_alerta: logger.info(f"  ⏳ SELL ignorada: precio lejos de resistencia")
            senal_sell_maxima = senal_sell_fuerte = senal_sell_media = senal_sell_alerta = False
        if not cerca_soporte:
            if senal_buy_alerta: logger.info(f"  ⏳ BUY ignorada: precio lejos de soporte")
            senal_buy_maxima = senal_buy_fuerte = senal_buy_media = senal_buy_alerta = False

        # ── EXCLUSIÓN MUTUA: una sola dirección por vela ──
        if senal_sell_alerta and senal_buy_alerta:
            if score_sell >= score_buy:
                senal_buy_alerta = False
                logger.info(f"  ⚖️ Exclusión mutua: BUY suprimida (SELL {score_sell} >= BUY {score_buy})")
            else:
                senal_sell_alerta = False
                logger.info(f"  ⚖️ Exclusión mutua: SELL suprimida (BUY {score_buy} > SELL {score_sell})")

        # ── PUBLICAR + FILTRO CONFLUENCIA MULTI-TF (GOLD 4H) ──
        _sesgo_dir = tf_bias.BIAS_BEARISH if score_sell > score_buy else tf_bias.BIAS_BULLISH if score_buy > score_sell else tf_bias.BIAS_NEUTRAL
        tf_bias.publicar_sesgo(simbolo, '4H', _sesgo_dir, max(score_sell, score_buy))
        # Publicar datos de canal para que 1H los consulte
        tf_bias.publicar_canal_4h(simbolo,
            alcista_roto=canal_alcista_roto_4h, bajista_roto=canal_bajista_roto_4h,
            linea_soporte=linea_soporte_canal_4h, linea_resist=linea_resist_canal_4h)
        _conf_sell = ""; _conf_buy = ""
        if senal_sell_alerta:
            _ok, _desc = tf_bias.verificar_confluencia(simbolo, '4H', tf_bias.BIAS_BEARISH)
            if not _ok:
                logger.info(f"  🚫 SELL bloqueada por TF superior: {_desc[:80]}")
                senal_sell_maxima = senal_sell_fuerte = senal_sell_media = senal_sell_alerta = False
            else:
                _conf_sell = _desc
        if senal_buy_alerta:
            _ok, _desc = tf_bias.verificar_confluencia(simbolo, '4H', tf_bias.BIAS_BULLISH)
            if not _ok:
                logger.info(f"  🚫 BUY bloqueada por TF superior: {_desc[:80]}")
                senal_buy_maxima = senal_buy_fuerte = senal_buy_media = senal_buy_alerta = False
            else:
                _conf_buy = _desc

        simbolo_db = f"{simbolo}_4H"

        # ── SEÑAL ACCIONABLE (antes de entrar en zona — pon la orden limit ahora) ──
        if aproximando_resistencia and not en_zona_resist and not cancelar_sell and senal_sell_alerta and not self.ya_enviada(f"{clave_vela}_PREP_SELL"):
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
            if self.db and not self.db.existe_senal_reciente(simbolo_db, "VENTA", horas=2):
                try:
                    self.db.guardar_senal({
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
                    logger.error(f"  ⚠️ Error BD: {e}")
            self.enviar(msg); self.marcar_enviada(f"{clave_vela}_PREP_SELL")

        if aproximando_soporte and not en_zona_soporte and not cancelar_buy and senal_buy_alerta and not self.ya_enviada(f"{clave_vela}_PREP_BUY"):
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
            if self.db and not self.db.existe_senal_reciente(simbolo_db, "COMPRA", horas=2):
                try:
                    self.db.guardar_senal({
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
                    logger.error(f"  ⚠️ Error BD: {e}")
            self.enviar(msg); self.marcar_enviada(f"{clave_vela}_PREP_BUY")

        # ── SEÑALES EN ZONA (confirmación si ya hubo señal accionable) ──
        if senal_sell_alerta and not cancelar_sell and rr_sell_tp1 >= 1.2:
            if self.ya_enviada(f"{clave_vela}_PREP_SELL") and not (senal_sell_fuerte or senal_sell_maxima):
                logger.info(f"  ℹ️  SELL ALERTA/MEDIA ignorada: señal accionable ya enviada")
            else:
                nivel = ("🔥 SELL MÁXIMA (4H)" if senal_sell_maxima else
                         "🔴 SELL FUERTE (4H)" if senal_sell_fuerte else
                         "⚠️ SELL MEDIA (4H)"  if senal_sell_media  else
                         "👀 SELL ALERTA (4H)")
                tipo_clave = ("SELL_MAX" if senal_sell_maxima else
                              "SELL_FUE" if senal_sell_fuerte else
                              "SELL_MED" if senal_sell_media  else "SELL_ALE")
                if not self.ya_enviada(f"{clave_vela}_{tipo_clave}"):
                    if self.ya_enviada(f"{clave_vela}_PREP_SELL"):
                        msg = (f"✅ <b>CONFIRMACIÓN SELL — ORO 4H</b>\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"{nivel} — precio ahora en zona\n"
                               f"💰 <b>Precio:</b> ${round(close, 2)}\n"
                               f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                               f"⏱️ <b>TF:</b> 4H  📅 {fecha}")
                        self.enviar(msg); self.marcar_enviada(f"{clave_vela}_{tipo_clave}")
                    else:
                        if self.db and self.db.existe_senal_reciente(simbolo_db, "VENTA", horas=2):
                            logger.info(f"  ℹ️  Señal VENTA duplicada - No se guarda"); return
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
                        if self.db:
                            try:
                                self.db.guardar_senal({
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
                                logger.error(f"  ⚠️ Error guardando en BD: {e}")
                        self.enviar(msg); self.marcar_enviada(f"{clave_vela}_{tipo_clave}")

        if senal_buy_alerta and not cancelar_buy and rr_buy_tp1 >= 1.2:
            if self.ya_enviada(f"{clave_vela}_PREP_BUY") and not (senal_buy_fuerte or senal_buy_maxima):
                logger.info(f"  ℹ️  BUY ALERTA/MEDIA ignorada: señal accionable ya enviada")
            else:
                nivel = ("🔥 BUY MÁXIMA (4H)"  if senal_buy_maxima else
                         "🟢 BUY FUERTE (4H)"  if senal_buy_fuerte else
                         "⚠️ BUY MEDIA (4H)"   if senal_buy_media  else
                         "👀 BUY ALERTA (4H)")
                tipo_clave = ("BUY_MAX" if senal_buy_maxima else
                              "BUY_FUE" if senal_buy_fuerte else
                              "BUY_MED" if senal_buy_media  else "BUY_ALE")
                if not self.ya_enviada(f"{clave_vela}_{tipo_clave}"):
                    if self.ya_enviada(f"{clave_vela}_PREP_BUY"):
                        msg = (f"✅ <b>CONFIRMACIÓN BUY — ORO 4H</b>\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"{nivel} — precio ahora en zona\n"
                               f"💰 <b>Precio:</b> ${round(close, 2)}\n"
                               f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                               f"⏱️ <b>TF:</b> 4H  📅 {fecha}")
                        self.enviar(msg); self.marcar_enviada(f"{clave_vela}_{tipo_clave}")
                    else:
                        if self.db and self.db.existe_senal_reciente(simbolo_db, "COMPRA", horas=2):
                            logger.info(f"  ℹ️  Señal COMPRA duplicada - No se guarda"); return
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
                        if self.db:
                            try:
                                self.db.guardar_senal({
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
                                logger.error(f"  ⚠️ Error guardando en BD: {e}")
                        self.enviar(msg); self.marcar_enviada(f"{clave_vela}_{tipo_clave}")

        # ── CANCELACIONES ──────────────────────────────────────────────────────
        if cancelar_sell and not self.ya_enviada(f"{clave_vela}_CANCEL_SELL"):
            msg = (f"❌ <b>CANCELAR SELL LIMIT — ORO (XAUUSD) 4H</b> ❌\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📌 <b>Orden prevista:</b> SELL LIMIT ${sell_limit:.2f}\n"
                   f"💰 <b>Precio actual:</b>  ${close:.2f}\n"
                   f"⚠️ <b>Motivo:</b> Precio rompió la resistencia (${zrh:.2f}) hacia arriba\n"
                   f"🚫 La entrada ya no es válida — cancela la orden limit\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"⏱️ <b>TF:</b> 4H  📅 {fecha}")
            if self.db:
                try:
                    self.db.cancelar_senales_pendientes(simbolo_db, "VENTA")
                except Exception as e:
                    logger.error(f"  ⚠️ Error BD al cancelar VENTA: {e}")
            self.enviar(msg)
            self.marcar_enviada(f"{clave_vela}_CANCEL_SELL")

        if cancelar_buy and not self.ya_enviada(f"{clave_vela}_CANCEL_BUY"):
            msg = (f"❌ <b>CANCELAR BUY LIMIT — ORO (XAUUSD) 4H</b> ❌\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📌 <b>Orden prevista:</b> BUY LIMIT ${buy_limit:.2f}\n"
                   f"💰 <b>Precio actual:</b>  ${close:.2f}\n"
                   f"⚠️ <b>Motivo:</b> Precio perforó el soporte (${zsl:.2f}) hacia abajo\n"
                   f"🚫 La entrada ya no es válida — cancela la orden limit\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"⏱️ <b>TF:</b> 4H  📅 {fecha}")
            if self.db:
                try:
                    self.db.cancelar_senales_pendientes(simbolo_db, "COMPRA")
                except Exception as e:
                    logger.error(f"  ⚠️ Error BD al cancelar COMPRA: {e}")
            self.enviar(msg)
            self.marcar_enviada(f"{clave_vela}_CANCEL_BUY")

        # ══════════════════════════════════════════════════════════
        # SEÑALES DE ROTURA DE NIVEL (BREAKOUT) — entrada a mercado
        # ══════════════════════════════════════════════════════════

        # ── Rotura alcista (precio superó resistencia con impulso) ──
        if rotura_alcista and not self.ya_enviada(f"{clave_vela}_BREAK_BUY"):
            # SL justo debajo del nivel roto (ahora soporte); TP con ATR
            sl_break_buy  = round(zrh - atr * 0.5, 2)
            tp1_break_buy = round(close + atr * params['atr_tp1_mult'], 2)
            tp2_break_buy = round(close + atr * params['atr_tp2_mult'], 2)
            tp3_break_buy = round(close + atr * params['atr_tp3_mult'], 2)
            rr_b1 = rr(close, sl_break_buy, tp1_break_buy)
            if rr_b1 >= 1.2:
                nivel_break = ("🔥 ROTURA MÁXIMA" if score_buy >= 14 else
                               "🟢 ROTURA FUERTE" if score_buy >= 10 else
                               "⚡ ROTURA MEDIA")
                msg = (f"🚀 <b>ROTURA ALCISTA — ORO (XAUUSD) 4H</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Nivel:</b> {nivel_break}\n"
                       f"💰 <b>Precio actual:</b>  ${round(close, 2)}\n"
                       f"📌 <b>COMPRA a MERCADO</b>  ← entrada inmediata\n"
                       f"🔓 <b>Resistencia rota:</b> ${round(zrh, 2)} (ahora soporte)\n"
                       f"🛑 <b>Stop Loss:</b>        ${sl_break_buy}  (-${round(close - sl_break_buy, 2)})\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🎯 <b>TP1:</b> ${tp1_break_buy}  R:R {rr_b1}:1\n"
                       f"🎯 <b>TP2:</b> ${tp2_break_buy}  R:R {rr(close, sl_break_buy, tp2_break_buy)}:1\n"
                       f"🎯 <b>TP3:</b> ${tp3_break_buy}  R:R {rr(close, sl_break_buy, tp3_break_buy)}:1\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                       f"⏱️ <b>TF:</b> 4H  📅 {fecha}  🔒 SWING")
                if self.db and not self.db.existe_senal_reciente(simbolo_db, "COMPRA", horas=2):
                    try:
                        self.db.guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                            'direccion': 'COMPRA', 'precio_entrada': close,
                            'tp1': tp1_break_buy, 'tp2': tp2_break_buy, 'tp3': tp3_break_buy,
                            'sl': sl_break_buy, 'score': score_buy,
                            'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 2),
                                                       'macd': round(macd, 2), 'atr': round(atr, 2)}),
                            'patron_velas': f"Rotura Alcista: resistencia_rota={round(zrh, 2)}",
                            'version_detector': 'GOLD 4H-v2.0'
                        })
                    except Exception as e:
                        logger.error(f"  ⚠️ Error BD: {e}")
                self.enviar(msg); self.marcar_enviada(f"{clave_vela}_BREAK_BUY")

        # ── Rotura bajista (precio rompió soporte con impulso) ──
        if rotura_bajista and not self.ya_enviada(f"{clave_vela}_BREAK_SELL"):
            sl_break_sell  = round(zsl + atr * 0.5, 2)
            tp1_break_sell = round(close - atr * params['atr_tp1_mult'], 2)
            tp2_break_sell = round(close - atr * params['atr_tp2_mult'], 2)
            tp3_break_sell = round(close - atr * params['atr_tp3_mult'], 2)
            rr_bs1 = rr(close, sl_break_sell, tp1_break_sell)
            if rr_bs1 >= 1.2:
                nivel_break = ("🔥 ROTURA MÁXIMA" if score_sell >= 14 else
                               "🔴 ROTURA FUERTE" if score_sell >= 10 else
                               "⚡ ROTURA MEDIA")
                msg = (f"📉 <b>ROTURA BAJISTA — ORO (XAUUSD) 4H</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Nivel:</b> {nivel_break}\n"
                       f"💰 <b>Precio actual:</b>  ${round(close, 2)}\n"
                       f"📌 <b>VENTA a MERCADO</b>  ← entrada inmediata\n"
                       f"🔓 <b>Soporte roto:</b>    ${round(zsl, 2)} (ahora resistencia)\n"
                       f"🛑 <b>Stop Loss:</b>       ${sl_break_sell}  (+${round(sl_break_sell - close, 2)})\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🎯 <b>TP1:</b> ${tp1_break_sell}  R:R {rr_bs1}:1\n"
                       f"🎯 <b>TP2:</b> ${tp2_break_sell}  R:R {rr(close, sl_break_sell, tp2_break_sell)}:1\n"
                       f"🎯 <b>TP3:</b> ${tp3_break_sell}  R:R {rr(close, sl_break_sell, tp3_break_sell)}:1\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                       f"⏱️ <b>TF:</b> 4H  📅 {fecha}  🔒 SWING")
                if self.db and not self.db.existe_senal_reciente(simbolo_db, "VENTA", horas=2):
                    try:
                        self.db.guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                            'direccion': 'VENTA', 'precio_entrada': close,
                            'tp1': tp1_break_sell, 'tp2': tp2_break_sell, 'tp3': tp3_break_sell,
                            'sl': sl_break_sell, 'score': score_sell,
                            'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 2),
                                                       'macd': round(macd, 2), 'atr': round(atr, 2)}),
                            'patron_velas': f"Rotura Bajista: soporte_roto={round(zsl, 2)}",
                            'version_detector': 'GOLD 4H-v2.0'
                        })
                    except Exception as e:
                        logger.error(f"  ⚠️ Error BD: {e}")
                self.enviar(msg); self.marcar_enviada(f"{clave_vela}_BREAK_SELL")

        # ══════════════════════════════════════════════════════════
        # SEÑALES DE DOBLE TECHO / DOBLE SUELO
        # ══════════════════════════════════════════════════════════

        # ── Doble Techo ──
        if dt_detectado and not self.ya_enviada(f"{clave_vela}_DTECHO"):
            # Medida proyectada = altura del patrón (techo - cuello)
            altura_dt    = dt_nivel_techo - dt_neckline
            entrada_dt   = close  # mercado: ya rompió bajo la neckline
            sl_dt        = round(dt_nivel_techo + atr * 0.5, 2)
            tp1_dt       = round(dt_neckline - altura_dt * 0.5, 2)   # 50% del movimiento medido
            tp2_dt       = round(dt_neckline - altura_dt * 1.0, 2)   # 100% medida
            tp3_dt       = round(dt_neckline - altura_dt * 1.5, 2)   # 150% extensión
            rr_dt1       = rr(entrada_dt, sl_dt, tp1_dt)
            if rr_dt1 >= 1.2:
                msg = (f"🔻 <b>DOBLE TECHO (M) — ORO (XAUUSD) 4H</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📐 <b>Patrón:</b> Doble Techo confirmado\n"
                       f"💰 <b>Precio actual:</b>  ${round(close, 2)}\n"
                       f"📌 <b>VENTA a MERCADO</b>  ← neckline rota\n"
                       f"📏 <b>Doble techo en:</b>  ${dt_nivel_techo}\n"
                       f"📏 <b>Cuello (neckline):</b> ${dt_neckline}\n"
                       f"🛑 <b>Stop Loss:</b>       ${sl_dt}  (sobre el techo)\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🎯 <b>TP1:</b> ${tp1_dt}  R:R {rr_dt1}:1  (50% medida)\n"
                       f"🎯 <b>TP2:</b> ${tp2_dt}  R:R {rr(entrada_dt, sl_dt, tp2_dt)}:1  (100% medida)\n"
                       f"🎯 <b>TP3:</b> ${tp3_dt}  R:R {rr(entrada_dt, sl_dt, tp3_dt)}:1  (150% ext)\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                       f"⏱️ <b>TF:</b> 4H  📅 {fecha}  🔒 SWING")
                if self.db and not self.db.existe_senal_reciente(simbolo_db, "VENTA", horas=2):
                    try:
                        self.db.guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                            'direccion': 'VENTA', 'precio_entrada': entrada_dt,
                            'tp1': tp1_dt, 'tp2': tp2_dt, 'tp3': tp3_dt, 'sl': sl_dt,
                            'score': score_sell,
                            'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 2),
                                                       'macd': round(macd, 2), 'atr': round(atr, 2)}),
                            'patron_velas': f"Doble Techo: techo={dt_nivel_techo} neckline={dt_neckline}",
                            'version_detector': 'GOLD 4H-v2.0'
                        })
                    except Exception as e:
                        logger.error(f"  ⚠️ Error BD: {e}")
                self.enviar(msg); self.marcar_enviada(f"{clave_vela}_DTECHO")

        # ── Doble Suelo ──
        if ds_detectado and not self.ya_enviada(f"{clave_vela}_DSUELO"):
            altura_ds    = ds_neckline - ds_nivel_suelo
            entrada_ds   = close
            sl_ds        = round(ds_nivel_suelo - atr * 0.5, 2)
            tp1_ds       = round(ds_neckline + altura_ds * 0.5, 2)
            tp2_ds       = round(ds_neckline + altura_ds * 1.0, 2)
            tp3_ds       = round(ds_neckline + altura_ds * 1.5, 2)
            rr_ds1       = rr(entrada_ds, sl_ds, tp1_ds)
            if rr_ds1 >= 1.2:
                msg = (f"🔺 <b>DOBLE SUELO (W) — ORO (XAUUSD) 4H</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📐 <b>Patrón:</b> Doble Suelo confirmado\n"
                       f"💰 <b>Precio actual:</b>  ${round(close, 2)}\n"
                       f"📌 <b>COMPRA a MERCADO</b>  ← neckline superada\n"
                       f"📏 <b>Doble suelo en:</b>  ${ds_nivel_suelo}\n"
                       f"📏 <b>Cuello (neckline):</b> ${ds_neckline}\n"
                       f"🛑 <b>Stop Loss:</b>       ${sl_ds}  (bajo el suelo)\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🎯 <b>TP1:</b> ${tp1_ds}  R:R {rr_ds1}:1  (50% medida)\n"
                       f"🎯 <b>TP2:</b> ${tp2_ds}  R:R {rr(entrada_ds, sl_ds, tp2_ds)}:1  (100% medida)\n"
                       f"🎯 <b>TP3:</b> ${tp3_ds}  R:R {rr(entrada_ds, sl_ds, tp3_ds)}:1  (150% ext)\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                       f"⏱️ <b>TF:</b> 4H  📅 {fecha}  🔒 SWING")
                if self.db and not self.db.existe_senal_reciente(simbolo_db, "COMPRA", horas=2):
                    try:
                        self.db.guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                            'direccion': 'COMPRA', 'precio_entrada': entrada_ds,
                            'tp1': tp1_ds, 'tp2': tp2_ds, 'tp3': tp3_ds, 'sl': sl_ds,
                            'score': score_buy,
                            'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 2),
                                                       'macd': round(macd, 2), 'atr': round(atr, 2)}),
                            'patron_velas': f"Doble Suelo: suelo={ds_nivel_suelo} neckline={ds_neckline}",
                            'version_detector': 'GOLD 4H-v2.0'
                        })
                    except Exception as e:
                        logger.error(f"  ⚠️ Error BD: {e}")
                self.enviar(msg); self.marcar_enviada(f"{clave_vela}_DSUELO")


def analizar(simbolo, params):
    return GoldDetector4H(simbolo=simbolo, tf_label='4H', params=params, telegram_thread_id=TELEGRAM_THREAD_ID).analizar(simbolo, params)


def main():
    logger.info("🚀 Detector GOLD 4H iniciado")
    logger.info(f"⏱️  Revisando cada {CHECK_INTERVAL // 60} minutos")
    enviar_telegram("🚀 <b>Detector GOLD 4H iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "📊 Monitorizando: XAUUSD (Gold)\n"
                    "⏱️ Timeframe: 4H\n"
                    "🔄 Revisión cada 7 minutos\n"
                    "💚 Filtros más estrictos que 1D")
    ciclo = 0
    while True:
        ciclo += 1
        ahora_utc = datetime.now(timezone.utc)
        if ahora_utc.weekday() == 5:
            from datetime import timedelta
            proximo_domingo_18 = (ahora_utc + timedelta(days=1)).replace(
                hour=18, minute=0, second=0, microsecond=0)
            segundos_espera = min((proximo_domingo_18 - ahora_utc).total_seconds(), 3600)
            logger.info(f"[{ahora_utc.strftime('%Y-%m-%d %H:%M')} UTC] 💤 Sábado — mercado cerrado.")
            time.sleep(segundos_espera)
            continue
        ahora = ahora_utc.strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"\n{'=' * 60}")
        logger.info(f"[{ahora}] 🔄 CICLO #{ciclo} - Iniciando análisis GOLD 4H")
        for simbolo, params in SIMBOLOS.items():
            logger.info(f"\n📊 Analizando {simbolo} [4H]...")
            analizar(simbolo, params)
        logger.info(f"⏳ Esperando {CHECK_INTERVAL // 60} minutos...")
        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()

