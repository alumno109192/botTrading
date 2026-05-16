"""
Winrate GOLD por modo activo — últimas 72 horas
  · GOLD 1H  (version_detector LIKE '%1H%')
  · GOLD 15M SCALP normal  (score < 12)
  · GOLD 15M MODO CAZA     (score >= 12, umbral del detector)
"""
import sys
sys.path.insert(0, 'c:\\PythonProjects\\BotTrading')

from adapters.database import DatabaseManager
from datetime import datetime, timedelta, timezone

UMBRAL_CAZA = 12   # igual que _UMBRAL_CAZA en detector_gold_15m.py

db = DatabaseManager()

cutoff = (datetime.now(timezone.utc) - timedelta(hours=72)).strftime('%Y-%m-%d %H:%M:%S')
print(f"Periodo analizado: {cutoff} UTC  →  ahora")
print(f"{'='*60}\n")

result = db.ejecutar_query(
    """SELECT id, simbolo, direccion, estado,
              tp1_alcanzado, tp2_alcanzado, tp3_alcanzado, sl_alcanzado,
              timestamp, precio_entrada, score, timeframe, version_detector
       FROM senales
       WHERE (UPPER(simbolo) LIKE '%XAU%' OR UPPER(simbolo) LIKE '%GOLD%'
              OR UPPER(COALESCE(asset,'')) LIKE '%GOLD%')
         AND timeframe IN ('1H', '15M')
         AND timestamp >= ?
       ORDER BY timeframe, timestamp DESC""",
    (cutoff,)
)

rows = result.rows if result else []

# ── Clasificar ────────────────────────────────────────────────────────────────
def resultado(row):
    estado = (row.get('estado') or '').upper()
    tp1 = row.get('tp1_alcanzado') in (1, True, '1', 'true', 'True')
    tp2 = row.get('tp2_alcanzado') in (1, True, '1', 'true', 'True')
    tp3 = row.get('tp3_alcanzado') in (1, True, '1', 'true', 'True')
    sl  = row.get('sl_alcanzado')  in (1, True, '1', 'true', 'True')
    if estado in ('TP1', 'TP2', 'TP3', 'TP', 'WIN', 'BREAKEVEN') or tp1 or tp2 or tp3:
        return 'WIN'
    if estado in ('SL', 'LOSS', 'STOPPED') or sl:
        return 'LOSS'
    return 'OPEN'   # ACTIVA, CANCELADA, etc.

def stats(lista):
    w = sum(1 for r in lista if resultado(r) == 'WIN')
    l = sum(1 for r in lista if resultado(r) == 'LOSS')
    o = sum(1 for r in lista if resultado(r) == 'OPEN')
    total = w + l
    wr = f"{w/total*100:.1f}%" if total else "—"
    return w, l, o, total, wr

# ── Grupos ────────────────────────────────────────────────────────────────────
g_1h    = [r for r in rows if r.get('timeframe') == '1H']
g_15m   = [r for r in rows if r.get('timeframe') == '15M']
g_caza  = [r for r in g_15m if (r.get('score') or 0) >= UMBRAL_CAZA]
g_scalp = [r for r in g_15m if (r.get('score') or 0) <  UMBRAL_CAZA]

# ── Imprimir detalle ──────────────────────────────────────────────────────────
def imprimir_detalle(titulo, lista):
    print(f"{'─'*60}")
    print(f"  {titulo}  ({len(lista)} señales)")
    print(f"{'─'*60}")
    for r in lista:
        res = resultado(r)
        icon = "✅" if res == 'WIN' else ("❌" if res == 'LOSS' else "⏳")
        score = r.get('score') or 0
        estado = (r.get('estado') or '').upper()
        direc = r.get('direccion', '')
        ts = str(r.get('timestamp', ''))[:16]
        print(f"  {icon} ID={str(r.get('id')):>4} | {direc:<6} | score={score:<3} | {estado:<12} | {ts}")
    w, l, o, total, wr = stats(lista)
    print(f"\n  Ganadas: {w}  |  Perdidas: {l}  |  Abiertas/Canceladas: {o}")
    if total:
        print(f"  >>> WINRATE: {wr}  ({w}/{total} cerradas) <<<")
    else:
        print("  >>> Sin operaciones cerradas <<<")
    print()

imprimir_detalle("GOLD 1H", g_1h)
imprimir_detalle(f"GOLD 15M SCALP NORMAL  (score < {UMBRAL_CAZA})", g_scalp)
imprimir_detalle(f"GOLD 15M MODO CAZA     (score >= {UMBRAL_CAZA})", g_caza)

# ── Resumen final ─────────────────────────────────────────────────────────────
print(f"{'='*60}")
print("  RESUMEN  (últimas 72h — operaciones cerradas)")
print(f"{'='*60}")
for titulo, lista in [("GOLD 1H      ", g_1h),
                      ("15M Scalp    ", g_scalp),
                      ("15M Caza     ", g_caza),
                      ("15M TOTAL    ", g_15m)]:
    w, l, o, total, wr = stats(lista)
    print(f"  {titulo} | WIN={w:>2}  LOSS={l:>2}  OPEN={o:>2}  → WINRATE: {wr:>6}  ({w}/{total})")
print(f"{'='*60}")
print(f"\nNota: MODO CAZA = señales 15M con score >= {UMBRAL_CAZA} (umbral del detector)")
print("      BREAKEVEN cuenta como WIN (TP1 tocado, SL movido a entry)")

