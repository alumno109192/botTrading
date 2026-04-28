"""
Signal Monitor - Monitorea señales activas y actualiza estados
Revisa cada 5 minutos todas las señales activas y verifica si alcanzaron TP o SL
"""

import time
import threading
import requests
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from adapters.database import DatabaseManager
from adapters.data_provider import get_ohlcv as _get_ohlcv

import logging
logger = logging.getLogger('bottrading')

# Cargar variables de entorno
load_dotenv()

# Configuración de Telegram
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def _parse_thread_id(val):
    """Convierte variable de entorno a int requerido por la API de Telegram."""
    try:
        return int(val) if val else None
    except (ValueError, TypeError):
        return None

THREAD_ID_SWING     = _parse_thread_id(os.environ.get('THREAD_ID_SWING'))      # 1D / 4H
THREAD_ID_INTRADAY  = _parse_thread_id(os.environ.get('THREAD_ID_INTRADAY'))   # 1H
THREAD_ID_SCALPING  = _parse_thread_id(os.environ.get('THREAD_ID_SCALPING'))   # 15M / 5M

def obtener_thread_id(simbolo: str):
    """Devuelve el message_thread_id de Telegram según el timeframe del símbolo."""
    sufijo = simbolo.split('_')[-1].upper() if '_' in simbolo else ''
    if sufijo in ('15M', '5M'):
        return THREAD_ID_SCALPING
    if sufijo == '1H':
        return THREAD_ID_INTRADAY
    # 4H, 1D y sin sufijo → Swing
    return THREAD_ID_SWING

# Mapeo de símbolos a tickers de yfinance
SIMBOLO_TO_TICKER = {
    'BTCUSD':  'BTC-USD',
    'XAUUSD':  'GC=F',      # Gold Futures
    'SPX500':  '^GSPC',     # S&P 500
    'NAS100':  '^IXIC',     # NASDAQ Composite
    'EURUSD':  'EURUSD=X',  # EUR/USD Forex
    'WTIUSD':  'CL=F',      # WTI Crude Oil Futures
    'XAGUSD':  'SI=F',      # Silver Futures
}

def _fetch_precios_ticker(ticker: str, db=None) -> tuple | None:
    """Obtiene (precio_actual, precio_max_5velas, precio_min_5velas) para un ticker.

    Estrategia sin contención de lock:
      1. Lee de la tabla ohlcv de BD (datos frescos del ohlcv_poller, sin lock).
         Si la vela más reciente tiene <= 10 min → devuelve directamente.
      2. Fallback a Twelve Data (get_ohlcv) si la BD no tiene datos recientes.
    """
    # ── Paso 1: BD (rápido, sin lock) ────────────────────────────────────────
    if db is not None:
        try:
            res = db.obtener_precio_reciente_bd(ticker, '5m', max_minutos=10)
            if res is not None:
                return res
        except Exception as e:
            logger.error(f"⚠️ [{ticker}] Error leyendo precio de BD: {e}")

    # ── Paso 2: Twelve Data vía get_ohlcv (fallback cuando BD > 10 min) ───────
    try:
        hist, _ = _get_ohlcv(ticker, period='1d', interval='5m')
        if hist is None or hist.empty:
            return None
        precio_actual = float(hist['Close'].iloc[-1])
        ventana       = hist.tail(5)
        precio_max    = float(ventana['High'].max())
        precio_min    = float(ventana['Low'].min())
        return (precio_actual, precio_max, precio_min)
    except Exception as e:
        logger.error(f"❌ Error descargando {ticker} via get_ohlcv: {e}")
        return None


def obtener_precio_actual(simbolo: str) -> tuple | None:
    """Obtiene precio actual y extremos de los últimos 5m para un símbolo.

    Wrapper de compatibilidad sobre _fetch_precios_ticker.
    Para uso en el monitor usa el caché por ticker del ciclo principal.

    Args:
        simbolo: p.ej. XAUUSD, XAUUSD_15M, BTCUSD_4H

    Returns:
        (precio_actual, precio_max_5m, precio_min_5m) o None
    """
    simbolo_base = simbolo.split('_')[0]
    ticker = SIMBOLO_TO_TICKER.get(simbolo_base)
    if not ticker:
        logger.warning(f"⚠️ Símbolo desconocido: {simbolo} (base: {simbolo_base})")
        return None
    return _fetch_precios_ticker(ticker)


def enviar_notificacion_telegram(mensaje: str, simbolo: str = None):
    """Envía una notificación a Telegram al hilo correcto según el símbolo"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': mensaje,
            'parse_mode': 'HTML'
        }
        if simbolo:
            thread_id = obtener_thread_id(simbolo)
            if thread_id:
                data['message_thread_id'] = thread_id

        response = requests.post(url, json=data, timeout=10)

        if response.status_code == 200:
            logger.info(f"✅ Notificación enviada: {mensaje[:50]}...")
        else:
            logger.error(f"⚠️ Error enviando notificación: {response.status_code}")
            
    except Exception as e:
        logger.error(f"❌ Error en Telegram: {e}")


def calcular_beneficio_pct(precio_entrada: float, precio_actual: float, 
                          direccion: str) -> float:
    """Calcula el porcentaje de beneficio/pérdida"""
    if direccion == 'COMPRA':
        return ((precio_actual - precio_entrada) / precio_entrada) * 100
    else:  # VENTA
        return ((precio_entrada - precio_actual) / precio_entrada) * 100


def verificar_niveles_compra(senal: dict, precio_actual: float,
                            precio_min: float, precio_max: float,
                            db: DatabaseManager,
                            progreso_50_enviado: set = None):
    """Verifica niveles para señales de COMPRA.

    Usa precio_max (High de los últimos 5m) para detectar TPs alcanzados
    brevemente entre polls, y precio_min (Low) para el SL.
    """
    senal_id = senal['id']
    simbolo = senal['simbolo']

    # Convertir valores numéricos de BD — guard para valores None (columnas no rellenadas)
    try:
        precio_entrada = float(senal['precio_entrada'])
        tp1 = float(senal['tp1'])
        tp2 = float(senal['tp2'])
        tp3 = float(senal['tp3'])
        sl  = float(senal['sl'])
    except (TypeError, ValueError):
        logger.info(f"\u26a0\ufe0f [monitor] Se\u00f1al {senal_id} tiene precios nulos/inv\u00e1lidos — saltando")
        return
    # Asegurar flags booleanos como int (Turso puede retornar string "0"/"1")
    tp1_alcanzado = bool(int(senal.get('tp1_alcanzado') or 0))
    tp2_alcanzado = bool(int(senal.get('tp2_alcanzado') or 0))
    tp3_alcanzado = bool(int(senal.get('tp3_alcanzado') or 0))
    sl_alcanzado  = bool(int(senal.get('sl_alcanzado')  or 0))

    # Verificar TP3 (mayor prioridad) — usa High reciente para capturar picos entre polls
    if precio_max >= tp3 and not tp3_alcanzado:
        beneficio = calcular_beneficio_pct(precio_entrada, tp3, 'COMPRA')
        db.actualizar_estado_senal(senal_id, 'TP3', beneficio)
        db.registrar_tp3_hit(senal_id, simbolo, 'COMPRA', tp3, precio_actual, beneficio)

        mensaje = f"""
