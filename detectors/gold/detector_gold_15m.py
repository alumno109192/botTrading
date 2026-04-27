"""
DETECTOR GOLD 15M - SCALPING
Análisis de XAUUSD en timeframe 15 minutos para operaciones de corto plazo
Optimizado para capturar movimientos rápidos con alta frecuencia
"""
import os
from services import tf_bias
from services.dxy_bias import get_dxy_bias, ajustar_score_por_dxy
from services.economic_calendar import obtener_aviso_macro
from adapters.data_provider import get_ohlcv
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
from adapters.telegram import enviar_telegram as _enviar_telegram_base

_aviso_macro = ""

def enviar_telegram(mensaje):
    sufijo = f"\n⚠️ <b>Evento macro próximo:</b> {_aviso_macro}" if _aviso_macro else ""
    return _enviar_telegram_base(mensaje + sufijo, TELEGRAM_THREAD_ID)

# Inicializar base de datos solo si las variables están configuradas
from adapters.database import get_db
from core.base_detector import BaseDetector
import logging
logger = logging.getLogger('bottrading')

db = get_db()

# ══════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════
TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_THREAD_ID = int(os.environ.get('THREAD_ID_SCALPING') or 0) or None

CHECK_INTERVAL = 2 * 60  # cada 2 minutos (scalping requiere alta frecuencia)

# ══════════════════════════════════════
# PARÁMETROS — SCALPING GOLD 15M
# ══════════════════════════════════════
SIMBOLOS = {
    'XAUUSD': {
        'ticker_yf':          'GC=F',       # Gold Futures
        # Zonas S/R calculadas automáticamente en analizar_simbolo() — sin mantenimiento manual
        'sr_lookback':        200,          # 200 velas 15M ≈ 2 días de historia
        'sr_zone_mult':       1.0,          # ancho de zona = atr × 1.0 (scalping, más amplio)
        # TPs calculados automáticamente como múltiplo de ATR — sin mantenimiento manual
        'atr_tp1_mult':       1.5,          # TP1: 1.5× ATR 15M (~$30-50 desde entry)
        'atr_tp2_mult':       2.5,          # TP2: 2.5× ATR
        'atr_tp3_mult':       4.0,          # TP3: 4.0× ATR (objetivo scalping amplio)
        'limit_offset_pct':   0.15,         # Offset muy pequeño (scalping)
        'anticipar_velas':    2,            # Menos anticipación
        'cancelar_dist':      1.2,          # Distancia de cancelación ajustada
        # Indicadores optimizados para SCALPING
        'rsi_length':         9,            # RSI más sensible (periodo corto)
        'rsi_min_sell':       65.0,         # Umbrales más sensibles
        'rsi_max_buy':        35.0,         
        'ema_fast_len':       5,            # EMAs muy rápidas
        'ema_slow_len':       13,           
        'ema_trend_len':      50,           # Tendencia de corto plazo
        'atr_length':         10,           # ATR más sensible
        'atr_sl_mult':        1.5,          # SL más ajustado (scalping)
        'vol_mult':           1.2,          # Volumen importante pero no crítico
        # Parámetros específicos de scalping
        'min_score_scalping': 3,            # Score mínimo más bajo (más señales)
        'max_perdidas_dia':   3,            # Máximo 3 pérdidas consecutivas
    }
}

# ══════════════════════════════════════
# CONTROL ANTI-SPAM Y GESTIÓN DE RIESGO
# ══════════════════════════════════════
alertas_enviadas = {}
ultimo_analisis = {}
perdidas_consecutivas = 0
ultima_senal_timestamp = None


# ══════════════════════════════════════
# INDICADORES TÉCNICOS
# ══════════════════════════════════════
from core.indicators import (calcular_rsi, calcular_atr, calcular_adx,
    patron_envolvente_alcista, patron_envolvente_bajista, patron_doji,
    detectar_stop_hunt_alcista, detectar_stop_hunt_bajista,
    detectar_canal_roto, detectar_precio_en_canal,
)

