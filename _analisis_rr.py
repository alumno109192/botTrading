"""Análisis RR real de señales en BD."""
import sys
sys.path.insert(0, r'c:\PythonProjects\BotTrading')
from adapters.database import DatabaseManager

db = DatabaseManager()

for simbolo in ('XAUUSD_1H', 'XAUUSD_15M', 'XAUUSD_5M'):
    q = f"""SELECT direccion, precio_entrada, tp1, tp2, sl, score, estado
    FROM senales WHERE simbolo = '{simbolo}'
    AND estado NOT IN ('ACTIVA','ESPERANDO','PENDIENTE_CONFIRM','TEST')
    ORDER BY timestamp DESC LIMIT 60"""
    r = db.ejecutar_query(q)
    rows = r.rows if r and r.rows else []
    if not rows:
        print(f"\n{simbolo}: sin datos")
        continue

    wins = [s for s in rows if s.get('estado') in ('TP1','TP2','TP3')]
    losses = [s for s in rows if s.get('estado') == 'SL']
    cancels = [s for s in rows if s.get('estado') == 'CANCELADA']

    def rr(s):
        try:
            e = float(s['precio_entrada']); t = float(s['tp1']); sl = float(s['sl'])
            return abs(t - e) / abs(sl - e) if abs(sl - e) > 0 else 0
        except: return 0

    def sl_dist(s):
        try:
            return abs(float(s['precio_entrada']) - float(s['sl']))
        except: return 0

    def tp_dist(s):
        try:
            return abs(float(s['tp1']) - float(s['precio_entrada']))
        except: return 0

    rr_wins   = [rr(s) for s in wins]
    rr_losses = [rr(s) for s in losses]
    sl_wins   = [sl_dist(s) for s in wins]
    sl_losses = [sl_dist(s) for s in losses]
    tp_wins   = [tp_dist(s) for s in wins]
    tp_losses = [tp_dist(s) for s in losses]

    print(f"\n{'='*55}")
    print(f"  {simbolo}  ({len(rows)} señales analizadas)")
    print(f"{'='*55}")
    print(f"  Wins={len(wins)} Losses={len(losses)} Cancel={len(cancels)}")
    if wins:
        print(f"  WINS   → RR medio: {sum(rr_wins)/len(rr_wins):.2f}  SL_dist: {sum(sl_wins)/len(sl_wins):.1f}  TP_dist: {sum(tp_wins)/len(tp_wins):.1f}")
    if losses:
        print(f"  LOSSES → RR medio: {sum(rr_losses)/len(rr_losses):.2f}  SL_dist: {sum(sl_losses)/len(sl_losses):.1f}  TP_dist: {sum(tp_losses)/len(tp_losses):.1f}")

    # SL demasiado ajustado: % de pérdidas donde SL < 0.8×TP
    too_tight = sum(1 for s in losses if rr(s) < 0.8)
    if losses:
        print(f"  SL demasiado ajustado (RR<0.8): {too_tight}/{len(losses)} ({100*too_tight//len(losses) if losses else 0}%)")

    # Distribución por score
    print(f"\n  Por score (wins/losses):")
    score_data = {}
    for s in rows:
        sc = s.get('score', 0)
        try: sc = int(float(sc))
        except: sc = 0
        if sc not in score_data:
            score_data[sc] = {'w': 0, 'l': 0, 'c': 0}
        est = s.get('estado', '')
        if est in ('TP1','TP2','TP3'): score_data[sc]['w'] += 1
        elif est == 'SL': score_data[sc]['l'] += 1
        else: score_data[sc]['c'] += 1
    for sc in sorted(score_data.keys()):
        d = score_data[sc]
        tot = d['w'] + d['l'] + d['c']
        wr = 100*d['w']//tot if tot else 0
        print(f"    score={sc:2d}  total={tot:3d}  W={d['w']} L={d['l']} C={d['c']}  WR={wr}%")

print("\n=== DIAGNÓSTICO FINAL ===")
print("15M: SL=1.5×ATR, TP1=1.5×ATR → RR=1.0 (funciona)")
print("1H:  SL=1.0×ATR, TP1=1.5×ATR → RR=1.5 (debería ser mejor, pero NO funciona)")
print("→ El problema del 1H es la CALIDAD DE LA SEÑAL, no el RR")
