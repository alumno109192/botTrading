"""
DETECTOR EUR/USD 5M - MICRO-SCALPING
Análisis de EURUSD en timeframe 5 minutos para operaciones ultra-rápidas.
Misma arquitectura que detector_gold_5m pero calibrado para la escala de pips
del EUR/USD (~0.0001 por pip). Sesión activa: 07:00-21:00 UTC.
"""
import os
import json
import time
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

from services import tf_bias
from services.economic_calendar import (
    obtener_aviso_macro, debe_bloquear_trading,
    enviar_alerta_bloqueo, verificar_y_notificar_reanudacion,
)
from adapters.data_provider import get_ohlcv
from adapters.database import get_db
from core.base_detector import BaseDetector
from core.indicators import (
    calcular_rsi, calcular_atr, calcular_adx,
    patron_envolvente_alcista, patron_envolvente_bajista, patron_doji,
    detectar_stop_hunt_alcista, detectar_stop_hunt_bajista,
    detectar_canal_roto, detectar_precio_en_canal,
    detectar_ruptura_soporte_horizontal, detectar_ruptura_resistencia_horizontal,
    detectar_retest_resistencia, detectar_retest_soporte,
    detectar_rechazo_en_directriz,
    detectar_cuña_descendente, detectar_cuña_ascendente,
    detectar_doble_techo, detectar_doble_suelo,
    detectar_v_reversal_alcista, detectar_v_reversal_bajista,
    calcular_aceleracion_rsi, calcular_micro_volatilidad, calcular_momentum_reciente,
)
import pandas as pd

logger = logging.getLogger('bottrading')

# ══════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════
TELEGRAM_THREAD_ID = int(os.environ.get('THREAD_ID_SCALPING') or 0) or None
CHECK_INTERVAL     = 60   # segundos entre análisis (micro-scalping)

# ══════════════════════════════════════
# PARÁMETROS — MICRO-SCALP EUR/USD 5M
# ══════════════════════════════════════
SIMBOLOS = {
    'EURUSD': {
        'ticker_yf':          'EURUSD=X',
        'sr_lookback':        80,
        'sr_zone_mult':       0.6,
        'atr_tp1_mult':       1.5,
        'atr_tp2_mult':       2.5,
        'atr_tp3_mult':       3.5,
        'atr_min':            0.0003,     # ATR mínimo: 3 pips (evitar baja volatilidad)
        'limit_offset_pct':   0.003,      # offset 0.003% (~0.3 pips, mínimo en EUR/USD)
        'rsi_length':         7,
        'rsi_min_sell':       65.0,
        'rsi_max_buy':        35.0,
        'ema_fast_len':       3,
        'ema_slow_len':       8,
        'ema_trend_len':      21,
        'atr_length':         7,
        'atr_sl_mult':        1.2,
        'vol_mult':           1.2,
        'min_score_scalping': 3,
        'max_perdidas_dia':   3,
    }
}


def analizar_price_action(df):
    score = 0
    vela = df.iloc[-1]
    vela_ant = df.iloc[-2]
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


