"""
Script de prueba para detector Bitcoin 4H
Ejecuta un análisis único sin loop infinito
"""
import sys
import os

# Añadir ruta al path para importar detector
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Importar el detector
from detectors.bitcoin import detector_bitcoin_4h

def test_detector_4h():
    print("=" * 70)
    print("🧪 PRUEBA DETECTOR BITCOIN 4H")
    print("=" * 70)
    
    # Parámetros del detector
    simbolos = detector_bitcoin_4h.SIMBOLOS
    
    print(f"\n📊 Configuración:")
    print(f"   Intervalo de análisis: {detector_bitcoin_4h.CHECK_INTERVAL // 60} minutos")
    print(f"   Símbolos: {list(simbolos.keys())}")
    
    for simbolo, params in simbolos.items():
        print(f"\n{'─' * 70}")
        print(f"🔍 Analizando {simbolo} en timeframe 4H...")
        print(f"{'─' * 70}")
        
        print(f"\n📈 Parámetros ajustados para 4H:")
        print(f"   EMA Rápida:  {params['ema_fast_len']} (vs 9 en 1D)")
        print(f"   EMA Lenta:   {params['ema_slow_len']} (vs 21 en 1D)")
        print(f"   RSI:         {params['rsi_length']} (vs 14 en 1D)")
        print(f"   ATR:         {params['atr_length']} (vs 14 en 1D)")
        print(f"   SL mult:     {params['atr_sl_mult']}x (vs 2.5x en 1D)")
        
        print(f"\n🎯 Zonas de trading:")
        print(f"   Resistencia: ${params['zona_resist_low']:,.0f} - ${params['zona_resist_high']:,.0f}")
        print(f"   Soporte:     ${params['zona_soporte_low']:,.0f} - ${params['zona_soporte_high']:,.0f}")
        
        print(f"\n🎯 Take Profits configurados:")
        print(f"   VENTA - TP1: ${params['tp1_venta']:,.0f}, TP2: ${params['tp2_venta']:,.0f}, TP3: ${params['tp3_venta']:,.0f}")
        print(f"   COMPRA - TP1: ${params['tp1_compra']:,.0f}, TP2: ${params['tp2_compra']:,.0f}, TP3: ${params['tp3_compra']:,.0f}")
        
        print(f"\n{'─' * 70}")
        print("🚀 Ejecutando análisis...")
        print(f"{'─' * 70}\n")
        
        try:
            # Ejecutar análisis unitario
            detector_bitcoin_4h.analizar(simbolo, params)
            print(f"\n{'─' * 70}")
            print(f"✅ Análisis de {simbolo} completado exitosamente")
            print(f"{'─' * 70}")
        except Exception as e:
            print(f"\n{'─' * 70}")
            print(f"❌ Error en análisis de {simbolo}: {e}")
            print(f"{'─' * 70}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 70)
    print("✅ PRUEBA COMPLETADA")
    print("=" * 70)
    print("\n📝 Notas:")
    print("   - Si no hay señales, es normal (depende de condiciones del mercado)")
    print("   - Scoring mínimo 4H: 5 (alerta), 9 (media), 12 (fuerte), 14 (máxima)")
    print("   - Más estricto que 1D para evitar ruido")
    print("   - Las alertas de Telegram solo se envían si hay señal válida")
    print("\n🔗 Para ejecutar continuamente:")
    print("   python detectors/bitcoin/detector_bitcoin_4h.py")
    print("\n")

if __name__ == '__main__':
    test_detector_4h()
