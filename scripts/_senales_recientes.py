from adapters.database import DatabaseManager
db = DatabaseManager()
result = db.ejecutar_query(
    'SELECT timestamp, simbolo, direccion, precio_entrada, tp1, sl, score, estado FROM senales ORDER BY timestamp DESC LIMIT 10'
)
rows = result.rows if result.rows else []
print(f"{'Timestamp':<30} {'Simbolo':<12} {'Dir':<6} {'Entrada':>8} {'TP1':>8} {'SL':>8} {'Score':>5} {'Estado'}")
print("-" * 90)
for r in rows:
    d = dict(r)
    print(f"{str(d['timestamp']):<30} {str(d['simbolo']):<12} {str(d['direccion']):<6} {float(d['precio_entrada']):>8.2f} {float(d['tp1']):>8.2f} {float(d['sl']):>8.2f} {d['score']:>5}  {d['estado']}")
