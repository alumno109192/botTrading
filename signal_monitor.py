"""
Signal Monitor - Monitorea señales activas y actualiza estados
Revisa cada 5 minutos todas las señales activas y verifica si alcanzaron TP o SL
"""

import time
import yfinance as yf
import requests
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from db_manager import DatabaseManager

# Cargar variables de entorno
load_dotenv()

# Configuración de Telegram
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
THREAD_ID_SWING     = os.environ.get('THREAD_ID_SWING')      # 1D / 4H
THREAD_ID_INTRADAY  = os.environ.get('THREAD_ID_INTRADAY')   # 1H
THREAD_ID_SCALPING  = os.environ.get('THREAD_ID_SCALPING')   # 15M / 5M

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

def obtener_precio_actual(simbolo: str) -> tuple:
    """
    Obtiene el precio actual y extremos recientes de un símbolo usando yfinance.

    Retorna una tupla (precio_actual, precio_max, precio_min) donde precio_max y
    precio_min son el máximo y mínimo de las últimas 5 velas de 1 minuto.
    Usar precio_max/precio_min en vez de precio_actual para detectar TPs/SLs que
    se tocaron brevemente entre ciclos del monitor.

    Args:
        simbolo: BTCUSD, XAUUSD, SPX500 (puede incluir sufijos como _4H, _1D, _15M)

    Returns:
        (precio_actual, precio_max_5m, precio_min_5m) o None si hay error
    """
    try:
        # Extraer símbolo base (sin sufijos _4H, _1D, _15M, etc.)
        simbolo_base = simbolo.split('_')[0]

        ticker = SIMBOLO_TO_TICKER.get(simbolo_base)
        if not ticker:
            print(f"⚠️ Símbolo desconocido: {simbolo} (base: {simbolo_base})")
            return None

        data = yf.Ticker(ticker)
        hist = data.history(period='1d', interval='1m')

        if hist.empty:
            print(f"⚠️ No hay datos para {simbolo}")
            return None

        precio_actual = float(hist['Close'].iloc[-1])

        # Extremos de los últimos 5 minutos para detectar picos/valles entre polls
        ventana = hist.tail(5)
        precio_max = float(ventana['High'].max())
        precio_min = float(ventana['Low'].min())

        return (precio_actual, precio_max, precio_min)

    except Exception as e:
        print(f"❌ Error obteniendo precio de {simbolo}: {e}")
        return None


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

        response = requests.post(url, data=data, timeout=10)

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


def monitor_senales():
    """
    Loop principal que monitorea señales activas cada 5 minutos
    """
    print("="*60)
    print("🔍 MONITOR DE SEÑALES INICIADO")
    print("="*60)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("⏱️  Intervalo de revisión: 5 minutos")
    print("="*60)
    print()
    
    # Inicializar conexión a base de datos
    try:
        db = DatabaseManager()
        print("✅ Conexión a base de datos establecida")
    except Exception as e:
        print(f"⚠️  Base de datos no disponible: {e}")
        print("📋 El monitor de señales requiere base de datos configurada")
        print("💤 Monitor en espera indefinida (no afecta los detectores)...")
        # Dormir indefinidamente sin hacer nada (el thread sigue vivo pero inactivo)
        while True:
            time.sleep(3600)  # Dormir 1 hora
        return
    
    ciclo = 0
    
    while True:
        try:
            ciclo += 1
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🔄 Ciclo #{ciclo} - Revisando señales...")
            
            # Obtener todas las señales activas
            senales_activas = db.obtener_senales_activas()
            
            if not senales_activas:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 📭 No hay señales activas")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Señales activas: {len(senales_activas)}")
                
                # Revisar cada señal
                for senal in senales_activas:
                    simbolo = senal['simbolo']
                    direccion = senal['direccion']

                    # Obtener precio actual + extremos de los últimos 5m (OHLC)
                    precios = obtener_precio_actual(simbolo)

                    if precios is None:
                        print(f"  ⚠️ No se pudo obtener precio de {simbolo}")
                        continue

                    precio_actual, precio_max, precio_min = precios

                    # Registrar precio actual en historial
                    db.registrar_precio(senal['id'], precio_actual)

                    # Convertir precio_entrada a float (fix Turso strings)
                    precio_entrada = float(senal['precio_entrada'])

                    # Mostrar estado actual
                    beneficio_actual = calcular_beneficio_pct(
                        precio_entrada,
                        precio_actual,
                        direccion
                    )

                    tp1 = float(senal['tp1'])
                    tp2 = float(senal['tp2'])
                    tp3 = float(senal['tp3'])
                    sl  = float(senal['sl'])
                    print(f"  📊 {simbolo} | {direccion} | "
                          f"Entrada: ${precio_entrada:.2f} | "
                          f"Actual: ${precio_actual:.2f} (H:{precio_max:.2f} L:{precio_min:.2f}) | "
                          f"Beneficio: {beneficio_actual:+.2f}% | "
                          f"TP1: ${tp1:.2f}  TP2: ${tp2:.2f}  TP3: ${tp3:.2f} | "
                          f"SL: ${sl:.2f}")

                    # Verificar niveles alcanzados usando extremos OHLC
                    if direccion == 'COMPRA':
                        verificar_niveles_compra(senal, precio_actual, precio_min, precio_max, db)
                    else:  # VENTA
                        verificar_niveles_venta(senal, precio_actual, precio_min, precio_max, db)
            
            # Cerrar señales muy antiguas (más de 7 días)
            if ciclo % 12 == 0:  # Cada hora (12 ciclos * 5 min = 60 min)
                cerrar_senales_antiguas(db, dias=7)
            
            # Esperar 3 minutos antes del próximo ciclo (balance óptimo)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⏳ Esperando 3 minutos...")
            time.sleep(180)  # 3 minutos = 180 segundos
            
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
