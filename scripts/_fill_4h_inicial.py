#!/usr/bin/env python3
"""
Script para forzar descarga inicial de datos 4H desde TwelveData.
Útil después de ajustar los parámetros de data_provider para 4H.

Descarga 3 meses (90 días) → ~540 velas necesarias para EMA de 400 periodos.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.data_provider import poll_ohlcv

def main():
    print("🔄 Forzando descarga inicial de datos 4H desde TwelveData...")
    print("📊 Objetivo: 540 velas (90 días × 6 velas/día) para EMA 400p")
    print()
    
    ok = poll_ohlcv('GC=F', '4h')
    
    if ok:
        print()
        print("✅ Descarga completada exitosamente")
        print("💡 El detector 4H ahora tendrá suficientes datos para calcular indicadores")
    else:
        print()
        print("❌ Error en la descarga")
        print("⚠️ Verifica que TWELVE_DATA_API_KEY esté configurada en .env")
        print("⚠️ Verifica rate limit de 55 req/min (plan Grow 55: sin límite diario)")

if __name__ == '__main__':
    main()
