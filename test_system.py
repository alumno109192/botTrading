"""
Test de Conexión y Funcionalidad del Sistema de Tracking
Ejecuta este script para verificar que todo está correctamente configurado
"""

import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv

def print_header(texto):
    """Imprime un header bonito"""
    print("\n" + "="*60)
    print(f"  {texto}")
    print("="*60)

def print_success(texto):
    """Imprime mensaje de éxito"""
    print(f"✅ {texto}")

def print_error(texto):
    """Imprime mensaje de error"""
    print(f"❌ {texto}")

def print_warning(texto):
    """Imprime mensaje de advertencia"""
    print(f"⚠️  {texto}")

def test_env_variables():
    """Verifica que todas las variables de entorno estén configuradas"""
    print_header("TEST 1: Variables de Entorno")
    
    load_dotenv()
    
    required_vars = {
        'TELEGRAM_TOKEN': os.environ.get('TELEGRAM_TOKEN'),
        'TELEGRAM_CHAT_ID': os.environ.get('TELEGRAM_CHAT_ID'),
        'TURSO_DATABASE_URL': os.environ.get('TURSO_DATABASE_URL'),
        'TURSO_AUTH_TOKEN': os.environ.get('TURSO_AUTH_TOKEN')
    }
    
    all_ok = True
    
    for var, descripcion in required_vars.items():
        value = os.environ.get(var)
        if value:
            # Ocultar parcialmente tokens para seguridad
            if 'TOKEN' in var:
                masked = f"{value[:10]}...{value[-5:]}" if len(value) > 15 else "***"
                print_success(f"{var}: {masked}")
            else:
                print_success(f"{var}: {value[:30]}...")
        else:
            print_error(f"{var} NO CONFIGURADO - {descripcion}")
            all_ok = False
    
    return all_ok

def test_dependencies():
    """Verifica que todas las dependencias estén instaladas"""
    print_header("TEST 2: Dependencias Instaladas")
    
    required_packages = [
        ('yfinance', 'yfinance'),
        ('pandas', 'pandas'),
        ('numpy', 'numpy'),
        ('requests', 'requests'),
        ('plotly', 'plotly'),
        ('tabulate', 'tabulate'),
        ('schedule', 'schedule')
    ]
    
    all_ok = True
    
    for package_import, package_name in required_packages:
        try:
            __import__(package_import)
            print_success(f"{package_name}")
        except ImportError:
            print_error(f"{package_name} - Instalar con: pip install {package_name}")
            all_ok = False
    
    return all_ok

def test_database_connection():
    """Verifica conexión a la base de datos"""
    print_header("TEST 3: Conexión a Base de Datos")
    
    try:
        from db_manager import DatabaseManager
        
        db = DatabaseManager()
        print_success("Conexión a Turso establecida")
        
        # Probar una consulta simple
        result = db.ejecutar_query("SELECT 1 as test")
        if result and result.rows:
            print_success("Consulta de prueba exitosa")
        else:
            print_warning("Consulta retornó resultado vacío")
        
        # Verificar señales activas
        activas = db.obtener_senales_activas()
        print_success(f"Señales activas encontradas: {len(activas)}")
        
        return True
        
    except Exception as e:
        print_error(f"Error de conexión: {str(e)[:100]}")
        return False

def test_signal_creation():
    """Prueba crear una señal de prueba"""
    print_header("TEST 4: Creación de Señal de Prueba")
    
    try:
        from db_manager import DatabaseManager
        import json
        
        db = DatabaseManager()
        
        # Crear señal de prueba
        senal_data = {
            'timestamp': datetime.now(timezone.utc),
            'simbolo': 'TEST',
            'direccion': 'COMPRA',
            'precio_entrada': 100.0,
            'tp1': 105.0,
            'tp2': 110.0,
            'tp3': 115.0,
            'sl': 95.0,
            'score': 10,
            'indicadores': json.dumps({'rsi': 45, 'macd': 0.5}),
            'patron_velas': 'TEST',
            'version_detector': 'TEST'
        }
        
        senal_id = db.guardar_senal(senal_data)
        print_success(f"Señal de prueba creada con ID: {senal_id}")
        
        # Eliminar señal de prueba
        db.cerrar_senal(senal_id, 'TEST')
        print_success("Señal de prueba eliminada correctamente")
        
        return True
        
    except Exception as e:
        print_error(f"Error creando señal: {str(e)[:100]}")
        return False

def test_stats_dashboard():
    """Prueba el dashboard de estadísticas"""
    print_header("TEST 5: Dashboard de Estadísticas")
    
    try:
        from stats_dashboard import StatsDashboard
        
        dashboard = StatsDashboard()
        print_success("Dashboard inicializado")
        
        # Calcular win rates
        win_rate_all = dashboard.calcular_win_rate('all')
        print_success(f"Win rate total: {win_rate_all:.1f}%")
        
        return True
        
    except Exception as e:
        print_error(f"Error en dashboard: {str(e)[:100]}")
        return False

def test_files_exist():
    """Verifica que todos los archivos necesarios existan"""
    print_header("TEST 6: Archivos del Sistema")
    
    required_files = [
        'db_manager.py',
        'signal_monitor.py',
        'stats_dashboard.py',
        'detector_bitcoin.py',
        'run_detectors.py',
        '.env'
    ]
    
    all_ok = True
    
    for filename in required_files:
        if os.path.exists(filename):
            print_success(f"{filename}")
        else:
            print_error(f"{filename} - ARCHIVO NO ENCONTRADO")
            all_ok = False
    
    return all_ok

def main():
    """Ejecuta todos los tests"""
    print("\n")
    print("╔══════════════════════════════════════════════════════════╗")
    print("║     🔍 TEST DE SISTEMA DE TRACKING DE SEÑALES        ║")
    print("╚══════════════════════════════════════════════════════════╝")
    
    tests = [
        ("Archivos del Sistema", test_files_exist),
        ("Variables de Entorno", test_env_variables),
        ("Dependencias", test_dependencies),
        ("Conexión a BD", test_database_connection),
        ("Creación de Señales", test_signal_creation),
        ("Dashboard", test_stats_dashboard),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print_error(f"Error ejecutando {test_name}: {e}")
            results.append((test_name, False))
    
    # Resumen final
    print_header("RESUMEN DE TESTS")
    
    total = len(results)
    passed = sum(1 for _, result in results if result)
    
    for test_name, result in results:
        if result:
            print_success(f"{test_name}")
        else:
            print_error(f"{test_name}")
    
    print("\n" + "="*60)
    print(f"  Tests pasados: {passed}/{total}")
    
    if passed == total:
        print("  🎉 ¡TODOS LOS TESTS PASARON! Sistema listo para usar.")
    else:
        print("  ⚠️  Algunos tests fallaron. Revisa los errores arriba.")
    
    print("="*60 + "\n")
    
    return passed == total

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