🎯🎯🎯 <b>TP3 ALCANZADO!</b>

📊 {simbolo} | COMPRA
💰 Entrada: ${precio_entrada:.2f}
✅ TP3: ${tp3:.2f}
📈 Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%

📋 <b>ACCIÓN RECOMENDADA:</b>
🔴 Cerrar el 100% restante de la posición
🏆 ¡Operación completada con éxito!
━━━━━━━━━━━━━━━━━━━━
🔖 <code>#{senal_id}</code>
        """
        enviar_notificacion_telegram(mensaje, simbolo)
        return

    # Verificar TP2 — usa High reciente
    if precio_max >= tp2 and not tp2_alcanzado:
        beneficio = calcular_beneficio_pct(precio_entrada, tp2, 'COMPRA')
        db.actualizar_estado_senal(senal_id, 'TP2', beneficio)
        db.registrar_tp2_hit(senal_id, simbolo, 'COMPRA', tp2, precio_actual, beneficio)

        mensaje = f"""
🎯🎯 <b>TP2 ALCANZADO</b>

📊 {simbolo} | COMPRA
💰 Entrada: ${precio_entrada:.2f}
✅ TP2: ${tp2:.2f}
📈 Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%

📋 <b>ACCIÓN RECOMENDADA:</b>
🔴 Cerrar 33% de la posición
🔒 Mover SL a TP1 (${tp1:.2f})
⏳ Dejar correr hacia TP3 (${tp3:.2f})
━━━━━━━━━━━━━━━━━━━━
🔖 <code>#{senal_id}</code>
        """
        enviar_notificacion_telegram(mensaje, simbolo)
        return

    # Verificar TP1 — usa High reciente
    if precio_max >= tp1 and not tp1_alcanzado:
        beneficio = calcular_beneficio_pct(precio_entrada, tp1, 'COMPRA')
        db.actualizar_estado_senal(senal_id, 'TP1', beneficio)
        db.registrar_tp1_hit(senal_id, simbolo, 'COMPRA', tp1, precio_actual, beneficio)

        mensaje = f"""
🎯 <b>TP1 ALCANZADO</b>

📊 {simbolo} | COMPRA
💰 Entrada: ${precio_entrada:.2f}
✅ TP1: ${tp1:.2f}
📈 Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%

