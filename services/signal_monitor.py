"""
Signal Monitor - Monitorea señales activas y actualiza estados
Revisa cada 5 minutos todas las señales activas y verifica si alcanzaron TP o SL
"""

import time
import threading
import requests
import os
from datetime import datetime, timezone, timedelta

# Si PAUSE_BD_WRITES=true se omiten las escrituras de alto volumen (historial_precios,
# bot_logs) pero se mantienen los cambios de estado de señales (TP/SL/activacion).
_PAUSE_BD_WRITES = os.environ.get('PAUSE_BD_WRITES', 'false').lower() == 'true'
from dotenv import load_dotenv
from adapters.database import DatabaseManager, get_secondary_db
from adapters.data_provider import get_ohlcv as _get_ohlcv
from core.base_detector import simbolo_a_nombre

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

# Tiempo máximo que una señal puede estar en estado PENDIENTE_CONFIRMACION
_TIMEOUT_PENDIENTE_CONFIRM_HORAS = 4

# Vigencia máxima de señales en estado ACTIVA por timeframe
# Pasado este tiempo sin que el precio toque SL/TP, la señal se caduca automáticamente
_MAX_VIGENCIA_ACTIVA_HORAS: dict = {
    '1M':  2,    # Micro-scalp ultra: contexto válido máx 2 horas
    '5M':  4,    # Micro-scalp: contexto válido máx 4 horas
    '15M': 8,    # Scalp: una sesión de trading (~8h)
    '1H':  16,   # Intraday: sesión completa + asiática nocturna, sin solapar día siguiente
    '2H':  24,   # Semi-swing: entre intraday y swing corto (1 día hábil)
    '4H':  72,   # Swing corto: 3 días (3 sesiones de 4H)
    '1D':  120,  # Swing largo: 5 días hábiles (1 semana de mercado)
}

# Horas RESTANTES para caducar a partir de las cuales se envía el aviso previo
_AVISO_CADUCIDAD_HORAS_RESTANTES: dict = {
    '1M':  0.5,  # Avisa con 30 min de antelación
    '5M':  1,    # Avisa con 1h de antelación
    '15M': 2,    # Avisa con 2h de antelación
    '1H':  4,    # Avisa con 4h de antelación
    '2H':  8,    # Avisa con 8h de antelación
    '4H':  24,   # Avisa con 24h de antelación
    '1D':  48,   # Avisa con 48h de antelación
}

def obtener_thread_id(simbolo: str):
    """Devuelve el message_thread_id de Telegram según el timeframe del símbolo."""
    sufijo = simbolo.split('_')[-1].upper() if '_' in simbolo else ''
    if sufijo in ('1M', '15M', '5M'):
        return THREAD_ID_SCALPING
    if sufijo in ('1H', '2H'):
        return THREAD_ID_INTRADAY
    # 4H, 1D y sin sufijo → Swing
    return THREAD_ID_SWING

# Mapeo de símbolos de señal → ticker interno (clave en data_provider/_TICKER_MAP_TWELVE)
SIMBOLO_TO_TICKER = {
    'BTCUSD':  'BTC-USD',
    'XAUUSD':  'GC=F',      # Gold Futures
    'SPX500':  '^GSPC',     # S&P 500
    'NAS100':  '^IXIC',     # NASDAQ Composite
    'EURUSD':  'EURUSD=X',  # EUR/USD Forex
    'WTIUSD':  'CL=F',      # WTI Crude Oil Futures
    'XAGUSD':  'SI=F',      # Silver Futures
    # Compatibilidad con señales antiguas guardadas con ticker yfinance como símbolo
    'GC=F':    'GC=F',
    'BTC-USD': 'BTC-USD',
    'SI=F':    'SI=F',
    'CL=F':    'CL=F',
    '^GSPC':   '^GSPC',
    '^IXIC':   '^IXIC',
}

def _fetch_precios_ticker(ticker: str, db=None) -> tuple | None:
    """Obtiene (precio_actual, precio_max_5velas, precio_min_5velas) para un ticker.

    Estrategia:
      1. Lee datos 1m de BD (ohlcv_poller — precisión minuto, captura mechas intra-vela).
         Si la vela más reciente tiene <= 10 min → devuelve directamente.
      2. Fallback a datos 5m de BD si no hay 1m frescos.
      3. Fallback a Twelve Data (get_ohlcv 1m) si la BD no tiene datos recientes.
    """
    # ── Paso 1: BD 1m (rápido, precisión al minuto) ───────────────────────────
    if db is not None:
        try:
            res = db.obtener_precio_reciente_bd(ticker, '1m', max_minutos=10)
            if res is not None:
                return res
        except Exception as e:
            logger.error(f"⚠️ [{ticker}] Error leyendo precio 1m de BD: {e}")
        # ── Paso 2: BD 5m (fallback si no hay 1m) ────────────────────────────
        try:
            res = db.obtener_precio_reciente_bd(ticker, '5m', max_minutos=10)
            if res is not None:
                return res
        except Exception as e:
            logger.error(f"⚠️ [{ticker}] Error leyendo precio 5m de BD: {e}")

    # ── Paso 3: Twelve Data vía get_ohlcv 1m (fallback cuando BD > 10 min) ───
    try:
        hist, _ = _get_ohlcv(ticker, period='1d', interval='1m')
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


def _sse_tipo_desde_mensaje(mensaje: str) -> str:
    """Detecta el tipo de evento SSE a partir del texto del mensaje."""
    txt = mensaje.upper()
    if '50%' in txt or 'AVANZANDO' in txt:
        return 'progreso'
    if 'TP3' in txt:
        return 'tp3'
    if 'TP2' in txt:
        return 'tp2'
    if 'TP1' in txt:
        return 'tp1'
    if 'STOP LOSS' in txt or '❌' in txt:
        return 'sl'
    if 'BREAKEVEN' in txt:
        return 'breakeven'
    if 'CADUCAD' in txt:
        return 'caducada'
    return 'actualizacion'


def _publicar_sse_senal(mensaje: str, simbolo: str) -> None:
    """Publica el evento de señal en el broker SSE (best-effort)."""
    try:
        from bridge.sse_broker import broker
        if broker.num_clientes == 0:
            return
        # simbolo puede ser 'XAUUSD_1H', 'XAUUSD_4H', etc.
        partes = simbolo.split('_') if simbolo else []
        simbolo_base = partes[0] if partes else (simbolo or '')
        timeframe = partes[1].upper() if len(partes) > 1 else ''
        import re
        dir_match = re.search(r'\b(COMPRA|VENTA)\b', mensaje, re.IGNORECASE)
        ent_match = re.search(r'Entrada[:\s$€£]+([0-9]+(?:[.,][0-9]+)?)', mensaje, re.IGNORECASE)
        id_match  = re.search(r'#(\d+)', mensaje)
        senal_id  = int(id_match.group(1)) if id_match else None
        broker.publicar_senal(
            tipo=_sse_tipo_desde_mensaje(mensaje),
            simbolo=simbolo_base,
            timeframe=timeframe,
            direccion=(dir_match.group(1).upper() if dir_match else ''),
            precio_entrada=float(ent_match.group(1).replace(',', '.')) if ent_match else 0.0,
            senal_id=senal_id,
        )
    except Exception:
        pass


