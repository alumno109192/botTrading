"""Script puntual: actualizar estado señal #63 a TP2."""
from adapters.database import get_db

db = get_db()

db.ejecutar_query("UPDATE senales SET estado = 'TP2' WHERE id = 63")

r = db.ejecutar_query("SELECT id, simbolo, direccion, precio_entrada, tp1, tp2, sl, score, estado FROM senales WHERE id = 63")
print(r.rows[0])