# ══════════════════════════════════════
# ANÁLISIS PRICE ACTION SCALPING
# ══════════════════════════════════════
def analizar_price_action_scalping(df):
    """Análisis de price action específico para scalping"""
    score = 0
    
    # 1. Momentum de vela actual (más peso en scalping)
    vela_actual = df.iloc[-1]
    vela_anterior = df.iloc[-2]
    
    body_actual = abs(vela_actual['Close'] - vela_actual['Open'])
    body_anterior = abs(vela_anterior['Close'] - vela_anterior['Open'])
    
    # Vela con cuerpo fuerte = momentum claro
    if body_actual > body_anterior * 1.3:
        score += 1
    
    # 2. Precio rompe máximos/mínimos recientes (5 velas)
    max_reciente = df['High'].iloc[-6:-1].max()
    min_reciente = df['Low'].iloc[-6:-1].min()
    
    if vela_actual['Close'] > max_reciente:
        score += 1  # Ruptura alcista
    elif vela_actual['Close'] < min_reciente:
        score += 1  # Ruptura bajista
    
    # 3. Secuencia de velas (3 velas consecutivas en misma dirección)
    ultimas_3 = df.iloc[-3:]
    todas_alcistas = all(ultimas_3['Close'] > ultimas_3['Open'])
    todas_bajistas = all(ultimas_3['Close'] < ultimas_3['Open'])
    
    if todas_alcistas or todas_bajistas:
        score += 1
    
    return score

# ══════════════════════════════════════
# FUNCIÓN PRINCIPAL DE ANÁLISIS
# ══════════════════════════════════════
def en_sesion_activa():
    """Solo operar en sesión London/NY: 07:00-17:00 UTC."""
    from datetime import timezone as tz
    hora_utc = datetime.now(tz.utc).hour
    return 7 <= hora_utc < 17