def enviar_notificacion_telegram(mensaje: str, simbolo: str = None, reply_to_message_id: int = None):
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
        if reply_to_message_id:
            data['reply_to_message_id'] = reply_to_message_id

        response = requests.post(url, json=data, timeout=10)

        if response.status_code == 200:
            logger.info(f"✅ Notificación enviada: {mensaje[:50]}...")
            # Publicar en SSE para que el frontend lo reciba en tiempo real
            _publicar_sse_senal(mensaje, simbolo or '')
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
    reply_msg_id = senal.get('telegram_message_id')

    # Convertir valores numéricos de BD — guard para valores None (columnas no rellenadas)
    try:
        precio_entrada = float(senal['precio_entrada'])
        tp1 = float(senal['tp1'])
        sl  = float(senal['sl'])
    except (TypeError, ValueError):
        logger.info(f"⚠️ [monitor] Señal {senal_id} tiene precios nulos/inválidos — saltando")
        return

    # Leer flags de TPs antes del guard para evitar falsa cancelación de breakeven
    tp1_alcanzado = bool(int(senal.get('tp1_alcanzado') or 0))
    tp2_alcanzado = bool(int(senal.get('tp2_alcanzado') or 0))
    tp3_alcanzado = bool(int(senal.get('tp3_alcanzado') or 0))
    sl_alcanzado  = bool(int(senal.get('sl_alcanzado')  or 0))

    # tp2/tp3 pueden ser None en señales contra tendencia (solo TP1 activo)
    tp2 = float(senal['tp2']) if senal.get('tp2') is not None else None
    tp3 = float(senal['tp3']) if senal.get('tp3') is not None else None

    # ── Guard: señal inoperable (SL ≈ entrada) ──────────────────────────────
    # Excepción: si TP1 ya fue alcanzado, SL=entry es breakeven legítimo → no cancelar
    _SL_MIN_DIST = 0.5
    if abs(sl - precio_entrada) < _SL_MIN_DIST and not tp1_alcanzado:
        logger.warning(
            f"⛔ [monitor] Señal #{senal_id} CANCELADA — SL ({sl}) ≈ entrada ({precio_entrada}): "
            f"señal inoperable (diferencia {abs(sl - precio_entrada):.4f} < {_SL_MIN_DIST})"
        )
        db.cerrar_senal(senal_id, 'CANCELADA')
        enviar_notificacion_telegram(
            f"⛔ <b>Señal #{senal_id} cancelada automáticamente</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {simbolo_a_nombre(simbolo.split('_')[0])} | COMPRA\n"
            f"💰 Entrada: ${precio_entrada:.2f}\n"
            f"🛑 SL detectado igual a entrada (${sl:.2f}) — señal inoperable\n"
            f"🔖 <code>#{senal_id}</code>",
            simbolo, reply_to_message_id=reply_msg_id
        )
        return

    # Verificar TP3 (mayor prioridad) — usa High reciente para capturar picos entre polls
    if tp3 is not None and precio_max >= tp3 and not tp3_alcanzado:
        beneficio = calcular_beneficio_pct(precio_entrada, tp3, 'COMPRA')
        db.actualizar_estado_senal(senal_id, 'TP3', beneficio)
        db.registrar_tp3_hit(senal_id, simbolo, 'COMPRA', tp3, precio_actual, beneficio)

        mensaje = f"""
� <b>TP3 ALCANZADO — CIERRA TODO AHORA</b>

📊 {simbolo} | COMPRA
💰 Entrada: ${precio_entrada:.2f}
✅ TP3: ${tp3:.2f}
📈 Actual: ${precio_actual:.2f}
💵 Beneficio total: +{beneficio:.2f}%

📋 <b>ACCIÓN — HACER AHORA:</b>
🔴 Cerrar el 100% restante a mercado
🏆 Operación completada
━━━━━━━━━━━━━━━━━━━━
🔖 <code>#{senal_id}</code>
        """
        enviar_notificacion_telegram(mensaje, simbolo, reply_to_message_id=reply_msg_id)
        return

    # Verificar TP2 — usa High reciente
    if tp2 is not None and precio_max >= tp2 and not tp2_alcanzado:
        beneficio = calcular_beneficio_pct(precio_entrada, tp2, 'COMPRA')
        db.actualizar_estado_senal(senal_id, 'TP2', beneficio)
        db.registrar_tp2_hit(senal_id, simbolo, 'COMPRA', tp2, precio_actual, beneficio)

        mensaje = f"""
⚠️ <b>TP2 ALCANZADO — CIERRA PARCIAL AHORA</b>

📊 {simbolo} | COMPRA
💰 Entrada: ${precio_entrada:.2f}
✅ TP2: ${tp2:.2f}
📈 Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%

📋 <b>ACCIÓN — HACER AHORA:</b>
🔴 Cerrar 33% de la posición a mercado
🔒 SL movido automáticamente a TP1 (${tp1:.2f})
{f"⏳ Dejar correr el resto hacia TP3 (${tp3:.2f})" if tp3 else "⏳ Dejar correr el resto hacia cierre manual"}
━━━━━━━━━━━━━━━━━━━━
🔖 <code>#{senal_id}</code>
        """
        enviar_notificacion_telegram(mensaje, simbolo, reply_to_message_id=reply_msg_id)
        return

    # Verificar TP1 — usa High reciente
    if precio_max >= tp1 and not tp1_alcanzado:
        beneficio = calcular_beneficio_pct(precio_entrada, tp1, 'COMPRA')
        db.actualizar_estado_senal(senal_id, 'TP1', beneficio)
        db.registrar_tp1_hit(senal_id, simbolo, 'COMPRA', tp1, precio_actual, beneficio)

        _accion_tp1_buy = (
            f"🔴 Cerrar 33% de la posición\n🔒 Mover SL a breakeven (${precio_entrada:.2f})\n⏳ Dejar correr hacia TP2 (${tp2:.2f})"
            if tp2 is not None
            else f"🔴 Cerrar el 100% — objetivo único (señal contra tendencia)\n🔒 No dejar correr más allá de TP1"
        )
        mensaje = f"""
⚠️ <b>TP1 ALCANZADO — CIERRA PARCIAL AHORA</b>

📊 {simbolo} | COMPRA
💰 Entrada: ${precio_entrada:.2f}
✅ TP1: ${tp1:.2f}
📈 Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%

📋 <b>ACCIÓN — HACER AHORA:</b>
{_accion_tp1_buy}
━━━━━━━━━━━━━━━━━━━━
🔖 <code>#{senal_id}</code>
        """
        enviar_notificacion_telegram(mensaje, simbolo, reply_to_message_id=reply_msg_id)
        return

    # Verificar progreso 50% hacia TP1 — aviso intermedio
    if progreso_50_enviado is not None and senal_id not in progreso_50_enviado:
        dist_total = abs(tp1 - precio_entrada)
        dist_recorrida = precio_max - precio_entrada  # BUY: precio sube
        if dist_total > 0 and dist_recorrida >= dist_total * 0.5:
            pct = round(dist_recorrida / dist_total * 100)
            beneficio_parcial = calcular_beneficio_pct(precio_entrada, precio_actual, 'COMPRA')
            msg_50 = (
                f"⚡ <b>Trade avanzando — {pct}% hacia TP1</b>\n"
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
            enviar_notificacion_telegram(msg_50, simbolo, reply_to_message_id=reply_msg_id)
            progreso_50_enviado.add(senal_id)
            logger.info(f"  ⚡ [50%] {simbolo} COMPRA — notificación de progreso enviada")
            return  # no verificar SL en el mismo ciclo

    # Verificar SL / Breakeven
    # Usa precio_actual (Close) en lugar de precio_min (Low) para evitar falsas
    # activaciones por mechas temporales que ya se han recuperado.
    if precio_actual <= sl and not sl_alcanzado:
        if tp2_alcanzado:
            # TP2 ya se tocó → SL fue movido a TP1 (trailing stop) → cierre con ganancia
            beneficio = calcular_beneficio_pct(precio_entrada, sl, 'COMPRA')
            db.actualizar_estado_senal(senal_id, 'SL', beneficio)
            mensaje = (
                f"🔒 <b>TRAILING STOP — CERRADO CON GANANCIA</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 COMPRA | TP2 alcanzado previamente\n"
                f"💰 Entrada: ${precio_entrada:.2f}\n"
                f"📍 Trailing stop en TP1: ${sl:.2f}\n"
                f"📉 Actual: ${precio_actual:.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Trade cerrado con <b>+{beneficio:.2f}%</b>\n"
                f"🔖 <code>#{senal_id}</code>"
            )
            enviar_notificacion_telegram(mensaje, simbolo, reply_to_message_id=reply_msg_id)
            logger.info(f"  🔒 [{simbolo}] TRAILING STOP BUY — cerrada en TP1 ${sl:.2f} (+{beneficio:.2f}%)")
        elif tp1_alcanzado:
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
            enviar_notificacion_telegram(mensaje, simbolo, reply_to_message_id=reply_msg_id)
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
            enviar_notificacion_telegram(mensaje, simbolo, reply_to_message_id=reply_msg_id)
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
    reply_msg_id = senal.get('telegram_message_id')

    # Convertir valores numéricos de BD — guard para valores None (columnas no rellenadas)
    try:
        precio_entrada = float(senal['precio_entrada'])
        tp1 = float(senal['tp1'])
        sl  = float(senal['sl'])
    except (TypeError, ValueError):
        logger.warning(f"⚠️ [monitor] Señal {senal_id} tiene precios nulos/inválidos — saltando")
        return

    # Leer flags de TPs antes del guard para evitar falsa cancelación de breakeven
    tp1_alcanzado = bool(int(senal.get('tp1_alcanzado') or 0))
    tp2_alcanzado = bool(int(senal.get('tp2_alcanzado') or 0))
    tp3_alcanzado = bool(int(senal.get('tp3_alcanzado') or 0))
    sl_alcanzado  = bool(int(senal.get('sl_alcanzado')  or 0))

    # tp2/tp3 pueden ser None en señales contra tendencia (solo TP1 activo)
    tp2 = float(senal['tp2']) if senal.get('tp2') is not None else None
    tp3 = float(senal['tp3']) if senal.get('tp3') is not None else None

    # ── Guard: señal inoperable (SL ≈ entrada) ──────────────────────────────
    # Excepción: si TP1 ya fue alcanzado, SL=entry es breakeven legítimo → no cancelar
    _SL_MIN_DIST = 0.5
    if abs(sl - precio_entrada) < _SL_MIN_DIST and not tp1_alcanzado:
        logger.warning(
            f"⛔ [monitor] Señal #{senal_id} CANCELADA — SL ({sl}) ≈ entrada ({precio_entrada}): "
            f"señal inoperable (diferencia {abs(sl - precio_entrada):.4f} < {_SL_MIN_DIST})"
        )
        db.cerrar_senal(senal_id, 'CANCELADA')
        enviar_notificacion_telegram(
            f"⛔ <b>Señal #{senal_id} cancelada automáticamente</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {simbolo_a_nombre(simbolo.split('_')[0])} | VENTA\n"
            f"💰 Entrada: ${precio_entrada:.2f}\n"
            f"🛑 SL detectado igual a entrada (${sl:.2f}) — señal inoperable\n"
            f"🔖 <code>#{senal_id}</code>",
            simbolo, reply_to_message_id=reply_msg_id
        )
        return

    # Verificar TP3 (menor precio) — usa Low reciente para capturar picos entre polls
    if tp3 is not None and precio_min <= tp3 and not tp3_alcanzado:
        beneficio = calcular_beneficio_pct(precio_entrada, tp3, 'VENTA')
        db.actualizar_estado_senal(senal_id, 'TP3', beneficio)
        db.registrar_tp3_hit(senal_id, simbolo, 'VENTA', tp3, precio_actual, beneficio)

        mensaje = f"""
� <b>TP3 ALCANZADO — CIERRA TODO AHORA</b>

📊 {simbolo} | VENTA
💰 Entrada: ${precio_entrada:.2f}
✅ TP3: ${tp3:.2f}
📉 Actual: ${precio_actual:.2f}
💵 Beneficio total: +{beneficio:.2f}%

📋 <b>ACCIÓN — HACER AHORA:</b>
🔴 Cerrar el 100% restante a mercado
🏆 Operación completada
━━━━━━━━━━━━━━━━━━━━
🔖 <code>#{senal_id}</code>
        """
        enviar_notificacion_telegram(mensaje, simbolo, reply_to_message_id=reply_msg_id)
        return

    # Verificar TP2 — usa Low reciente
    if tp2 is not None and precio_min <= tp2 and not tp2_alcanzado:
        beneficio = calcular_beneficio_pct(precio_entrada, tp2, 'VENTA')
        db.actualizar_estado_senal(senal_id, 'TP2', beneficio)
        db.registrar_tp2_hit(senal_id, simbolo, 'VENTA', tp2, precio_actual, beneficio)

        mensaje = f"""
⚠️ <b>TP2 ALCANZADO — CIERRA PARCIAL AHORA</b>

📊 {simbolo} | VENTA
💰 Entrada: ${precio_entrada:.2f}
✅ TP2: ${tp2:.2f}
📉 Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%

📋 <b>ACCIÓN — HACER AHORA:</b>
🔴 Cerrar 33% de la posición a mercado
🔒 SL movido automáticamente a TP1 (${tp1:.2f})
{f"⏳ Dejar correr el resto hacia TP3 (${tp3:.2f})" if tp3 else "⏳ Dejar correr el resto hacia cierre manual"}
━━━━━━━━━━━━━━━━━━━━
🔖 <code>#{senal_id}</code>
        """
        enviar_notificacion_telegram(mensaje, simbolo, reply_to_message_id=reply_msg_id)
        return

    # Verificar TP1 — usa Low reciente
    if precio_min <= tp1 and not tp1_alcanzado:
        beneficio = calcular_beneficio_pct(precio_entrada, tp1, 'VENTA')
        db.actualizar_estado_senal(senal_id, 'TP1', beneficio)
        db.registrar_tp1_hit(senal_id, simbolo, 'VENTA', tp1, precio_actual, beneficio)

        _accion_tp1_sell = (
            f"🔴 Cerrar 33% de la posición\n🔒 Mover SL a breakeven (${precio_entrada:.2f})\n⏳ Dejar correr hacia TP2 (${tp2:.2f})"
            if tp2 is not None
            else f"🔴 Cerrar el 100% — objetivo único (señal contra tendencia)\n🔒 No dejar correr más allá de TP1"
        )
        mensaje = f"""
⚠️ <b>TP1 ALCANZADO — CIERRA PARCIAL AHORA</b>

📊 {simbolo} | VENTA
💰 Entrada: ${precio_entrada:.2f}
✅ TP1: ${tp1:.2f}
📉 Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%

📋 <b>ACCIÓN — HACER AHORA:</b>
{_accion_tp1_sell}
━━━━━━━━━━━━━━━━━━━━
🔖 <code>#{senal_id}</code>
        """
        enviar_notificacion_telegram(mensaje, simbolo, reply_to_message_id=reply_msg_id)
        return

    # Verificar progreso 50% hacia TP1 — aviso intermedio
    if progreso_50_enviado is not None and senal_id not in progreso_50_enviado:
        dist_total = abs(precio_entrada - tp1)
        dist_recorrida = precio_entrada - precio_min  # VENTA: precio baja
        if dist_total > 0 and dist_recorrida >= dist_total * 0.5:
            pct = round(dist_recorrida / dist_total * 100)
            beneficio_parcial = calcular_beneficio_pct(precio_entrada, precio_actual, 'VENTA')
            msg_50 = (
                f"⚡ <b>Trade avanzando — {pct}% hacia TP1</b>\n"
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
            enviar_notificacion_telegram(msg_50, simbolo, reply_to_message_id=reply_msg_id)
            progreso_50_enviado.add(senal_id)
            logger.info(f"  ⚡ [50%] {simbolo} VENTA — notificación de progreso enviada")
            return  # no verificar SL en el mismo ciclo

    # Verificar SL / Breakeven
    # Usa precio_actual (Close) en lugar de precio_max (High) para evitar falsas
    # activaciones por mechas temporales que ya se han recuperado.
    if precio_actual >= sl and not sl_alcanzado:
        if tp2_alcanzado:
            # TP2 ya se tocó → SL fue movido a TP1 (trailing stop) → cierre con ganancia
            beneficio = calcular_beneficio_pct(precio_entrada, sl, 'VENTA')
            db.actualizar_estado_senal(senal_id, 'SL', beneficio)
            mensaje = (
                f"🔒 <b>TRAILING STOP — CERRADO CON GANANCIA</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 VENTA | TP2 alcanzado previamente\n"
                f"💰 Entrada: ${precio_entrada:.2f}\n"
                f"📍 Trailing stop en TP1: ${sl:.2f}\n"
                f"📈 Actual: ${precio_actual:.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Trade cerrado con <b>+{beneficio:.2f}%</b>\n"
                f"🔖 <code>#{senal_id}</code>"
            )
            enviar_notificacion_telegram(mensaje, simbolo, reply_to_message_id=reply_msg_id)
            logger.info(f"  🔒 [{simbolo}] TRAILING STOP SELL — cerrada en TP1 ${sl:.2f} (+{beneficio:.2f}%)")
        elif tp1_alcanzado:
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
            enviar_notificacion_telegram(mensaje, simbolo, reply_to_message_id=reply_msg_id)
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
            enviar_notificacion_telegram(mensaje, simbolo, reply_to_message_id=reply_msg_id)
        return


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
        enviar_notificacion_telegram(msg, simbolo, reply_to_message_id=senal.get('telegram_message_id'))
        trampa_avisada.add(senal_id)
        logger.info(
            f"  ⚠️ [{simbolo}] TRAMPA detectada señal #{senal_id} ({direccion}): "
            + " | ".join(motivos)
        )

    except Exception as e:
        logger.debug(f"  [trampa] Error analizando {simbolo}: {e}")


def _verificar_reversal_post_tp1(senal: dict, _reversal_tp1_avisado: set, db) -> None:
    """
    Gestiona reversiones cuando el trade está entre TP1 y TP2.

    Decisión binaria (sin mensajes ambiguos):
      - 3/3 señales → cierra la señal en breakeven automáticamente
      - 2/3 señales → mensaje tranquilizador: SL protege, mantenemos hacia TP2
      - <2 señales  → sin acción

    Condiciones evaluadas (de 3):
      1. Precio retrocedió ≥ 0.4×ATR desde el máximo/mínimo reciente de 8 velas
      2. ≥ 2 velas contra-tendencia en las últimas 4
      3. RSI en zona extrema y revirtiendo

    Notifica solo UNA VEZ por señal via _reversal_tp1_avisado.
    """
    from core.indicators import calcular_atr

    senal_id  = senal['id']
    simbolo   = senal['simbolo']
    direccion = senal['direccion']

    tp1_alcanzado = bool(int(senal.get('tp1_alcanzado') or 0))
    tp2_alcanzado = bool(int(senal.get('tp2_alcanzado') or 0))

    # Solo actuar entre TP1 y TP2
    if not tp1_alcanzado or tp2_alcanzado:
        return

    # Avisar solo una vez por señal
    if senal_id in _reversal_tp1_avisado:
        return

    simbolo_base = simbolo.split('_')[0]
    ticker = SIMBOLO_TO_TICKER.get(simbolo_base)
    if not ticker:
        return

    sufijo = simbolo.split('_')[-1].upper() if '_' in simbolo else ''
    if sufijo == '5M':
        interval, period = '5m', '1d'
    elif sufijo == '15M':
        interval, period = '15m', '2d'
    elif sufijo == '1H':
        interval, period = '1h', '5d'
    else:  # 4H, 1D
        interval, period = '4h', '30d'

    try:
        df, _ = _get_ohlcv(ticker, period=period, interval=interval)
        if df is None or len(df) < 15:
            return

        close = df['Close']
        high  = df['High']
        low   = df['Low']

        atr = float(calcular_atr(df, 14).iloc[-1])
        if atr <= 0:
            return

        precio_entrada = float(senal['precio_entrada'])
        tp2 = float(senal['tp2'])
        sl  = float(senal['sl'])
        precio_actual  = float(close.iloc[-1])

        # RSI 14
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, float('inf'))
        rsi_series   = 100 - (100 / (1 + rs))
        rsi_actual   = float(rsi_series.iloc[-1])
        rsi_anterior = float(rsi_series.iloc[-2])

        senales_reversal = []

        if direccion == 'COMPRA':
            # 1. Retroceso desde máximo reciente
            max_reciente = float(high.tail(8).max())
            retroceso = max_reciente - precio_actual
            if retroceso >= 0.4 * atr:
                senales_reversal.append(
                    f"Retrocedió {retroceso:.1f} pts desde máx reciente ${max_reciente:.2f} ({retroceso/atr:.1f}×ATR)"
                )
            # 2. Velas bajistas en las últimas 4
            ultimas_4 = df.tail(4)
            bajistas = sum(1 for _, r in ultimas_4.iterrows() if r['Close'] < r['Open'])
            if bajistas >= 2:
                senales_reversal.append(f"{bajistas} velas bajistas en las últimas 4 velas")
            # 3. RSI sobrecomprado y bajando
            if rsi_actual > 65 and rsi_actual < rsi_anterior:
                senales_reversal.append(
                    f"RSI sobrecomprado y bajando ({rsi_anterior:.0f}→{rsi_actual:.0f})"
                )

        else:  # VENTA
            # 1. Recuperación desde mínimo reciente
            min_reciente = float(low.tail(8).min())
            recuperacion = precio_actual - min_reciente
            if recuperacion >= 0.4 * atr:
                senales_reversal.append(
                    f"Recuperó {recuperacion:.1f} pts desde mín reciente ${min_reciente:.2f} ({recuperacion/atr:.1f}×ATR)"
                )
            # 2. Velas alcistas en las últimas 4
            ultimas_4 = df.tail(4)
            alcistas = sum(1 for _, r in ultimas_4.iterrows() if r['Close'] > r['Open'])
            if alcistas >= 2:
                senales_reversal.append(f"{alcistas} velas alcistas en las últimas 4 velas")
            # 3. RSI sobrevendido y subiendo
            if rsi_actual < 35 and rsi_actual > rsi_anterior:
                senales_reversal.append(
                    f"RSI sobrevendido y subiendo ({rsi_anterior:.0f}→{rsi_actual:.0f})"
                )

        if len(senales_reversal) < 2:
            return

        beneficio = calcular_beneficio_pct(precio_entrada, precio_actual, direccion)
        icono = '🟢' if direccion == 'COMPRA' else '🔴'
        reply_msg_id = senal.get('telegram_message_id')
        iconos_senal = ['📉', '🕯️', '📊']
        senal_lines = "\n".join(f"  • {iconos_senal[i]} {s}" for i, s in enumerate(senales_reversal))

        if len(senales_reversal) == 3:
            # Reversión confirmada → cerrar en breakeven automáticamente
            db.actualizar_estado_senal(senal_id, 'BREAKEVEN')
            msg = (
                f"🚫 <b>SEÑAL CERRADA</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{icono} {simbolo} | {direccion}\n"
                f"💰 Entrada: ${precio_entrada:.2f}\n"
                f"📍 Cierre en breakeven: ${sl:.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Capital protegido — sin pérdida\n"
                f"🔖 <code>#{senal_id}</code>"
            )
            logger.info(
                f"  🚫 [{simbolo}] Señal #{senal_id} CERRADA por reversión confirmada (3/3)"
            )
        else:
            # 2/3: bajo presión pero SL garantiza el capital → no notificar, seguir monitoreando
            logger.info(
                f"  📊 [{simbolo}] Señal #{senal_id} bajo presión (2/3) — manteniendo hacia TP2 (sin notificación)"
            )
            return

        enviar_notificacion_telegram(msg, simbolo, reply_to_message_id=reply_msg_id)
        _reversal_tp1_avisado.add(senal_id)

    except Exception as e:
        logger.debug(f"  [reversal_tp1] Error analizando {simbolo}: {e}")


def _verificar_reversal_post_tp2(senal: dict, _reversal_tp2_avisado: set, db) -> None:
    """
    Gestiona reversiones cuando el trade está entre TP2 y TP3.

    Decisión binaria (sin mensajes ambiguos):
      - 3/3 señales → cierra la señal asegurando el beneficio de TP2
      - 2/3 señales → mensaje tranquilizador: beneficio protegido, mantenemos hacia TP3
      - <2 señales  → sin acción

    Condiciones evaluadas (de 3):
      1. Precio retrocedió ≥ 0.4×ATR desde el máximo/mínimo reciente de 8 velas
      2. ≥ 2 velas contra-tendencia en las últimas 4
      3. RSI en zona extrema y revirtiendo

    Avisa solo UNA VEZ por señal via _reversal_tp2_avisado.
    """
    from core.indicators import calcular_atr

    senal_id  = senal['id']
    simbolo   = senal['simbolo']
    direccion = senal['direccion']

    tp2_alcanzado = bool(int(senal.get('tp2_alcanzado') or 0))
    tp3_alcanzado = bool(int(senal.get('tp3_alcanzado') or 0))

    # Solo actuar entre TP2 y TP3
    if not tp2_alcanzado or tp3_alcanzado:
        return

    # Avisar solo una vez por señal
    if senal_id in _reversal_tp2_avisado:
        return

    simbolo_base = simbolo.split('_')[0]
    ticker = SIMBOLO_TO_TICKER.get(simbolo_base)
    if not ticker:
        return

    sufijo = simbolo.split('_')[-1].upper() if '_' in simbolo else ''
    if sufijo == '5M':
        interval, period = '5m', '1d'
    elif sufijo == '15M':
        interval, period = '15m', '2d'
    elif sufijo == '1H':
        interval, period = '1h', '5d'
    else:  # 4H, 1D
        interval, period = '4h', '30d'

    try:
        df, _ = _get_ohlcv(ticker, period=period, interval=interval)
        if df is None or len(df) < 15:
            return

        close = df['Close']
        high  = df['High']
        low   = df['Low']

        atr = float(calcular_atr(df, 14).iloc[-1])
        if atr <= 0:
            return

        precio_entrada = float(senal['precio_entrada'])
        tp1 = float(senal['tp1'])
        tp3 = float(senal['tp3'])
        precio_actual  = float(close.iloc[-1])

        # RSI 14
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, float('inf'))
        rsi_series   = 100 - (100 / (1 + rs))
        rsi_actual   = float(rsi_series.iloc[-1])
        rsi_anterior = float(rsi_series.iloc[-2])

        senales_reversal = []

        if direccion == 'COMPRA':
            # 1. Retroceso desde máximo reciente
            max_reciente = float(high.tail(8).max())
            retroceso = max_reciente - precio_actual
            if retroceso >= 0.4 * atr:
                senales_reversal.append(
                    f"Retrocedió {retroceso:.1f} pts desde máx reciente ${max_reciente:.2f} ({retroceso/atr:.1f}×ATR)"
                )
            # 2. Velas bajistas en las últimas 4
            ultimas_4 = df.tail(4)
            bajistas = sum(1 for _, r in ultimas_4.iterrows() if r['Close'] < r['Open'])
            if bajistas >= 2:
                senales_reversal.append(f"{bajistas} velas bajistas en las últimas 4 velas")
            # 3. RSI sobrecomprado y bajando
            if rsi_actual > 65 and rsi_actual < rsi_anterior:
                senales_reversal.append(
                    f"RSI sobrecomprado y bajando ({rsi_anterior:.0f}→{rsi_actual:.0f})"
                )

        else:  # VENTA
            # 1. Recuperación desde mínimo reciente
            min_reciente = float(low.tail(8).min())
            recuperacion = precio_actual - min_reciente
            if recuperacion >= 0.4 * atr:
                senales_reversal.append(
                    f"Recuperó {recuperacion:.1f} pts desde mín reciente ${min_reciente:.2f} ({recuperacion/atr:.1f}×ATR)"
                )
            # 2. Velas alcistas en las últimas 4
            ultimas_4 = df.tail(4)
            alcistas = sum(1 for _, r in ultimas_4.iterrows() if r['Close'] > r['Open'])
            if alcistas >= 2:
                senales_reversal.append(f"{alcistas} velas alcistas en las últimas 4 velas")
            # 3. RSI sobrevendido y subiendo
            if rsi_actual < 35 and rsi_actual > rsi_anterior:
                senales_reversal.append(
                    f"RSI sobrevendido y subiendo ({rsi_anterior:.0f}→{rsi_actual:.0f})"
                )

        if len(senales_reversal) < 2:
            return

        beneficio = calcular_beneficio_pct(precio_entrada, precio_actual, direccion)
        icono = '🟢' if direccion == 'COMPRA' else '🔴'
        reply_msg_id = senal.get('telegram_message_id')
        iconos_senal = ['📉', '🕯️', '📊']
        senal_lines = "\n".join(f"  • {iconos_senal[i]} {s}" for i, s in enumerate(senales_reversal))

        if len(senales_reversal) == 3:
            # Reversión confirmada → cerrar asegurando el beneficio de TP2
            db.cerrar_senal(senal_id, 'TP2', beneficio)
            msg = (
                f"✅ <b>SEÑAL CERRADA — Beneficio asegurado en TP2</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{icono} {simbolo} | {direccion}\n"
                f"💰 Entrada: ${precio_entrada:.2f}\n"
                f"📍 Cierre:  ${precio_actual:.2f}  ({beneficio:+.2f}%)\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f" Ganancia bloqueada — TP3 cancelado\n"
                f"🔖 <code>#{senal_id}</code>"
            )
            logger.info(
                f"  ✅ [{simbolo}] Señal #{senal_id} CERRADA en TP2 por reversión confirmada (3/3)"
            )
        else:
            # 2/3: presión no confirmada → no notificar, seguir monitoreando hacia TP3
            logger.info(
                f"  📊 [{simbolo}] Señal #{senal_id} bajo presión (2/3) — manteniendo hacia TP3 (sin notificación)"
            )
            return

        enviar_notificacion_telegram(msg, simbolo, reply_to_message_id=reply_msg_id)
        _reversal_tp2_avisado.add(senal_id)

    except Exception as e:
        logger.debug(f"  [reversal_tp2] Error analizando {simbolo}: {e}")


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
            enviar_notificacion_telegram(msg_caduca, simbolo, reply_to_message_id=senal.get('telegram_message_id'))
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
            tp2 = float(senal['tp2']) if senal.get('tp2') is not None else None
            tp3 = float(senal['tp3']) if senal.get('tp3') is not None else None
            sl  = float(senal['sl'])
            score = senal.get('score', '?')
        except (TypeError, ValueError):
            logger.warning(f"  ⚠️ [#{senal['id']}] Precios inválidos en PENDIENTE_CONFIRM — saltando")
            continue

        # ── Verificar si el precio ya superó TP1 sin que hubiera posición ──
        precios_now = _fetch_precios_ticker(ticker, db=db)
        if precios_now is not None:
            precio_actual, precio_max, precio_min = precios_now
            tp1_superado = (
                (direccion == 'VENTA'  and precio_min <= tp1) or
                (direccion == 'COMPRA' and precio_max >= tp1)
            )
            if tp1_superado:
                db.caducar_senal_pendiente(senal_id)
                icono_dir = '📉' if direccion == 'VENTA' else '📈'
                msg_perdida = (
                    f"⚡ <b>Señal perdida — TP1 alcanzado sin posición</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"{icono_dir} {simbolo} | {direccion}\n"
                    f"💰 Entrada esperada: ${precio_entrada:.2f}\n"
                    f"🎯 TP1 ya superado: ${tp1:.2f}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"ℹ️ La señal estaba pendiente de confirmación — no había posición abierta"
                )
                enviar_notificacion_telegram(msg_perdida, simbolo, reply_to_message_id=senal.get('telegram_message_id'))
                logger.info(f"  ⚡ Señal {senal_id} ({simbolo} {direccion}) caducada: TP1 alcanzado sin posición")
                continue

        # Analizar velas 1M en tiempo real
        confirmado, desc_1m = _confirmar_con_velas_1m(ticker, direccion, precio_entrada)

        if not confirmado:
            logger.info(f"  ⏳ {simbolo} {direccion} ({int(antiguedad_h*60)}min) — 1M no confirma: {desc_1m}")
            continue

        # ✅ Confirmación recibida — activar señal y notificar
        db.confirmar_senal_pendiente(senal_id)

        _nombre     = simbolo_a_nombre(simbolo)
        _tf_display = senal.get('timeframe', '1H')
        flecha = '📌 BUY LIMIT' if direccion == 'COMPRA' else '📌 SELL LIMIT'
        icono  = '🟢' if direccion == 'COMPRA' else '🔴'
        msg_confirm = (
            f"✅ <b>ENTRADA CONFIRMADA — {_nombre} {_tf_display}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{icono} <b>Dirección:</b> {direccion}\n"
            f"🕐 <b>Confirmado por:</b> análisis velas 1M\n"
            f"{flecha}: <b>${precio_entrada:.2f}</b>  ← PON LA ORDEN AHORA\n"
            f"🛑 <b>Stop Loss:</b> ${sl:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>TP1:</b> ${tp1:.2f}\n"
            + (f"🎯 <b>TP2:</b> ${tp2:.2f}\n"
               f"🎯 <b>TP3:</b> ${tp3:.2f}\n"
               if tp2 is not None else
               f"⚠️ <i>Solo TP1 — señal contra tendencia</i>\n")
            + f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Score {_tf_display}:</b> {score}/21  ⏱️ <b>TF:</b> {_tf_display}+1M\n"
            f"<code>{desc_1m}</code>"
        )
        enviar_notificacion_telegram(msg_confirm, simbolo, reply_to_message_id=senal.get('telegram_message_id'))
        logger.info(f"  ✅ Señal {senal_id} ({simbolo} {direccion}) confirmada: {desc_1m}")


