"""
Signal Monitor - Monitorea señales activas y actualiza estados
Revisa cada 5 minutos todas las señales activas y verifica si alcanzaron TP o SL
"""

import time
import threading
import yfinance as yf
import requests
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from db_manager import DatabaseManager

# Lock propio para serializar llamadas a yf.Ticker.history() (yfinance no es thread-safe)
_yf_lock = threading.Lock()

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

def _fetch_precios_ticker(ticker: str) -> tuple | None:
    """Descarga velas de 1 minuto para un ticker de yfinance y devuelve
    (precio_actual, precio_max_5m, precio_min_5m) o None si hay error.

    Llamar UNA sola vez por ticker por ciclo y reutilizar el resultado
    para todas las señales que compartan el mismo subyacente.
    """
    try:
        with _yf_lock:
            hist = yf.Ticker(ticker).history(period='1d', interval='1m')
        if hist.empty:
            return None
        precio_actual = float(hist['Close'].iloc[-1])
        ventana       = hist.tail(5)          # últimos 5 minutos
        precio_max    = float(ventana['High'].max())
        precio_min    = float(ventana['Low'].min())
        return (precio_actual, precio_max, precio_min)
    except Exception as e:
        print(f"❌ Error descargando {ticker}: {e}")
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
        print(f"⚠️ Símbolo desconocido: {simbolo} (base: {simbolo_base})")
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
            print(f"✅ Notificación enviada: {mensaje[:50]}...")
        else:
            print(f"⚠️ Error enviando notificación: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Error en Telegram: {e}")


def calcular_beneficio_pct(precio_entrada: float, precio_actual: float, 
                          direccion: str) -> float:
    """Calcula el porcentaje de beneficio/pérdida"""
    if direccion == 'COMPRA':
        return ((precio_actual - precio_entrada) / precio_entrada) * 100
    else:  # VENTA
        return ((precio_entrada - precio_actual) / precio_entrada) * 100


