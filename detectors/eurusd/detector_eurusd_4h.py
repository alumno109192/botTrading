"""
DETECTOR EUR/USD 4H - SWING TRADING
Análisis de EURUSD en timeframe 4 horas para operaciones swing de varios días.
Consume datos 4H directamente del poller. Sesión activa: 07:00-21:00 UTC.
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
from core.base_detector_eurusd import EURUSDBaseDetector as BaseDetector
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
    calcular_aceleracion_rsi,
)
import pandas as pd

logger = logging.getLogger('bottrading')

# ══════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════
TELEGRAM_THREAD_ID = int(os.environ.get('THREAD_ID_SWING') or 0) or None
CHECK_INTERVAL     = 3600   # 1 hora entre análisis (vela 4H)

# ══════════════════════════════════════
# PARÁMETROS — SWING EUR/USD 4H
# ══════════════════════════════════════
SIMBOLOS = {
    'EURUSD': {
        'ticker_yf':          'EURUSD=X',
        'sr_lookback':        100,
        'sr_zone_mult':       0.8,
        'atr_tp1_mult':       2.0,
        'atr_tp2_mult':       3.0,
        'atr_tp3_mult':       4.5,
        'atr_min':            0.0008,     # ATR mínimo: 8 pips
        'limit_offset_pct':   0.01,
        'rsi_length':         14,
        'rsi_min_sell':       55.0,
        'rsi_max_buy':        45.0,
        'ema_fast_len':       12,
        'ema_slow_len':       26,
        'ema_trend_len':      200,
        'atr_length':         14,
        'atr_sl_mult':        1.0,
        'spread':             0.00015,    # Spread típico broker EURUSD (~1.5 pips)
        'vol_mult':           1.0,
        'min_score_swing':    5,
        'max_perdidas_semana': 2,
    }
}


class EURUSDDetector4H(BaseDetector):
    def analizar(self, simbolo, params):

        # ── Bloqueo por eventos críticos ──
        bloqueado, desc_evento, minutos = debe_bloquear_trading(180)
        if bloqueado:
            logger.warning(f"  🚫 [EURUSD 4H] Trading bloqueado — {desc_evento} en {minutos} min")
            enviar_alerta_bloqueo(desc_evento, minutos, ['EURUSD 4H'])
            return

        self.aviso_macro = obtener_aviso_macro(90, '4H', simbolo)

        try:
            # Datos 4H directamente del poller BD (95 días)
            df, _ = get_ohlcv(params['ticker_yf'], period='95d', interval='4h')
            if df is None or df.empty or len(df) < 50:
                logger.warning(f"⚠️ Datos insuficientes para {simbolo} EURUSD 4H")
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

            _rsi_baj_3, _rsi_sub_3 = calcular_aceleracion_rsi(rsi_series)

            if rsi >= params['rsi_min_sell']:
                score_sell += 2
                if _rsi_baj_3:
                    score_sell += 1
            elif rsi >= 55:
                score_sell += 1
            if rsi <= params['rsi_max_buy']:
                score_buy += 2
                if _rsi_sub_3:
                    score_buy += 1
            elif rsi <= 45:
                score_buy += 1

            if ema_fast.iloc[-1] < ema_slow.iloc[-1]:
                score_sell += 2
                if ema_fast.iloc[-2] >= ema_slow.iloc[-2]:
                    score_sell += 1
            if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
                score_buy += 2
                if ema_fast.iloc[-2] <= ema_slow.iloc[-2]:
                    score_buy += 1

            # EMA 200 como filtro de tendencia macro (peso extra en swing)
            if close < ema_trend.iloc[-1]:
                score_sell += 3
            else:
                score_buy += 3

            if en_zona_resist or aproximando_resist:
                score_sell += 3
            if en_zona_soporte or aproximando_soporte:
                score_buy += 3

            if adx > 25:
                if score_sell >= score_buy:
                    score_sell += 1
                else:
                    score_buy += 1

            vol_medio  = df['Volume'].iloc[-20:].mean()
            vol_actual = df['Volume'].iloc[-3:].mean()   # 3 velas 4H = 12h
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
            if detectar_stop_hunt_alcista(df):
                score_buy += 3

            _lkb = params['sr_lookback']
            canal_alc_roto, canal_baj_roto, _, _ = detectar_canal_roto(df, atr, lookback=_lkb, wing=3)
            en_resist_canal, en_sop_canal, _, _  = detectar_precio_en_canal(df, atr, lookback=_lkb, wing=3)

            if canal_alc_roto:
                score_sell += 2
            if canal_baj_roto:
                score_buy += 2
            if en_resist_canal:
                score_sell += 3
            if en_sop_canal:
                score_buy += 3

            _rup_sop, _ = detectar_ruptura_soporte_horizontal(df, atr, lookback=_lkb, wing=3)
            _rup_res, _ = detectar_ruptura_resistencia_horizontal(df, atr, lookback=_lkb, wing=3)
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

            _cuña_desc, _, _ = detectar_cuña_descendente(df, atr, lookback=_lkb, wing=2, max_amplitud_pct=0.03)
            _cuña_asc, _, _  = detectar_cuña_ascendente(df, atr, lookback=_lkb, wing=2, max_amplitud_pct=0.03)
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

            _dt, _dt_niv, _ = detectar_doble_techo(df, atr, lookback=80, tol_mult=0.8)
            _ds, _ds_niv, _ = detectar_doble_suelo(df, atr, lookback=80, tol_mult=0.8)
            if _dt:
                score_sell += 5
            if _ds:
                score_buy += 5

            v_rev_alc, _, _ = detectar_v_reversal_alcista(df, atr, lookback=20, min_caida_atr=3.0, min_rebote_atr=2.5)
            v_rev_baj, _, _ = detectar_v_reversal_bajista(df, atr, lookback=20, min_subida_atr=3.0, min_caida_atr=2.5)
            if v_rev_alc:
                score_buy += 5
            if v_rev_baj:
                score_sell += 5

            max_score = 40

            score_sell, score_buy, _ = self.ajustar_scores_por_volumen(
                score_sell, score_buy, vol_actual, vol_medio, params['vol_mult'])

            # ── Correlación USD: Gold confirma / contradice EUR/USD ──────────
            score_sell, score_buy, _gold_corr = self.ajustar_score_por_correlacion_gold(
                score_sell, score_buy, '4H')
            if _gold_corr:
                logger.info(f"  🔗 [EURUSD 4H] Correlación Gold: {_gold_corr}")

            _umbral_fue = self.umbral_adaptativo(10, atr, atr_media)

            # 4H no filtra por sesión (velas largas atraviesan múltiples sesiones)
            senal_sell_fuerte = score_sell >= _umbral_fue
            senal_buy_fuerte  = score_buy  >= _umbral_fue

            cancelar_sell = (close < zsl) or (rsi < 25)
            cancelar_buy  = (close > zrh) or (rsi > 75)

            atr_efectivo = max(atr, params['atr_min'])
            asm = params['atr_sl_mult']
            spread = params.get('spread', 0.00015)  # costo de cierre (~1.5 pips EURUSD)
            sl_venta  = round(close + atr_efectivo * asm + spread, 5)
            sl_compra = round(close - atr_efectivo * asm - spread, 5)

            offset_pct = params['limit_offset_pct']
            sell_limit = close * (1 + offset_pct / 100)
            buy_limit  = close * (1 - offset_pct / 100)

            # VATR: factor de volumen — amplía TPs en mercados con impulso, los reduce en apáticos
            _vol_factor = min(max(vol_actual / vol_medio, 0.75), 1.50) if vol_medio > 0 else 1.0

            # TPs dinámicos basados en ATR ajustado por volumen (VATR) — ajustados por spread de cierre
            tp1_v = round(sell_limit - atr_efectivo * params['atr_tp1_mult'] * _vol_factor - spread, 5)
            tp2_v = round(sell_limit - atr_efectivo * params['atr_tp2_mult'] * _vol_factor - spread, 5)
            tp3_v = round(sell_limit - atr_efectivo * params['atr_tp3_mult'] * _vol_factor - spread, 5)
            tp1_c = round(buy_limit  + atr_efectivo * params['atr_tp1_mult'] * _vol_factor + spread, 5)
            tp2_c = round(buy_limit  + atr_efectivo * params['atr_tp2_mult'] * _vol_factor + spread, 5)
            tp3_c = round(buy_limit  + atr_efectivo * params['atr_tp3_mult'] * _vol_factor + spread, 5)

            def rr(limit, sl, tp):
                return round(abs(tp - limit) / abs(sl - limit), 1) if abs(sl - limit) > 0 else 0

            fecha = df.index[-1].strftime('%Y-%m-%d %H:%M')

            if simbolo in self.ultimo_analisis:
                ul = self.ultimo_analisis[simbolo]
                if (ul['fecha'] == fecha and
                        abs(ul['score_sell'] - score_sell) <= 1 and
                        abs(ul['score_buy'] - score_buy) <= 1):
                    return
            self.ultimo_analisis[simbolo] = {'fecha': fecha, 'score_sell': score_sell, 'score_buy': score_buy}

            logger.info(f"  [EURUSD 4H] {fecha}  Close:{close:.5f}  SELL:{score_sell}  BUY:{score_buy}  RSI:{rsi:.1f}  ADX:{adx:.1f}  ATR:{atr:.5f}")

            if senal_sell_fuerte and senal_buy_fuerte:
                if score_sell >= score_buy:
                    senal_buy_fuerte = False
                else:
                    senal_sell_fuerte = False

            _sesgo_dir = (tf_bias.BIAS_BEARISH if score_sell > score_buy
                          else tf_bias.BIAS_BULLISH if score_buy > score_sell
                          else tf_bias.BIAS_NEUTRAL)
            tf_bias.publicar_sesgo(simbolo, '4H', _sesgo_dir, max(score_sell, score_buy))

            _conf_sell = ""
            _conf_buy  = ""
            if senal_sell_fuerte:
                _ok, _desc = tf_bias.verificar_confluencia(simbolo, '4H', tf_bias.BIAS_BEARISH)
                if not _ok:
                    senal_sell_fuerte = False
                else:
                    _conf_sell = _desc
            if senal_buy_fuerte:
                _ok, _desc = tf_bias.verificar_confluencia(simbolo, '4H', tf_bias.BIAS_BULLISH)
                if not _ok:
                    senal_buy_fuerte = False
                else:
                    _conf_buy = _desc

            RR_MINIMO = 2.0   # Swing exige mayor R:R
            if rr(sell_limit, sl_venta, tp1_v) < RR_MINIMO:
                cancelar_sell = True
            if rr(buy_limit, sl_compra, tp1_c) < RR_MINIMO:
                cancelar_buy = True

            simbolo_db = f"{simbolo}_4H"

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
            if _retest_res:
                patrones.append(f"🔻 Retest Resist {_niv_rr:.5f}")
            if _retest_sop:
                patrones.append(f"🔺 Retest Soporte {_niv_rs:.5f}")
            if _dt:
                patrones.append(f"🔻 Doble Techo {_dt_niv:.5f}")
            if _ds:
                patrones.append(f"🔺 Doble Suelo {_ds_niv:.5f}")
            if v_rev_alc:
                patrones.append("⚡ V-Reversal Alcista")
            if v_rev_baj:
                patrones.append("⚡ V-Reversal Bajista")
            diag = "\n".join(patrones) if patrones else "Sin patrones destacados"

            _condiciones_bd = {
                'rsi': round(float(rsi), 1), 'atr': round(float(atr), 6),
                'adx': round(float(adx), 1),
                'ema_fast': round(float(ema_fast.iloc[-1]), 6),
                'ema_slow': round(float(ema_slow.iloc[-1]), 6),
                'ema_trend': round(float(ema_trend.iloc[-1]), 6),
                'score_sell': score_sell, 'score_buy': score_buy,
            }

            if senal_sell_fuerte and not cancelar_sell:
                if self.db and self.db.existe_senal_activa_tf(simbolo_db):
                    logger.info(f"  ℹ️  SELL 4H bloqueada — señal activa en {simbolo_db}")
                else:
                    hora_envio = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
                    msg = (
                        f"🌊 SELL SWING — <b>{self.nombre_display} {self.tf_label}</b>\n"
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
                        f"⏱️ <b>TF:</b> 4H  🕐 {hora_envio}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🔍 <b>Patrones:</b>\n{diag}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌊 SWING — Operación multi-día"
                    )
                    if _conf_sell:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_sell}"
                    if self.db:
                        try:
                            self._guardar_senal({
                                'timestamp': datetime.now(timezone.utc),
                                'timestamp_entry': df.index[-1].isoformat(),
                                'simbolo': simbolo_db, 'asset': 'EURUSD', 'timeframe': '4H',
                                'direccion': 'VENTA', 'precio_entrada': round(sell_limit, 5),
                                'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                                'score': score_sell,
                                'indicadores': json.dumps(_condiciones_bd),
                                'version_detector': 'EURUSD-4H-v1.0',
                            })
                        except Exception as e:
                            logger.error(f"  ⚠️ Error guardando señal EURUSD 4H SELL: {e}")
                    self.enviar(msg)

            if senal_buy_fuerte and not cancelar_buy:
                if self.db and self.db.existe_senal_activa_tf(simbolo_db):
                    logger.info(f"  ℹ️  BUY 4H bloqueada — señal activa en {simbolo_db}")
                else:
                    hora_envio = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
                    msg = (
                        f"🌊 BUY SWING — <b>{self.nombre_display} {self.tf_label}</b>\n"
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
                        f"⏱️ <b>TF:</b> 4H  🕐 {hora_envio}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🔍 <b>Patrones:</b>\n{diag}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌊 SWING — Operación multi-día"
                    )
                    if _conf_buy:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_buy}"
                    if self.db:
                        try:
                            self._guardar_senal({
                                'timestamp': datetime.now(timezone.utc),
                                'timestamp_entry': df.index[-1].isoformat(),
                                'simbolo': simbolo_db, 'asset': 'EURUSD', 'timeframe': '4H',
                                'direccion': 'COMPRA', 'precio_entrada': round(buy_limit, 5),
                                'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                                'score': score_buy,
                                'indicadores': json.dumps(_condiciones_bd),
                                'version_detector': 'EURUSD-4H-v1.0',
                            })
                        except Exception as e:
                            logger.error(f"  ⚠️ Error guardando señal EURUSD 4H BUY: {e}")
                    self.enviar(msg)

        except Exception as e:
            logger.error(f"❌ Error analizando {simbolo} [EURUSD 4H]: {e}", exc_info=True)


def analizar_simbolo(simbolo, params):
    return EURUSDDetector4H(
        simbolo=simbolo, tf_label='4H',
        params=params, telegram_thread_id=TELEGRAM_THREAD_ID
    ).analizar(simbolo, params)


def main():
    from adapters.telegram import enviar_telegram as _tg
    _tg("🚀 <b>Detector EUR/USD 4H SWING iniciado</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⏱️ Análisis cada hora\n"
        "🌊 Operaciones multi-día", TELEGRAM_THREAD_ID)
    ciclo = 0
    while True:
        ciclo += 1
        verificar_y_notificar_reanudacion()
        ahora_utc = datetime.now(timezone.utc)
        if ahora_utc.weekday() == 5:
            logger.info(f"[EURUSD 4H] 💤 Sábado — mercado cerrado")
            time.sleep(3600)
            continue
        logger.info(f"[{ahora_utc.strftime('%H:%M:%S')}] 🔄 EURUSD 4H — ciclo #{ciclo}")
        for simbolo, params in SIMBOLOS.items():
            analizar_simbolo(simbolo, params)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