# Tiempo máximo (horas) que una orden LIMIT puede estar pendiente sin ejecutarse
_EXPIRY_PENDIENTE_H = {
    'scalping': 2,    # 5M / 15M → 2 horas
    'intraday': 12,   # 1H      → 12 horas
    'swing':    48,   # 4H / 1D → 2 días
}


def _cancelar_orden_pendiente(senal: dict, precio_actual: float, sl: float,
                               categoria: str, ahora: datetime,
                               db: DatabaseManager) -> bool:
    """Cancela una orden LIMIT no ejecutada si:
      - El precio ya superó el SL (la orden no tiene sentido aunque se ejecutara), o
      - Lleva demasiado tiempo pendiente sin ejecutarse.
    Retorna True si la señal fue cancelada (el caller debe hacer 'continue').
    """
    senal_id       = senal['id']
    simbolo        = senal['simbolo']
    direccion      = senal['direccion']
    precio_entrada = float(senal['precio_entrada'])
    reply_msg_id   = senal.get('telegram_message_id')

    # ── 0. Guard anti-race-condition ──────────────────────────────────────────
    # Re-lee el estado actual de BD antes de cancelar. Si la señal ya no está
    # ESPERANDO (fue activada/cerrada externamente entre el fetch del loop y
    # este punto), aborta la cancelación para no sobreescribir ese estado.
    try:
        res_estado = db.ejecutar_query(
            "SELECT estado FROM senales WHERE id = ?", (senal_id,)
        )
        if res_estado.rows:
            estado_actual = res_estado.rows[0]['estado'] if isinstance(res_estado.rows[0], dict) else res_estado.rows[0][0]
            if estado_actual != 'ESPERANDO':
                logger.warning(
                    f"  ⚠️ [#{senal_id}] Cancelación abortada — "
                    f"señal ya está '{estado_actual}' en BD (no ESPERANDO)."
                )
                return False
    except Exception as _e:
        logger.warning(f"  ⚠️ [#{senal_id}] No se pudo re-verificar estado: {_e}")

    # ── 1. SL superado sin que la orden fuera ejecutada ──────────────────────
    sl_superado = (
        (direccion == 'VENTA'  and precio_actual >= sl) or
        (direccion == 'COMPRA' and precio_actual <= sl)
    )
    if sl_superado:
        db.cerrar_senal(senal_id, 'CANCELADA', 0.0)
        icono = '📈' if direccion == 'VENTA' else '📉'
        msg = (
            f"❌ <b>ORDEN CANCELADA — SL superado sin ejecución</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {simbolo} | {direccion}\n"
            f"💰 Entrada (no ejecutada): ${precio_entrada:.2f}\n"
            f"🛑 SL: ${sl:.2f}\n"
            f"{icono} Precio actual: ${precio_actual:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"ℹ️ La orden límite nunca se confirmó como ejecutada\n"
            f"   y el precio ya cruzó el nivel de stop loss.\n"
            f"🔖 <code>#{senal_id}</code>"
        )
        enviar_notificacion_telegram(msg, simbolo, reply_to_message_id=reply_msg_id)
        logger.info(
            f"  ❌ [#{senal_id}] Orden LIMIT cancelada — SL superado "
            f"(${precio_actual:.2f} vs SL ${sl:.2f})"
        )
        return True

    # ── 2. Caducidad por tiempo ───────────────────────────────────────────────
    ts_str = senal.get('timestamp')
    if ts_str:
        try:
            ts_senal = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
            if ts_senal.tzinfo is None:
                ts_senal = ts_senal.replace(tzinfo=timezone.utc)
            horas_pendiente = (ahora - ts_senal).total_seconds() / 3600
            limite_h = _EXPIRY_PENDIENTE_H.get(categoria, 48)
            if horas_pendiente >= limite_h:
                db.cerrar_senal(senal_id, 'CANCELADA', 0.0)
                msg = (
                    f"⏱️ <b>ORDEN CANCELADA — Tiempo expirado</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 {simbolo} | {direccion}\n"
                    f"💰 Entrada (no ejecutada): ${precio_entrada:.2f}\n"
                    f"⏰ Pendiente: {horas_pendiente:.1f}h (límite {limite_h}h)\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"ℹ️ La orden límite expiró sin ejecutarse.\n"
                    f"🔖 <code>#{senal_id}</code>"
                )
                enviar_notificacion_telegram(msg, simbolo, reply_to_message_id=reply_msg_id)
                logger.info(
                    f"  ⏱️ [#{senal_id}] Orden LIMIT cancelada — "
                    f"{horas_pendiente:.1f}h pendiente (límite {limite_h}h)"
                )
                return True
        except Exception as e:
            logger.warning(f"  ⚠️ [#{senal_id}] No se pudo verificar caducidad: {e}")

    return False


