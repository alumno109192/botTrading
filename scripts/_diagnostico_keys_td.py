#!/usr/bin/env python3
"""
Script de diagnóstico para verificar el estado de las keys de TwelveData.
Muestra cuántas peticiones ha hecho cada key hoy y si alguna está en cooldown.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    print("🔍 Diagnóstico de TwelveData API Keys")
    print("=" * 60)
    
    # Cargar keys desde .env del proyecto
    import sys
    import os
    # Asegurar que el path incluye el directorio raíz
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        print("⚠️ python-dotenv no disponible, usando variables de entorno del sistema")
    
    keys_config = [
        ('key1',  'TWELVE_DATA_API_KEY'),
        ('key2',  'TWELVE_DATA_API_KEY_2'),
        ('key3',  'TWELVE_DATA_API_KEY_3'),
        ('key4',  'TWELVE_DATA_API_KEY_4'),
        ('key5',  'TWELVE_DATA_API_KEY_5'),
        ('key6',  'TWELVE_DATA_API_KEY_6'),
        ('key7',  'TWELVE_DATA_API_KEY_7'),
        ('key8',  'TWELVE_DATA_API_KEY_8'),
        ('key9',  'TWELVE_DATA_API_KEY_9'),
        ('key10', 'TWELVE_DATA_API_KEY_10'),
        ('key11', 'TWELVE_DATA_API_KEY_11'),
    ]
    
    keys_activas = []
    for alias, env_name in keys_config:
        key = os.environ.get(env_name, '').strip()
        if key:
            keys_activas.append((alias, key[:10] + '...'))
    
    print(f"\n📊 Keys configuradas: {len(keys_activas)}/11")
    print()
    
    if not keys_activas:
        print("❌ No hay keys de TwelveData configuradas en .env")
        print("💡 Configura al menos TWELVE_DATA_API_KEY en el archivo .env")
        return
    
    for alias, key_preview in keys_activas:
        print(f"  ✅ {alias:6} — {key_preview}")
    
    # Verificar uso desde BD
    print()
    print("📈 Uso de keys hoy (límite: 800 req/día por key):")
    print()
    
    try:
        from adapters.database import DatabaseManager
        db = DatabaseManager()
        uso = db.obtener_uso_keys_hoy()
        
        total_peticiones = 0
        for alias, _ in keys_activas:
            count = uso.get(alias, 0)
            total_peticiones += count
            porcentaje = (count / 800) * 100
            
            if count >= 800:
                emoji = "🔴"
                estado = "AGOTADA"
            elif count >= 600:
                emoji = "🟡"
                estado = "ALTA"
            else:
                emoji = "🟢"
                estado = "OK"
            
            barra = "█" * int(porcentaje / 10) + "░" * (10 - int(porcentaje / 10))
            print(f"  {emoji} {alias:6} {count:4}/800 [{barra}] {porcentaje:5.1f}% — {estado}")
        
        print()
        print(f"📊 Total peticiones hoy: {total_peticiones} ({total_peticiones}/{len(keys_activas)*800} capacidad)")
        print(f"💡 Capacidad restante: {len(keys_activas)*800 - total_peticiones} peticiones")
        
    except Exception as e:
        print(f"⚠️ No se pudo consultar la BD: {e}")
    
    # Verificar estado de cooldown
    print()
    print("⏱️ Estado de cooldown (límite: 7-8 req/minuto por key):")
    print()
    
    try:
        from adapters.data_provider import _key_cooldown, _key_minute_count
        import time
        
        ahora = time.time()
        alguna_en_cooldown = False
        
        for alias, _ in keys_activas:
            cooldown_until = _key_cooldown.get(alias, 0)
            if ahora < cooldown_until:
                segundos_restantes = int(cooldown_until - ahora)
                print(f"  ⏸️ {alias:6} — en cooldown por {segundos_restantes}s")
                alguna_en_cooldown = True
            else:
                minute_info = _key_minute_count.get(alias)
                if minute_info:
                    print(f"  ✅ {alias:6} — {minute_info['count']}/7 peticiones este minuto")
                else:
                    print(f"  ✅ {alias:6} — sin uso este minuto")
        
        if not alguna_en_cooldown and not any(_key_minute_count.get(alias) for alias, _ in keys_activas):
            print("  ✅ Ninguna key en cooldown")
    
    except Exception as e:
        print(f"⚠️ No se pudo verificar cooldown: {e}")
    
    # Test de conexión
    print()
    print("🔌 Test de conexión a TwelveData:")
    print()
    
    try:
        import requests
        test_url = "https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=1day&outputsize=1&apikey=" + os.environ.get('TWELVE_DATA_API_KEY', '')
        r = requests.get(test_url, timeout=10)
        
        if r.status_code == 200:
            data = r.json()
            if 'values' in data:
                print("  ✅ Conexión exitosa — API respondiendo correctamente")
            elif data.get('status') == 'error':
                msg = data.get('message', 'error desconocido')
                print(f"  ⚠️ API responde con error: {msg}")
        else:
            print(f"  ❌ Error HTTP {r.status_code}")
    
    except Exception as e:
        print(f"  ❌ Error de conexión: {e}")
    
    print()
    print("=" * 60)
    print("💡 Si todas las keys están agotadas, el sistema usará yfinance (delay 15 min)")
    print("💡 Las keys se resetean a las 00:00 UTC (medianoche)")

if __name__ == '__main__':
    main()
