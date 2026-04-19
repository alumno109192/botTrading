"""Test simple de conexión a BD"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

print("1. Iniciando test...")

try:
    print("2. Importando db_manager...")
    from adapters.database import DatabaseManager
    
    print("3. Creando instancia...")
    db = DatabaseManager()
    
    print("4. Probando query simple...")
    result = db.ejecutar_query("SELECT 1 as test")
    
    print(f"5. Resultado: {result.rows}")
    print("✅ TEST EXITOSO!")
    
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
