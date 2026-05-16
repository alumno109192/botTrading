import sys; sys.path.insert(0, '.')
from adapters.database import get_db
db = get_db()

# Ver columnas de la tabla senales
try:
    r = db.ejecutar_query("PRAGMA table_info(senales)")
    print("=== COLUMNAS DE senales:")
    for row in r.rows:
        print(f"  {row}")
except Exception as e:
    print("Error PRAGMA:", e)

# Señales con IDs altos (536-540) — las del screenshot
try:
    r2 = db.ejecutar_query("SELECT * FROM senales WHERE id >= 535 ORDER BY id DESC")
    print(f"\n=== IDs 535+ [{len(r2.rows)} filas]:")
    for row in r2.rows:
        entry = float(row.get('precio_entrada') or 0)
        sl    = float(row.get('sl') or 0)
        flag  = ""
        if row.get('direccion') == 'VENTA' and sl < entry:
            flag = "  ⚠️ SL INVERTIDO (SL<entry para SELL)"
        print(f"  #{row.get('id')} {row.get('simbolo')} {row.get('direccion')} entry={entry} sl={sl} score={row.get('score')} nivel={row.get('nivel')} estado={row.get('estado')}{flag}")
        print(f"    patron={str(row.get('patron_velas',''))[:60]}")
        print(f"    version={row.get('version_detector')} beneficio={row.get('beneficio_final_pct')}")
except Exception as e:
    print("Error IDs altos:", e)

# El signal_monitor y el frontend: ¿qué campo usa como "ENTRADA"?
# Buscar la señal BUY 5M ACTIVA
try:
    r3 = db.ejecutar_query("SELECT * FROM senales WHERE estado='ACTIVA' ORDER BY id DESC LIMIT 5")
    print(f"\n=== ACTIVAS ACTUALES [{len(r3.rows)} filas]:")
    for row in r3.rows:
        entry = float(row.get('precio_entrada') or 0)
        sl    = float(row.get('sl') or 0)
        print(f"  #{row.get('id')} {row.get('simbolo')} {row.get('direccion')} entry={entry} sl={sl} score={row.get('score')} nivel={row.get('nivel')}")
        print(f"    tp1={row.get('tp1')} tp2={row.get('tp2')} tp3={row.get('tp3')}")
        print(f"    patron={str(row.get('patron_velas',''))[:60]}")
except Exception as e:
    print("Error activas:", e)
