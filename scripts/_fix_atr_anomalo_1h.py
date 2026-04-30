#!/usr/bin/env python3
"""
Script para limpiar datos contaminados de 1H y forzar descarga fresca.
Útil cuando se detecta ATR anómalo o datos corruptos en el detector 1H.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    print("🧹 Limpiando datos contaminados de 1H...")
    print("=" * 60)
    
    # 1. Limpiar cache de memoria
    print("\n1️⃣ Limpiando cache de memoria...")
    try:
        from adapters.data_provider import _intraday_cache, _intraday_cache_lock
        with _intraday_cache_lock:
            claves_antes = len(_intraday_cache)
            claves_1h = [k for k in _intraday_cache if '1h' in str(k).lower()]
            for k in claves_1h:
                _intraday_cache.pop(k, None)
            print(f"  ✅ Cache memoria limpiado: {len(claves_1h)} entradas de 1H eliminadas")
            print(f"  📊 Total cache: {claves_antes} → {len(_intraday_cache)} entradas")
    except Exception as e:
        print(f"  ⚠️ Error limpiando cache memoria: {e}")
    
    # 2. Purgar datos de BD para 1H
    print("\n2️⃣ Limpiando base de datos...")
    try:
        from adapters.database import DatabaseManager
        db = DatabaseManager()
        
        # Purgar todas las velas de 1H
        db.purgar_velas_antiguas('GC=F', '1h', dias_max=0)
        print(f"  ✅ Base de datos limpiada: todas las velas de 1H eliminadas")
        
        # Verificar que está vacía
        velas_restantes = len(db.obtener_velas('GC=F', '1h', '30d'))
        print(f"  📊 Velas restantes en BD: {velas_restantes}")
        
    except Exception as e:
        print(f"  ⚠️ Error limpiando BD: {e}")
    
    # 3. Forzar descarga fresca desde TwelveData
    print("\n3️⃣ Descargando datos frescos desde TwelveData...")
    try:
        from adapters.data_provider import poll_ohlcv
        
        ok = poll_ohlcv('GC=F', '1h')
        
        if ok:
            print(f"  ✅ Datos frescos descargados exitosamente")
            
            # Verificar ATR de los nuevos datos
            db = DatabaseManager()
            velas = db.obtener_velas('GC=F', '1h', '7d')
            print(f"  📊 Nuevas velas en BD: {len(velas)}")
            
            if len(velas) >= 30:
                import pandas as pd
                from core.indicators import calcular_atr
                
                df = pd.DataFrame(velas)
                df['ts'] = pd.to_datetime(df['ts'], format='ISO8601', utc=True)
                df = df.set_index('ts')
                df = df.rename(columns={
                    'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'
                })
                df = df[['Open', 'High', 'Low', 'Close']].astype(float)
                
                df['atr'] = calcular_atr(df, 14)
                atr_actual = float(df['atr'].iloc[-1])
                
                if 5 <= atr_actual <= 80:
                    print(f"  ✅ ATR verificado: ${atr_actual:.2f} (rango normal: $5-$80)")
                else:
                    print(f"  ⚠️ ATR fuera de rango: ${atr_actual:.2f} (esperado: $5-$80)")
                    print(f"  💡 Si el problema persiste, verifica TWELVE_DATA_API_KEY")
        else:
            print(f"  ❌ Error descargando datos frescos")
            print(f"  💡 Verifica que TWELVE_DATA_API_KEY esté configurada")
            print(f"  💡 Ejecuta: python scripts/_diagnostico_keys_td.py")
            
    except Exception as e:
        print(f"  ⚠️ Error descargando datos: {e}")
    
    print()
    print("=" * 60)
    print("✅ Limpieza completada")
    print("💡 El detector 1H usará datos frescos en el próximo ciclo (60s)")

if __name__ == '__main__':
    main()