class EURUSDDetector5M(BaseDetector):
    def analizar(self, simbolo, params):

        # ── Bloqueo por eventos críticos (BCE, Fed, NFP, CPI) ──
        bloqueado, desc_evento, minutos = debe_bloquear_trading(90)
        if bloqueado:
            logger.warning(f"  🚫 [EURUSD 5M] Trading bloqueado — {desc_evento} en {minutos} min")
            enviar_alerta_bloqueo(desc_evento, minutos, ['EURUSD 5M'])
            return

        self.aviso_macro = obtener_aviso_macro(30, '5M', simbolo)

        try:
            df, is_delayed = get_ohlcv(params['ticker_yf'], period='7d', interval='5m')

            if df is None or df.empty or len(df) < 50:
                logger.warning(f"⚠️ Datos insuficientes para {simbolo} EURUSD 5M")
                return

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)

            close = df['Close'].iloc[-1]

            rsi_series = calcular_rsi(df['Close'], params['rsi_length'])
            rsi = rsi_series.iloc[-1]

            ema_fast  = df['Close'].ewm(span=params['ema_fast_len']).mean()
            ema_slow  = df['Close'].ewm(span=params['ema_slow_len']).mean()
            ema_trend = df['Close'].ewm(span=params['ema_trend_len']).mean()

            _atr_series = calcular_atr(df, params['atr_length'])
            atr       = float(_atr_series.iloc[-1])
            atr_media = float(_atr_series.rolling(20).mean().iloc[-1])
            adx, _, _ = calcular_adx(df)
            adx = adx.iloc[-1]

            zrl, zrh, zsl, zsh = self.calcular_zonas_sr(
                df, atr, params['sr_lookback'], params['sr_zone_mult'])
            tol = round(atr * 0.4, 6)

            en_zona_resist      = (zrl <= close <= zrh)
            en_zona_soporte     = (zsl <= close <= zsh)
            aproximando_resist  = (zrl - tol <= close < zrl)
            aproximando_soporte = (zsh < close <= zsh + tol)

            # ── SCORING ──────────────────────────────────────────────────────
            score_sell = 0
            score_buy  = 0

            pa = analizar_price_action(df)
            if df['Close'].iloc[-1] < df['Open'].iloc[-1]:
                score_sell += pa
            else:
                score_buy += pa

            _rsi_baj_3, _rsi_sub_3 = calcular_aceleracion_rsi(rsi_series)
            _micro_vol    = calcular_micro_volatilidad(df)
            _momentum_rec = calcular_momentum_reciente(df)

            if rsi >= params['rsi_min_sell']:
                score_sell += 2
                if _rsi_baj_3:
                    score_sell += 1
            elif rsi >= 60:
                score_sell += 1
            if rsi <= params['rsi_max_buy']:
                score_buy += 2
                if _rsi_sub_3:
                    score_buy += 1
            elif rsi <= 40:
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

            vol_medio  = df['Volume'].iloc[-20:].mean()
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

            if detectar_stop_hunt_bajista(df):
                score_sell += 3
                logger.info("  🎯 [EURUSD 5M] Stop Hunt BAJISTA — +3 SELL")
            if detectar_stop_hunt_alcista(df):
                score_buy += 3
                logger.info("  🎯 [EURUSD 5M] Stop Hunt ALCISTA — +3 BUY")

            _lkb = params['sr_lookback']
            canal_alc_roto, canal_baj_roto, linea_sop, linea_res = detectar_canal_roto(df, atr, lookback=_lkb, wing=3)
            en_resist_canal, en_sop_canal, l_res_p, l_sop_p = detectar_precio_en_canal(df, atr, lookback=_lkb, wing=3)

            if canal_alc_roto:
                score_sell += 2
            if canal_baj_roto:
                score_buy += 2
            if en_resist_canal:
                score_sell += 3
            if en_sop_canal:
                score_buy += 3

            _rup_sop, _niv_sop = detectar_ruptura_soporte_horizontal(df, atr, lookback=_lkb, wing=3)
            _rup_res, _niv_res = detectar_ruptura_resistencia_horizontal(df, atr, lookback=_lkb, wing=3)
            if _rup_sop:
                score_sell += 4
            if _rup_res:
                score_buy += 4

            _retest_res, _niv_rr = detectar_retest_resistencia(df, atr, lookback=_lkb, wing=3)
            _retest_sop, _niv_rs = detectar_retest_soporte(df, atr, lookback=_lkb, wing=3)
            if _retest_res:
                score_sell += 5
            if _retest_sop:
                score_buy += 5

            _rec_baj, _ = detectar_rechazo_en_directriz(df, atr, lookback=_lkb, wing=3, direccion='bajista')
            _rec_alc, _ = detectar_rechazo_en_directriz(df, atr, lookback=_lkb, wing=3, direccion='alcista')
            if _rec_baj:
                score_sell += 4
            if _rec_alc:
                score_buy += 4

            _cuña_desc, _t_d, _s_d = detectar_cuña_descendente(df, atr, lookback=_lkb, wing=2, max_amplitud_pct=0.015)
            _cuña_asc, _t_a, _s_a  = detectar_cuña_ascendente(df, atr, lookback=_lkb, wing=2, max_amplitud_pct=0.015)
            if _cuña_desc == 'ruptura_alcista':
                score_buy += 5
            elif _cuña_desc == 'ruptura_bajista':
                score_sell += 5
            elif _cuña_desc == 'compresion':
                score_buy += 2
            if _cuña_asc == 'ruptura_bajista':
                score_sell += 5
            elif _cuña_asc == 'compresion':
                score_sell += 2

            _dt, _dt_niv, _dt_neck = detectar_doble_techo(df, atr, lookback=60, tol_mult=0.6)
            _ds, _ds_niv, _ds_neck = detectar_doble_suelo(df, atr, lookback=60, tol_mult=0.6)
            if _dt:
                score_sell += 4
            if _ds:
                score_buy += 4

            v_rev_alc, v_min, v_prec_alc = detectar_v_reversal_alcista(df, atr, lookback=20, min_caida_atr=3.0, min_rebote_atr=2.5)
            v_rev_baj, v_max, v_prec_baj = detectar_v_reversal_bajista(df, atr, lookback=20, min_subida_atr=3.0, min_caida_atr=2.5)
            if v_rev_alc:
                score_buy += 5
            if v_rev_baj:
                score_sell += 5

            # ── Confirmación 1M ───────────────────────────────────────────────
            _umbral_conf = 10
            if 5 <= score_sell < _umbral_conf or 5 <= score_buy < _umbral_conf:
                try:
                    df_1m, _ = get_ohlcv(params['ticker_yf'], period='1d', interval='1m')
                    if df_1m is not None and len(df_1m) >= 10:
                        atr_1m = float(calcular_atr(df_1m, 7).iloc[-1])
                        if 5 <= score_sell < _umbral_conf:
                            if (patron_envolvente_bajista(df_1m) or
                                    detectar_stop_hunt_bajista(df_1m, atr_1m) or
                                    detectar_rechazo_en_directriz(df_1m, atr_1m, lookback=60, wing=2, direccion='bajista')[0]):
                                score_sell += 2
                        if 5 <= score_buy < _umbral_conf:
                            if (patron_envolvente_alcista(df_1m) or
                                    detectar_stop_hunt_alcista(df_1m, atr_1m) or
                                    detectar_rechazo_en_directriz(df_1m, atr_1m, lookback=60, wing=2, direccion='alcista')[0]):
                                score_buy += 2
                except Exception:
                    pass

            max_score = 30

            # ── Ajustes de volatilidad y momentum ────────────────────────────
            score_sell, score_buy, _vol_bajo = self.ajustar_scores_por_volumen(
                score_sell, score_buy, vol_actual, vol_medio, params['vol_mult'])

            if _micro_vol > 1.5:
                if score_sell > score_buy:
                    score_sell = min(score_sell + 1, max_score)
                elif score_buy > score_sell:
                    score_buy = min(score_buy + 1, max_score)
            elif _micro_vol < 0.8:
                score_sell = max(0, score_sell - 1)
                score_buy  = max(0, score_buy  - 1)

            if _momentum_rec == -1 and (en_zona_resist or aproximando_resist):
                score_sell = min(score_sell + 1, max_score)
            elif _momentum_rec == 1 and (en_zona_soporte or aproximando_soporte):
                score_buy = min(score_buy + 1, max_score)

            _umbral_fue = self.umbral_adaptativo(10, atr, atr_media)
            senal_sell_fuerte = score_sell >= _umbral_fue
            senal_buy_fuerte  = score_buy  >= _umbral_fue

            # ── Filtro sesión óptima (07-21 UTC) ─────────────────────────────
            if not self.en_sesion_optima():
                senal_sell_fuerte = False
                senal_buy_fuerte  = False

            cancelar_sell = (close < zsl) or (rsi < 25)
            cancelar_buy  = (close > zrh) or (rsi > 75)

            atr_efectivo = max(atr, params['atr_min'])
            asm = params['atr_sl_mult']
            sl_venta  = round(close + atr_efectivo * asm, 5)
            sl_compra = round(close - atr_efectivo * asm, 5)

            offset_pct = params['limit_offset_pct']
            sell_limit = close * (1 + offset_pct / 100)
            buy_limit  = close * (1 - offset_pct / 100)

            tp1_v = round(sell_limit - atr_efectivo * params['atr_tp1_mult'], 5)
            tp2_v = round(sell_limit - atr_efectivo * params['atr_tp2_mult'], 5)
            tp3_v = round(sell_limit - atr_efectivo * params['atr_tp3_mult'], 5)
            tp1_c = round(buy_limit  + atr_efectivo * params['atr_tp1_mult'], 5)
            tp2_c = round(buy_limit  + atr_efectivo * params['atr_tp2_mult'], 5)
            tp3_c = round(buy_limit  + atr_efectivo * params['atr_tp3_mult'], 5)

            def rr(limit, sl, tp):
                return round(abs(tp - limit) / abs(sl - limit), 1) if abs(sl - limit) > 0 else 0

            fecha = df.index[-1].strftime('%Y-%m-%d %H:%M')

            # ── Guard duplicado ───────────────────────────────────────────────
            if simbolo in self.ultimo_analisis:
                ul = self.ultimo_analisis[simbolo]
                if (ul['fecha'] == fecha and
                        abs(ul['score_sell'] - score_sell) <= 1 and
                        abs(ul['score_buy'] - score_buy) <= 1):
                    return
            self.ultimo_analisis[simbolo] = {'fecha': fecha, 'score_sell': score_sell, 'score_buy': score_buy}

            logger.info(f"  [EURUSD 5M] {fecha}  Close: {close:.5f}  SELL:{score_sell}  BUY:{score_buy}  RSI:{rsi:.1f}  ADX:{adx:.1f}  ATR:{atr:.5f}")

            # ── Anti-spam ─────────────────────────────────────────────────────
            clave_vela = f"{simbolo}_{fecha}"

            def ya_enviada(tipo):
                clave = f"{clave_vela}_{tipo}"
                if self.alertas_enviadas.get(clave, 0) > time.time() - 172800:
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

            # ── Exclusión mutua ───────────────────────────────────────────────
            if senal_sell_fuerte and senal_buy_fuerte:
                if score_sell >= score_buy:
                    senal_buy_fuerte = False
                else:
                    senal_sell_fuerte = False

            _sesgo_dir = (tf_bias.BIAS_BEARISH if score_sell > score_buy
                          else tf_bias.BIAS_BULLISH if score_buy > score_sell
                          else tf_bias.BIAS_NEUTRAL)
            tf_bias.publicar_sesgo(simbolo, '5M', _sesgo_dir, max(score_sell, score_buy))

            _conf_sell = ""
            _conf_buy  = ""
            if senal_sell_fuerte:
                _ok, _desc = tf_bias.verificar_confluencia(simbolo, '5M', tf_bias.BIAS_BEARISH)
                if not _ok:
                    senal_sell_fuerte = False
                else:
                    _conf_sell = _desc
            if senal_buy_fuerte:
                _ok, _desc = tf_bias.verificar_confluencia(simbolo, '5M', tf_bias.BIAS_BULLISH)
                if not _ok:
                    senal_buy_fuerte = False
                else:
                    _conf_buy = _desc

            # ── Modo caza (zona activa 1H) ────────────────────────────────────
            _zona_1h_buy  = tf_bias.obtener_zona_activa(simbolo, tf_bias.BIAS_BULLISH)
            _zona_1h_sell = tf_bias.obtener_zona_activa(simbolo, tf_bias.BIAS_BEARISH)
            _UMBRAL_CAZA = 4

            if _zona_1h_buy and not senal_buy_fuerte and self.en_sesion_optima():
                _tol_1h = _zona_1h_buy['atr'] * 0.6
                if (_zona_1h_buy['zsl'] - _tol_1h <= close <= _zona_1h_buy['zsh'] + _tol_1h and
                        score_buy >= _UMBRAL_CAZA):
                    senal_buy_fuerte = True
                    _conf_buy = f"⚡ Setup 1H / Entrada 5M — zona soporte {_zona_1h_buy['zsl']:.5f}–{_zona_1h_buy['zsh']:.5f}"

            if _zona_1h_sell and not senal_sell_fuerte and self.en_sesion_optima():
                _tol_1h = _zona_1h_sell['atr'] * 0.6
                if (_zona_1h_sell['zrl'] - _tol_1h <= close <= _zona_1h_sell['zrh'] + _tol_1h and
                        score_sell >= _UMBRAL_CAZA):
                    senal_sell_fuerte = True
                    _conf_sell = f"⚡ Setup 1H / Entrada 5M — zona resist {_zona_1h_sell['zrl']:.5f}–{_zona_1h_sell['zrh']:.5f}"

            RR_MINIMO = 1.5
            if rr(sell_limit, sl_venta, tp1_v) < RR_MINIMO:
                cancelar_sell = True
            if rr(buy_limit, sl_compra, tp1_c) < RR_MINIMO:
                cancelar_buy = True

            simbolo_db = f"{simbolo}_5M"

            # ── Patrones para mensaje ─────────────────────────────────────────
            patrones = []
            if patron_envolvente_bajista(df):
                patrones.append("📉 Envolvente Bajista")
            if patron_envolvente_alcista(df):
                patrones.append("📈 Envolvente Alcista")
            if patron_doji(df):
                patrones.append("⚪ Doji")
            if detectar_stop_hunt_bajista(df):
                patrones.append("🎯 Stop Hunt Bajista")
            if detectar_stop_hunt_alcista(df):
                patrones.append("🎯 Stop Hunt Alcista")
            if _dt:
                patrones.append(f"🔻 Doble Techo {_dt_niv:.5f}")
            if _ds:
                patrones.append(f"🔺 Doble Suelo {_ds_niv:.5f}")
            if v_rev_alc:
                patrones.append(f"⚡ V-Reversal Alcista")
            if v_rev_baj:
                patrones.append(f"⚡ V-Reversal Bajista")
            diag = "\n".join(patrones) if patrones else "Sin patrones destacados"

            _condiciones_bd = {
                'rsi': round(float(rsi), 1), 'atr': round(float(atr), 6),
                'adx': round(float(adx), 1),
                'ema_fast': round(float(ema_fast.iloc[-1]), 6),
                'ema_slow': round(float(ema_slow.iloc[-1]), 6),
                'ema_trend': round(float(ema_trend.iloc[-1]), 6),
                'score_sell': score_sell, 'score_buy': score_buy,
                'en_zona_resist': bool(en_zona_resist), 'en_zona_soporte': bool(en_zona_soporte),
            }

            # ── SEÑAL SELL ────────────────────────────────────────────────────
            if senal_sell_fuerte and not cancelar_sell:
                if self.db and self.db.existe_senal_activa_tf(simbolo_db):
                    logger.info(f"  ℹ️  SELL 5M bloqueada — señal activa en {simbolo_db}")
                else:
                    hora_envio = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
                    msg = (
                        f"🔥 SELL FUERTE — <b>EUR/USD 5M MICRO-SCALP</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"💰 <b>Precio:</b>     {close:.5f}\n"
                        f"📌 <b>SELL LIMIT:</b> {sell_limit:.5f}\n"
                        f"🛑 <b>Stop Loss:</b>  {sl_venta:.5f}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 <b>TP1:</b> {tp1_v:.5f}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                        f"🎯 <b>TP2:</b> {tp2_v:.5f}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                        f"🎯 <b>TP3:</b> {tp3_v:.5f}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 <b>Score:</b> {score_sell}/{max_score}  📉 <b>RSI:</b> {rsi:.1f}  📐 <b>ADX:</b> {adx:.1f}\n"
                        f"⏱️ <b>TF:</b> 5M  🕐 {hora_envio}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🔍 <b>Patrones:</b>\n{diag}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🔒 MICRO-SCALP — Cerrar máx 30 min"
                    )
                    if _conf_sell:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_sell}"
                    if self.db:
                        try:
                            self._guardar_senal({
                                'timestamp': datetime.now(timezone.utc),
                                'timestamp_entry': df.index[-1].isoformat(),
                                'simbolo': simbolo_db, 'asset': 'EURUSD', 'timeframe': '5M',
                                'direccion': 'VENTA', 'precio_entrada': round(sell_limit, 5),
                                'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                                'score': score_sell,
                                'indicadores': json.dumps(_condiciones_bd),
                                'version_detector': 'EURUSD-5M-v1.0',
                            })
                        except Exception as e:
                            logger.error(f"  ⚠️ Error guardando señal EURUSD 5M SELL: {e}")
                    self.enviar(msg)

            # ── SEÑAL BUY ─────────────────────────────────────────────────────
            if senal_buy_fuerte and not cancelar_buy:
                if self.db and self.db.existe_senal_activa_tf(simbolo_db):
                    logger.info(f"  ℹ️  BUY 5M bloqueada — señal activa en {simbolo_db}")
                else:
                    hora_envio = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
                    msg = (
                        f"🔥 BUY FUERTE — <b>EUR/USD 5M MICRO-SCALP</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"💰 <b>Precio:</b>    {close:.5f}\n"
                        f"📌 <b>BUY LIMIT:</b> {buy_limit:.5f}\n"
                        f"🛑 <b>Stop Loss:</b> {sl_compra:.5f}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 <b>TP1:</b> {tp1_c:.5f}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                        f"🎯 <b>TP2:</b> {tp2_c:.5f}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                        f"🎯 <b>TP3:</b> {tp3_c:.5f}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 <b>Score:</b> {score_buy}/{max_score}  📉 <b>RSI:</b> {rsi:.1f}  📐 <b>ADX:</b> {adx:.1f}\n"
                        f"⏱️ <b>TF:</b> 5M  🕐 {hora_envio}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🔍 <b>Patrones:</b>\n{diag}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🔒 MICRO-SCALP — Cerrar máx 30 min"
                    )
                    if _conf_buy:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_buy}"
                    if self.db:
                        try:
                            self._guardar_senal({
                                'timestamp': datetime.now(timezone.utc),
                                'timestamp_entry': df.index[-1].isoformat(),
                                'simbolo': simbolo_db, 'asset': 'EURUSD', 'timeframe': '5M',
                                'direccion': 'COMPRA', 'precio_entrada': round(buy_limit, 5),
                                'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                                'score': score_buy,
                                'indicadores': json.dumps(_condiciones_bd),
                                'version_detector': 'EURUSD-5M-v1.0',
                            })
                        except Exception as e:
                            logger.error(f"  ⚠️ Error guardando señal EURUSD 5M BUY: {e}")
                    self.enviar(msg)

        except Exception as e:
            logger.error(f"❌ Error analizando {simbolo} [EURUSD 5M]: {e}", exc_info=True)


def analizar_simbolo(simbolo, params):
    return EURUSDDetector5M(
        simbolo=simbolo, tf_label='5M',
        params=params, telegram_thread_id=TELEGRAM_THREAD_ID
    ).analizar(simbolo, params)


def main():
    from adapters.telegram import enviar_telegram as _tg
    _tg("🚀 <b>Detector EUR/USD 5M MICRO-SCALPING iniciado</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⏱️ Análisis cada 60 segundos\n"
        "⚡ Sesión activa: 07:00-21:00 UTC", TELEGRAM_THREAD_ID)
    ciclo = 0
    while True:
        ciclo += 1
        verificar_y_notificar_reanudacion()
        ahora_utc = datetime.now(timezone.utc)
        if ahora_utc.weekday() == 5:
            logger.info(f"[EURUSD 5M] 💤 Sábado — mercado cerrado")
            time.sleep(3600)
            continue
        logger.info(f"[{ahora_utc.strftime('%H:%M:%S')}] 🔄 EURUSD 5M — ciclo #{ciclo}")
        for simbolo, params in SIMBOLOS.items():
            analizar_simbolo(simbolo, params)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
