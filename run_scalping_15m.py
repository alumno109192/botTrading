"""
Run Scalping 15M - Ejecuta solo el detector de GOLD 15M Scalping
Script independiente para operaciones de alta frecuencia
"""

import sys
import os

# Añadir ruta del detector
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'detectors', 'gold'))

import detector_gold_15m

if __name__ == '__main__':
    print("="*60)
    print("⚡ DETECTOR GOLD 15M SCALPING - MODO STANDALONE")
    print("="*60)
    print()
    print("📊 Activo: XAUUSD (Oro)")
    print("⏱️  Timeframe: 15 minutos")
    print("🔄 Frecuencia: Análisis cada 2 minutos")
    print("📈 Estrategia: Scalping (operaciones rápidas)")
    print()
    print("🎯 Objetivos:")
    print("  💰 TP1: $30 (conservador)")
    print("  💰 TP2: $50 (medio)")
    print("  💰 TP3: $80 (agresivo)")
    print()
    print("📊 Score mínimo:")
    print("  ⚡ Scalp: 3/15")
    print("  ⚠️  Media: 5/15")
    print("  🔥 Fuerte: 8/15")
    print()
    print("🛡️  Protecciones:")
    print("  🛑 SL ajustado: 1.5x ATR")
    print("  ⛔ Límite pérdidas: 3 consecutivas")
    print()
    print("="*60)
    print()
    print("🚀 Iniciando detector...")
    print()
    
    # Ejecutar el detector
    detector_gold_15m.main()
