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
import requests

# Importar los módulos de los detectores
import detector_gold_copy
import detector_spx_copy
import detector_bitcoin
import signal_monitor

app = Flask(__name__)

# Estado del sistema
estado_sistema = {
    'iniciado': datetime.now().isoformat(),
    'ultima_actividad_cron': None,
    'detectores': {
        'gold': 'iniciando',
        'spx': 'iniciando',
        'bitcoin': 'iniciando',
        'monitor': 'iniciando'
    }
}

# Referencias a los threads para monitoreo
threads_detectores = {}

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

def keep_alive():
    """Mantiene la instancia activa haciendo ping interno cada 5 minutos"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 💚 Keep-alive iniciado")
    time.sleep(120)  # Esperar 2 minutos al inicio para que Flask esté listo
    
    while True:
        try:
            # Hacer ping a nuestro propio endpoint /health
            port = int(os.environ.get('PORT', 5000))
            url = f"http://localhost:{port}/health"
            
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 💚 Keep-alive ping OK")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Keep-alive ping failed: {response.status_code}")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Keep-alive error: {e}")
        
        time.sleep(60)  # 1 minuto = 60 segundos

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
    threads_detectores['gold'] = hilo_gold
    
    # Hilo para detector de SPX500
    hilo_spx = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR SPX (SPX500)", detector_spx_copy, "spx"),
        name="DetectorSPX",
        daemon=True
    )
    hilos.append(hilo_spx)
    threads_detectores['spx'] = hilo_spx
    
    # Hilo para detector de Bitcoin
    hilo_btc = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR BITCOIN (BTCUSD)", detector_bitcoin, "bitcoin"),
        name="DetectorBitcoin",
        daemon=True
    )
    hilos.append(hilo_btc)
    threads_detectores['bitcoin'] = hilo_btc
    
    # Hilo para monitor de señales
    hilo_monitor = threading.Thread(
        target=ejecutar_detector,
        args=("MONITOR SEÑALES", signal_monitor, "monitor"),
        name="SignalMonitor",
        daemon=True
    )
    hilos.append(hilo_monitor)
    threads_detectores['monitor'] = hilo_monitor
    
    # Hilo para keep-alive (evita que Render duerma la instancia)
    hilo_keepalive = threading.Thread(
        target=keep_alive,
        name="KeepAlive",
        daemon=True
    )
    hilos.append(hilo_keepalive)
    
    # Iniciar todos los hilos
    for hilo in hilos:
        hilo.start()
        time.sleep(2)
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Todos los detectores están activos")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 💚 Keep-alive activo (ping cada 5 min)\n")

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

@app.route('/cron')
def cron_ping():
    """Endpoint para CRON jobs - Mantiene el servicio activo y verifica threads"""
    ahora = datetime.now()
    estado_sistema['ultima_actividad_cron'] = ahora.isoformat()
    
    # Verificar estado de threads
    threads_vivos = {}
    threads_muertos = []
    
    for nombre, thread in threads_detectores.items():
        if thread.is_alive():
            threads_vivos[nombre] = 'vivo'
        else:
            threads_vivos[nombre] = 'muerto'
            threads_muertos.append(nombre)
    
    # Log de actividad
    print(f"[{ahora.strftime('%H:%M:%S')}] 🔔 CRON ping recibido - Threads vivos: {len([t for t in threads_vivos.values() if t == 'vivo'])}/{len(threads_detectores)}")
    
    if threads_muertos:
        print(f"[{ahora.strftime('%H:%M:%S')}] ⚠️ Threads muertos detectados: {', '.join(threads_muertos)}")
    
    return jsonify({
        'status': 'alive',
        'timestamp': ahora.isoformat(),
        'threads': threads_vivos,
        'threads_activos': len([t for t in threads_vivos.values() if t == 'vivo']),
        'threads_totales': len(threads_detectores),
        'alerta': 'Hay threads muertos' if threads_muertos else None,
        'detectores': estado_sistema['detectores']
    }), 200

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
