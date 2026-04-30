#!/usr/bin/env python3
"""
Script para diagnosticar datos de 1H desde diferentes fuentes.
Compara TwelveData vs yfinance para detectar problemas de datos.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def calcular_atr_simple(df):
    """Calcula ATR simplificado para diagnóstico"""
    import pandas as pd
    import numpy as np
    
    df = df.copy()
    df['h_l'] = df['High'] - df['Low']
    df['h_pc'] = np.abs(df['High'] - df['Close'].shift(1))
    df['l_pc'] = np.abs(df['Low'] - df['Close'].shift(1))
    df['tr'] = df[['h_l', 'h_pc', 'l_pc']].max(axis=1)
    df['atr'] = df['tr'].rolling(14).mean()
    return df

def main():
    print("🔍 Diagnóstico de datos 1H — TwelveData vs yfinance")
    print("=" * 70)
    
    # 1. Obtener datos desde TwelveData
    print("\n1️⃣ Datos desde TwelveData (key1):")
    try:
        from adapters.database import DatabaseManager
        db = DatabaseManager()
        
        velas_td = db.obtener_velas('GC=F', '1h', '7d')
        print(f"  📊 Velas en BD: {len(velas_td)}")
        
        if velas_td:
            import pandas as pd
            df_td = pd.DataFrame(velas_td)
            df_td['ts'] = pd.to_datetime(df_td['ts'], format='ISO8601', utc=True)
            df_td = df_td.set_index('ts').sort_index()
            df_td = df_td.rename(columns={
                'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'
            })
            df_td = df_td[['Open', 'High', 'Low', 'Close']].astype(float)
            
            # Estadísticas básicas
            print(f"  📈 Última vela: {df_td.index[-1]}")
            print(f"  💰 Precio actual: ${df_td['Close'].iloc[-1]:.2f}")
            print(f"  📊 Rango precios: ${df_td['Low'].min():.2f} - ${df_td['High'].max():.2f}")
            
            # Calcular ATR
            df_td = calcular_atr_simple(df_td)
            atr_td = df_td['atr'].iloc[-1]
            print(f"  📉 ATR (14): ${atr_td:.2f}")
            
            # Analizar rangos de velas
            df_td['range'] = df_td['High'] - df_td['Low']
            rango_promedio = df_td['range'].tail(20).mean()
            rango_max = df_td['range'].tail(20).max()
            print(f"  📏 Rango promedio (20 velas): ${rango_promedio:.2f}")
            print(f"  📏 Rango máximo (20 velas): ${rango_max:.2f}")
            
            # Detectar velas anómalas
            velas_anomalas = df_td[df_td['range'] > 100].tail(5)
            if not velas_anomalas.empty:
                print(f"\n  ⚠️ Velas con rango > $100 detectadas:")
                for idx, row in velas_anomalas.iterrows():
                    print(f"    • {idx}: H:{row['High']:.2f} L:{row['Low']:.2f} Range:{row['range']:.2f}")
            
    except Exception as e:
        print(f"  ❌ Error: {e}")
        df_td = None
    
    # 2. Obtener datos desde yfinance (fallback)
    print("\n2️⃣ Datos desde yfinance (fallback):")
    try:
        import yfinance as yf
        ticker = yf.Ticker('GC=F')
        df_yf = ticker.history(period='7d', interval='1h')
        
        if not df_yf.empty:
            print(f"  📊 Velas descargadas: {len(df_yf)}")
            print(f"  📈 Última vela: {df_yf.index[-1]}")
            print(f"  💰 Precio actual: ${df_yf['Close'].iloc[-1]:.2f}")
            print(f"  📊 Rango precios: ${df_yf['Low'].min():.2f} - ${df_yf['High'].max():.2f}")
            
            # Calcular ATR
            df_yf = calcular_atr_simple(df_yf)
            atr_yf = df_yf['atr'].iloc[-1]
            print(f"  📉 ATR (14): ${atr_yf:.2f}")
            
            # Analizar rangos
            df_yf['range'] = df_yf['High'] - df_yf['Low']
            rango_promedio_yf = df_yf['range'].tail(20).mean()
            print(f"  📏 Rango promedio (20 velas): ${rango_promedio_yf:.2f}")
            
        else:
            print(f"  ⚠️ yfinance no devolvió datos")
            df_yf = None
            
    except Exception as e:
        print(f"  ❌ Error: {e}")
        df_yf = None
    
    # 3. Comparación
    print("\n3️⃣ Comparación de fuentes:")
    print("  " + "=" * 66)
    
    if df_td is not None and df_yf is not None:
        atr_td = df_td['atr'].iloc[-1]
        atr_yf = df_yf['atr'].iloc[-1]
        diff_atr = abs(atr_td - atr_yf)
        diff_pct = (diff_atr / atr_yf) * 100 if atr_yf > 0 else 0
        
        print(f"  ATR TwelveData:  ${atr_td:7.2f}")
        print(f"  ATR yfinance:    ${atr_yf:7.2f}")
        print(f"  Diferencia:      ${diff_atr:7.2f} ({diff_pct:.1f}%)")
        
        if diff_pct > 50:
            print(f"\n  ⚠️ PROBLEMA DETECTADO: Diferencia > 50%")
            print(f"  💡 TwelveData está devolviendo datos incorrectos para 1H")
            print(f"  💡 Solución: Cambiar a yfinance para 1H temporalmente")
        elif atr_td > 80:
            print(f"\n  ⚠️ ATR anormalmente alto en ambas fuentes")
            print(f"  💡 Posible volatilidad extrema en el mercado")
            print(f"  💡 O problema en el símbolo XAU/USD de ambas APIs")
        else:
            print(f"\n  ✅ Datos consistentes entre fuentes")
    
    # 4. Recomendación
    print("\n4️⃣ Recomendación:")
    print("  " + "=" * 66)
    
    if df_td is not None:
        atr = df_td['atr'].iloc[-1]
        if atr > 80:
            print(f"  🔧 ATR anómalo detectado (${atr:.2f} > $80)")
            print(f"  📝 Opciones:")
            print(f"     1. Usar datos de 5M y resamplear a 1H (confiable)")
            print(f"     2. Cambiar a yfinance para 1H (delay 15 min)")
            print(f"     3. Aumentar umbral ATR a $150 (temporal)")
            print(f"     4. Reportar problema a TwelveData")
        else:
            print(f"  ✅ ATR normal (${atr:.2f}) — datos correctos")
    
    print()
    print("=" * 70)

if __name__ == '__main__':
    main()