def _avisar_proximas_a_caducar(db: DatabaseManager, _aviso_caducidad_enviado: set) -> None:
    """
    Envía un aviso por Telegram cuando una señal ACTIVA está próxima a caducar.
    Se dispara cuando las horas restantes hasta el límite bajan de _AVISO_CADUCIDAD_HORAS_RESTANTES.
    Solo envía el aviso una vez por señal (controlado con _aviso_caducidad_enviado).
    """
    ahora = datetime.now(timezone.utc)
    max_horas_fallback = 336

    result = db.ejecutar_query("SELECT id, simbolo, direccion, timestamp, precio_entrada, sl, tp1, tp2, tp3, telegram_message_id FROM senales WHERE estado = 'ACTIVA'")
    if not result.rows:
        return

    for row in result.rows:
        senal    = dict(row)
        senal_id = senal['id']
        if senal_id in _aviso_caducidad_enviado:
            continue

        simbolo  = senal['simbolo']
        ts_raw   = senal['timestamp']

        try:
            ts = datetime.fromisoformat(ts_raw.replace('Z', '+00:00')) if isinstance(ts_raw, str) else ts_raw
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            antiguedad_h = (ahora - ts).total_seconds() / 3600
        except Exception:
            continue

        sufijo   = simbolo.split('_')[-1].upper() if '_' in simbolo else ''
        max_h    = _MAX_VIGENCIA_ACTIVA_HORAS.get(sufijo, max_horas_fallback)
        umbral_h = _AVISO_CADUCIDAD_HORAS_RESTANTES.get(sufijo, 24)
        restantes_h = max_h - antiguedad_h

        if restantes_h > umbral_h or restantes_h <= 0:
            continue  # Aún no toca avisar (o ya caducó)

        # Enviar aviso
        _aviso_caducidad_enviado.add(senal_id)
        _nombre  = simbolo_a_nombre(simbolo)
        tf_label = sufijo if sufijo else 'N/A'
        icono    = '🟢' if senal['direccion'] == 'COMPRA' else '🔴'
        try:
            entrada = float(senal['precio_entrada'] or 0)
            sl      = float(senal['sl'] or 0)
            tp1     = float(senal['tp1'] or 0)
            tp2     = float(senal['tp2'] or 0)
            tp3     = float(senal['tp3'] or 0)
            niveles = (f"📌 Entrada: ${entrada:.2f}  SL: ${sl:.2f}\n"
                       f"🎯 TP1: ${tp1:.2f}  TP2: ${tp2:.2f}  TP3: ${tp3:.2f}")
        except (TypeError, ValueError):
            niveles = ''

        if restantes_h < 1:
            tiempo_txt = f"{int(restantes_h * 60)} min"
        else:
            tiempo_txt = f"{restantes_h:.1f}h"

        msg = (
            f"⚠️ <b>SEÑAL PRÓXIMA A CADUCAR — {_nombre} {tf_label}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{icono} <b>{senal['direccion']}</b> | ID #{senal_id}\n"
            f"⌛ Caduca en: <b>{tiempo_txt}</b> (abierta {antiguedad_h:.1f}h / límite {max_h}h)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{niveles}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"ℹ️ Si no hay actividad se cerrará automáticamente"
        )
        enviar_notificacion_telegram(msg, simbolo, reply_to_message_id=senal.get('telegram_message_id'))
        logger.info(f"  ⚠️ Aviso caducidad señal {senal_id} ({simbolo}) — caduca en {tiempo_txt}")