📋 <b>ACCIÓN RECOMENDADA:</b>
🔴 Cerrar 33% de la posición
🔒 Mover SL a breakeven (${precio_entrada:.2f})
⏳ Dejar correr hacia TP2 (${tp2:.2f})
━━━━━━━━━━━━━━━━━━━━
🔖 <code>#{senal_id}</code>
        """
        enviar_notificacion_telegram(mensaje, simbolo)
        return

    # Verificar progreso 50% hacia TP1 — aviso intermedio
    if progreso_50_enviado is not None and senal_id not in progreso_50_enviado:
        dist_total = abs(tp1 - precio_entrada)
        dist_recorrida = precio_max - precio_entrada  # BUY: precio sube
        if dist_total > 0 and dist_recorrida >= dist_total * 0.5:
            pct = round(dist_recorrida / dist_total * 100)
            beneficio_parcial = calcular_beneficio_pct(precio_entrada, precio_actual, 'COMPRA')
            msg_50 = (
                f"⚡ <b>Trade avanzando — 50% hacia TP1</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 {simbolo} | COMPRA\n"
                f"💰 Entrada: ${precio_entrada:.2f}\n"
                f"📍 Actual: ${precio_actual:.2f}  ({pct}% del camino)\n"
                f"🎯 TP1: ${tp1:.2f}  |  Faltan ${tp1 - precio_actual:.2f}\n"
                f"💵 P&L actual: {beneficio_parcial:+.2f}%\n"
                f"🔒 Considera mover SL a breakeven (${precio_entrada:.2f})\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔖 <code>#{senal_id}</code>"
            )
            enviar_notificacion_telegram(msg_50, simbolo)
            progreso_50_enviado.add(senal_id)
            logger.info(f"  ⚡ [50%] {simbolo} COMPRA — notificación de progreso enviada")

    # Verificar SL / Breakeven — usa Low reciente
    if precio_min <= sl and not sl_alcanzado:
        if tp1_alcanzado:
            # TP1 ya se tocó → SL fue movido a breakeven automáticamente → cierre en 0
            db.actualizar_estado_senal(senal_id, 'BREAKEVEN', 0.0)
            db.registrar_breakeven_hit(senal_id, simbolo, 'COMPRA', sl, precio_actual)
            mensaje = (
                f"🔄 <b>BREAKEVEN — {simbolo}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 COMPRA | TP1 alcanzado previamente\n"
                f"💰 Entrada: ${precio_entrada:.2f}\n"
                f"📍 Precio tocó breakeven: ${sl:.2f}\n"
                f"📉 Actual: ${precio_actual:.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Trade cerrado en <b>0% de pérdida</b>\n"
                f"🔍 El bot buscará nueva oportunidad de entrada\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔖 <code>#{senal_id}</code>"
            )
            enviar_notificacion_telegram(mensaje, simbolo)
            logger.info(f"  🔄 [{simbolo}] BREAKEVEN BUY — cerrada en entrada ${precio_entrada:.2f}")
        else:
            beneficio = calcular_beneficio_pct(precio_entrada, sl, 'COMPRA')
            db.actualizar_estado_senal(senal_id, 'SL', beneficio)
            mensaje = (
                f"❌ <b>STOP LOSS ACTIVADO</b>\n\n"
                f"📊 {simbolo} | COMPRA\n"
                f"💰 Entrada: ${precio_entrada:.2f}\n"
                f"🛑 SL: ${sl:.2f}\n"
                f"📉 Actual: ${precio_actual:.2f}\n"
                f"💸 Pérdida: {beneficio:.2f}%\n\n"
                f"📋 <b>ACCIÓN RECOMENDADA:</b>\n"
                f"🔴 Cerrar el 100% de la posición\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔖 <code>#{senal_id}</code>"
            )
            enviar_notificacion_telegram(mensaje, simbolo)
        return


def verificar_niveles_venta(senal: dict, precio_actual: float,
                           precio_min: float, precio_max: float,
                           db: DatabaseManager,
                           progreso_50_enviado: set = None):
    """Verifica niveles para señales de VENTA.

    Usa precio_min (Low de los últimos 5m) para detectar TPs alcanzados
    brevemente entre polls, y precio_max (High) para el SL.
    """
    senal_id = senal['id']
    simbolo = senal['simbolo']

    # Convertir valores numéricos de BD — guard para valores None (columnas no rellenadas)
    try:
        precio_entrada = float(senal['precio_entrada'])
        tp1 = float(senal['tp1'])
        tp2 = float(senal['tp2'])
        tp3 = float(senal['tp3'])
        sl  = float(senal['sl'])
    except (TypeError, ValueError):
        logger.warning(f"⚠️ [monitor] Señal {senal_id} tiene precios nulos/inválidos — saltando")
        return
    # Asegurar flags booleanos como int (Turso puede retornar string "0"/"1")
    tp1_alcanzado = bool(int(senal.get('tp1_alcanzado') or 0))
    tp2_alcanzado = bool(int(senal.get('tp2_alcanzado') or 0))
    tp3_alcanzado = bool(int(senal.get('tp3_alcanzado') or 0))
    sl_alcanzado  = bool(int(senal.get('sl_alcanzado')  or 0))

    # Verificar TP3 (menor precio) — usa Low reciente para capturar picos entre polls
    if precio_min <= tp3 and not tp3_alcanzado:
        beneficio = calcular_beneficio_pct(precio_entrada, tp3, 'VENTA')
        db.actualizar_estado_senal(senal_id, 'TP3', beneficio)
        db.registrar_tp3_hit(senal_id, simbolo, 'VENTA', tp3, precio_actual, beneficio)

        mensaje = f"""
🎯🎯🎯 <b>TP3 ALCANZADO!</b>

📊 {simbolo} | VENTA
💰 Entrada: ${precio_entrada:.2f}
✅ TP3: ${tp3:.2f}
📉 Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%

📋 <b>ACCIÓN RECOMENDADA:</b>
🔴 Cerrar el 100% restante de la posición
🏆 ¡Operación completada con éxito!
━━━━━━━━━━━━━━━━━━━━
🔖 <code>#{senal_id}</code>
        """
        enviar_notificacion_telegram(mensaje, simbolo)
        return

    # Verificar TP2 — usa Low reciente
    if precio_min <= tp2 and not tp2_alcanzado:
        beneficio = calcular_beneficio_pct(precio_entrada, tp2, 'VENTA')
        db.actualizar_estado_senal(senal_id, 'TP2', beneficio)
        db.registrar_tp2_hit(senal_id, simbolo, 'VENTA', tp2, precio_actual, beneficio)

        mensaje = f"""
🎯🎯 <b>TP2 ALCANZADO</b>

📊 {simbolo} | VENTA
💰 Entrada: ${precio_entrada:.2f}
✅ TP2: ${tp2:.2f}
📉 Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%

📋 <b>ACCIÓN RECOMENDADA:</b>
🔴 Cerrar 33% de la posición
🔒 Mover SL a TP1 (${tp1:.2f})
⏳ Dejar correr hacia TP3 (${tp3:.2f})
━━━━━━━━━━━━━━━━━━━━
🔖 <code>#{senal_id}</code>
        """
        enviar_notificacion_telegram(mensaje, simbolo)
        return

    # Verificar TP1 — usa Low reciente
    if precio_min <= tp1 and not tp1_alcanzado:
        beneficio = calcular_beneficio_pct(precio_entrada, tp1, 'VENTA')
        db.actualizar_estado_senal(senal_id, 'TP1', beneficio)
        db.registrar_tp1_hit(senal_id, simbolo, 'VENTA', tp1, precio_actual, beneficio)

        mensaje = f"""
🎯 <b>TP1 ALCANZADO</b>

📊 {simbolo} | VENTA
💰 Entrada: ${precio_entrada:.2f}
✅ TP1: ${tp1:.2f}
📉 Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%

