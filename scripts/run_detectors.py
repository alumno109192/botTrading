"""
Run Detectors - Solo XAUUSD 1H + 15M (operativa simplificada EMA + S/R)
Incluye signal_monitor.py para tracking de señales
"""

import threading
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from datetime import datetime

# Solo los dos detectores que tienen edge probado
from detectors.gold import detector_gold_1h
from detectors.gold import detector_gold_15m
import services.signal_monitor as signal_monitor


def ejecutar_detector(nombre, modulo):
    """Ejecuta un detector en un bucle de reintentos (sin recursión)."""
    while True:
        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔵 Iniciando {nombre}...")
            modulo.main()
        except KeyboardInterrupt:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⚠️ {nombre} detenido por usuario")
            break
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error en {nombre}: {e}")
            time.sleep(60)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 Reintentando {nombre}...")


def main():
    print("=" * 60)
    print("🚀 SISTEMA DE DETECCIÓN — Modo simplificado (1H + 15M)")
    print("=" * 60)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("📊 Detectores activos:")
    print("  🥇 XAUUSD 1H  → detector_gold_1h.py")
    print("  ⚡ XAUUSD 15M → detector_gold_15m.py")
    print("  🔍 MONITOR    → signal_monitor.py")
    print()
    print("📈 Estrategia: EMA + Soporte/Resistencia (priorizada por scoring)")
    print("💾 Base de datos: Turso (SQLite Cloud)")
    print("📊 Tracking: TP1, TP2, TP3, SL automático")
    print("=" * 60)
    print()

    hilos = []

    hilo_1h = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR GOLD 1H", detector_gold_1h),
        name="DetectorGold1H",
        daemon=True
    )
    hilos.append(hilo_1h)

    hilo_15m = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR GOLD 15M", detector_gold_15m),
        name="DetectorGold15M",
        daemon=True
    )
    hilos.append(hilo_15m)

    hilo_monitor = threading.Thread(
        target=signal_monitor.monitor_senales,
        name="SignalMonitor",
        daemon=True
    )
    hilos.append(hilo_monitor)

    for hilo in hilos:
        hilo.start()
        time.sleep(2)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Detectores activos: 1H + 15M + Monitor")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📌 Presiona Ctrl+C para detener\n")

    try:
        while True:
            hilos_activos = [h for h in hilos if h.is_alive()]
            if len(hilos_activos) < len(hilos):
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Algún hilo se detuvo")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 Reiniciando sistema...")
                break
            time.sleep(60)
    except KeyboardInterrupt:
        print(f"\n\n[{datetime.now().strftime('%H:%M:%S')}] 🛑 Deteniendo detectores...")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 👋 Sistema finalizado")
        sys.exit(0)


if __name__ == '__main__':
    main()
