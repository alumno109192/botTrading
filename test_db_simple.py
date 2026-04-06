"""Test simple de conexión a BD"""

print("1. Iniciando test...")

try:
    print("2. Importando db_manager...")
    from db_manager import DatabaseManager
    
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
