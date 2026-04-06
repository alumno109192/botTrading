"""
Bot Trading - Web Service con detectores en background
Servidor Flask que mantiene vivo el servicio en Render
Los detectores se ejecutan en threads separados
"""

from flask import Flask, jsonify
import threading
import time
from datetime import datetime
import os

# Importar los módulos de los detectores
import detector_gold_copy
import detector_spx_copy
import detector_bitcoin
import signal_monitor

app = Flask(__name__)

# Estado del sistema
estado_sistema = {
    'iniciado': datetime.now().isoformat(),
    'detectores': {
        'gold': 'iniciando',
        'spx': 'iniciando',
        'bitcoin': 'iniciando',
        'monitor': 'iniciando'
    }
}

def ejecutar_detector(nombre, modulo, clave_estado):
    """Ejecuta un detector en un hilo separado"""
    try:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔵 Iniciando {nombre}...")
        estado_sistema['detectores'][clave_estado] = 'activo'
        modulo.main()
    except KeyboardInterrupt:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⚠️ {nombre} detenido por usuario")
        estado_sistema['detectores'][clave_estado] = 'detenido'
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error en {nombre}: {e}")
        estado_sistema['detectores'][clave_estado] = f'error: {str(e)}'
        # Reintentar en 60 segundos
        time.sleep(60)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 Reintentando {nombre}...")
        ejecutar_detector(nombre, modulo, clave_estado)

def iniciar_detectores():
    """Inicia todos los detectores en background"""
    print("="*60)
    print("🚀 SISTEMA DE DETECCIÓN DE SEÑALES INICIADO")
    print("="*60)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("📊 Detectores activos:")
    print("  🥇 XAUUSD (Oro)       → detector_gold.py")
    print("  📈 SPX500 (S&P 500)   → detector_spx.py")
    print("  ₿  BTCUSD (Bitcoin)   → detector_bitcoin.py")
    print("  🔍 MONITOR SEÑALES    → signal_monitor.py")
    print("="*60)
    
    # Crear hilos para cada detector
    hilos = []
    
    # Hilo para detector de Oro
    hilo_gold = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR GOLD (XAUUSD)", detector_gold_copy, "gold"),
        name="DetectorGold",
        daemon=True
    )
    hilos.append(hilo_gold)
    
    # Hilo para detector de SPX500
    hilo_spx = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR SPX (SPX500)", detector_spx_copy, "spx"),
        name="DetectorSPX",
        daemon=True
    )
    hilos.append(hilo_spx)
    
    # Hilo para detector de Bitcoin
    hilo_btc = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR BITCOIN (BTCUSD)", detector_bitcoin, "bitcoin"),
        name="DetectorBitcoin",
        daemon=True
    )
    hilos.append(hilo_btc)
    
    # Hilo para monitor de señales
    hilo_monitor = threading.Thread(
        target=ejecutar_detector,
        args=("MONITOR SEÑALES", signal_monitor, "monitor"),
        name="SignalMonitor",
        daemon=True
    )
    hilos.append(hilo_monitor)
    
    # Iniciar todos los hilos
    for hilo in hilos:
        hilo.start()
        time.sleep(2)
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Todos los detectores están activos\n")

# ========================================
# RUTAS FLASK
# ========================================

@app.route('/')
def home():
    """Endpoint principal - Health check"""
    return jsonify({
        'status': 'online',
        'servicio': 'Bot Trading - Detectores de Señales',
        'iniciado': estado_sistema['iniciado'],
        'detectores': estado_sistema['detectores']
    })

@app.route('/health')
def health():
    """Health check para Render"""
    return jsonify({'status': 'healthy'}), 200

@app.route('/status')
def status():
    """Estado detallado del sistema"""
    return jsonify(estado_sistema)

# ========================================
# INICIO DE LA APLICACIÓN
# ========================================

if __name__ == '__main__':
    # Iniciar detectores en background
    iniciar_detectores()
    
    # Iniciar servidor Flask
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Servidor Flask iniciando en puerto {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