📋 <b>ACCIÓN RECOMENDADA:</b>
🔴 Cerrar 33% de la posición
🔒 Mover SL a breakeven (${precio_entrada:.2f})
⏳ Dejar correr hacia TP2 (${tp2:.2f})
━━━━━━━━━━━━━━━━━━━━
🔖 <code>#{senal_id}</code>
        """
        enviar_notificacion_telegram(mensaje, simbolo)
        return

    # Verificar progreso 50% hacia TP1 — aviso intermedio
    if progreso_50_enviado is not None and senal_id not in progreso_50_enviado:
        dist_total = abs(precio_entrada - tp1)
        dist_recorrida = precio_entrada - precio_min  # VENTA: precio baja
        if dist_total > 0 and dist_recorrida >= dist_total * 0.5:
            pct = round(dist_recorrida / dist_total * 100)
            beneficio_parcial = calcular_beneficio_pct(precio_entrada, precio_actual, 'VENTA')
            msg_50 = (
                f"⚡ <b>Trade avanzando — 50% hacia TP1</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 {simbolo} | VENTA\n"
                f"💰 Entrada: ${precio_entrada:.2f}\n"
                f"📍 Actual: ${precio_actual:.2f}  ({pct}% del camino)\n"
                f"🎯 TP1: ${tp1:.2f}  |  Faltan ${precio_actual - tp1:.2f}\n"
                f"💵 P&L actual: {beneficio_parcial:+.2f}%\n"
                f"🔒 Considera mover SL a breakeven (${precio_entrada:.2f})\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔖 <code>#{senal_id}</code>"
            )
            enviar_notificacion_telegram(msg_50, simbolo)
            progreso_50_enviado.add(senal_id)
            logger.info(f"  ⚡ [50%] {simbolo} VENTA — notificación de progreso enviada")

    # Verificar SL / Breakeven — usa High reciente
    if precio_max >= sl and not sl_alcanzado:
        if tp1_alcanzado:
            # TP1 ya se tocó → SL fue movido a breakeven automáticamente → cierre en 0
            db.actualizar_estado_senal(senal_id, 'BREAKEVEN', 0.0)
            db.registrar_breakeven_hit(senal_id, simbolo, 'VENTA', sl, precio_actual)
            mensaje = (
                f"🔄 <b>BREAKEVEN — {simbolo}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 VENTA | TP1 alcanzado previamente\n"
                f"💰 Entrada: ${precio_entrada:.2f}\n"
                f"📍 Precio tocó breakeven: ${sl:.2f}\n"
                f"📈 Actual: ${precio_actual:.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Trade cerrado en <b>0% de pérdida</b>\n"
                f"🔍 El bot buscará nueva oportunidad de entrada\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔖 <code>#{senal_id}</code>"
            )
            enviar_notificacion_telegram(mensaje, simbolo)
            logger.info(f"  🔄 [{simbolo}] BREAKEVEN SELL — cerrada en entrada ${precio_entrada:.2f}")
        else:
            beneficio = calcular_beneficio_pct(precio_entrada, sl, 'VENTA')
            db.actualizar_estado_senal(senal_id, 'SL', beneficio)
            mensaje = (
                f"❌ <b>STOP LOSS ACTIVADO</b>\n\n"
                f"📊 {simbolo} | VENTA\n"
                f"💰 Entrada: ${precio_entrada:.2f}\n"
                f"🛑 SL: ${sl:.2f}\n"
                f"📈 Actual: ${precio_actual:.2f}\n"
                f"💸 Pérdida: {beneficio:.2f}%\n\n"
                f"📋 <b>ACCIÓN RECOMENDADA:</b>\n"
                f"🔴 Cerrar el 100% de la posición\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔖 <code>#{senal_id}</code>"
            )
            enviar_notificacion_telegram(mensaje, simbolo)
        return


_TIMEOUT_PENDIENTE_CONFIRM_HORAS = 2  # señal 1H caduca si no hay confirmación en 2h


def _verificar_trampa_patron(senal: dict, db: DatabaseManager, trampa_avisada: set):
    """
    Detecta si el precio está atrapado en un patrón de indecisión mientras
    hay una señal activa sin TP1 alcanzado. Envía alerta para cerrar preventivamente.

    Patrones de trampa:
      - Cuña en compresión (cualquier dirección) → indecisión total
      - Cuña adversa a la dirección del trade (ruptura en contra)
      - Doble techo con señal de COMPRA activa → riesgo de giro bajista
      - Doble suelo con señal de VENTA activa  → riesgo de giro alcista

    Solo avisa una vez por señal (trampa_avisada evita spam).
    """
    from core.indicators import (calcular_atr, detectar_cuña_descendente,
                                  detectar_cuña_ascendente, detectar_doble_techo,
                                  detectar_doble_suelo)

    senal_id  = senal['id']
    simbolo   = senal['simbolo']
    direccion = senal['direccion']

    # Solo si TP1 NO ha sido alcanzado (trade aún en riesgo)
    tp1_alcanzado = bool(int(senal.get('tp1_alcanzado') or 0))
    if tp1_alcanzado:
        return

    # Avisar solo una vez por señal
    if senal_id in trampa_avisada:
        return

    simbolo_base = simbolo.split('_')[0]
    ticker = SIMBOLO_TO_TICKER.get(simbolo_base)
    if not ticker:
        return

    try:
        precio_entrada = float(senal['precio_entrada'])
        sl  = float(senal['sl'])
        tp1 = float(senal['tp1'])
    except (TypeError, ValueError):
        return

    # Parámetros según TF del símbolo
    sufijo = simbolo.split('_')[-1].upper() if '_' in simbolo else ''
    if sufijo == '5M':
        interval, period, lookback, wing = '5m',  '2d', 60, 2
    elif sufijo == '15M':
        interval, period, lookback, wing = '15m', '3d', 80, 2
    elif sufijo == '1H':
        interval, period, lookback, wing = '1h',  '5d', 50, 3
    else:  # 4H, 1D
        interval, period, lookback, wing = '4h',  '60d', 40, 3

    try:
        df, _ = _get_ohlcv(ticker, period=period, interval=interval)
        if df is None or len(df) < 20:
            return

        atr = float(calcular_atr(df, 7).iloc[-1])

        cuña_desc, t_desc, s_desc = detectar_cuña_descendente(
            df, atr, lookback=lookback, wing=wing, max_amplitud_pct=0.04)
        cuña_asc,  t_asc,  s_asc  = detectar_cuña_ascendente(
            df, atr, lookback=lookback, wing=wing, max_amplitud_pct=0.04)
        dt, dt_nivel, dt_neck = detectar_doble_techo(df, atr, lookback=lookback, tol_mult=0.7)
        ds, ds_nivel, ds_neck = detectar_doble_suelo(df, atr, lookback=lookback, tol_mult=0.7)

        motivos = []

        # ── Compresión (cuña sin romper → indecisión total) ──────────────────
        if cuña_desc == 'compresion':
            motivos.append(f"📐 Cuña descendente en compresión (${s_desc:.1f}–${t_desc:.1f})")
        if cuña_asc == 'compresion':
            motivos.append(f"📐 Cuña ascendente en compresión (${s_asc:.1f}–${t_asc:.1f})")

        # ── Patrón adverso según dirección del trade ──────────────────────────
        if direccion == 'COMPRA':
            if cuña_asc == 'ruptura_bajista':
                motivos.append(f"📐 Cuña ASC rota a la BAJA → posible caída hacia ${s_asc:.1f}")
            if dt:
                motivos.append(f"🔻 Doble techo (techo=${dt_nivel:.1f}, cuello=${dt_neck:.1f}) — resistencia doble")
        else:  # VENTA
            if cuña_desc == 'ruptura_alcista':
                motivos.append(f"📐 Cuña DESC rota al ALZA → posible subida hacia ${t_desc:.1f}")
            if ds:
                motivos.append(f"🔺 Doble suelo (suelo=${ds_nivel:.1f}, cuello=${ds_neck:.1f}) — soporte doble")

        if not motivos:
            return

        precio_actual = float(df['Close'].iloc[-1])
        beneficio = calcular_beneficio_pct(precio_entrada, precio_actual, direccion)
        icono = '🟢' if direccion == 'COMPRA' else '🔴'

        msg = (
            f"⚠️ <b>ALERTA: PRECIO ATRAPADO EN PATRÓN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{icono} {simbolo} | {direccion}\n"
            f"💰 Entrada: ${precio_entrada:.2f}\n"
            f"📍 Actual:  ${precio_actual:.2f}  ({beneficio:+.2f}%)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔍 <b>Patrón(es) detectado(s):</b>\n"
            + "\n".join(f"  • {m}" for m in motivos) + "\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>Acción sugerida:</b> considera cerrar en breakeven\n"
            f"   o ajustar SL a ${precio_actual:.2f} para limitar riesgo\n"
            f"🛑 SL actual: ${sl:.2f}  |  TP1: ${tp1:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔖 <code>#{senal_id}</code>"
        )
        enviar_notificacion_telegram(msg, simbolo)
        trampa_avisada.add(senal_id)
        logger.info(
            f"  ⚠️ [{simbolo}] TRAMPA detectada señal #{senal_id} ({direccion}): "
            + " | ".join(motivos)
        )

    except Exception as e:
        logger.debug(f"  [trampa] Error analizando {simbolo}: {e}")





def _confirmar_con_velas_1m(ticker: str, direccion: str, precio_entrada: float) -> tuple:
    """
    Analiza las últimas velas de 1M para verificar que el momentum actual
    confirma la dirección de la señal 1H.

    Criterios:
      - EMA9 y EMA21 alineadas con la dirección
      - RSI14 en zona consistente (>48 para COMPRA, <52 para VENTA)
      - Precio actual dentro de 2×ATR(1M) del precio de entrada (no se escapó)

    Returns:
        (confirmado: bool, descripcion: str)
    """
    try:
        hist, _ = _get_ohlcv(ticker, period='1d', interval='1m')
        if hist is None or hist.empty or len(hist) < 22:
            return False, "Sin datos 1M suficientes"

        close = hist['Close']
        precio_actual = float(close.iloc[-1])

        # EMA 9 y EMA 21
        ema9  = float(close.ewm(span=9,  adjust=False).mean().iloc[-1])
        ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])

        # RSI 14
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, float('inf'))
        rsi   = float(100 - (100 / (1 + rs.iloc[-1])))

        # ATR 14 sobre velas 1M — para medir si el precio se alejó de la entrada
        tr_1m  = (hist['High'] - hist['Low']).rolling(14).mean()
        atr_1m = float(tr_1m.iloc[-1])

        # Precio demasiado lejos del nivel de entrada (más de 2 ATR)
        distancia = abs(precio_actual - precio_entrada)
        if atr_1m > 0 and distancia > 2 * atr_1m:
            return False, (
                f"Precio escapó de entrada "
                f"(actual ${precio_actual:.2f} vs limit ${precio_entrada:.2f}, "
                f"dist={distancia:.2f} > 2×ATR={2*atr_1m:.2f})"
            )

        # Criterios de momentum por dirección
        if direccion == 'COMPRA':
            ema_ok  = ema9 > ema21
            rsi_ok  = rsi > 48
            confirmado = ema_ok and rsi_ok
            ema_sym = '>' if ema9 > ema21 else '<'
        else:  # VENTA
            ema_ok  = ema9 < ema21
            rsi_ok  = rsi < 52
            confirmado = ema_ok and rsi_ok
            ema_sym = '<' if ema9 < ema21 else '>'

        desc = (
            f"1M: EMA9={ema9:.2f} {'>' if ema9 > ema21 else '<'} EMA21={ema21:.2f} | "
            f"RSI={rsi:.1f} | Precio=${precio_actual:.2f} | ATR={atr_1m:.2f}"
        )
        # Escapar para HTML (el desc se usa dentro de <code>...</code>)
        desc = desc.replace('<', '&lt;').replace('>', '&gt;')
        return confirmado, desc

    except Exception as e:
        return False, f"Error analizando velas 1M: {e}"


def _verificar_pendientes_confirm(db: DatabaseManager):
    """
    Revisa señales 1H en estado PENDIENTE_CONFIRM y las activa si las velas
    de 1M confirman momentum alineado con la dirección.
    Caduca las que llevan más de _TIMEOUT_PENDIENTE_CONFIRM_HORAS sin confirmar.
    """
    pendientes = db.obtener_senales_pendientes_confirm()
    if not pendientes:
        return

    ahora = datetime.now(timezone.utc)
    logger.info(f"  ⏳ Pendientes de confirmación 1H: {len(pendientes)}")

    for senal in pendientes:
        senal_id  = senal['id']
        simbolo   = senal['simbolo']          # ej. XAUUSD_1H
        direccion = senal['direccion']        # COMPRA | VENTA
        ts_raw    = senal['timestamp']

        # Calcular antigüedad
        try:
            if isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw.replace('Z', '+00:00'))
            else:
                ts = ts_raw
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            antiguedad_h = (ahora - ts).total_seconds() / 3600
        except Exception:
            antiguedad_h = 0

        if antiguedad_h > _TIMEOUT_PENDIENTE_CONFIRM_HORAS:
            db.caducar_senal_pendiente(senal_id)
            msg_caduca = (
                f"⏰ <b>Setup 1H caducado</b> — sin confirmación 15M/5M\n"
                f"📊 {simbolo} | {direccion}\n"
                f"⌛ Esperó {int(antiguedad_h*60)} min sin alineación inferior"
            )
            enviar_notificacion_telegram(msg_caduca, simbolo)
            logger.info(f"  ⏰ Señal {senal_id} ({simbolo} {direccion}) caducada por timeout")
            continue

# Obtener ticker para este símbolo
        simbolo_base = simbolo.split('_')[0]
        ticker = SIMBOLO_TO_TICKER.get(simbolo_base)
        if not ticker:
            logger.warning(f"  ⚠️ Ticker desconocido para {simbolo} — saltando")
            continue

        try:
            precio_entrada = float(senal['precio_entrada'])
            tp1 = float(senal['tp1'])
            tp2 = float(senal['tp2'])
            tp3 = float(senal['tp3'])
            sl  = float(senal['sl'])
            score = senal.get('score', '?')
        except (TypeError, ValueError):
            precio_entrada = tp1 = tp2 = tp3 = sl = 0.0
            score = '?'

        # Analizar velas 1M en tiempo real
        confirmado, desc_1m = _confirmar_con_velas_1m(ticker, direccion, precio_entrada)

        if not confirmado:
            logger.info(f"  ⏳ {simbolo} {direccion} ({int(antiguedad_h*60)}min) — 1M no confirma: {desc_1m}")
            continue

        # ✅ Confirmación recibida — activar señal y notificar
        db.confirmar_senal_pendiente(senal_id)

        flecha = '📌 BUY LIMIT' if direccion == 'COMPRA' else '📌 SELL LIMIT'
        icono  = '🟢' if direccion == 'COMPRA' else '🔴'
        msg_confirm = (
            f"✅ <b>ENTRADA CONFIRMADA — ORO (XAUUSD) 1H</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{icono} <b>Dirección:</b> {direccion}\n"
            f"🕐 <b>Confirmado por:</b> análisis velas 1M\n"
            f"{flecha}: <b>${precio_entrada:.2f}</b>  ← PON LA ORDEN AHORA\n"
            f"🛑 <b>Stop Loss:</b> ${sl:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>TP1:</b> ${tp1:.2f}\n"
            f"🎯 <b>TP2:</b> ${tp2:.2f}\n"
            f"🎯 <b>TP3:</b> ${tp3:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Score 1H:</b> {score}/21  ⏱️ <b>TF:</b> 1H+1M\n"
            f"<code>{desc_1m}</code>"
        )
        enviar_notificacion_telegram(msg_confirm, simbolo)
        logger.info(f"  ✅ Señal {senal_id} ({simbolo} {direccion}) confirmada: {desc_1m}")


def cerrar_senales_antiguas(db: DatabaseManager, dias: int = 7):
    """
    Cierra automáticamente señales que llevan más de X días activas
    
    Args:
        db: Instancia de DatabaseManager
        dias: Días después de los cuales cerrar señal
    """
    fecha_limite = datetime.now(timezone.utc) - timedelta(days=dias)
    
    query = """
    SELECT id, simbolo, direccion, timestamp
    FROM senales
    WHERE estado = 'ACTIVA'
    AND timestamp < ?
    """
    
    result = db.ejecutar_query(query, (fecha_limite.isoformat(),))
    
    if result.rows:
        for row in result.rows:
            senal = dict(row)
            db.cerrar_senal(senal['id'], 'CANCELADA', 0.0)
            logger.info(f"🗓️ Señal {senal['id']} cerrada por antigüedad (>{dias} días)")



# ── Intervalos de revisión por timeframe ──────────────────────────────────────
# La clave es el sufijo del campo 'simbolo' en la BD (ej: XAUUSD_15M → '15M')
_INTERVALO_SCALPING  = 30    # segundos — 5M y 15M  (precio nuevo cada ~30s en yfinance 1m)
_INTERVALO_INTRADAY  = 90    # segundos — 1H
_INTERVALO_SWING     = 300   # segundos — 4H, 1D y sin sufijo

# Tick del bucle principal: MCD de los intervalos anteriores
_TICK               = 30    # segundos


def _categoria_senal(simbolo: str) -> str:
    """Devuelve la categoría de una señal según su sufijo de timeframe."""
    sufijo = simbolo.split('_')[-1].upper() if '_' in simbolo else ''
    if sufijo in ('5M', '15M'):
        return 'scalping'
    if sufijo == '1H':
        return 'intraday'
    return 'swing'


def _intervalo_para(categoria: str) -> int:
    """Devuelve el intervalo de revisión en segundos para una categoría."""
    return {
        'scalping': _INTERVALO_SCALPING,
        'intraday': _INTERVALO_INTRADAY,
        'swing':    _INTERVALO_SWING,
    }.get(categoria, _INTERVALO_SWING)


def monitor_senales():
    """
    Loop principal con revisión diferenciada por timeframe:
      • 5M / 15M  → cada 45 segundos
      • 1H        → cada 2 minutos
      • 4H / 1D   → cada 5 minutos
    """
    logger.info("="*60)
    logger.info("🔍 MONITOR DE SEÑALES INICIADO")
    logger.info("="*60)
    logger.info(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"⏱️  Scalping (5M/15M): cada {_INTERVALO_SCALPING}s  ← tick base")
    logger.info(f"⏱️  Intraday  (1H):    cada {_INTERVALO_INTRADAY}s")
    logger.info(f"⏱️  Swing  (4H/1D):   cada {_INTERVALO_SWING}s")
    logger.info(f"⚡ Fetch deduplicado: 1 llamada por ticker subyacente por ciclo")
    logger.info("="*60)


    # Inicializar conexión a base de datos
    db = None
    while db is None:
        try:
            db = DatabaseManager()
            logger.info("✅ Conexión a base de datos establecida")
        except Exception as e:
            logger.warning(f"⚠️  Base de datos no disponible: {e}")
            logger.info("🔁 Reintentando en 60 segundos...")
            time.sleep(60)

    # Registro del último momento en que se revisó cada señal (id → datetime)
    ultimo_check: dict = {}
    # Set de IDs de señales para las que ya se envió el aviso del 50% recorrido
    _progreso_50_enviado: set = set()
    # Set de IDs de señales para las que ya se envió la alerta de trampa de patrón
    _trampa_avisada: set = set()
    # Último tick en que se verificó la trampa por señal (evita llamadas API excesivas)
    _ultimo_check_trampa: dict = {}
    ciclo = 0
    # ISO week numbers de la última apertura/cierre notificados (evita mensajes duplicados)
    _semana_apertura_enviada: int = -1
    _semana_cierre_enviada: int = -1

    while True:
        try:
            ciclo += 1
            ahora_utc = datetime.now(timezone.utc)

            # ── Fin de semana: pausa hasta el domingo 21:00 UTC ─────────────
            # Sábado (5) todo el día o Domingo (6) antes de las 21:00 UTC
            es_sabado  = ahora_utc.weekday() == 5
            es_domingo_antes_apertura = (ahora_utc.weekday() == 6 and ahora_utc.hour < 21)
            if es_sabado or es_domingo_antes_apertura:
                # Calcular segundos hasta el próximo domingo 21:00 UTC
                dias_hasta_domingo = (6 - ahora_utc.weekday()) % 7
                if dias_hasta_domingo == 0 and ahora_utc.hour >= 21:
                    dias_hasta_domingo = 7  # ya pasó la apertura de este domingo → siguiente semana
                apertura = (ahora_utc + timedelta(days=dias_hasta_domingo)).replace(
                    hour=21, minute=0, second=0, microsecond=0
                )
                segundos_espera = min((apertura - ahora_utc).total_seconds(), 3600)
                logger.info(f"[{ahora_utc.strftime('%Y-%m-%d %H:%M')} UTC] 💤 Fin de semana — "
                      f"monitor en pausa hasta Dom 21:00 UTC. Revisando en {int(segundos_espera//60)} min...")
                time.sleep(segundos_espera)
                continue

            semana_actual = ahora_utc.isocalendar()[1]

            # ── Notificación APERTURA DE MERCADOS (Dom ≥21:00 o Lunes) ──────
            if semana_actual != _semana_apertura_enviada and ahora_utc.weekday() in (6, 0):
                msg = (
                    "🟢 <b>APERTURA DE MERCADOS</b>\n"
                    f"📅 {ahora_utc.strftime('%A %d/%m/%Y %H:%M')} UTC\n"
                    "⚡ Mercados de futuros activos — monitor iniciado"
                )
                enviar_notificacion_telegram(msg)
                _semana_apertura_enviada = semana_actual
                logger.info(f"[{ahora_utc.strftime('%H:%M')} UTC] 🟢 Mensaje apertura enviado a Telegram")

            # ── Notificación CIERRE DE MERCADOS (Viernes ≥21:00 UTC) ────────
            if ahora_utc.weekday() == 4 and ahora_utc.hour >= 21 and semana_actual != _semana_cierre_enviada:
                msg = (
                    "🔴 <b>CIERRE DE MERCADOS</b>\n"
                    f"📅 {ahora_utc.strftime('%A %d/%m/%Y %H:%M')} UTC\n"
                    "🌙 Mercados cerrados hasta el Domingo 22:00 UTC"
                )
                enviar_notificacion_telegram(msg)
                _semana_cierre_enviada = semana_actual
                logger.info(f"[{ahora_utc.strftime('%H:%M')} UTC] 🔴 Mensaje cierre enviado a Telegram")

            ahora = ahora_utc.astimezone()   # hora local para logs existentes
            logger.info(f"\n[{ahora.strftime('%H:%M:%S')}] 🔄 Tick #{ciclo}")

            senales_activas = db.obtener_senales_activas()

            if not senales_activas:
                logger.info(f"[{ahora.strftime('%H:%M:%S')}] 📭 No hay señales activas")
            else:
                logger.info(f"[{ahora.strftime('%H:%M:%S')}] 📊 Señales activas: {len(senales_activas)}")

                # ── Paso 1: decidir qué señales toca revisar este tick ──────
                senales_a_revisar = []
                for senal in senales_activas:
                    senal_id  = senal['id']
                    categoria = _categoria_senal(senal['simbolo'])
                    intervalo = _intervalo_para(categoria)
                    ultimo    = ultimo_check.get(senal_id)
                    if ultimo is None or (ahora - ultimo).total_seconds() >= intervalo:
                        senales_a_revisar.append(senal)
                        ultimo_check[senal_id] = ahora

                if not senales_a_revisar:
                    logger.info(f"[{ahora.strftime('%H:%M:%S')}] ⏭️  Ninguna señal requiere revisión este tick")
                else:
                    # ── Paso 2: fetch deduplicado — 1 llamada por ticker ─────
                    tickers_necesarios = {}
                    for senal in senales_a_revisar:
                        base   = senal['simbolo'].split('_')[0]
                        ticker = SIMBOLO_TO_TICKER.get(base)
                        if ticker and ticker not in tickers_necesarios:
                            tickers_necesarios[ticker] = base

                    cache_precios: dict = {}   # ticker → (actual, max, min)
                    for ticker, base in tickers_necesarios.items():
                        result = _fetch_precios_ticker(ticker, db=db)
                        if result is not None:
                            cache_precios[ticker] = result
                            logger.info(f"  📡 {base} ({ticker}): ${result[0]:.2f}  H:{result[1]:.2f}  L:{result[2]:.2f}")
                        else:
                            logger.warning(f"  ⚠️ Sin datos para {base} ({ticker})")
                            try:
                                db.guardar_log(f"_fetch_precios_ticker devolvió None para {ticker} ({base})",
                                               'WARNING', 'signal_monitor', base)
                            except Exception:
                                pass

                # ── Paso 3: verificar niveles usando precios cacheados ────────
                for senal in senales_a_revisar:
                    simbolo   = senal['simbolo']
                    senal_id  = senal['id']
                    categoria = _categoria_senal(simbolo)
                    intervalo = _intervalo_para(categoria)

                    base   = simbolo.split('_')[0]
                    ticker = SIMBOLO_TO_TICKER.get(base)
                    precios = cache_precios.get(ticker) if ticker else None
                    if precios is None:
                        logger.warning(f"  ⚠️ No se pudo obtener precio de {simbolo}")
                        continue

                    precio_actual, precio_max, precio_min = precios

                    try:
                        db.registrar_precio(senal_id, precio_actual, senal_data=senal)
                    except Exception as e_reg:
                        logger.error(f"  ❌ registrar_precio falló para señal {senal_id}: {e_reg}")
                        try:
                            db.guardar_log(f"registrar_precio error señal #{senal_id}: {e_reg}",
                                           'ERROR', 'signal_monitor', simbolo.split('_')[0])
                        except Exception:
                            pass

                    precio_entrada = float(senal['precio_entrada'])
                    direccion      = senal['direccion']
                    beneficio_actual = calcular_beneficio_pct(
                        precio_entrada, precio_actual, direccion
                    )

                    tp1 = float(senal['tp1'])
                    tp2 = float(senal['tp2'])
                    tp3 = float(senal['tp3'])
                    sl  = float(senal['sl'])

                    etiqueta_tf = f"[{categoria.upper():<8} {intervalo}s]"
                    linea = (
                        f"  📊 {etiqueta_tf} {simbolo} | {direccion} | "
                        f"Entrada: ${precio_entrada:.2f} | "
                        f"Actual: ${precio_actual:.2f} (H:{precio_max:.2f} L:{precio_min:.2f}) | "
                        f"P&L: {beneficio_actual:+.2f}% | "
                        f"TPs: {tp1:.2f}/{tp2:.2f}/{tp3:.2f} SL: {sl:.2f}"
                    )
                    logger.info(linea)
                    try:
                        db.guardar_log(
                            f"#{senal_id} {simbolo} {direccion} | "
                            f"entry={precio_entrada:.2f} actual={precio_actual:.2f} "
                            f"(H:{precio_max:.2f} L:{precio_min:.2f}) "
                            f"P&L={beneficio_actual:+.2f}% | "
                            f"TP1={tp1:.2f} TP2={tp2:.2f} TP3={tp3:.2f} SL={sl:.2f}",
                            'INFO', 'signal_monitor', simbolo.split('_')[0]
                        )
                    except Exception:
                        pass

                    if direccion == 'COMPRA':
                        verificar_niveles_compra(senal, precio_actual, precio_min, precio_max, db, _progreso_50_enviado)
                    else:
                        verificar_niveles_venta(senal, precio_actual, precio_min, precio_max, db, _progreso_50_enviado)

                    # ── Verificar trampa de patrón (cada 10 ticks ≈ 5 min, sin TP1) ──
                    _ultimo_trampa = _ultimo_check_trampa.get(senal_id)
                    _intervalo_trampa = 10  # ticks (~5 min)
                    if (_ultimo_trampa is None or
                            ciclo - _ultimo_trampa >= _intervalo_trampa):
                        _verificar_trampa_patron(senal, db, _trampa_avisada)
                        _ultimo_check_trampa[senal_id] = ciclo

            # Limpiar del diccionario señales que ya no están activas
            ids_activos = {s['id'] for s in senales_activas} if senales_activas else set()
            for sid in list(ultimo_check):
                if sid not in ids_activos:
                    del ultimo_check[sid]
            _progreso_50_enviado.intersection_update(ids_activos)
            _trampa_avisada.intersection_update(ids_activos)
            for sid in list(_ultimo_check_trampa):
                if sid not in ids_activos:
                    del _ultimo_check_trampa[sid]

            # Cada 2 ticks (60s) verificar confirmaciones pendientes de señales 1H
            if ciclo % 2 == 0:
                _verificar_pendientes_confirm(db)

            # Cada ~hora (cada 120 ticks × 30s = 60 min) cerrar señales antiguas
            if ciclo % 120 == 0:
                cerrar_senales_antiguas(db, dias=2)

            # Heartbeat cada 10 ticks (~5 min) → puebla bot_logs y confirma que el monitor corre
            if ciclo % 10 == 0:
                n_activas = len(senales_activas) if senales_activas else 0
                try:
                    db.guardar_log(
                        f"Monitor tick #{ciclo} | señales activas: {n_activas}",
                        'INFO', 'signal_monitor'
                    )
                except Exception:
                    pass

            logger.info(f"[{ahora.strftime('%H:%M:%S')}] ⏳ Próximo tick en {_TICK}s...")
            time.sleep(_TICK)

        except KeyboardInterrupt:
            logger.warning(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Monitor detenido por usuario")
            break

        except Exception as e:
            logger.error(f"\n[{datetime.now().strftime('%H:%M:%S')}] ❌ Error en monitor: {e}")
            import traceback; traceback.print_exc()
            logger.info("🔄 Reintentando en 60 segundos...")
            try:
                db = DatabaseManager()
                db.guardar_log(f"Monitor crash: {e}", 'ERROR', 'signal_monitor')
                logger.info("✅ Reconexón a BD exitosa")
            except Exception as re_err:
                logger.warning(f"⚠️  Reconexón fallida: {re_err}")
            time.sleep(60)


def main():
    """Punto de entrada principal - Llama a monitor_senales()"""
    monitor_senales()


if __name__ == '__main__':
    main()
