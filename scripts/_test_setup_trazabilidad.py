"""
Script de prueba para verificar el sistema de trazabilidad SETUP → TP
Simula un SETUP 5M y verifica que el ID se incluya correctamente
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.database import DatabaseManager
from datetime import datetime, timezone
import json

def test_setup_con_id():
    """Prueba que los SETUP guardados tengan ID y message_id"""
    
    print("=" * 70)
    print("TEST - Sistema de Trazabilidad SETUP → TP")
    print("=" * 70)
    
    db = DatabaseManager()
    
    # Simular un SETUP SELL 5M
    print("\n📊 Simulando SETUP SELL 5M...")
    
    senal_data = {
        'timestamp': datetime.now(timezone.utc),
        'simbolo': 'XAUUSD_5M',
        'direccion': 'VENTA',
        'precio_entrada': 4538.78,
        'tp1': 4272.51,
        'tp2': 4002.61,
        'tp3': 3732.71,
        'sl': 4808.69,
        'score': 9,
        'indicadores': json.dumps({'rsi': 48.1, 'adx': 70.6, 'atr': 3.5}),
        'patron_velas': 'aviso_setup_temprano',
        'version_detector': '5M-AVISO-v1-TEST',
        'telegram_thread_id': None  # Se añadirá en producción
    }
    
    # Guardar señal
    senal_id = db.guardar_senal(senal_data)
    print(f"✅ Señal guardada con ID: {senal_id}")
    
    # Simular que se envió a Telegram y obtuvimos un message_id
    test_message_id = 99999  # En producción, este vendría de Telegram
    db.ejecutar_query(
        "UPDATE senales SET telegram_message_id = ? WHERE id = ?",
        (test_message_id, senal_id)
    )
    print(f"✅ Message ID de Telegram guardado: {test_message_id}")
    
    # Verificar que se guardó correctamente
    print("\n🔍 Verificando datos guardados...")
    query = "SELECT id, simbolo, direccion, precio_entrada, telegram_message_id FROM senales WHERE id = ?"
    result = db.ejecutar_query(query, (senal_id,))
    
    if result.rows:
        senal = dict(result.rows[0])
        print(f"\n📋 Datos de la señal:")
        print(f"   ID: {senal['id']}")
        print(f"   Símbolo: {senal['simbolo']}")
        print(f"   Dirección: {senal['direccion']}")
        print(f"   Precio entrada: ${senal['precio_entrada']}")
        print(f"   Telegram Message ID: {senal.get('telegram_message_id')}")
        
        if senal.get('telegram_message_id'):
            print("\n✅ PASS - La señal tiene telegram_message_id")
            print("   Las notificaciones de TP/SL harán reply a este mensaje")
        else:
            print("\n❌ FAIL - telegram_message_id es NULL")
    else:
        print("❌ Error: no se encontró la señal guardada")
    
    # Limpiar señal de prueba
    print(f"\n🧹 Limpiando señal de prueba (ID {senal_id})...")
    db.cerrar_senal(senal_id, 'TEST', 0.0)
    
    print("\n" + "=" * 70)
    print("✅ TEST COMPLETADO")
    print("=" * 70)
    print("\n💡 Flujo en producción:")
    print("   1. Detector detecta SETUP (score ≥ 6)")
    print("   2. Guarda señal en BD → obtiene ID")
    print("   3. Envía mensaje a Telegram → obtiene message_id")
    print("   4. Guarda message_id en BD")
    print("   5. Signal monitor notifica TP/SL haciendo reply al message_id")


if __name__ == "__main__":
    try:
        test_setup_con_id()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
