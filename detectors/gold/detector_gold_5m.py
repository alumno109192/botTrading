"""
DETECTOR GOLD 5M - MICRO-SCALPING
Análisis de XAUUSD en timeframe 5 minutos para operaciones ultra-rápidas
Confluencia obligatoria con 1D + 4H + 1H + 15M (sesgo multi-TF)
"""
import os
from services import tf_bias
from services.dxy_bias import get_dxy_bias, ajustar_score_por_dxy
from services.cot_bias import get_cot_bias, ajustar_score_por_cot
from services.open_interest import get_oi_bias, ajustar_score_por_oi
from services.economic_calendar import obtener_aviso_macro
from adapters.data_provider import get_ohlcv
import yfinance as yf
import pandas as pd
import numpy as np
import requests
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
TELEGRAM_TOKEN     = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_THREAD_ID = int(os.environ.get('THREAD_ID_SCALPING') or 0) or None

CHECK_INTERVAL = 60  # cada 1 minuto (micro-scalping máxima frecuencia)

# ══════════════════════════════════════
# PARÁMETROS — MICRO-SCALP GOLD 5M
# ══════════════════════════════════════
SIMBOLOS = {
    'XAUUSD': {
        'ticker_yf':          'GC=F',
        # Zonas S/R calculadas automáticamente — sin mantenimiento manual
        'sr_lookback':        100,          # 100 velas 5M ≈ 8 horas de historia
        'sr_zone_mult':       0.8,          # ancho de zona = atr × 0.8
        # TPs calculados automáticamente como múltiplo de ATR — sin mantenimiento manual
        'atr_tp1_mult':       1.0,          # TP1: 1.0× ATR 5M (~$15-25 desde entry)
        'atr_tp2_mult':       2.0,          # TP2: 2.0× ATR
        'atr_tp3_mult':       3.0,          # TP3: 3.0× ATR (objetivo micro-scalp amplio)
        'limit_offset_pct':   0.08,         # Offset mínimo (micro-scalp)
        'anticipar_velas':    2,
        'cancelar_dist':      1.0,
        'rsi_length':         7,            # RSI ultra-rápido
        'rsi_min_sell':       68.0,
        'rsi_max_buy':        32.0,
        'ema_fast_len':       3,            # EMAs ultra-rápidas
        'ema_slow_len':       8,
        'ema_trend_len':      21,
        'atr_length':         7,
        'atr_sl_mult':        1.0,          # SL muy ajustado
        'vol_mult':           1.3,
        'min_score_scalping': 3,
        'max_perdidas_dia':   3,
    }
}

# ══════════════════════════════════════
# CONTROL ANTI-SPAM
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
    detectar_ruptura_soporte_horizontal, detectar_ruptura_resistencia_horizontal,
    detectar_retest_resistencia, detectar_retest_soporte,
    detectar_rechazo_en_directriz,
    detectar_cuña_descendente, detectar_cuña_ascendente,
)



def analizar_price_action(df):
    score = 0
    vela = df.iloc[-1]; vela_ant = df.iloc[-2]
    body = abs(vela['Close'] - vela['Open'])
    body_ant = abs(vela_ant['Close'] - vela_ant['Open'])
    if body > body_ant * 1.3:
        score += 1
    max_rec = df['High'].iloc[-6:-1].max()
    min_rec = df['Low'].iloc[-6:-1].min()
    if vela['Close'] > max_rec or vela['Close'] < min_rec:
        score += 1
    ultimas_3 = df.iloc[-3:]
    if all(ultimas_3['Close'] > ultimas_3['Open']) or all(ultimas_3['Close'] < ultimas_3['Open']):
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


