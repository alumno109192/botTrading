#!/usr/bin/env python3
"""
Script para limpiar cache contaminado del data_provider.
Útil cuando hay ATR anómalo o datos mezclados entre timeframes.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.data_provider import _intraday_cache, _intraday_cache_lock

def limpiar_cache():
    """Limpia todo el cache de memoria del data_provider"""
    with _intraday_cache_lock:
        total = len(_intraday_cache)
        _intraday_cache.clear()
        print(f"✅ Cache limpiado: {total} entradas eliminadas")
        print("💡 El próximo ciclo descargará datos frescos")

if __name__ == '__main__':
    print("🧹 Limpiando cache de data_provider...")
    limpiar_cache()
