"""
Análisis de condiciones que generan SL vs TP3 en XAUUSD_5M.
Lee el campo indicadores (JSON) y calcula la frecuencia de cada condición
en señales SL vs TP3.
"""
import json
from collections import defaultdict
from adapters.database import get_db

db = get_db()

res = db.ejecutar_query("""
    SELECT estado, score, direccion, indicadores
    FROM senales
    WHERE simbolo = 'XAUUSD_5M'
    AND estado IN ('SL', 'TP3')
    AND indicadores IS NOT NULL
    ORDER BY id
""")

# Condiciones booleanas a analizar
BOOL_KEYS = [
    'en_zona_resist', 'en_zona_soporte',
    'aproximando_resist', 'aproximando_soporte',
    'envolvente_bajista', 'envolvente_alcista',
    'stop_hunt_bajista', 'stop_hunt_alcista',
    'v_rev_alcista_5m', 'v_rev_bajista_5m',
    'dt_5m', 'ds_5m',
    'canal_alc_roto', 'canal_baj_roto',
    'en_resist_canal_baj', 'en_sop_canal_alc',
    'rup_sop', 'rup_res',
    'retest_resist', 'retest_sop',
    'rec_dir_baj', 'rec_dir_alc',
]

# Contadores: {clave: {SL: n_true, TP3: n_true, SL_tot, TP3_tot}}
counts = defaultdict(lambda: {'SL': 0, 'TP3': 0, 'SL_tot': 0, 'TP3_tot': 0})
totals = {'SL': 0, 'TP3': 0}

for r in res.rows:
    estado = r['estado']
    totals[estado] += 1
    try:
        raw = r['indicadores']
        ind = json.loads(raw)
        if isinstance(ind, str):   # doble-serializado
            ind = json.loads(ind)
        if not isinstance(ind, dict):
            continue
    except Exception:
        continue
    for k in BOOL_KEYS:
        counts[k][f'{estado}_tot'] += 1
        if ind.get(k):
            counts[k][estado] += 1

print(f"Total señales con datos: SL={totals['SL']}  TP3={totals['TP3']}")
print()
print(f"{'Condición':<22}  {'SL%':>5}  {'TP%':>5}  {'Ratio SL/TP':>10}  (n_SL / n_TP3)")
print("-" * 65)

rows = []
for k in BOOL_KEYS:
    c = counts[k]
    sl_tot = c['SL_tot'] or 1
    tp_tot = c['TP3_tot'] or 1
    sl_pct = 100 * c['SL'] / sl_tot
    tp_pct = 100 * c['TP3'] / tp_tot
    ratio = sl_pct / (tp_pct + 0.1)
    rows.append((k, sl_pct, tp_pct, ratio, c['SL'], c['TP3']))

# Ordenar por ratio SL/TP desc (las más peligrosas arriba)
rows.sort(key=lambda x: -x[3])
for k, sl_pct, tp_pct, ratio, n_sl, n_tp in rows:
    marker = " ⚠️" if ratio > 2.0 else (" ✅" if ratio < 0.5 else "")
    print(f"  {k:<22}  {sl_pct:5.1f}%  {tp_pct:5.1f}%  {ratio:10.2f}x  ({n_sl}/{n_tp}){marker}")

# Análisis numérico: RSI, ADX, ATR ratio
print()
print("=== MEDIAS DE INDICADORES NUMÉRICOS ===")
num_keys = ['rsi', 'adx', 'score_sell', 'score_buy']
sums = defaultdict(lambda: {'SL': 0.0, 'TP3': 0.0, 'SL_n': 0, 'TP3_n': 0})

for r in res.rows:
    estado = r['estado']
    try:
        raw = r['indicadores']
        ind = json.loads(raw)
        if isinstance(ind, str):
            ind = json.loads(ind)
        if not isinstance(ind, dict):
            continue
    except Exception:
        continue
    for k in num_keys:
        v = ind.get(k)
        if v is not None:
            sums[k][estado] += float(v)
            sums[k][f'{estado}_n'] += 1

for k in num_keys:
    s = sums[k]
    media_sl = s['SL'] / (s['SL_n'] or 1)
    media_tp = s['TP3'] / (s['TP3_n'] or 1)
    print(f"  {k:<16}  SL_media={media_sl:6.1f}   TP3_media={media_tp:6.1f}")

# Scores altos (>=10) que terminan en SL: ¿qué condiciones los disparan?
print()
print("=== CONDICIONES MÁS FRECUENTES EN SCORES ALTOS (>=10) QUE TERMINAN EN SL ===")
high_sl = []
for r in res.rows:
    if r['estado'] == 'SL' and (r['score'] or 0) >= 10:
        try:
            raw = r['indicadores']
            ind = json.loads(raw)
            if isinstance(ind, str):
                ind = json.loads(ind)
            if isinstance(ind, dict):
                high_sl.append(ind)
        except Exception:
            pass

if high_sl:
    print(f"  (n={len(high_sl)} señales score>=10 con SL)")
    cond_freq = defaultdict(int)
    for ind in high_sl:
        for k in BOOL_KEYS:
            if ind.get(k):
                cond_freq[k] += 1
    for k, n in sorted(cond_freq.items(), key=lambda x: -x[1]):
        if n > 0:
            print(f"  {k:<30}  {n}/{len(high_sl)}  ({100*n/len(high_sl):.0f}%)")
