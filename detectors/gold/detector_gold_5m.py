"""
DETECTOR GOLD 5M - MICRO-SCALPING
Análisis de XAUUSD en timeframe 5 minutos para operaciones ultra-rápidas
Confluencia obligatoria con 1D + 4H + 1H + 15M (sesgo multi-TF)
"""
import os
from services import tf_bias
from services.dxy_bias import get_dxy_bias, ajustar_score_por_dxy
from services.economic_calendar import hay_evento_impacto
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
        print("✅ [5M] Sistema de tracking de BD activado")
    else:
        print("⚠️  [5M] Variables Turso no configuradas - Sistema funcionará sin tracking de BD")
except Exception as e:
    print(f"⚠️  [5M] No se pudo inicializar BD: {e}")
    db = None

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
from core.indicators import calcular_rsi, calcular_atr, calcular_adx, patron_envolvente_alcista, patron_envolvente_bajista, patron_doji

def calcular_zonas_sr(df, atr, lookback, zone_mult):
    """
    Detecta automáticamente zonas S/R desde swing highs/lows históricos.
    No requiere mantenimiento manual — se adapta al precio actual y la volatilidad.
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

def analizar_simbolo(simbolo, params):
    global perdidas_consecutivas, ultima_senal_timestamp

    if not en_sesion_activa():
        print(f"  ⏸️  [5M] Fuera de sesión (07-17 UTC) — análisis saltado")
        return

    # ── Filtro calendario económico ──
    bloqueado, descripcion = hay_evento_impacto(ventana_minutos=30)
    if bloqueado:
        print(f"  🚫 [5M] Señal bloqueada por evento macro: {descripcion}")
        return

    try:
        df, is_delayed = get_ohlcv(params['ticker_yf'], period='2d', interval='5m')
        if is_delayed:
            print("  ⚠️  [5M] Datos con 15 min de delay (yfinance free). Entradas de scalping pueden estar desfasadas.")

        if df.empty or len(df) < 50:
            print(f"⚠️ Datos insuficientes para {simbolo} 5M")
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
                print(f"⚠️ Columnas inesperadas ({len(df.columns)}): {df.columns.tolist()}")
                return

        close = df['Close'].iloc[-1]

        rsi_len = params['rsi_length']
        rsi = calcular_rsi(df['Close'], rsi_len).iloc[-1]

        ema_fast  = df['Close'].ewm(span=params['ema_fast_len']).mean()
        ema_slow  = df['Close'].ewm(span=params['ema_slow_len']).mean()
        ema_trend = df['Close'].ewm(span=params['ema_trend_len']).mean()

        atr_len = params['atr_length']
        atr = calcular_atr(df, atr_len).iloc[-1]
        adx, _, _ = calcular_adx(df)
        adx = adx.iloc[-1]

        zrl, zrh, zsl, zsh = calcular_zonas_sr(df, atr, params['sr_lookback'], params['sr_zone_mult'])
        tol = round(atr * 0.4, 2)   # tolerancia dinámica: 40% del ATR
        print(f"  📍 Zonas auto — Resist: ${zrl:.1f}-${zrh:.1f} | Soporte: ${zsl:.1f}-${zsh:.1f}")

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

        max_score = 15

        # Umbrales 5M — solo FUERTE llega a Telegram
        senal_sell_fuerte = score_sell >= 8
        senal_buy_fuerte  = score_buy  >= 8

        # ── Ajuste por sesgo DXY (correlación inversa Gold/USD) ──
        dxy_bias = get_dxy_bias()
        score_buy, score_sell = ajustar_score_por_dxy(score_buy, score_sell, dxy_bias)
        # Recalcular umbrales tras ajuste DXY
        senal_sell_fuerte = score_sell >= 8
        senal_buy_fuerte  = score_buy  >= 8

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
        if simbolo in ultimo_analisis:
            ul = ultimo_analisis[simbolo]
            if (ul['fecha'] == fecha and
                    abs(ul['score_sell'] - score_sell) <= 1 and
                    abs(ul['score_buy'] - score_buy) <= 1):
                print(f"  ℹ️  Vela {fecha} ya analizada — sin cambios")
                return

        ultimo_analisis[simbolo] = {'fecha': fecha, 'score_sell': score_sell, 'score_buy': score_buy}

        print(f"  📅 {fecha}  💰 Close: {round(close, 2)}")
        print(f"  🔴 SELL {score_sell}/{max_score} | 🟢 BUY {score_buy}/{max_score}")
        print(f"  📉 RSI: {round(rsi, 1)} | ADX: {round(adx, 1)} | ATR: {round(atr, 2)}")

        # ── PÉRDIDAS CONSECUTIVAS ────────────────────────
        if perdidas_consecutivas >= params['max_perdidas_dia']:
            print(f"  ⛔ Trading pausado: {perdidas_consecutivas} pérdidas consecutivas")
            if not (senal_sell_fuerte or senal_buy_fuerte):
                return
            perdidas_consecutivas = 0

        # ── ANTI-SPAM ────────────────────────────────────
        clave_vela = f"{simbolo}_{fecha}"

        def ya_enviada(tipo):
            return alertas_enviadas.get(f"{clave_vela}_{tipo}", 0) > time.time() - 172800  # 48h TTL

        def marcar_enviada(tipo):
            alertas_enviadas[f"{clave_vela}_{tipo}"] = time.time()
            if len(alertas_enviadas) > 500:
                _c = time.time() - 172800
                for _k in [k for k in list(alertas_enviadas) if alertas_enviadas[k] < _c]:
                    del alertas_enviadas[_k]

        # ── EXCLUSIÓN MUTUA ──
        if senal_sell_fuerte and senal_buy_fuerte:
            if score_sell >= score_buy:
                senal_buy_fuerte = False
                print(f"  ⚖️ Exclusión mutua: BUY suprimida (SELL {score_sell} >= BUY {score_buy})")
            else:
                senal_sell_fuerte = False
                print(f"  ⚖️ Exclusión mutua: SELL suprimida (BUY {score_buy} > SELL {score_sell})")

        _sesgo_dir = tf_bias.BIAS_BEARISH if score_sell > score_buy else tf_bias.BIAS_BULLISH if score_buy > score_sell else tf_bias.BIAS_NEUTRAL
        tf_bias.publicar_sesgo(simbolo, '5M', _sesgo_dir, max(score_sell, score_buy))
        _conf_sell = ""; _conf_buy = ""

        if senal_sell_fuerte:
            _ok, _desc = tf_bias.verificar_confluencia(simbolo, '5M', tf_bias.BIAS_BEARISH)
            if not _ok:
                print(f"  🚫 SELL bloqueada por TF superior: {_desc[:80]}")
                senal_sell_fuerte = False
            else:
                _conf_sell = _desc
        if senal_buy_fuerte:
            _ok, _desc = tf_bias.verificar_confluencia(simbolo, '5M', tf_bias.BIAS_BULLISH)
            if not _ok:
                print(f"  🚫 BUY bloqueada por TF superior: {_desc[:80]}")
                senal_buy_fuerte = False
            else:
                _conf_buy = _desc

        # ── FILTRO R:R MÍNIMO 1.5 (Micro-Scalp 5M) ──
        RR_MINIMO = 1.5
        rr_sell_tp1 = rr(sell_limit, sl_venta,  tp1_v)
        rr_buy_tp1  = rr(buy_limit,  sl_compra, tp1_c)
        if rr_sell_tp1 < RR_MINIMO:
            print(f'  ⛔ SELL bloqueada: R:R TP1={rr_sell_tp1} < {RR_MINIMO}')
            cancelar_sell = True
        if rr_buy_tp1 < RR_MINIMO:
            print(f'  ⛔ BUY bloqueada: R:R TP1={rr_buy_tp1} < {RR_MINIMO}')
            cancelar_buy = True

        simbolo_db = f"{simbolo}_5M"

        # ── SEÑALES VENTA — solo FUERTE ──
        if senal_sell_fuerte and not cancelar_sell:
            if db and db.existe_senal_activa_tf(simbolo_db):
                print(f"  ℹ️  SELL 5M bloqueada: ya existe señal ACTIVA en {simbolo_db}")
            else:
                msg = (f"🔥 SELL FUERTE — <b>GOLD 5M MICRO-SCALP</b>\n"
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
                if db:
                    try:
                        db.guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                            'direccion': 'VENTA', 'precio_entrada': sell_limit,
                            'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                            'score': score_sell,
                            'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 1),
                                                       'atr': round(atr, 2)}),
                            'patron_velas': f"Envolvente:{patron_envolvente_bajista(df)}, Doji:{patron_doji(df)}",
                            'version_detector': '5M-MICRO-v2.0'
                        })
                    except Exception as e:
                        print(f"  ⚠️ Error guardando señal: {e}")
                enviar_telegram(msg)

        # ── SEÑALES COMPRA — solo FUERTE ──
        if senal_buy_fuerte and not cancelar_buy:
            if db and db.existe_senal_activa_tf(simbolo_db):
                print(f"  ℹ️  BUY 5M bloqueada: ya existe señal ACTIVA en {simbolo_db}")
            else:
                msg = (f"🔥 BUY FUERTE — <b>GOLD 5M MICRO-SCALP</b>\n"
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
                if db:
                    try:
                        db.guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                            'direccion': 'COMPRA', 'precio_entrada': buy_limit,
                            'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                            'score': score_buy,
                            'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 1),
                                                       'atr': round(atr, 2)}),
                            'patron_velas': f"Envolvente:{patron_envolvente_alcista(df)}, Doji:{patron_doji(df)}",
                            'version_detector': '5M-MICRO-v2.0'
                        })
                    except Exception as e:
                        print(f"  ⚠️ Error guardando señal: {e}")
                enviar_telegram(msg)

    except Exception as e:
        print(f"❌ Error analizando {simbolo} [5M]: {e}")


# ══════════════════════════════════════
# FUNCIÓN MAIN
# ══════════════════════════════════════
def main():
    """Función principal para ejecutar el detector"""
    global perdidas_consecutivas
    enviar_telegram("🚀 <b>Detector GOLD 5M MICRO-SCALP iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "⏱️  Análisis cada 1 minuto\n"
                    "⚡ Confluencia 1D + 4H + 1H + 15M\n"
                    "🎯 TPs: $20 / $35 / $60\n"
                    "🔒 Operaciones máx 30 min")

    # Cargar pérdidas consecutivas desde BD al arrancar (sobrevive reinicios)
    if db:
        try:
            perdidas_consecutivas = db.contar_perdidas_consecutivas('XAUUSD_5M')
            print(f"📊 [5M] Pérdidas consecutivas cargadas desde BD: {perdidas_consecutivas}")
        except Exception:
            pass

    ciclo = 0
    while True:
        ciclo += 1
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔄 CICLO #{ciclo} — GOLD 5M MICRO-SCALP")

        for simbolo, params in SIMBOLOS.items():
            print(f"📊 Analizando {simbolo} [5M MICRO-SCALP]...")
            analizar_simbolo(simbolo, params)

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Ciclo #{ciclo} completado — esperando {CHECK_INTERVAL}s")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
