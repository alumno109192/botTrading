import sys; sys.path.insert(0, '.')
from adapters.database import DatabaseManager
db = DatabaseManager()
q = 'SELECT id, direccion, precio_entrada, tp1, tp2, sl, estado, tp1_alcanzado, timestamp FROM senales WHERE id IN (55,56,57,58)'
r = db.ejecutar_query(q)
for row in r.rows:
    d = dict(row)
    print("#%s %s entrada=%.2f  TP1=%.2f  SL=%.2f  tp1_hit=%s  estado=%s" % (
        d['id'], d['direccion'], float(d['precio_entrada']),
        float(d['tp1']), float(d['sl']), d['tp1_alcanzado'], d['estado']))

# El low de la vela 00:00 UTC fue 4672 — TP1 de señales VENTA era ~4685 y ~4694
# Verificar si el signal_monitor en Render detectó eso
print()
print("=== Minimo precio alcanzado hoy segun velas ===")
import yfinance as yf
df = yf.Ticker('GC=F').history(period='2d', interval='5m')
df_hoy = df[df.index.date >= df.index[-1].date()]
print("Min hoy: %.2f  Max hoy: %.2f  Velas: %d" % (df['Low'].min(), df['High'].max(), len(df)))
print("Min ultimas 24h: %.2f" % df['Low'].tail(288).min())