class GoldDetector15M(BaseDetector):
    def analizar(self, simbolo, params):
        global perdidas_consecutivas, ultima_senal_timestamp

        # ── Aviso calendario económico (no bloquea, solo advierte en el mensaje) ──
        self.aviso_macro = obtener_aviso_macro(45, '15M', simbolo)

        try:
            # Descargar datos 5M y resamplear a 15M (comparte caché con detectores 5M y 1H)
            df_5m, is_delayed = get_ohlcv(params['ticker_yf'], period='7d', interval='5m')
            df = df_5m.resample('15min').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
            if is_delayed:
                logger.warning("  ⚠️  [15M] Datos con 15 min de delay (yfinance free). Señales de entrada pueden estar desfasadas.")
        
            if df.empty or len(df) < 100:
                logger.warning(f"⚠️ Datos insuficientes para {simbolo}")
                return
        
            # Limpiar columnas
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)
        
            # Renombrar columnas solo si es necesario (data_provider ya las normaliza)
            if 'Open' not in df.columns:
                if len(df.columns) == 6:
                    df.columns = ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']
                elif len(df.columns) == 5:
                    df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
                else:
                    logger.warning(f"⚠️ Columnas inesperadas ({len(df.columns)}): {df.columns.tolist()}")
                    return
        
            # Calcular indicadores
            close = df['Close'].iloc[-1]
            vol   = df['Volume'].iloc[-6:].mean()
        
            rsi_len = params['rsi_length']
            rsi = calcular_rsi(df['Close'], rsi_len).iloc[-1]
        
            ema_fast = df['Close'].ewm(span=params['ema_fast_len']).mean()
            ema_slow = df['Close'].ewm(span=params['ema_slow_len']).mean()
            ema_trend = df['Close'].ewm(span=params['ema_trend_len']).mean()
        
            atr_len = params['atr_length']
            _atr_series = calcular_atr(df, atr_len)
            atr = float(_atr_series.iloc[-1])
            atr_media = float(_atr_series.rolling(20).mean().iloc[-1])
        
            adx, _, _ = calcular_adx(df)
            adx = adx.iloc[-1]
        
            # Parámetros de zonas (calculados automáticamente)
            zrl, zrh, zsl, zsh = self.calcular_zonas_sr(df, atr, params['sr_lookback'], params['sr_zone_mult'])
            tol = round(atr * 0.4, 2)   # tolerancia dinámica: 40% del ATR
            logger.info(f"  📍 Zonas auto — Resist: ${zrl:.1f}-${zrh:.1f} | Soporte: ${zsl:.1f}-${zsh:.1f}")
        
            # Detectar zonas
            en_zona_resist = (zrl <= close <= zrh)
            en_zona_soporte = (zsl <= close <= zsh)
            aproximando_resistencia = (zrl - tol <= close < zrl)
            aproximando_soporte = (zsh < close <= zsh + tol)
        
            # ══════════════════════════════════════
            # SCORING SYSTEM - SCALPING (más sensible)
            # ══════════════════════════════════════
            score_sell = 0
            score_buy  = 0
        
            # 1. PRICE ACTION SCALPING (peso importante)
            pa_score = analizar_price_action_scalping(df)
            if df['Close'].iloc[-1] < df['Open'].iloc[-1]:  # Vela bajista
                score_sell += pa_score
            else:
                score_buy += pa_score
        
            # 2. RSI (más sensible en scalping)
            if rsi >= params['rsi_min_sell']:
                score_sell += 2
            elif rsi >= 60:
                score_sell += 1
        
            if rsi <= params['rsi_max_buy']:
                score_buy += 2
            elif rsi <= 40:
                score_buy += 1
        
            # 3. EMAs (cruce rápido = señal)
            if ema_fast.iloc[-1] < ema_slow.iloc[-1]:
                score_sell += 2
                if ema_fast.iloc[-2] >= ema_slow.iloc[-2]:  # Cruce reciente
                    score_sell += 1
        
            if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
                score_buy += 2
                if ema_fast.iloc[-2] <= ema_slow.iloc[-2]:  # Cruce reciente
                    score_buy += 1
        
            # 4. Tendencia general (EMA 50)
            if close < ema_trend.iloc[-1]:
                score_sell += 1
            else:
                score_buy += 1
        
            # 5. Zonas de soporte/resistencia
            if en_zona_resist or aproximando_resistencia:
                score_sell += 2
            if en_zona_soporte or aproximando_soporte:
                score_buy += 2
        
            # 6. ADX (fuerza de tendencia)
            if adx > 25:  # Tendencia fuerte
                if score_sell > score_buy:
                    score_sell += 1
                else:
                    score_buy += 1
        
            # 7. Volumen (confirma movimiento)
            vol_medio = df['Volume'].iloc[-20:].mean()
            if vol > vol_medio * params['vol_mult']:
                if score_sell > score_buy:
                    score_sell += 1
                else:
                    score_buy += 1
        
            # 8. Patrones de velas
            if patron_envolvente_bajista(df):
                score_sell += 2
            if patron_envolvente_alcista(df):
                score_buy += 2

            # 9. Stop Hunt / Falsa Ruptura (patrón de alta fiabilidad en Gold)
            if detectar_stop_hunt_bajista(df):
                score_sell += 3
                logger.info(f"  🎯 [15M] Stop Hunt BAJISTA detectado — +3 pts SELL")
            if detectar_stop_hunt_alcista(df):
                score_buy += 3
                logger.info(f"  🎯 [15M] Stop Hunt ALCISTA detectado — +3 pts BUY")

            # 10. Canal roto / directriz (patrón de rotura 15M) ─────────────
            _lkb15 = params.get('sr_lookback', 200)
            _zm15  = params.get('sr_zone_mult', 1.0)
            canal_alc_roto_15m, canal_baj_roto_15m, \
                linea_sop_canal_15m, linea_res_canal_15m = detectar_canal_roto(
                    df, atr, lookback=_lkb15, wing=3)
            en_resist_canal_baj_15m, en_sop_canal_alc_15m, \
                linea_res_precio_15m, linea_sop_precio_15m = detectar_precio_en_canal(
                    df, atr, lookback=_lkb15, wing=3)

            if canal_alc_roto_15m:
                score_sell += 2
                logger.info(f"  🔻 [15M] CANAL ALCISTA ROTO — línea soporte ${linea_sop_canal_15m:.2f}")
            if canal_baj_roto_15m:
                score_buy += 2
                logger.info(f"  🔺 [15M] CANAL BAJISTA ROTO — línea resist ${linea_res_canal_15m:.2f}")
            if en_resist_canal_baj_15m:
                score_sell += 3
                logger.info(f"  📐 [15M] PRECIO EN DIRECTRIZ BAJISTA — ${linea_res_precio_15m:.2f}")
            if en_sop_canal_alc_15m:
                score_buy += 3
                logger.info(f"  📐 [15M] PRECIO EN DIRECTRIZ ALCISTA — ${linea_sop_precio_15m:.2f}")

            # Score máximo: ~24 puntos
            max_score = 24
        
            # ══════════════════════════════════════
            # NIVELES DE SEÑAL 15M — solo FUERTE llega a Telegram
            # ══════════════════════════════════════
            senal_sell_fuerte = score_sell >= 8
            senal_buy_fuerte  = score_buy  >= 8

            # ── Ajuste por sesgo DXY (correlación inversa Gold/USD) ──
            dxy_bias = get_dxy_bias()
            score_buy, score_sell = ajustar_score_por_dxy(score_buy, score_sell, dxy_bias)

            # ── Filtro de volumen: penalizar señales en velas de bajo volumen ──
            score_sell, score_buy, _vol_bajo = self.ajustar_scores_por_volumen(
                score_sell, score_buy, vol, vol_medio, params['vol_mult'])
            if _vol_bajo:
                logger.info(f"  ⚠️ [15M] Volumen bajo ({vol:.0f} < {vol_medio * params['vol_mult']:.0f}) — scores penalizados -3")

            # Recalcular umbrales tras ajuste DXY y filtro de volumen (con umbral adaptativo)
            _umbral_fue = self.umbral_adaptativo(8, atr, atr_media)
            senal_sell_fuerte = score_sell >= _umbral_fue
            senal_buy_fuerte  = score_buy  >= _umbral_fue

            # Cancelaciones (más estrictas en scalping)
            cancelar_sell = (close < zsh) or (rsi < 30)
            cancelar_buy  = (close > zrh) or (rsi > 70)
        
            # ── SL y TP para SCALPING ──
            asm = params['atr_sl_mult']
            sl_venta  = close + (atr * asm)
            sl_compra = close - (atr * asm)
        
            # Límites de entrada
            offset_pct = params['limit_offset_pct']
            sell_limit = close * (1 + offset_pct / 100)
            buy_limit  = close * (1 - offset_pct / 100)

            tp1_v = round(sell_limit - atr * params['atr_tp1_mult'], 2)
            tp2_v = round(sell_limit - atr * params['atr_tp2_mult'], 2)
            tp3_v = round(sell_limit - atr * params['atr_tp3_mult'], 2)
            tp1_c = round(buy_limit  + atr * params['atr_tp1_mult'], 2)
            tp2_c = round(buy_limit  + atr * params['atr_tp2_mult'], 2)
            tp3_c = round(buy_limit  + atr * params['atr_tp3_mult'], 2)
        
            def rr(limit, sl, tp):
                return round(abs(tp - limit) / abs(sl - limit), 1) if abs(sl - limit) > 0 else 0
        
            # ── Log consola ──
            fecha = df.index[-1].strftime('%Y-%m-%d %H:%M')
        
            # ══════════════════════════════════════
            # VERIFICAR SI YA SE ANALIZÓ
            # ══════════════════════════════════════
            clave_simbolo = simbolo
        
            if clave_simbolo in self.ultimo_analisis:
                ultima_fecha = self.ultimo_analisis[clave_simbolo]['fecha']
                ultimo_score_sell = self.ultimo_analisis[clave_simbolo]['score_sell']
                ultimo_score_buy = self.ultimo_analisis[clave_simbolo]['score_buy']
            
                if (ultima_fecha == fecha and 
                    abs(ultimo_score_sell - score_sell) <= 1 and 
                    abs(ultimo_score_buy - score_buy) <= 1):
                    logger.info(f"  ℹ️  Vela {fecha} ya analizada - Sin cambios")
                    return
        
            self.ultimo_analisis[clave_simbolo] = {
                'fecha': fecha,
                'score_sell': score_sell,
                'score_buy': score_buy
            }
        
            logger.info(f"  📅 Vela:  {fecha}")
            logger.info(f"  💰 Close: {round(close, 2)}")
            logger.info(f"  📊 Score SELL: {score_sell}/{max_score} | Score BUY: {score_buy}/{max_score}")
            logger.info(f"  🔴 SELL Fuerte:{senal_sell_fuerte}  🟢 BUY Fuerte:{senal_buy_fuerte}")
            logger.info(f"  📉 RSI: {round(rsi, 1)} | ADX: {round(adx, 1)} | ATR: {round(atr, 2)}")
        
            # ── CONTROL DE PÉRDIDAS CONSECUTIVAS (consultado desde BD) ──
            perdidas_consecutivas = self.db.contar_perdidas_consecutivas(f"{simbolo}_15M") if self.db else 0
            if perdidas_consecutivas >= params['max_perdidas_dia']:
                if not (senal_sell_fuerte or senal_buy_fuerte):
                    logger.warning(f"  ⛔ Trading pausado: {perdidas_consecutivas} pérdidas consecutivas — esperando señal fuerte")
                    return
                logger.info(f"  ✅ Señal fuerte detectada tras {perdidas_consecutivas} pérdidas — reanudando")
        
            # ══════════════════════════════════════
            # ANTI-SPAM
            # ══════════════════════════════════════
            clave_vela = f"{simbolo}_{fecha}"
        
            def ya_enviada(tipo):
                clave = f"{clave_vela}_{tipo}"
                ts_mem = self.alertas_enviadas.get(clave, 0)
                if ts_mem > time.time() - 172800:
                    return True
                if self.db:
                    ts_db = self.db.get_antispam(clave)
                    if ts_db > time.time() - 172800:
                        self.alertas_enviadas[clave] = ts_db
                        return True
                return False
        
            def marcar_enviada(tipo):
                clave = f"{clave_vela}_{tipo}"
                self.alertas_enviadas[clave] = time.time()
                if self.db:
                    self.db.set_antispam(clave, self.alertas_enviadas[clave])
            
            # ── EXCLUSIÓN MUTUA ──
            if senal_sell_fuerte and senal_buy_fuerte:
                if score_sell >= score_buy:
                    senal_buy_fuerte = False
                    logger.info(f"  ⚖️ Exclusión mutua: BUY suprimida (SELL {score_sell} >= BUY {score_buy})")
                else:
                    senal_sell_fuerte = False
                    logger.info(f"  ⚖️ Exclusión mutua: SELL suprimida (BUY {score_buy} > SELL {score_sell})")

            _sesgo_dir = tf_bias.BIAS_BEARISH if score_sell > score_buy else tf_bias.BIAS_BULLISH if score_buy > score_sell else tf_bias.BIAS_NEUTRAL
            tf_bias.publicar_sesgo(simbolo, '15M', _sesgo_dir, max(score_sell, score_buy))
            _conf_sell = ""; _conf_buy = ""
            if senal_sell_fuerte:
                _ok, _desc = tf_bias.verificar_confluencia(simbolo, '15M', tf_bias.BIAS_BEARISH)
                if not _ok:
                    logger.info(f"  🚫 SELL bloqueada por TF superior: {_desc[:80]}")
                    senal_sell_fuerte = False
                else:
                    _conf_sell = _desc
            if senal_buy_fuerte:
                _ok, _desc = tf_bias.verificar_confluencia(simbolo, '15M', tf_bias.BIAS_BULLISH)
                if not _ok:
                    logger.info(f"  🚫 BUY bloqueada por TF superior: {_desc[:80]}")
                    senal_buy_fuerte = False
                else:
                    _conf_buy = _desc

            # ════════════════════════════════════        # ENVIAR SEÑALES SCALPING
            # ══════════════════════════════════════
        

            # ── FILTRO R:R MÍNIMO 1.5 (Scalping 15M) ──
            RR_MINIMO = 1.5
            rr_sell_tp1 = rr(sell_limit, sl_venta,  tp1_v)
            rr_buy_tp1  = rr(buy_limit,  sl_compra, tp1_c)
            if rr_sell_tp1 < RR_MINIMO:
                logger.warning(f'  ⛔ SELL bloqueada: R:R TP1={rr_sell_tp1} < {RR_MINIMO}')
                cancelar_sell = True
            if rr_buy_tp1 < RR_MINIMO:
                logger.warning(f'  ⛔ BUY bloqueada: R:R TP1={rr_buy_tp1} < {RR_MINIMO}')
                cancelar_buy = True

            simbolo_db = f"{simbolo}_15M"

            # ── SEÑALES VENTA — solo FUERTE ──
            if senal_sell_fuerte and not cancelar_sell:
                if self.db and self.db.existe_senal_activa_tf(simbolo_db):
                    logger.info(f"  ℹ️  SELL 15M bloqueada: ya existe señal ACTIVA en {simbolo_db}")
                else:
                    msg = (f"🔥 SELL FUERTE — <b>GOLD 15M SCALPING</b>\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"💰 <b>Precio:</b>     ${round(close, 2)}\n"
                           f"📌 <b>SELL LIMIT:</b> ${round(sell_limit, 2)}\n"
                           f"🛑 <b>Stop Loss:</b>  ${round(sl_venta, 2)}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"🎯 <b>TP1:</b> ${tp1_v}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                           f"🎯 <b>TP2:</b> ${tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                           f"🎯 <b>TP3:</b> ${tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"📊 <b>Score:</b> {score_sell}/{max_score}  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                           f"⏱️ <b>TF:</b> 15M  📅 {fecha}")
                    if _conf_sell:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_sell}"
                    if self.db:
                        try:
                            self.db.guardar_senal({
                                'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                                'direccion': 'VENTA', 'precio_entrada': sell_limit,
                                'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                                'score': score_sell,
                                'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 1),
                                                           'atr': round(atr, 2)}),
                                'patron_velas': f"Envolvente:{patron_envolvente_bajista(df)}, Doji:{patron_doji(df)}, StopHunt:{detectar_stop_hunt_bajista(df)}",
                                'version_detector': '15M-SCALP-v2.0'
                            })
                        except Exception as e:
                            logger.error(f"  ⚠️ Error guardando señal: {e}")
                    self.enviar(msg)

            # ── SEÑALES COMPRA — solo FUERTE ──
            if senal_buy_fuerte and not cancelar_buy:
                if self.db and self.db.existe_senal_activa_tf(simbolo_db):
                    logger.info(f"  ℹ️  BUY 15M bloqueada: ya existe señal ACTIVA en {simbolo_db}")
                else:
                    msg = (f"🔥 BUY FUERTE — <b>GOLD 15M SCALPING</b>\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"💰 <b>Precio:</b>    ${round(close, 2)}\n"
                           f"📌 <b>BUY LIMIT:</b> ${round(buy_limit, 2)}\n"
                           f"🛑 <b>Stop Loss:</b> ${round(sl_compra, 2)}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"🎯 <b>TP1:</b> ${tp1_c}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                           f"🎯 <b>TP2:</b> ${tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                           f"🎯 <b>TP3:</b> ${tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"📊 <b>Score:</b> {score_buy}/{max_score}  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                           f"⏱️ <b>TF:</b> 15M  📅 {fecha}")
                    if _conf_buy:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_buy}"
                    if self.db:
                        try:
                            self.db.guardar_senal({
                                'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                                'direccion': 'COMPRA', 'precio_entrada': buy_limit,
                                'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                                'score': score_buy,
                                'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 1),
                                                           'atr': round(atr, 2)}),
                                'patron_velas': f"Envolvente:{patron_envolvente_alcista(df)}, Doji:{patron_doji(df)}, StopHunt:{detectar_stop_hunt_alcista(df)}",
                                'version_detector': '15M-SCALP-v2.0'
                            })
                        except Exception as e:
                            logger.error(f"  ⚠️ Error guardando señal: {e}")
                    self.enviar(msg)
    
        except Exception as e:
            logger.error(f"❌ Error analizando {simbolo}: {e}")



def analizar_simbolo(simbolo, params):
    return GoldDetector15M(simbolo=simbolo, tf_label='15M', params=params, telegram_thread_id=TELEGRAM_THREAD_ID).analizar(simbolo, params)


def main():
    """Función principal para ejecutar el detector."""
    enviar_telegram("🚀 <b>Detector GOLD 15M SCALPING iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "⏱️  Análisis cada 2 minutos\n"
                    "⚡ Optimizado para operaciones rápidas\n"
                    "📊 Score mínimo: 3/15")
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
        logger.info(f"[{ahora_utc.strftime('%Y-%m-%d %H:%M:%S')}] 🔄 CICLO #{ciclo} - GOLD 15M SCALPING")
        for simbolo, params in SIMBOLOS.items():
            analizar_simbolo(simbolo, params)
        logger.info(f"⏳ Esperando {CHECK_INTERVAL // 60} minutos...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

