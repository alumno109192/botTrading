"""
DETECTOR GOLD 5M - MICRO-SCALPING
Análisis de XAUUSD en timeframe 5 minutos para operaciones ultra-rápidas
Confluencia obligatoria con 1D + 4H + 1H + 15M (sesgo multi-TF)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import tf_bias
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

# Inicializar base de datos solo si las variables están configuradas
db = None
try:
    turso_url = os.environ.get('TURSO_DATABASE_URL')
    turso_token = os.environ.get('TURSO_AUTH_TOKEN')
    if turso_url and turso_token:
        from db_manager import DatabaseManager
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
        'zona_resist_high':   3350.0,
        'zona_resist_low':    3330.0,
        'zona_soporte_high':  3250.0,
        'zona_soporte_low':   3230.0,
        'tp1_venta':          3310.0,       # TP1 (-$20)
        'tp2_venta':          3295.0,       # TP2 (-$35)
        'tp3_venta':          3270.0,       # TP3 (-$60)
        'tp1_compra':         3270.0,       # TP1 (+$20)
        'tp2_compra':         3285.0,       # TP2 (+$35)
        'tp3_compra':         3310.0,       # TP3 (+$60)
        'tolerancia':         5.0,          # Tolerancia muy ajustada
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
            print(f"❌ Error Telegram → {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"❌ Error Telegram: {e}")

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

def calcular_atr(df, length):
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(length).mean()

def calcular_adx(df, length=14):
    h, l, c = df['High'], df['Low'], df['Close']
    plus_dm  = h.diff().clip(lower=0)
    minus_dm = (-l.diff()).clip(lower=0)
    tr = calcular_atr(df, 1) * length
    plus_di  = 100 * (plus_dm.ewm(alpha=1/length).mean() / tr)
    minus_di = 100 * (minus_dm.ewm(alpha=1/length).mean() / tr)
    dx  = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    return dx.ewm(alpha=1/length).mean()

def patron_envolvente_alcista(df):
    c1_bear = df['Close'].iloc[-2] < df['Open'].iloc[-2]
    c2_bull = df['Close'].iloc[-1] > df['Open'].iloc[-1]
    c2_envuelve = (df['Close'].iloc[-1] > df['Open'].iloc[-2] and
                   df['Open'].iloc[-1] < df['Close'].iloc[-2])
    return c1_bear and c2_bull and c2_envuelve

def patron_envolvente_bajista(df):
    c1_bull = df['Close'].iloc[-2] > df['Open'].iloc[-2]
    c2_bear = df['Close'].iloc[-1] < df['Open'].iloc[-1]
    c2_envuelve = (df['Close'].iloc[-1] < df['Open'].iloc[-2] and
                   df['Open'].iloc[-1] > df['Close'].iloc[-2])
    return c1_bull and c2_bear and c2_envuelve

def patron_doji(df):
    body = abs(df['Close'].iloc[-1] - df['Open'].iloc[-1])
    rng  = df['High'].iloc[-1] - df['Low'].iloc[-1]
    return body < (rng * 0.1) if rng > 0 else False

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
def analizar_simbolo(simbolo, params):
    global perdidas_consecutivas, ultima_senal_timestamp

    try:
        df = yf.download(params['ticker_yf'], period='2d', interval='5m', progress=False)

        if df.empty or len(df) < 50:
            print(f"⚠️ Datos insuficientes para {simbolo} 5M")
            return

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)

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
        adx = calcular_adx(df).iloc[-1]

        zrh = params['zona_resist_high']
        zrl = params['zona_resist_low']
        zsh = params['zona_soporte_high']
        zsl = params['zona_soporte_low']
        tol = params['tolerancia']

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

        senal_sell_fuerte = score_sell >= 8
        senal_sell_media  = score_sell >= 5
        senal_sell_scalp  = score_sell >= 3

        senal_buy_fuerte  = score_buy >= 8
        senal_buy_media   = score_buy >= 5
        senal_buy_scalp   = score_buy >= 3

        cancelar_sell = (close < zsl) or (rsi < 28)
        cancelar_buy  = (close > zrh) or (rsi > 72)

        asm = params['atr_sl_mult']
        sl_venta  = close + (atr * asm)
        sl_compra = close - (atr * asm)

        tp1_v = params['tp1_venta'];  tp2_v = params['tp2_venta'];  tp3_v = params['tp3_venta']
        tp1_c = params['tp1_compra']; tp2_c = params['tp2_compra']; tp3_c = params['tp3_compra']

        offset_pct = params['limit_offset_pct']
        sell_limit = close * (1 + offset_pct / 100)
        buy_limit  = close * (1 - offset_pct / 100)

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
            return alertas_enviadas.get(f"{clave_vela}_{tipo}", False)

        def marcar_enviada(tipo):
            alertas_enviadas[f"{clave_vela}_{tipo}"] = True

        # ══════════════════════════════════════
        # EXCLUSIÓN MUTUA + SESGO MULTI-TF
        # ══════════════════════════════════════
        _any_sell = senal_sell_scalp or senal_sell_media or senal_sell_fuerte
        _any_buy  = senal_buy_scalp  or senal_buy_media  or senal_buy_fuerte

        if _any_sell and _any_buy:
            if score_sell >= score_buy:
                senal_buy_scalp = senal_buy_media = senal_buy_fuerte = False
                print(f"  ⚖️ Exclusión mutua: BUY suprimida (SELL {score_sell} >= BUY {score_buy})")
            else:
                senal_sell_scalp = senal_sell_media = senal_sell_fuerte = False
                print(f"  ⚖️ Exclusión mutua: SELL suprimida (BUY {score_buy} > SELL {score_sell})")
            _any_sell = senal_sell_scalp or senal_sell_media or senal_sell_fuerte
            _any_buy  = senal_buy_scalp  or senal_buy_media  or senal_buy_fuerte

        _sesgo_dir = tf_bias.BIAS_BEARISH if score_sell > score_buy else tf_bias.BIAS_BULLISH if score_buy > score_sell else tf_bias.BIAS_NEUTRAL
        tf_bias.publicar_sesgo(simbolo, '5M', _sesgo_dir, max(score_sell, score_buy))
        _conf_sell = ""; _conf_buy = ""

        if _any_sell:
            _ok, _desc = tf_bias.verificar_confluencia(simbolo, '5M', tf_bias.BIAS_BEARISH)
            if not _ok:
                print(f"  🚫 SELL bloqueada por TF superior: {_desc[:80]}")
                senal_sell_scalp = senal_sell_media = senal_sell_fuerte = False
            else:
                _conf_sell = _desc
        if _any_buy:
            _ok, _desc = tf_bias.verificar_confluencia(simbolo, '5M', tf_bias.BIAS_BULLISH)
            if not _ok:
                print(f"  🚫 BUY bloqueada por TF superior: {_desc[:80]}")
                senal_buy_scalp = senal_buy_media = senal_buy_fuerte = False
            else:
                _conf_buy = _desc

        # ══════════════════════════════════════
        # ENVIAR SEÑALES MICRO-SCALP
        # ══════════════════════════════════════


        # ── FILTRO R:R MÍNIMO 1.2 ──
        rr_sell_tp1 = rr(sell_limit, sl_venta,  tp1_v)
        rr_buy_tp1  = rr(buy_limit,  sl_compra, tp1_c)
        if rr_sell_tp1 < 1.2:
            print(f'  ⛔ SELL bloqueada: R:R TP1={rr_sell_tp1} < 1.2')
            cancelar_sell = True
        if rr_buy_tp1 < 1.2:
            print(f'  ⛔ BUY bloqueada: R:R TP1={rr_buy_tp1} < 1.2')
            cancelar_buy = True

        # ── SEÑALES VENTA ──
        if (senal_sell_scalp or senal_sell_media or senal_sell_fuerte) and not cancelar_sell:
            nivel = ("🔥 SELL FUERTE" if senal_sell_fuerte else
                     "🔴 SELL MEDIA"  if senal_sell_media  else
                     "⚡ SCALP SELL")
            tipo_clave = ("SELL_FUE" if senal_sell_fuerte else
                          "SELL_MED" if senal_sell_media  else
                          "SCALP_SEL")

            if not ya_enviada(tipo_clave):
                if db and db.existe_senal_reciente(f"{simbolo}_5M", 'VENTA', horas=1):
                    print(f"  ℹ️  Señal VENTA 5M duplicada")
                    return

                calidad = "🔥 ALTA" if senal_sell_fuerte else "⚠️ MEDIA" if senal_sell_media else "⚡ MICRO"

                msg = (f"{nivel} — <b>GOLD 5M MICRO-SCALP</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"💰 <b>Precio:</b>     ${round(close, 2)}\n"
                       f"📌 <b>SELL LIMIT:</b> ${round(sell_limit, 2)}\n"
                       f"🛑 <b>Stop Loss:</b>  ${round(sl_venta, 2)}\n"
                       f"🎯 <b>TP1:</b> ${tp1_v}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                       f"🎯 <b>TP2:</b> ${tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                       f"🎯 <b>TP3:</b> ${tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_sell}/{max_score} | <b>Calidad:</b> {calidad}\n"
                       f"📉 <b>RSI:</b> {round(rsi, 1)} | <b>ADX:</b> {round(adx, 1)}\n"
                       f"⏱️ <b>TF:</b> 5M  📅 {fecha}\n"
                       f"🔒 MICRO-SCALP — Cerrar máx 30 min")

                if _conf_sell:
                    msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_sell}"

                if db:
                    try:
                        db.guardar_senal({
                            'timestamp': datetime.now(timezone.utc),
                            'simbolo': f"{simbolo}_5M",
                            'direccion': 'VENTA',
                            'precio_entrada': sell_limit,
                            'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v,
                            'sl': sl_venta,
                            'score': score_sell,
                            'timeframe': '5M',
                            'indicadores': json.dumps({
                                'rsi': round(rsi, 1), 'adx': round(adx, 1),
                                'atr': round(atr, 2),
                                'ema_fast': round(ema_fast.iloc[-1], 2),
                                'ema_slow': round(ema_slow.iloc[-1], 2)
                            }),
                            'patron_velas': f"Envolvente:{patron_envolvente_bajista(df)}, Doji:{patron_doji(df)}",
                            'version_detector': '5M-MICRO-v1.0'
                        })
                    except Exception as e:
                        print(f"  ⚠️ Error guardando señal: {e}")

                enviar_telegram(msg)
                marcar_enviada(tipo_clave)

        # ── SEÑALES COMPRA ──
        if (senal_buy_scalp or senal_buy_media or senal_buy_fuerte) and not cancelar_buy:
            nivel = ("🔥 BUY FUERTE" if senal_buy_fuerte else
                     "🟢 BUY MEDIA"  if senal_buy_media  else
                     "⚡ SCALP BUY")
            tipo_clave = ("BUY_FUE" if senal_buy_fuerte else
                          "BUY_MED" if senal_buy_media  else
                          "SCALP_BUY")

            if not ya_enviada(tipo_clave):
                if db and db.existe_senal_reciente(f"{simbolo}_5M", 'COMPRA', horas=1):
                    print(f"  ℹ️  Señal COMPRA 5M duplicada")
                    return

                calidad = "🔥 ALTA" if senal_buy_fuerte else "⚠️ MEDIA" if senal_buy_media else "⚡ MICRO"

                msg = (f"{nivel} — <b>GOLD 5M MICRO-SCALP</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"💰 <b>Precio:</b>    ${round(close, 2)}\n"
                       f"📌 <b>BUY LIMIT:</b> ${round(buy_limit, 2)}\n"
                       f"🛑 <b>Stop Loss:</b> ${round(sl_compra, 2)}\n"
                       f"🎯 <b>TP1:</b> ${tp1_c}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                       f"🎯 <b>TP2:</b> ${tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                       f"🎯 <b>TP3:</b> ${tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_buy}/{max_score} | <b>Calidad:</b> {calidad}\n"
                       f"📉 <b>RSI:</b> {round(rsi, 1)} | <b>ADX:</b> {round(adx, 1)}\n"
                       f"⏱️ <b>TF:</b> 5M  📅 {fecha}\n"
                       f"🔒 MICRO-SCALP — Cerrar máx 30 min")

                if _conf_buy:
                    msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_buy}"

                if db:
                    try:
                        db.guardar_senal({
                            'timestamp': datetime.now(timezone.utc),
                            'simbolo': f"{simbolo}_5M",
                            'direccion': 'COMPRA',
                            'precio_entrada': buy_limit,
                            'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c,
                            'sl': sl_compra,
                            'score': score_buy,
                            'timeframe': '5M',
                            'indicadores': json.dumps({
                                'rsi': round(rsi, 1), 'adx': round(adx, 1),
                                'atr': round(atr, 2),
                                'ema_fast': round(ema_fast.iloc[-1], 2),
                                'ema_slow': round(ema_slow.iloc[-1], 2)
                            }),
                            'patron_velas': f"Envolvente:{patron_envolvente_alcista(df)}, Doji:{patron_doji(df)}",
                            'version_detector': '5M-MICRO-v1.0'
                        })
                    except Exception as e:
                        print(f"  ⚠️ Error guardando señal: {e}")

                enviar_telegram(msg)
                marcar_enviada(tipo_clave)

    except Exception as e:
        print(f"❌ Error analizando {simbolo} [5M]: {e}")


# ══════════════════════════════════════
# FUNCIÓN MAIN
# ══════════════════════════════════════
def main():
    """Función principal para ejecutar el detector"""
    enviar_telegram("🚀 <b>Detector GOLD 5M MICRO-SCALP iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "⏱️  Análisis cada 1 minuto\n"
                    "⚡ Confluencia 1D + 4H + 1H + 15M\n"
                    "🎯 TPs: $20 / $35 / $60\n"
                    "🔒 Operaciones máx 30 min")

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