def _invalidar_por_alejamiento_atr(db: DatabaseManager) -> None:
    """
    Invalida señales ACTIVA cuyo precio actual se alejó más de 1 ATR del entry.

    Concepto para entender:
    ─────────────────────────────────────────────────────────────────────────────
    ATR (Average True Range) mide la volatilidad media del activo en las últimas
    N velas. Si el precio ya se movió MÁS de 1 ATR desde el entry sin entrar en
    la señal, significa que:
      1. El R/R original ya no es el mismo — el SL queda muy lejos del precio
      2. El nivel que generó la señal perdió relevancia — el mercado ya lo ignoró
      3. Entrar ahora sería "perseguir el precio", un error clásico en trading

    Ejemplo concreto con Gold 1H:
      - Entry: 3100, SL: 3090, TP: 3115, ATR(14) del 1H ≈ 15 puntos
      - Si el precio actual es 3115 (= entry + 1 ATR) y nunca tocó el SL → señal
        BUY con entry en 3100 ya no tiene sentido porque el precio ya subió toda
        la amplitud media esperada sin ejecutar la entrada
    ─────────────────────────────────────────────────────────────────────────────

    Multiplier configurable: _ATR_INVALIDACION_MULTIPLIER
      - 1.0 = invalida al alejarse 1 ATR completo (conservador)
      - 1.5 = más tolerante, útil para TFs altos con mucho ruido
    """
    _ATR_INVALIDACION_MULTIPLIER = 1.0   # 1 ATR de distancia máxima desde el entry

    query = """
    SELECT id, simbolo, direccion, entry, sl, tp1, atr
    FROM senales
    WHERE estado = 'ACTIVA' AND atr IS NOT NULL AND atr > 0
    """
    result = db.ejecutar_query(query)
    if not result.rows:
        return

    for row in result.rows:
        senal     = dict(row)
        senal_id  = senal['id']
        simbolo   = senal['simbolo']
        direccion = senal['direccion']
        entry     = senal.get('entry')
        atr       = senal.get('atr')

        if not entry or not atr:
            continue

        # Obtener precio actual
        simbolo_base = simbolo.split('_')[0]
        ticker = SIMBOLO_TO_TICKER.get(simbolo_base)
        if not ticker:
            continue
        precios = _fetch_precios_ticker(ticker, db)
        if not precios:
            continue
        precio_actual = precios[0]

        distancia = abs(precio_actual - entry)
        umbral    = atr * _ATR_INVALIDACION_MULTIPLIER

        if distancia <= umbral:
            continue  # Precio sigue cerca del entry → señal válida

        # El precio se alejó demasiado → invalidar
        db.cerrar_senal(senal_id, 'CADUCADA', 0.0)
        _nombre   = simbolo_a_nombre(simbolo)
        sufijo    = simbolo.split('_')[-1].upper() if '_' in simbolo else 'N/A'
        lado      = '🟢' if direccion == 'COMPRA' else '🔴'

        logger.info(
            f"📏 Señal {senal_id} ({simbolo} {direccion}) invalidada por alejamiento ATR: "
            f"distancia={distancia:.2f} > umbral={umbral:.2f} (ATR={atr:.2f})"
        )

        msg = (
            f"📏 <b>Señal invalidada — precio lejos del entry</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{lado} <b>{_nombre} {sufijo} {direccion}</b>\n"
            f"\n"
            f"🎯 Entry original: <b>{entry:.2f}</b>\n"
            f"📍 Precio actual:  <b>{precio_actual:.2f}</b>\n"
            f"📐 Distancia:      <b>{distancia:.2f} pts</b>\n"
            f"📊 ATR({sufijo}):  <b>{atr:.2f} pts</b>\n"
            f"\n"
            f"ℹ️ El precio se alejó más de <b>1 ATR</b> del entry.\n"
            f"Entrar ahora cambiaría el R/R original — señal cerrada."
        )
        enviar_notificacion_telegram(msg, simbolo)