class GoldDetector5M(BaseDetector):
    def analizar(self, simbolo, params):
        global perdidas_consecutivas, ultima_senal_timestamp

        # ── Aviso calendario económico (no bloquea, solo advierte en el mensaje) ──
        self.aviso_macro = obtener_aviso_macro(30, '5M', simbolo)

        try:
            df, is_delayed = get_ohlcv(params['ticker_yf'], period='7d', interval='5m')
            if is_delayed:
                logger.warning("  ⚠️  [5M] Datos con 15 min de delay (yfinance free). Entradas de scalping pueden estar desfasadas.")

            if df.empty or len(df) < 50:
                logger.warning(f"⚠️ Datos insuficientes para {simbolo} 5M")
                return

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

            close = df['Close'].iloc[-1]

            rsi_len = params['rsi_length']
            rsi = calcular_rsi(df['Close'], rsi_len).iloc[-1]

            ema_fast  = df['Close'].ewm(span=params['ema_fast_len']).mean()
            ema_slow  = df['Close'].ewm(span=params['ema_slow_len']).mean()
            ema_trend = df['Close'].ewm(span=params['ema_trend_len']).mean()

            atr_len = params['atr_length']
            _atr_series = calcular_atr(df, atr_len)
            atr = float(_atr_series.iloc[-1])
            atr_media = float(_atr_series.rolling(20).mean().iloc[-1])
            adx, _, _ = calcular_adx(df)
            adx = adx.iloc[-1]

            zrl, zrh, zsl, zsh = self.calcular_zonas_sr(df, atr, params['sr_lookback'], params['sr_zone_mult'])
            tol = round(atr * 0.4, 2)   # tolerancia dinámica: 40% del ATR
            logger.info(f"  📍 Zonas auto — Resist: ${zrl:.1f}-${zrh:.1f} | Soporte: ${zsl:.1f}-${zsh:.1f}")

            en_zona_resist       = (zrl <= close <= zrh)
            en_zona_soporte      = (zsl <= close <= zsh)
            aproximando_resist   = (zrl - tol <= close < zrl)
            aproximando_soporte  = (zsh < close <= zsh + tol)

            # ── SCORING ──────────────────────────────────────
            score_sell = 0; score_buy = 0
            pa = analizar_price_action(df)
            if df['Close'].iloc[-1] < df['Open'].iloc[-1]:
                score_sell += pa
            else:
                score_buy += pa

            if rsi >= params['rsi_min_sell']:
                score_sell += 2
            elif rsi >= 62:
                score_sell += 1
            if rsi <= params['rsi_max_buy']:
                score_buy += 2
            elif rsi <= 38:
                score_buy += 1

            if ema_fast.iloc[-1] < ema_slow.iloc[-1]:
                score_sell += 2
                if ema_fast.iloc[-2] >= ema_slow.iloc[-2]:
                    score_sell += 1
            if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
                score_buy += 2
                if ema_fast.iloc[-2] <= ema_slow.iloc[-2]:
                    score_buy += 1

            if close < ema_trend.iloc[-1]:
                score_sell += 1
            else:
                score_buy += 1

            if en_zona_resist or aproximando_resist:
                score_sell += 2
            if en_zona_soporte or aproximando_soporte:
                score_buy += 2

            if adx > 25:
                if score_sell >= score_buy:
                    score_sell += 1
                else:
                    score_buy += 1

            vol_medio = df['Volume'].iloc[-20:].mean()
            vol_actual = df['Volume'].iloc[-6:].mean()
            if vol_actual > vol_medio * params['vol_mult']:
                if score_sell >= score_buy:
                    score_sell += 1
                else:
                    score_buy += 1

            if patron_envolvente_bajista(df):
                score_sell += 2
            if patron_envolvente_alcista(df):
                score_buy += 2

            # Stop Hunt / Falsa Ruptura (patrón de alta fiabilidad en Gold)
            if detectar_stop_hunt_bajista(df):
                score_sell += 3
                logger.info(f"  🎯 [5M] Stop Hunt BAJISTA detectado — +3 pts SELL")
            if detectar_stop_hunt_alcista(df):
                score_buy += 3
                logger.info(f"  🎯 [5M] Stop Hunt ALCISTA detectado — +3 pts BUY")

            # ── Canal roto / directriz (patrón de rotura 5M) ──────────────────
            _lkb5 = params.get('sr_lookback', 100)
            _zm5  = params.get('sr_zone_mult', 0.8)
            canal_alc_roto_5m, canal_baj_roto_5m, \
                linea_sop_canal_5m, linea_res_canal_5m = detectar_canal_roto(
                    df, atr, lookback=_lkb5, wing=3)
            en_resist_canal_baj_5m, en_sop_canal_alc_5m, \
                linea_res_precio_5m, linea_sop_precio_5m = detectar_precio_en_canal(
                    df, atr, lookback=_lkb5, wing=3)

            if canal_alc_roto_5m:
                score_sell += 2
                logger.info(f"  🔻 [5M] CANAL ALCISTA ROTO — línea soporte ${linea_sop_canal_5m:.2f}")
            if canal_baj_roto_5m:
                score_buy += 2
                logger.info(f"  🔺 [5M] CANAL BAJISTA ROTO — línea resist ${linea_res_canal_5m:.2f}")
            if en_resist_canal_baj_5m:
                score_sell += 3
                logger.info(f"  📐 [5M] PRECIO EN DIRECTRIZ BAJISTA — ${linea_res_precio_5m:.2f}")
            if en_sop_canal_alc_5m:
                score_buy += 3
                logger.info(f"  📐 [5M] PRECIO EN DIRECTRIZ ALCISTA — ${linea_sop_precio_5m:.2f}")

            # ── Ruptura horizontal directa (sin retest) 5M ─────────────────
            _lkb5_h = params.get('sr_lookback', 100)
            _rup_sop_5m, _niv_sop_5m = detectar_ruptura_soporte_horizontal(
                df, atr, lookback=_lkb5_h, wing=3)
            _rup_res_5m, _niv_res_5m = detectar_ruptura_resistencia_horizontal(
                df, atr, lookback=_lkb5_h, wing=3)
            if _rup_sop_5m:
                score_sell += 4
                logger.info(f"  💥 [5M] RUPTURA SOPORTE ${_niv_sop_5m:.2f} — +4 pts SELL")
            if _rup_res_5m:
                score_buy += 4
                logger.info(f"  💥 [5M] RUPTURA RESISTENCIA ${_niv_res_5m:.2f} — +4 pts BUY")

            # ── Retest soporte→resistencia / resistencia→soporte (5M) ───────────
            _retest_res_5m, _niv_retest_res_5m = detectar_retest_resistencia(
                df, atr, lookback=_lkb5_h, wing=3)
            _retest_sop_5m, _niv_retest_sop_5m = detectar_retest_soporte(
                df, atr, lookback=_lkb5_h, wing=3)
            if _retest_res_5m:
                score_sell += 5
                logger.info(f"  🔁 [5M] RETEST RESISTENCIA ${_niv_retest_res_5m:.2f} — +5 pts SELL")
            if _retest_sop_5m:
                score_buy += 5
                logger.info(f"  🔁 [5M] RETEST SOPORTE ${_niv_retest_sop_5m:.2f} — +5 pts BUY")

            # ── Rechazo en directriz (5M) ────────────────────────────────────────
            _rec_dir_baj_5m, _precio_dir_baj_5m = detectar_rechazo_en_directriz(
                df, atr, lookback=_lkb5_h, wing=3, direccion='bajista')
            _rec_dir_alc_5m, _precio_dir_alc_5m = detectar_rechazo_en_directriz(
                df, atr, lookback=_lkb5_h, wing=3, direccion='alcista')
            if _rec_dir_baj_5m:
                score_sell += 4
                logger.info(f"  📐 [5M] RECHAZO EN DIRECTRIZ BAJISTA ${_precio_dir_baj_5m:.2f} — +4 pts SELL")
            if _rec_dir_alc_5m:
                score_buy += 4
                logger.info(f"  📐 [5M] RECHAZO EN DIRECTRIZ ALCISTA ${_precio_dir_alc_5m:.2f} — +4 pts BUY")

            # ── Cuña descendente / ascendente (5M) ──────────────────────────────
            # wing=2 y amplitud laxa (TF corto tiene más ruido que 4H)
            _cuña_desc_5m, _t_desc_5m, _s_desc_5m = detectar_cuña_descendente(
                df, atr, lookback=_lkb5_h, wing=2, max_amplitud_pct=0.035)
            _cuña_asc_5m, _t_asc_5m, _s_asc_5m = detectar_cuña_ascendente(
                df, atr, lookback=_lkb5_h, wing=2, max_amplitud_pct=0.035)
            if _cuña_desc_5m == 'ruptura_alcista':
                score_buy += 5
                logger.info(f"  📐 [5M] CUÑA DESC ROTA AL ALZA (techo ${_t_desc_5m:.2f}) — +5 pts BUY")
            elif _cuña_desc_5m == 'ruptura_bajista':
                score_sell += 5
                logger.info(f"  📐 [5M] CUÑA DESC ROTA A LA BAJA (suelo ${_s_desc_5m:.2f}) — +5 pts SELL")
            elif _cuña_desc_5m == 'compresion':
                score_buy += 2
                logger.info(f"  📐 [5M] CUÑA DESC en compresión ${_s_desc_5m:.2f}-${_t_desc_5m:.2f} — +2 pts BUY")
            if _cuña_asc_5m == 'ruptura_bajista':
                score_sell += 5
                logger.info(f"  📐 [5M] CUÑA ASC ROTA A LA BAJA (suelo ${_s_asc_5m:.2f}) — +5 pts SELL")
            elif _cuña_asc_5m == 'compresion':
                score_sell += 2
                logger.info(f"  📐 [5M] CUÑA ASC en compresión ${_s_asc_5m:.2f}-${_t_asc_5m:.2f} — +2 pts SELL")

            # ── Confirmación 1M — "la puntilla" ─────────────────────────────────
            # Solo se consulta si estamos en zona de desempate (score cerca del umbral)
            # Evita llamadas innecesarias a la API y mantiene el intervalo bajo
            _umbral_conf = 8  # igual que _umbral_fue antes del ajuste DXY/vol
            _necesita_conf_sell = 4 <= score_sell < _umbral_conf
            _necesita_conf_buy  = 4 <= score_buy  < _umbral_conf
            if _necesita_conf_sell or _necesita_conf_buy:
                try:
                    df_1m, _ = get_ohlcv(params['ticker_yf'], period='1d', interval='1m')
                    if df_1m is not None and len(df_1m) >= 10:
                        atr_1m = float(calcular_atr(df_1m, 7).iloc[-1])
                        # SELL: envolvente bajista o stop hunt bajista en 1M
                        if _necesita_conf_sell:
                            _env_baj_1m  = patron_envolvente_bajista(df_1m)
                            _sh_baj_1m   = detectar_stop_hunt_bajista(df_1m, atr_1m)
                            _rej_dir_1m  = detectar_rechazo_en_directriz(
                                df_1m, atr_1m, lookback=60, wing=2, direccion='bajista')[0]
                            if _env_baj_1m or _sh_baj_1m or _rej_dir_1m:
                                score_sell += 2
                                motivo = ('envolvente' if _env_baj_1m
                                          else 'stop hunt' if _sh_baj_1m else 'directriz')
                                logger.info(f"  🎯 [1M] Confirmación SELL ({motivo}) — +2 pts SELL")
                        # BUY: envolvente alcista o stop hunt alcista en 1M
                        if _necesita_conf_buy:
                            _env_alc_1m  = patron_envolvente_alcista(df_1m)
                            _sh_alc_1m   = detectar_stop_hunt_alcista(df_1m, atr_1m)
                            _rej_dir_1m_b = detectar_rechazo_en_directriz(
                                df_1m, atr_1m, lookback=60, wing=2, direccion='alcista')[0]
                            if _env_alc_1m or _sh_alc_1m or _rej_dir_1m_b:
                                score_buy += 2
                                motivo = ('envolvente' if _env_alc_1m
                                          else 'stop hunt' if _sh_alc_1m else 'directriz')
                                logger.info(f"  🎯 [1M] Confirmación BUY ({motivo}) — +2 pts BUY")
                except Exception as _e_1m:
                    logger.debug(f"  [1M] No se pudo obtener confirmación: {_e_1m}")

            max_score = 30  # +2 posibles del confirmador 1M

            # Umbrales 5M — solo FUERTE llega a Telegram
            senal_sell_fuerte = score_sell >= 8
            senal_buy_fuerte  = score_buy  >= 8

            # ── Ajuste por sesgo DXY (correlación inversa Gold/USD) ──
            dxy_bias = get_dxy_bias()
            score_buy, score_sell = ajustar_score_por_dxy(score_buy, score_sell, dxy_bias)

            # ── Ajuste por COT Report (posiciones institucionales semanales) ──
            _cot_bias, _cot_ratio = get_cot_bias()
            score_buy, score_sell = ajustar_score_por_cot(score_buy, score_sell, _cot_bias)

            # ── Ajuste por Open Interest / Volumen (fuerza de tendencia) ──
            _oi_bias = get_oi_bias()
            score_buy, score_sell = ajustar_score_por_oi(score_buy, score_sell, _oi_bias)

            # ── Filtro de volumen: penalizar señales en velas de bajo volumen ──
            score_sell, score_buy, _vol_bajo = self.ajustar_scores_por_volumen(
                score_sell, score_buy, vol_actual, vol_medio, params['vol_mult'])
            if _vol_bajo:
                logger.info(f"  ⚠️ [5M] Volumen bajo ({vol_actual:.0f} < {vol_medio * params['vol_mult']:.0f}) — scores penalizados -3")

            # Recalcular umbrales tras ajuste DXY y filtro de volumen (con umbral adaptativo)
            _umbral_fue = self.umbral_adaptativo(8, atr, atr_media)
            senal_sell_fuerte = score_sell >= _umbral_fue
            senal_buy_fuerte  = score_buy  >= _umbral_fue

            # ── Filtro de sesión: fuera de 08-21 UTC bloquear todo (TF corto = ruido nocturno) ──
            if not self.en_sesion_optima():
                logger.info(f"  🌙 [5M] Fuera sesión óptima — señales suprimidas (08-21 UTC)")
                senal_sell_fuerte = False
                senal_buy_fuerte  = False

            cancelar_sell = (close < zsl) or (rsi < 28)
            cancelar_buy  = (close > zrh) or (rsi > 72)

            asm = params['atr_sl_mult']
            sl_venta  = close + (atr * asm)
            sl_compra = close - (atr * asm)

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

            fecha = df.index[-1].strftime('%Y-%m-%d %H:%M')

            # ── LOG CONSOLA ─────────────────────────────────
            if simbolo in self.ultimo_analisis:
                ul = self.ultimo_analisis[simbolo]
                if (ul['fecha'] == fecha and
                        abs(ul['score_sell'] - score_sell) <= 1 and
                        abs(ul['score_buy'] - score_buy) <= 1):
                    logger.info(f"  ℹ️  Vela {fecha} ya analizada — sin cambios")
                    return

            self.ultimo_analisis[simbolo] = {'fecha': fecha, 'score_sell': score_sell, 'score_buy': score_buy}

            logger.info(f"  📅 {fecha}  💰 Close: {round(close, 2)}")
            logger.info(f"  🔴 SELL {score_sell}/{max_score} | 🟢 BUY {score_buy}/{max_score}")
            logger.info(f"  📉 RSI: {round(rsi, 1)} | ADX: {round(adx, 1)} | ATR: {round(atr, 2)}")

            # ── PÉRDIDAS CONSECUTIVAS ────────────────────────
            if perdidas_consecutivas >= params['max_perdidas_dia']:
                logger.warning(f"  ⛔ Trading pausado: {perdidas_consecutivas} pérdidas consecutivas")
                if not (senal_sell_fuerte or senal_buy_fuerte):
                    return
                perdidas_consecutivas = 0

            # ── ANTI-SPAM ────────────────────────────────────
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
            tf_bias.publicar_sesgo(simbolo, '5M', _sesgo_dir, max(score_sell, score_buy))
            _conf_sell = ""; _conf_buy = ""

            if senal_sell_fuerte:
                _ok, _desc = tf_bias.verificar_confluencia(simbolo, '5M', tf_bias.BIAS_BEARISH)
                if not _ok:
                    logger.info(f"  🚫 SELL bloqueada por TF superior: {_desc[:80]}")
                    senal_sell_fuerte = False
                else:
                    _conf_sell = _desc
            if senal_buy_fuerte:
                _ok, _desc = tf_bias.verificar_confluencia(simbolo, '5M', tf_bias.BIAS_BULLISH)
                if not _ok:
                    logger.info(f"  🚫 BUY bloqueada por TF superior: {_desc[:80]}")
                    senal_buy_fuerte = False
                else:
                    _conf_buy = _desc

            # ── MODO CAZA: activar si hay zona activa del 1H ───────────────────────────
            # El 1H publicó zona activa (score≥5). Si el precio está dentro
            # y score_5M≥4, disparamos con umbral reducido y precio 5M.
            _zona_1h_buy  = tf_bias.obtener_zona_activa(simbolo, tf_bias.BIAS_BULLISH)
            _zona_1h_sell = tf_bias.obtener_zona_activa(simbolo, tf_bias.BIAS_BEARISH)
            _modo_caza_buy  = False
            _modo_caza_sell = False
            _UMBRAL_CAZA    = 4   # mitad del umbral normal — el 1H ya confirmó

            if _zona_1h_buy and not senal_buy_fuerte and self.en_sesion_optima():
                _tol_1h = _zona_1h_buy['atr'] * 0.6
                _en_zona_1h = (_zona_1h_buy['zsl'] - _tol_1h <= close <= _zona_1h_buy['zsh'] + _tol_1h)
                if _en_zona_1h and score_buy >= _UMBRAL_CAZA:
                    senal_buy_fuerte = True
                    _modo_caza_buy   = True
                    _conf_buy = (f"⚡ <b>Setup 1H / Entrada 5M</b>\n"
                                 f"📍 Zona 1H soporte: ${_zona_1h_buy['zsl']:.2f}–${_zona_1h_buy['zsh']:.2f} | Score 1H: {_zona_1h_buy['score_1h']}/21\n"
                                 f"📌 Limit 1H: ${_zona_1h_buy['buy_limit']:.2f} → ajustado 5M: ${buy_limit:.2f}")
                    logger.info(f"  🎯 [5M] MODO CAZA BUY — zona 1H activa (score {score_buy}) → señal activada")

            if _zona_1h_sell and not senal_sell_fuerte and self.en_sesion_optima():
                _tol_1h = _zona_1h_sell['atr'] * 0.6
                _en_zona_1h = (_zona_1h_sell['zrl'] - _tol_1h <= close <= _zona_1h_sell['zrh'] + _tol_1h)
                if _en_zona_1h and score_sell >= _UMBRAL_CAZA:
                    senal_sell_fuerte = True
                    _modo_caza_sell   = True
                    _conf_sell = (f"⚡ <b>Setup 1H / Entrada 5M</b>\n"
                                  f"📍 Zona 1H resistencia: ${_zona_1h_sell['zrl']:.2f}–${_zona_1h_sell['zrh']:.2f} | Score 1H: {_zona_1h_sell['score_1h']}/21\n"
                                  f"📌 Limit 1H: ${_zona_1h_sell['sell_limit']:.2f} → ajustado 5M: ${sell_limit:.2f}")
                    logger.info(f"  🎯 [5M] MODO CAZA SELL — zona 1H activa (score {score_sell}) → señal activada")

            # Títulos dinámicos según modo
            _titulo_sell = ("⚡ ENTRADA PRECISA — <b>Setup 1H / Entrada 5M</b>"
                            if _modo_caza_sell else "🔥 SELL FUERTE — <b>GOLD 5M MICRO-SCALP</b>")
            _titulo_buy  = ("⚡ ENTRADA PRECISA — <b>Setup 1H / Entrada 5M</b>"
                            if _modo_caza_buy  else "🔥 BUY FUERTE — <b>GOLD 5M MICRO-SCALP</b>")

            # ── FILTRO R:R MÍNIMO 1.5 (Micro-Scalp 5M) ──
            RR_MINIMO = 1.5
            rr_sell_tp1 = rr(sell_limit, sl_venta,  tp1_v)
            rr_buy_tp1  = rr(buy_limit,  sl_compra, tp1_c)
            if rr_sell_tp1 < RR_MINIMO:
                logger.warning(f'  ⛔ SELL bloqueada: R:R TP1={rr_sell_tp1} < {RR_MINIMO}')
                cancelar_sell = True
            if rr_buy_tp1 < RR_MINIMO:
                logger.warning(f'  ⛔ BUY bloqueada: R:R TP1={rr_buy_tp1} < {RR_MINIMO}')
                cancelar_buy = True

            simbolo_db = f"{simbolo}_5M"

            # ── SEÑALES VENTA — solo FUERTE ──
            if senal_sell_fuerte and not cancelar_sell:
                if self.db and self.db.existe_senal_activa_tf(simbolo_db):
                    logger.info(f"  ℹ️  SELL 5M bloqueada: ya existe señal ACTIVA en {simbolo_db}")
                else:
                    msg = (f"{_titulo_sell}\n"
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
                           f"⏱️ <b>TF:</b> 5M  📅 {fecha}\n"
                           f"🔒 MICRO-SCALP — Cerrar máx 30 min")
                    if _conf_sell:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_sell}"
                    if self.db:
                        try:
                            self._guardar_senal({
                                'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                                'direccion': 'VENTA', 'precio_entrada': sell_limit,
                                'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                                'score': score_sell,
                                'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 1),
                                                           'atr': round(atr, 2)}),
                                'patron_velas': f"Envolvente:{patron_envolvente_bajista(df)}, Doji:{patron_doji(df)}, StopHunt:{detectar_stop_hunt_bajista(df)}",
                                'version_detector': '5M-MICRO-v2.0'
                            })
                        except Exception as e:
                            logger.error(f"  ⚠️ Error guardando señal: {e}")
                    self.enviar(msg)

            # ── SEÑALES COMPRA — solo FUERTE ──
            if senal_buy_fuerte and not cancelar_buy:
                if self.db and self.db.existe_senal_activa_tf(simbolo_db):
                    logger.info(f"  ℹ️  BUY 5M bloqueada: ya existe señal ACTIVA en {simbolo_db}")
                else:
                    msg = (f"{_titulo_buy}\n"
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
                           f"⏱️ <b>TF:</b> 5M  📅 {fecha}\n"
                           f"🔒 MICRO-SCALP — Cerrar máx 30 min")
                    if _conf_buy:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_buy}"
                    if self.db:
                        try:
                            self._guardar_senal({
                                'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                                'direccion': 'COMPRA', 'precio_entrada': buy_limit,
                                'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                                'score': score_buy,
                                'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 1),
                                                           'atr': round(atr, 2)}),
                                'patron_velas': f"Envolvente:{patron_envolvente_alcista(df)}, Doji:{patron_doji(df)}, StopHunt:{detectar_stop_hunt_alcista(df)}",
                                'version_detector': '5M-MICRO-v2.0'
                            })
                        except Exception as e:
                            logger.error(f"  ⚠️ Error guardando señal: {e}")
                    self.enviar(msg)

        except Exception as e:
            logger.error(f"❌ Error analizando {simbolo} [5M]: {e}")




