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

# Mapeo de símbolos a tickers de yfinance
SIMBOLO_TO_TICKER = {
    'BTCUSD': 'BTC-USD',
    'XAUUSD': 'GC=F',  # Gold Futures
    'SPX500': '^GSPC'  # S&P 500
}

def obtener_precio_actual(simbolo: str) -> float:
    """
    Obtiene el precio actual de un símbolo usando yfinance
    
    Args:
        simbolo: BTCUSD, XAUUSD, SPX500 (puede incluir sufijos como _4H, _1D, _15M)
        
    Returns:
        Precio actual o None si hay error
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
        
        precio = float(hist['Close'].iloc[-1])
        return precio
        
    except Exception as e:
        print(f"❌ Error obteniendo precio de {simbolo}: {e}")
        return None


def enviar_notificacion_telegram(mensaje: str):
    """Envía una notificación a Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': mensaje,
            'parse_mode': 'HTML'
        }
        
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


def verificar_niveles_compra(senal: dict, precio_actual: float, db: DatabaseManager):
    """Verifica niveles para señales de COMPRA"""
    senal_id = senal['id']
    simbolo = senal['simbolo']
    
    # Convertir valores numéricos de BD a float (fix para Turso que retorna strings)
    precio_entrada = float(senal['precio_entrada'])
    tp1 = float(senal['tp1'])
    tp2 = float(senal['tp2'])
    tp3 = float(senal['tp3'])
    sl = float(senal['sl'])
    
    # Verificar TP3 (mayor prioridad)
    if precio_actual >= tp3 and not senal['tp3_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, precio_actual, 'COMPRA')
        db.actualizar_estado_senal(senal_id, 'TP3', beneficio)
        
        mensaje = f"""
🎯🎯🎯 <b>TP3 ALCANZADO!</b>

📊 Símbolo: {simbolo}
🔼 Dirección: COMPRA
💰 Precio Entrada: ${precio_entrada:.2f}
✅ TP3: ${tp3:.2f}
📈 Precio Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%

🏆 ¡Excelente operación!
        """
        enviar_notificacion_telegram(mensaje)
        return
    
    # Verificar TP2
    if precio_actual >= tp2 and not senal['tp2_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, precio_actual, 'COMPRA')
        db.actualizar_estado_senal(senal_id, 'TP2', beneficio)
        
        mensaje = f"""
🎯🎯 <b>TP2 ALCANZADO</b>

📊 {simbolo} | COMPRA
💰 Entrada: ${precio_entrada:.2f}
✅ TP2: ${tp2:.2f}
📈 Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%
        """
        enviar_notificacion_telegram(mensaje)
        return
    
    # Verificar TP1
    if precio_actual >= tp1 and not senal['tp1_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, precio_actual, 'COMPRA')
        db.actualizar_estado_senal(senal_id, 'TP1', beneficio)
        
        mensaje = f"""
🎯 <b>TP1 ALCANZADO</b>

📊 {simbolo} | COMPRA
💰 Entrada: ${precio_entrada:.2f}
✅ TP1: ${tp1:.2f}
📈 Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%
        """
        enviar_notificacion_telegram(mensaje)
        return
    
    # Verificar SL (Stop Loss)
    if precio_actual <= sl and not senal['sl_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, precio_actual, 'COMPRA')
        db.actualizar_estado_senal(senal_id, 'SL', beneficio)
        
        mensaje = f"""
❌ <b>STOP LOSS ACTIVADO</b>

📊 {simbolo} | COMPRA
💰 Entrada: ${precio_entrada:.2f}
🛑 SL: ${sl:.2f}
📉 Actual: ${precio_actual:.2f}
💸 Pérdida: {beneficio:.2f}%
        """
        enviar_notificacion_telegram(mensaje)
        return


def verificar_niveles_venta(senal: dict, precio_actual: float, db: DatabaseManager):
    """Verifica niveles para señales de VENTA"""
    senal_id = senal['id']
    simbolo = senal['simbolo']
    
    # Convertir valores numéricos de BD a float (fix para Turso que retorna strings)
    precio_entrada = float(senal['precio_entrada'])
    tp1 = float(senal['tp1'])
    tp2 = float(senal['tp2'])
    tp3 = float(senal['tp3'])
    sl = float(senal['sl'])
    
    # Verificar TP3 (menor precio)
    if precio_actual <= tp3 and not senal['tp3_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, precio_actual, 'VENTA')
        db.actualizar_estado_senal(senal_id, 'TP3', beneficio)
        
        mensaje = f"""
🎯🎯🎯 <b>TP3 ALCANZADO!</b>

📊 Símbolo: {simbolo}
🔽 Dirección: VENTA
💰 Precio Entrada: ${precio_entrada:.2f}
✅ TP3: ${tp3:.2f}
📉 Precio Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%

🏆 ¡Excelente operación!
        """
        enviar_notificacion_telegram(mensaje)
        return
    
    # Verificar TP2
    if precio_actual <= tp2 and not senal['tp2_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, precio_actual, 'VENTA')
        db.actualizar_estado_senal(senal_id, 'TP2', beneficio)
        
        mensaje = f"""
🎯🎯 <b>TP2 ALCANZADO</b>

📊 {simbolo} | VENTA
💰 Entrada: ${precio_entrada:.2f}
✅ TP2: ${tp2:.2f}
📉 Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%
        """
        enviar_notificacion_telegram(mensaje)
        return
    
    # Verificar TP1
    if precio_actual <= tp1 and not senal['tp1_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, precio_actual, 'VENTA')
        db.actualizar_estado_senal(senal_id, 'TP1', beneficio)
        
        mensaje = f"""
🎯 <b>TP1 ALCANZADO</b>

📊 {simbolo} | VENTA
💰 Entrada: ${precio_entrada:.2f}
✅ TP1: ${tp1:.2f}
📉 Actual: ${precio_actual:.2f}
💵 Beneficio: +{beneficio:.2f}%
        """
        enviar_notificacion_telegram(mensaje)
        return
    
    # Verificar SL (Stop Loss)
    if precio_actual >= sl and not senal['sl_alcanzado']:
        beneficio = calcular_beneficio_pct(precio_entrada, precio_actual, 'VENTA')
        db.actualizar_estado_senal(senal_id, 'SL', beneficio)
        
        mensaje = f"""
❌ <b>STOP LOSS ACTIVADO</b>

📊 {simbolo} | VENTA
💰 Entrada: ${precio_entrada:.2f}
🛑 SL: ${sl:.2f}
📈 Actual: ${precio_actual:.2f}
💸 Pérdida: {beneficio:.2f}%
        """
        enviar_notificacion_telegram(mensaje)
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
                    
                    # Obtener precio actual
                    precio_actual = obtener_precio_actual(simbolo)
                    
                    if precio_actual is None:
                        print(f"  ⚠️ No se pudo obtener precio de {simbolo}")
                        continue
                    
                    # Registrar precio en historial
                    db.registrar_precio(senal['id'], precio_actual)
                    
                    # Convertir precio_entrada a float (fix Turso strings)
                    precio_entrada = float(senal['precio_entrada'])
                    
                    # Mostrar estado actual
                    beneficio_actual = calcular_beneficio_pct(
                        precio_entrada, 
                        precio_actual, 
                        direccion
                    )
                    
                    print(f"  📊 {simbolo} | {direccion} | "
                          f"Entrada: ${precio_entrada:.2f} | "
                          f"Actual: ${precio_actual:.2f} | "
                          f"Beneficio: {beneficio_actual:+.2f}%")
                    
                    # Verificar niveles alcanzados
                    if direccion == 'COMPRA':
                        verificar_niveles_compra(senal, precio_actual, db)
                    else:  # VENTA
                        verificar_niveles_venta(senal, precio_actual, db)
            
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