def cerrar_senales_antiguas(db: DatabaseManager, dias: int = 7):
    """
    Caduca señales ACTIVA que superaron el límite de vigencia para su timeframe.
    Usa _MAX_VIGENCIA_ACTIVA_HORAS; el parámetro `dias` actúa como techo absoluto
    para TFs sin sufijo reconocido.
    """
    ahora = datetime.now(timezone.utc)
    # Techo absoluto: señales sin sufijo reconocido usan `dias` como fallback
    max_horas_fallback = dias * 24

    query = """
    SELECT id, simbolo, direccion, timestamp
    FROM senales
    WHERE estado = 'ACTIVA'
    """
    result = db.ejecutar_query(query)
    if not result.rows:
        return

    for row in result.rows:
        senal    = dict(row)
        senal_id = senal['id']
        simbolo  = senal['simbolo']
        ts_raw   = senal['timestamp']

        # Calcular antigüedad en horas
        try:
            ts = datetime.fromisoformat(ts_raw.replace('Z', '+00:00')) if isinstance(ts_raw, str) else ts_raw
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            antiguedad_h = (ahora - ts).total_seconds() / 3600
        except Exception:
            continue

        # Límite de vigencia según TF del símbolo
        sufijo = simbolo.split('_')[-1].upper() if '_' in simbolo else ''
        max_horas = _MAX_VIGENCIA_ACTIVA_HORAS.get(sufijo, max_horas_fallback)

        if antiguedad_h <= max_horas:
            continue

        # Caducar señal
        db.cerrar_senal(senal_id, 'CADUCADA', 0.0)
        logger.info(f"⏰ Señal {senal_id} ({simbolo} {senal['direccion']}) caducada "
                    f"por vigencia máxima {max_horas}h (abierta {antiguedad_h:.1f}h)")

        # Notificar por Telegram
        _nombre = simbolo_a_nombre(simbolo)
        tf_label = sufijo if sufijo else 'N/A'
        msg = (
            f"⏰ <b>Señal caducada — {_nombre} {tf_label}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{'🟢' if senal['direccion'] == 'COMPRA' else '🔴'} "
            f"<b>{senal['direccion']}</b> abierta hace <b>{antiguedad_h:.0f}h</b>\n"
            f"⌛ Vigencia máxima para {tf_label}: {max_horas}h\n"
            f"ℹ️ Sin actividad — señal cerrada automáticamente"
        )
        enviar_notificacion_telegram(msg, simbolo)



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