def verificar_niveles_compra(senal: dict, precio_actual: float,
                            precio_min: float, precio_max: float,
                            db: DatabaseManager):
    """Verifica niveles para señales de COMPRA.

    Usa precio_max (High de los últimos 5m) para detectar TPs alcanzados
    brevemente entre polls, y precio_min (Low) para el SL.
    """
    senal_id = senal['id']
    simbolo = senal['simbolo']

    # Convertir valores numéricos de BD a float (fix para Turso que retorna strings)
    precio_entrada = float(senal['precio_entrada'])
    tp1 = float(senal['tp1'])
    tp2 = float(senal['tp2'])
    tp3 = float(senal['tp3'])
    sl = float(senal['sl'])

    # Verificar TP3 (mayor prioridad) — usa High reciente para capturar picos entre polls
    if precio_max >= tp3 and not senal['tp3_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, tp3, 'COMPRA')
        db.actualizar_estado_senal(senal_id, 'TP3', beneficio)

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
        """
        enviar_notificacion_telegram(mensaje, simbolo)
        return

    # Verificar TP2 — usa High reciente
    if precio_max >= tp2 and not senal['tp2_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, tp2, 'COMPRA')
        db.actualizar_estado_senal(senal_id, 'TP2', beneficio)

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
        """
        enviar_notificacion_telegram(mensaje, simbolo)
        return

    # Verificar TP1 — usa High reciente
    if precio_max >= tp1 and not senal['tp1_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, tp1, 'COMPRA')
        db.actualizar_estado_senal(senal_id, 'TP1', beneficio)

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
        """
        enviar_notificacion_telegram(mensaje, simbolo)
        return

    # Verificar SL (Stop Loss) — usa Low reciente para capturar caídas entre polls
    if precio_min <= sl and not senal['sl_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, sl, 'COMPRA')
        db.actualizar_estado_senal(senal_id, 'SL', beneficio)
        
        mensaje = f"""
❌ <b>STOP LOSS ACTIVADO</b>

📊 {simbolo} | COMPRA
💰 Entrada: ${precio_entrada:.2f}
🛑 SL: ${sl:.2f}
📉 Actual: ${precio_actual:.2f}
💸 Pérdida: {beneficio:.2f}%

📋 <b>ACCIÓN RECOMENDADA:</b>
🔴 Cerrar el 100% de la posición
        """
        enviar_notificacion_telegram(mensaje, simbolo)
        return


def verificar_niveles_venta(senal: dict, precio_actual: float,
                           precio_min: float, precio_max: float,
                           db: DatabaseManager):
    """Verifica niveles para señales de VENTA.

    Usa precio_min (Low de los últimos 5m) para detectar TPs alcanzados
    brevemente entre polls, y precio_max (High) para el SL.
    """
    senal_id = senal['id']
    simbolo = senal['simbolo']

    # Convertir valores numéricos de BD a float (fix para Turso que retorna strings)
    precio_entrada = float(senal['precio_entrada'])
    tp1 = float(senal['tp1'])
    tp2 = float(senal['tp2'])
    tp3 = float(senal['tp3'])
    sl = float(senal['sl'])

    # Verificar TP3 (menor precio) — usa Low reciente para capturar picos entre polls
    if precio_min <= tp3 and not senal['tp3_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, tp3, 'VENTA')
        db.actualizar_estado_senal(senal_id, 'TP3', beneficio)

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
        """
        enviar_notificacion_telegram(mensaje, simbolo)
        return

    # Verificar TP2 — usa Low reciente
    if precio_min <= tp2 and not senal['tp2_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, tp2, 'VENTA')
        db.actualizar_estado_senal(senal_id, 'TP2', beneficio)

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
        """
        enviar_notificacion_telegram(mensaje, simbolo)
        return

    # Verificar TP1 — usa Low reciente
    if precio_min <= tp1 and not senal['tp1_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, tp1, 'VENTA')
        db.actualizar_estado_senal(senal_id, 'TP1', beneficio)

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
        """
        enviar_notificacion_telegram(mensaje, simbolo)
        return

    # Verificar SL (Stop Loss) — usa High reciente para capturar subidas entre polls
    if precio_max >= sl and not senal['sl_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, sl, 'VENTA')
        db.actualizar_estado_senal(senal_id, 'SL', beneficio)

        mensaje = f"""
❌ <b>STOP LOSS ACTIVADO</b>

📊 {simbolo} | VENTA
💰 Entrada: ${precio_entrada:.2f}
🛑 SL: ${sl:.2f}
📈 Actual: ${precio_actual:.2f}
💸 Pérdida: {beneficio:.2f}%

📋 <b>ACCIÓN RECOMENDADA:</b>
🔴 Cerrar el 100% de la posición
        """
        enviar_notificacion_telegram(mensaje, simbolo)
        return


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
            print(f"🗓️ Señal {senal['id']} cerrada por antigüedad (>{dias} días)")



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
    print("="*60)
    print("🔍 MONITOR DE SEÑALES INICIADO")
    print("="*60)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⏱️  Scalping (5M/15M): cada {_INTERVALO_SCALPING}s  ← tick base")
    print(f"⏱️  Intraday  (1H):    cada {_INTERVALO_INTRADAY}s")
    print(f"⏱️  Swing  (4H/1D):   cada {_INTERVALO_SWING}s")
    print(f"⚡ Fetch deduplicado: 1 llamada por ticker subyacente por ciclo")
    print("="*60)
    print()

    # Inicializar conexión a base de datos
    db = None
    while db is None:
        try:
            db = DatabaseManager()
            print("✅ Conexión a base de datos establecida")
        except Exception as e:
            print(f"⚠️  Base de datos no disponible: {e}")
            print("🔁 Reintentando en 60 segundos...")
            time.sleep(60)

    # Registro del último momento en que se revisó cada señal (id → datetime)
    ultimo_check: dict = {}
    ciclo = 0

    while True:
        try:
            ciclo += 1
            ahora = datetime.now()
            print(f"\n[{ahora.strftime('%H:%M:%S')}] 🔄 Tick #{ciclo}")

            senales_activas = db.obtener_senales_activas()

            if not senales_activas:
                print(f"[{ahora.strftime('%H:%M:%S')}] 📭 No hay señales activas")
            else:
                print(f"[{ahora.strftime('%H:%M:%S')}] 📊 Señales activas: {len(senales_activas)}")

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
                    print(f"[{ahora.strftime('%H:%M:%S')}] ⏭️  Ninguna señal requiere revisión este tick")
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
                        result = _fetch_precios_ticker(ticker)
                        if result is not None:
                            cache_precios[ticker] = result
                            print(f"  📡 {base} ({ticker}): ${result[0]:.2f}  H:{result[1]:.2f}  L:{result[2]:.2f}")
                        else:
                            print(f"  ⚠️ Sin datos para {base} ({ticker})")

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
                        print(f"  ⚠️ No se pudo obtener precio de {simbolo}")
                        continue

                    precio_actual, precio_max, precio_min = precios

                    db.registrar_precio(senal_id, precio_actual)

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
                    print(f"  📊 {etiqueta_tf} {simbolo} | {direccion} | "
                          f"Entrada: ${precio_entrada:.2f} | "
                          f"Actual: ${precio_actual:.2f} (H:{precio_max:.2f} L:{precio_min:.2f}) | "
                          f"P&L: {beneficio_actual:+.2f}% | "
                          f"TPs: {tp1:.2f}/{tp2:.2f}/{tp3:.2f} SL: {sl:.2f}")

                    if direccion == 'COMPRA':
                        verificar_niveles_compra(senal, precio_actual, precio_min, precio_max, db)
                    else:
                        verificar_niveles_venta(senal, precio_actual, precio_min, precio_max, db)

            # Limpiar del diccionario señales que ya no están activas
            ids_activos = {s['id'] for s in senales_activas} if senales_activas else set()
            for sid in list(ultimo_check):
                if sid not in ids_activos:
                    del ultimo_check[sid]

            # Cada ~hora (cada 120 ticks × 30s = 60 min) cerrar señales antiguas
            if ciclo % 120 == 0:
                cerrar_senales_antiguas(db, dias=7)

            print(f"[{ahora.strftime('%H:%M:%S')}] ⏳ Próximo tick en {_TICK}s...")
            time.sleep(_TICK)

        except KeyboardInterrupt:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Monitor detenido por usuario")
            break

        except Exception as e:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ❌ Error en monitor: {e}")
            print("🔄 Reintentando en 60 segundos...")
            time.sleep(60)


def main():
    """Punto de entrada principal - Llama a monitor_senales()"""
    monitor_senales()


if __name__ == '__main__':
    main()
