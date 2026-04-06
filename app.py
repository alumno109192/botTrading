"""
Run Detectors - Ejecuta múltiples detectores de señales simultáneamente
Ejecuta detector_gold.py, detector_spx.py y detector_bitcoin.py en hilos separados
Incluye signal_monitor.py para tracking de señales
"""

import threading
import time
from datetime import datetime
import sys

# Importar los módulos de los detectores
import detector_gold_copy
import detector_spx_copy
import detector_bitcoin
import signal_monitor

def ejecutar_detector(nombre, modulo):
    """Ejecuta un detector en un hilo separado"""
    try:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔵 Iniciando {nombre}...")
        modulo.main()
    except KeyboardInterrupt:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⚠️ {nombre} detenido por usuario")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error en {nombre}: {e}")
        # Reintentar en 60 segundos
        time.sleep(60)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 Reintentando {nombre}...")
        ejecutar_detector(nombre, modulo)

def main():
    print("="*60)
    print("🚀 SISTEMA DE DETECCIÓN DE SEÑALES INICIADO")
    print("="*60)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("📊 Detectores activos (con indicadores de alta prioridad):")
    print("  🥇 XAUUSD (Oro)       → detector_gold.py")
    print("  📈 SPX500 (S&P 500)   → detector_spx.py")
    print("  ₿  BTCUSD (Bitcoin)   → detector_bitcoin.py")
    print("  🔍 MONITOR SEÑALES    → signal_monitor.py")
    print()
    print("📈 Indicadores implementados:")
    print("  ✅ Bandas de Bollinger  ✅ MACD")
    print("  ✅ OBV (Volumen)        ✅ ADX (Tendencia)")
    print("  ✅ Evening/Morning Star (Patrones 3 velas)")
    print()
    print("💾 Base de datos: Turso (SQLite Cloud)")
    print("📊 Tracking: TP1, TP2, TP3, SL automático")
    print("⏱️  Intervalo: 14 minutos cada detector")
    print("🔍 Monitor: Revisa señales cada 5 minutos")
    print("💚 Servidor: Siempre activo")
    print("✅ Anti-spam: Solo alertas en velas nuevas")
    print("🎯 Score máximo: 24 puntos (antes 15)")
    print("="*60)
    print()

    # Crear hilos para cada detector
    hilos = []
    
    # Hilo para detector de Oro
    hilo_gold = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR GOLD (XAUUSD)", detector_gold_copy),
        name="DetectorGold",
        daemon=True
    )
    hilos.append(hilo_gold)
    
    # Hilo para detector de SPX500
    hilo_spx = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR SPX (SPX500)", detector_spx_copy),
        name="DetectorSPX",
        daemon=True
    )
    hilos.append(hilo_spx)
    
    # Hilo para detector de Bitcoin
    hilo_btc = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR BITCOIN (BTCUSD)", detector_bitcoin),
        name="DetectorBitcoin",
        daemon=True
    )
    hilos.append(hilo_btc)
    
    # ⭐ NUEVO: Hilo para monitor de señales
    hilo_monitor = threading.Thread(
        target=signal_monitor.monitor_senales,
        name="SignalMonitor",
        daemon=True
    )
    hilos.append(hilo_monitor)
    
    # Iniciar todos los hilos
    for hilo in hilos:
        hilo.start()
        time.sleep(2)  # Pequeña pausa entre inicios
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Todos los detectores están activos")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📌 Presiona Ctrl+C para detener\n")
    
    try:
        # Mantener el programa principal activo
        while True:
            # Verificar que todos los hilos sigan vivos
            hilos_activos = [h for h in hilos if h.is_alive()]
            
            if len(hilos_activos) < len(hilos):
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Algún hilo se detuvo")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 Reiniciando sistema...")
                break
            
            time.sleep(60)  # Verificar cada minuto
            
    except KeyboardInterrupt:
        print(f"\n\n[{datetime.now().strftime('%H:%M:%S')}] 🛑 Deteniendo todos los detectores...")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 👋 Sistema finalizado")
        sys.exit(0)

if __name__ == '__main__':
    main()
