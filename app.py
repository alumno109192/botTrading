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
import sys

# Forzar flush inmediato de logs (crítico para Render)
sys.stdout.reconfigure(line_buffering=True)

# Importar los módulos de los detectores organizados
from detectors.bitcoin import detector_bitcoin_1d
from detectors.bitcoin import detector_bitcoin_4h
from detectors.gold import detector_gold_1d
from detectors.gold import detector_gold_4h
from detectors.spx import detector_spx_1d
from detectors.spx import detector_spx_4h
import signal_monitor

app = Flask(__name__)

# Estado del sistema
estado_sistema = {
    'iniciado': datetime.now().isoformat(),
    'ultima_actividad_cron': None,
    'detectores': {
        'bitcoin_1d': 'iniciando',
        'bitcoin_4h': 'iniciando',
        'gold_1d': 'iniciando',
        'gold_4h': 'iniciando',
        'spx_1d': 'iniciando',
        'spx_4h': 'iniciando',
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
                sys.stdout.flush()
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Keep-alive ping failed: {response.status_code}")
                sys.stdout.flush()
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Keep-alive error: {e}")
            sys.stdout.flush()
        
        time.sleep(60)  # 1 minuto = 60 segundos

def iniciar_detectores():
    """Inicia todos los detectores en background"""
    print("="*60)
    print("🚀 SISTEMA DE DETECCIÓN DE SEÑALES INICIADO")
    print("="*60)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("📊 Detectores activos (Timeframes 1D + 4H):")
    print("  ₿  BTCUSD   → 1D + 4H  (2 detectores)")
    print("  🥇 XAUUSD   → 1D + 4H  (2 detectores)")
    print("  📈 SPX500   → 1D + 4H  (2 detectores)")
    print("  🔍 MONITOR  → Tracking de señales")
    print("  📊 TOTAL: 7 threads de detección")
    print("="*60)
    print()
    
    # Crear hilos para cada detector
    hilos = []
    
    print("🔧 Creando threads...")
    print()
    print("  📊 DETECTORES TIMEFRAME 1D (Diario):")
    
    try:
        # Bitcoin 1D
        print("  📦 Creando thread: Bitcoin 1D...")
        hilo_btc_1d = threading.Thread(
            target=ejecutar_detector,
            args=("DETECTOR BITCOIN 1D", detector_bitcoin_1d, "bitcoin_1d"),
            name="DetectorBitcoin1D",
            daemon=True
        )
        hilos.append(hilo_btc_1d)
        threads_detectores['bitcoin_1d'] = hilo_btc_1d
        print("    ✓ Thread Bitcoin 1D creado")
    except Exception as e:
        print(f"    ✗ Error creando thread Bitcoin 1D: {e}")
    
    try:
        # Gold 1D
        print("  📦 Creando thread: Gold 1D...")
        hilo_gold_1d = threading.Thread(
            target=ejecutar_detector,
            args=("DETECTOR GOLD 1D", detector_gold_1d, "gold_1d"),
            name="DetectorGold1D",
            daemon=True
        )
        hilos.append(hilo_gold_1d)
        threads_detectores['gold_1d'] = hilo_gold_1d
        print("    ✓ Thread Gold 1D creado")
    except Exception as e:
        print(f"    ✗ Error creando thread Gold 1D: {e}")
    
    try:
        # SPX 1D
        print("  📦 Creando thread: SPX 1D...")
        hilo_spx_1d = threading.Thread(
            target=ejecutar_detector,
            args=("DETECTOR SPX 1D", detector_spx_1d, "spx_1d"),
            name="DetectorSPX1D",
            daemon=True
        )
        hilos.append(hilo_spx_1d)
        threads_detectores['spx_1d'] = hilo_spx_1d
        print("    ✓ Thread SPX 1D creado")
    except Exception as e:
        print(f"    ✗ Error creando thread SPX 1D: {e}")
    
    print()
    print("  📊 DETECTORES TIMEFRAME 4H (4 Horas):")
    
    try:
        # Bitcoin 4H
        print("  📦 Creando thread: Bitcoin 4H...")
        hilo_btc_4h = threading.Thread(
            target=ejecutar_detector,
            args=("DETECTOR BITCOIN 4H", detector_bitcoin_4h, "bitcoin_4h"),
            name="DetectorBitcoin4H",
            daemon=True
        )
        hilos.append(hilo_btc_4h)
        threads_detectores['bitcoin_4h'] = hilo_btc_4h
        print("    ✓ Thread Bitcoin 4H creado")
    except Exception as e:
        print(f"    ✗ Error creando thread Bitcoin 4H: {e}")
    
    try:
        # Gold 4H
        print("  📦 Creando thread: Gold 4H...")
        hilo_gold_4h = threading.Thread(
            target=ejecutar_detector,
            args=("DETECTOR GOLD 4H", detector_gold_4h, "gold_4h"),
            name="DetectorGold4H",
            daemon=True
        )
        hilos.append(hilo_gold_4h)
        threads_detectores['gold_4h'] = hilo_gold_4h
        print("    ✓ Thread Gold 4H creado")
    except Exception as e:
        print(f"    ✗ Error creando thread Gold 4H: {e}")
    
    try:
        # SPX 4H
        print("  📦 Creando thread: SPX 4H...")
        hilo_spx_4h = threading.Thread(
            target=ejecutar_detector,
            args=("DETECTOR SPX 4H", detector_spx_4h, "spx_4h"),
            name="DetectorSPX4H",
            daemon=True
        )
        hilos.append(hilo_spx_4h)
        threads_detectores['spx_4h'] = hilo_spx_4h
        print("    ✓ Thread SPX 4H creado")
    except Exception as e:
        print(f"    ✗ Error creando thread SPX 4H: {e}")
    
    print()
    
    print("  📊 OTROS SERVICIOS:")
    
    try:
        # Hilo para monitor de señales
        print("  📦 Creando thread: Monitor...")
        hilo_monitor = threading.Thread(
            target=ejecutar_detector,
            args=("MONITOR SEÑALES", signal_monitor, "monitor"),
            name="SignalMonitor",
            daemon=True
        )
        hilos.append(hilo_monitor)
        threads_detectores['monitor'] = hilo_monitor
        print("    ✓ Thread MONITOR creado")
    except Exception as e:
        print(f"    ✗ Error creando thread MONITOR: {e}")
    
    try:
        # Hilo para keep-alive (evita que Render duerma la instancia)
        print("  📦 Creando thread: Keep-alive...")
        hilo_keepalive = threading.Thread(
            target=keep_alive,
            name="KeepAlive",
            daemon=True
        )
        hilos.append(hilo_keepalive)
        print("    ✓ Thread KEEP-ALIVE creado")
    except Exception as e:
        print(f"    ✗ Error creando thread KEEP-ALIVE: {e}")
    
    # Iniciar todos los hilos
    print(f"\n🚀 Iniciando {len(hilos)} threads...")
    print()
    for i, hilo in enumerate(hilos, 1):
        try:
            hilo.start()
            print(f"  [{i}/{len(hilos)}] ✓ {hilo.name} iniciado")
            time.sleep(1)  # Reducido a 1 segundo entre threads
        except Exception as e:
            print(f"  [{i}/{len(hilos)}] ✗ Error iniciando {hilo.name}: {e}")
    
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ✅ Proceso de inicio completado")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Detectores 1D: 3 (Bitcoin, Gold, SPX)")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Detectores 4H: 3 (Bitcoin, Gold, SPX)")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Otros: 2 (Monitor + Keep-alive)")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Total threads: {len(threads_detectores)} detectores + 1 keep-alive")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 💚 Keep-alive activo (ping cada 1 min)\n")
    sys.stdout.flush()

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
    
    # Verificar si hay threads registrados
    if not threads_detectores:
        print(f"[{ahora.strftime('%H:%M:%S')}] 🔔 CRON ping recibido - ⚠️ NO HAY THREADS REGISTRADOS (error en inicialización)")
        sys.stdout.flush()
        return jsonify({
            'status': 'alive_sin_detectores',
            'timestamp': ahora.isoformat(),
            'error': 'No hay threads de detectores registrados',
            'threads': {},
            'threads_activos': 0,
            'threads_totales': 0,
            'alerta': 'Sistema arrancó sin detectores - revisar logs de inicio'
        }), 200
    
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
    sys.stdout.flush()  # Forzar flush inmediato para Render
    
    if threads_muertos:
        print(f"[{ahora.strftime('%H:%M:%S')}] ⚠️ Threads muertos detectados: {', '.join(threads_muertos)}")
        sys.stdout.flush()
    
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
    print("\n" + "="*60)
    print("🌟 INICIANDO BOT TRADING")
    print("="*60)
    
    try:
        # Iniciar detectores en background
        print("📦 Iniciando detectores en background...")
        iniciar_detectores()
        print("✅ Detectores iniciados correctamente\n")
    except Exception as e:
        print(f"❌ ERROR FATAL iniciando detectores: {e}")
        import traceback
        traceback.print_exc()
        print("⚠️ El servidor Flask arrancará SIN detectores\n")
    
    # Iniciar servidor Flask
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Servidor Flask iniciando en puerto {port}...")
    print("="*60 + "\n")
    app.run(host='0.0.0.0', port=port, debug=False)