def _verificar_senales_esperando(db: DatabaseManager, ahora: datetime) -> None:
    """
    Revisa todas las señales en estado ESPERANDO (orden LIMIT colocada, precio aún no llegó).

    Para cada una:
      - BUY LIMIT ejecutado cuando precio_min <= precio_entrada → activa la señal
      - SELL LIMIT ejecutado cuando precio_max >= precio_entrada → activa la señal
      - Si el SL fue cruzado sin ejecución → cancela
      - Si expiró el tiempo límite según TF → cancela

    Al activar envía un mensaje "✅ ORDEN ACTIVADA" a Telegram como reply al mensaje original.
    """
    esperando = db.obtener_senales_esperando()
    if not esperando:
        return

    logger.info(f"  ⏳ Señales ESPERANDO: {len(esperando)}")

    for senal in esperando:
        senal_id       = senal['id']
        simbolo        = senal['simbolo']
        direccion      = senal['direccion']
        reply_msg_id   = senal.get('telegram_message_id')

        try:
            precio_entrada = float(senal['precio_entrada'])
            tp1 = float(senal['tp1'])
            tp2 = float(senal['tp2']) if senal.get('tp2') is not None else None
            tp3 = float(senal['tp3']) if senal.get('tp3') is not None else None
            sl  = float(senal['sl'])
        except (TypeError, ValueError):
            logger.warning(f"  ⚠️ [#{senal_id}] Precios inválidos en ESPERANDO — saltando")
            continue

        base   = simbolo.split('_')[0]
        ticker = SIMBOLO_TO_TICKER.get(base)
        if not ticker:
            logger.warning(f"  ⚠️ [#{senal_id}] Ticker desconocido para {simbolo} — saltando")
            continue

        precios = _fetch_precios_ticker(ticker, db=db)
        if precios is None:
            logger.warning(f"  ⚠️ [#{senal_id}] Sin precio para {simbolo}")
            continue

        precio_actual, precio_max, precio_min = precios

        # ── Verificar si la orden fue ejecutada ──────────────────────────
        # IMPORTANTE: se verifica ANTES del chequeo de SL para evitar que
        # una spike que cruza entry+SL en el mismo ciclo cancele la orden
        # en lugar de activarla (el fill en entry ocurre antes que el SL).
        # Tolerancia de $0.10 para absorber discrepancias mínimas del proveedor
        # de datos (e.g. bid/ask, redondeo de vela 1m vs precio real del broker).
        _TOL = 0.10
        ejecutada = False
        if direccion == 'COMPRA':
            # BUY LIMIT: el precio baja hasta la entrada
            ejecutada = precio_min <= precio_entrada + _TOL
        else:
            # SELL LIMIT: el precio sube hasta la entrada
            ejecutada = precio_max >= precio_entrada - _TOL

        if ejecutada:
            pass  # continúa abajo para activar
        else:
            # ── Cancelar si SL fue cruzado sin que la orden se ejecutara ─
            categoria = _categoria_senal(simbolo)
            if _cancelar_orden_pendiente(senal, precio_actual, sl, categoria, ahora, db):
                continue  # ya cancelada
            logger.info(
                f"  ⏳ [#{senal_id}] {simbolo} {direccion} — "
                f"esperando entrada ${precio_entrada:.2f} "
                f"(actual ${precio_actual:.2f}  H:{precio_max:.2f}  L:{precio_min:.2f})"
            )
            continue

        # ── ¡Ejecutada! → activar señal y notificar ──────────────────────
        db.activar_senal_esperando(senal_id)
        logger.info(
            f"  ✅ [#{senal_id}] {simbolo} {direccion} — "
            f"ORDEN ACTIVADA: precio tocó entrada ${precio_entrada:.2f}"
        )

        _nombre     = simbolo_a_nombre(simbolo)
        sufijo      = simbolo.split('_')[-1].upper() if '_' in simbolo else ''
        icono       = '🟢' if direccion == 'COMPRA' else '🔴'
        flecha_dir  = '📈 COMPRA' if direccion == 'COMPRA' else '📉 VENTA'
        decimales   = 2 if precio_entrada > 100 else 5

        def _fmt(v):
            return f"${v:.{decimales}f}" if decimales == 2 else f"{v:.{decimales}f}"

        msg = (
            f"✅ <b>ORDEN ACTIVADA — {_nombre} {sufijo}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{icono} <b>{flecha_dir}</b>\n"
            f"💰 <b>Entrada ejecutada:</b> {_fmt(precio_entrada)}\n"
            f"📍 <b>Precio actual:</b> {_fmt(precio_actual)}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>TP1:</b> {_fmt(tp1)}\n"
            + (f"🎯 <b>TP2:</b> {_fmt(tp2)}\n"
               f"🎯 <b>TP3:</b> {_fmt(tp3)}\n"
               if tp2 is not None else
               f"⚠️ <i>Solo TP1 — señal contra tendencia</i>\n")
            + f"🛑 <b>Stop Loss:</b> {_fmt(sl)}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>El trade está ahora activo.</b> Gestiona tu posición.\n"
            f"🔖 <code>#{senal_id}</code>"
        )
        enviar_notificacion_telegram(msg, simbolo, reply_to_message_id=reply_msg_id)


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
    # Set de IDs de señales para las que ya se envió la alerta de reversión post-TP1
    _reversal_tp1_avisado: set = set()
    # Set de IDs de señales para las que ya se envió la alerta de reversión post-TP2
    _reversal_tp2_avisado: set = set()
    # Último tick en que se verificó la trampa por señal (evita llamadas API excesivas)
    _ultimo_check_trampa: dict = {}
    # Set de IDs de señales cuya orden LIMIT ya fue ejecutada (precio tocó la entrada)
    _ordenes_ejecutadas: set = set()
    # Set de IDs de señales para las que ya se envió el aviso de caducidad próxima
    _aviso_caducidad_enviado: set = set()
    ciclo = 0
    # ISO week numbers de la última apertura/cierre notificados (evita mensajes duplicados)
    _semana_apertura_enviada: int = -1
    _semana_cierre_enviada: int = -1
    # Eventos para los que ya se ejecutó el auto-cierre (evita cerrar 2 veces el mismo evento)
    _autoclose_ejecutado: set = set()

    # Configuración de auto-cierre por eventos macro (de .env)
    _AUTO_CLOSE_CFG = os.environ.get('AUTO_CLOSE_ON_EVENTS', 'none').lower().strip()
    if _AUTO_CLOSE_CFG not in ('none', 'fomc', 'high_impact', 'all'):
        logger.warning(f"⚠️  AUTO_CLOSE_ON_EVENTS='{_AUTO_CLOSE_CFG}' no válido — usando 'none'")
        _AUTO_CLOSE_CFG = 'none'
    if _AUTO_CLOSE_CFG != 'none':
        logger.info(f"⚡ Auto-cierre por eventos macro: {_AUTO_CLOSE_CFG.upper()}")
    # Flag para evitar enviar el resumen diario más de una vez por franja horaria
    _resumen_diario_sl_enviado: bool = False

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
                try:
                    from services.market_state import set_mercado_abierto
                    set_mercado_abierto(True, origen="APERTURA DE MERCADOS")
                except Exception as _e:
                    logger.warning(f"[market_state] No se pudo actualizar estado apertura: {_e}")

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
                try:
                    from services.market_state import set_mercado_abierto
                    set_mercado_abierto(False, origen="CIERRE DE MERCADOS")
                except Exception as _e:
                    logger.warning(f"[market_state] No se pudo actualizar estado cierre: {_e}")

            ahora = ahora_utc.astimezone()   # hora local para logs existentes
            logger.info(f"\n[{ahora.strftime('%H:%M:%S')}] 🔄 Tick #{ciclo}")

            # ── Auto-cierre por evento macro (configurable vía AUTO_CLOSE_ON_EVENTS) ──
            if _AUTO_CLOSE_CFG != 'none':
                try:
                    from services.economic_calendar import debe_cerrar_senales_activas
                    _cerrar, _desc_evento = debe_cerrar_senales_activas(_AUTO_CLOSE_CFG)
                    if _cerrar and _desc_evento not in _autoclose_ejecutado:
                        _senales_para_cerrar = db.obtener_senales_activas()
                        if _senales_para_cerrar:
                            logger.warning(
                                f"⚠️  Auto-cierre por evento macro: {_desc_evento} "
                                f"({len(_senales_para_cerrar)} señales)"
                            )
                            _cerradas_resumen = []
                            for _s in _senales_para_cerrar:
                                try:
                                    _sid      = _s['id']
                                    _simbolo  = _s['simbolo']
                                    _dir      = _s['direccion']
                                    _entrada  = float(_s['precio_entrada'])
                                    _base     = _simbolo.split('_')[0]
                                    _ticker   = SIMBOLO_TO_TICKER.get(_base)
                                    _precio_cierre = _entrada  # fallback
                                    if _ticker:
                                        _res = _fetch_precios_ticker(_ticker, db=db)
                                        if _res:
                                            _precio_cierre = _res[0]
                                    _benef = calcular_beneficio_pct(_entrada, _precio_cierre, _dir)
                                    db.cerrar_senal(_sid, 'CERRADA_EVENTO_MACRO', _benef)
                                    _cerradas_resumen.append(
                                        f"  #{_sid} {_simbolo} {_dir} "
                                        f"entry=${_entrada:.2f} close=${_precio_cierre:.2f} "
                                        f"({_benef:+.2f}%)"
                                    )
                                    # Notificación individual por señal
                                    _icono = '🟢' if _dir == 'COMPRA' else '🔴'
                                    enviar_notificacion_telegram(
                                        f"⚠️ <b>SEÑAL CERRADA POR EVENTO MACRO</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━\n"
                                        f"{_icono} {_simbolo} | {_dir}\n"
                                        f"💰 Entrada: ${_entrada:.2f}\n"
                                        f"💰 Cierre:  ${_precio_cierre:.2f}\n"
                                        f"{'📈' if _benef >= 0 else '📉'} P&L: {_benef:+.2f}%\n"
                                        f"━━━━━━━━━━━━━━━━━━━━\n"
                                        f"📌 Evento: {_desc_evento}\n"
                                        f"⚙️ Auto-cierre: {_AUTO_CLOSE_CFG.upper()}\n"
                                        f"🔖 <code>#{_sid}</code>",
                                        _simbolo,
                                        reply_to_message_id=_s.get('telegram_message_id')
                                    )
                                except Exception as _e_s:
                                    logger.error(f"  ❌ Error cerrando señal {_s.get('id')}: {_e_s}")

                            # Resumen agregado
                            _lineas = "\n".join(_cerradas_resumen) if _cerradas_resumen else "  (ninguna)"
                            enviar_notificacion_telegram(
                                f"🚫 <b>CIERRE AUTOMÁTICO — EVENTO MACRO</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"⚠️ <b>Evento:</b> {_desc_evento}\n"
                                f"📊 <b>Señales cerradas:</b> {len(_cerradas_resumen)}\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"{_lineas}\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"⚙️ Config: AUTO_CLOSE_ON_EVENTS={_AUTO_CLOSE_CFG.upper()}\n"
                                f"⏰ {ahora_utc.strftime('%Y-%m-%d %H:%M UTC')}"
                            )
                            _autoclose_ejecutado.add(_desc_evento)
                        else:
                            logger.info(f"  ℹ️  Auto-cierre: {_desc_evento} — no hay señales activas")
                            _autoclose_ejecutado.add(_desc_evento)
                except Exception as _e_ac:
                    logger.error(f"  ❌ Error en auto-cierre macro: {_e_ac}")

            # ── Verificar señales ESPERANDO (orden LIMIT no ejecutada aún) ──
            _verificar_senales_esperando(db, ahora)

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
                            # Publicar precio vía SSE al frontend (una vez por símbolo por ciclo)
                            try:
                                from bridge.sse_broker import broker as _sse_broker
                                _sse_broker.publicar_precio(symbol=base, precio=result[0])
                            except Exception:
                                pass
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
                        if not _PAUSE_BD_WRITES:
                            sec_db = get_secondary_db()
                            if sec_db:
                                sec_db.registrar_precio(senal_id, precio_actual, senal_data=senal)
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
                        # Señal ACTIVA = orden ya ejecutada (llegó a la entrada desde ESPERANDO)
                        verificar_niveles_compra(senal, precio_actual, precio_min, precio_max, db, _progreso_50_enviado)
                    else:
                        # Señal ACTIVA = orden ya ejecutada (llegó a la entrada desde ESPERANDO)
                        verificar_niveles_venta(senal, precio_actual, precio_min, precio_max, db, _progreso_50_enviado)

                    # ── Verificar trampa de patrón (cada 10 ticks ≈ 5 min, sin TP1) ──
                    _ultimo_trampa = _ultimo_check_trampa.get(senal_id)
                    _intervalo_trampa = 10  # ticks (~5 min)
                    if (_ultimo_trampa is None or
                            ciclo - _ultimo_trampa >= _intervalo_trampa):
                        _verificar_trampa_patron(senal, db, _trampa_avisada)
                        _verificar_reversal_post_tp1(senal, _reversal_tp1_avisado, db)
                        _verificar_reversal_post_tp2(senal, _reversal_tp2_avisado, db)
                        _ultimo_check_trampa[senal_id] = ciclo

            # Limpiar del diccionario señales que ya no están activas
            ids_activos = {s['id'] for s in senales_activas} if senales_activas else set()
            for sid in list(ultimo_check):
                if sid not in ids_activos:
                    del ultimo_check[sid]
            _progreso_50_enviado.intersection_update(ids_activos)
            _trampa_avisada.intersection_update(ids_activos)
            _reversal_tp1_avisado.intersection_update(ids_activos)
            _reversal_tp2_avisado.intersection_update(ids_activos)
            _ordenes_ejecutadas.intersection_update(ids_activos)
            _aviso_caducidad_enviado.intersection_update(ids_activos)
            for sid in list(_ultimo_check_trampa):
                if sid not in ids_activos:
                    del _ultimo_check_trampa[sid]

            # Cada 2 ticks (60s) verificar confirmaciones pendientes de señales 1H
            if ciclo % 2 == 0:
                _verificar_pendientes_confirm(db)

            # Cada ~hora (cada 120 ticks × 30s = 60 min) caducar señales que
            # superaron su vigencia máxima según timeframe (_MAX_VIGENCIA_ACTIVA_HORAS)
            # y avisar de las que están próximas a caducar
            if ciclo % 120 == 0:
                _avisar_proximas_a_caducar(db, _aviso_caducidad_enviado)
                cerrar_senales_antiguas(db, dias=14)

            # Cada ~30 min (cada 60 ticks × 30s) revisar si el precio se alejó
            # más de 1 ATR del entry → R/R invalidado aunque no haya expirado el tiempo
            if ciclo % 60 == 0:
                _invalidar_por_alejamiento_atr(db)

            # Heartbeat cada 60 ticks (~30 min) → confirma que el monitor corre
            if ciclo % 60 == 0:
                n_activas = len(senales_activas) if senales_activas else 0
                try:
                    db.guardar_log(
                        f"Monitor tick #{ciclo} | señales activas: {n_activas}",
                        'INFO', 'signal_monitor'
                    )
                except Exception:
                    pass

            # ── Resumen diario de SLs a las 22:00 UTC ──────────────────────────────
            _hora_utc = ahora_utc.hour
            _min_utc  = ahora_utc.minute
            if _hora_utc == 22 and _min_utc < 5 and not _resumen_diario_sl_enviado:
                try:
                    senales_hoy = db.obtener_senales_cerradas_recientes(horas=24)
                    sl_count  = sum(1 for s in senales_hoy if s.get('estado') == 'SL')
                    tp1_count = sum(1 for s in senales_hoy if s.get('estado') == 'TP1')
                    tp2_count = sum(1 for s in senales_hoy if s.get('estado') == 'TP2')
                    tp3_count = sum(1 for s in senales_hoy if s.get('estado') == 'TP3')
                    total = sl_count + tp1_count + tp2_count + tp3_count
                    if total > 0:
                        win_rate = round((tp1_count + tp2_count + tp3_count) / total * 100)
                        resumen = (
                            f"📊 <b>RESUMEN DIARIO — {ahora_utc.strftime('%Y-%m-%d')}</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"🎯 TP1: {tp1_count}  |  🎯🎯 TP2: {tp2_count}  |  🎯🎯🎯 TP3: {tp3_count}\n"
                            f"❌ SL activados: <b>{sl_count}</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"📈 Total señales cerradas: {total}\n"
                            f"✅ Win rate (TP1+): <b>{win_rate}%</b>\n"
                            f"⏰ {ahora_utc.strftime('%Y-%m-%d %H:%M UTC')}"
                        )
                        enviar_notificacion_telegram(resumen, 'XAUUSD')
                        logger.info(f"📊 Resumen diario enviado: {sl_count} SLs, {tp1_count + tp2_count + tp3_count} TPs")
                    _resumen_diario_sl_enviado = True
                except Exception as _r_e:
                    logger.warning(f"⚠️ Error enviando resumen diario: {_r_e}")
            elif _hora_utc != 22:
                _resumen_diario_sl_enviado = False

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
