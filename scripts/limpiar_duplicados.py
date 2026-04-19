"""
Script para limpiar señales duplicadas en la BD.
Mantiene solo la señal más antigua de cada grupo (simbolo + direccion)
y cancela todas las demás que estén ACTIVAS.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.database import DatabaseManager

def limpiar_duplicados():
    db = DatabaseManager()
    
    # Obtener todos los grupos con más de 1 señal ACTIVA
    query_grupos = """
    SELECT simbolo, direccion, COUNT(*) as total, MIN(id) as id_mantener
    FROM senales
    WHERE estado = 'ACTIVA'
    GROUP BY simbolo, direccion
    HAVING COUNT(*) > 1
    """
    
    result = db.ejecutar_query(query_grupos)
    
    if not result.rows:
        print("✅ No hay señales duplicadas activas.")
        return
    
    total_canceladas = 0
    
    for row in result.rows:
        simbolo = row['simbolo']
        direccion = row['direccion']
        total = int(row['total'])
        id_mantener = int(row['id_mantener'])
        
        print(f"\n📊 {simbolo} | {direccion} → {total} señales activas, manteniendo ID {id_mantener}")
        
        # Obtener IDs de los duplicados (todos excepto el más antiguo)
        query_duplicados = """
        SELECT id FROM senales
        WHERE simbolo = ?
        AND direccion = ?
        AND estado = 'ACTIVA'
        AND id != ?
        """
        dup_result = db.ejecutar_query(query_duplicados, (simbolo, direccion, id_mantener))
        
        for dup_row in dup_result.rows:
            dup_id = int(dup_row['id'])
            # Cancelar el duplicado
            db.ejecutar_query(
                "UPDATE senales SET estado = 'CANCELADA', beneficio_final_pct = 0 WHERE id = ?",
                (dup_id,)
            )
            print(f"  🗑️  Cancelada señal duplicada ID {dup_id}")
            total_canceladas += 1
    
    print(f"\n✅ Limpieza completada: {total_canceladas} señales duplicadas canceladas.")

if __name__ == '__main__':
    limpiar_duplicados()