def analizar_simbolo(simbolo, params):
    return GoldDetector5M(simbolo=simbolo, tf_label='5M', params=params, telegram_thread_id=TELEGRAM_THREAD_ID).analizar(simbolo, params)


def main():
    """Función principal para ejecutar el detector."""
    global perdidas_consecutivas
    enviar_telegram("🚀 <b>Detector GOLD 5M MICRO-SCALP iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "⏱️  Análisis cada 1 minuto\n"
                    "⚡ Confluencia 1D + 4H + 1H + 15M\n"
                    "🔒 Operaciones máx 30 min")
    try:
        from adapters.database import get_db as _get_db
        _db = _get_db()
        if _db:
            perdidas_consecutivas = _db.contar_perdidas_consecutivas('XAUUSD_5M')
            logger.info(f"📊 [5M] Pérdidas consecutivas cargadas desde BD: {perdidas_consecutivas}")
    except Exception:
        pass
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
        logger.info(f"\n[{ahora_utc.strftime('%Y-%m-%d %H:%M:%S')}] 🔄 CICLO #{ciclo} — GOLD 5M MICRO-SCALP")
        for simbolo, params in SIMBOLOS.items():
            analizar_simbolo(simbolo, params)
        logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Ciclo #{ciclo} completado — esperando {CHECK_INTERVAL}s")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

