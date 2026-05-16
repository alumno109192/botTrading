import sys; sys.path.insert(0, '.')
from adapters.database import get_db
db = get_db()

# Todas las señales de hoy sin filtro de estado
r = db.ejecutar_query("""
SELECT id, simbolo, direccion, precio_entrada, sl, tp1,
       score, nivel, patron_velas, estado, ciclo_vida,
       strftime('%Y-%m-%d %H:%M', fecha_senal) as fecha,
       version_detector
FROM senales
WHERE simbolo LIKE 'XAUUSD%'
ORDER BY fecha_senal DESC LIMIT 15
""")
print(f"=== ÚLTIMAS 15 SEÑALES XAUUSD ===  [{len(r.rows)} filas]")
for row in r.rows:
    cols = dict(zip(r.columns, row))
    entry = float(cols['precio_entrada'] or 0)
    sl    = float(cols['sl'] or 0)
    flag  = ""
    if cols['direccion'] == 'VENTA' and sl < entry:
        flag = "  ⚠️ SL INVERTIDO"
    elif cols['direccion'] == 'COMPRA' and sl > entry:
        flag = "  ⚠️ SL INVERTIDO"
    score = int(cols['score'] or 0)
    nivel = cols['nivel'] or ''
    if nivel == 'FUERTE' and score < 13:
        flag += "  ⚠️ SCORE<13 pero FUERTE"
    print(f"  #{cols['id']} {cols['fecha']} | {cols['simbolo']} {cols['direccion']} | entry={entry} sl={sl} | score={score} nivel={nivel} | estado={cols['estado']} | patron={str(cols['patron_velas'])[:40]}{flag}")

# IDs específicos del screenshot por precio
print("\n=== BUSCANDO POR PRECIO DE IMAGEN ===")
r2 = db.ejecutar_query("""
SELECT id, simbolo, direccion, precio_entrada, sl, tp1, tp2, tp3,
       score, nivel, patron_velas, estado, ciclo_vida,
       strftime('%Y-%m-%d %H:%M', fecha_senal) as fecha,
       version_detector, beneficio_final_pct
FROM senales
WHERE simbolo LIKE 'XAUUSD%'
  AND (
    (precio_entrada BETWEEN 4780 AND 4790 AND direccion='VENTA')
    OR
    (precio_entrada BETWEEN 4700 AND 4712 AND direccion='COMPRA')
  )
ORDER BY fecha_senal DESC LIMIT 10
""")
print(f"[{len(r2.rows)} coincidencias por precio]")
for row in r2.rows:
    cols = dict(zip(r2.columns, row))
    print("  " + str(cols))
