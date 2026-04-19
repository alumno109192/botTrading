"""
backtest_signals.py — Análisis de rendimiento sobre señales históricas

Lee todas las señales cerradas de la BD y genera métricas de rendimiento:
- Win rate por TF, dirección y score
- PnL estimado en ATR
- Qué score threshold maximiza la relación beneficio/señales

Uso:
    python backtest_signals.py
    python backtest_signals.py --simbolo XAUUSD_1H
    python backtest_signals.py --export resultados.csv
"""
import os
import sys
import argparse
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    from adapters.database import DatabaseManager
except ImportError as e:
    print(f"❌ No se pudo importar DatabaseManager: {e}")
    sys.exit(1)


# ══════════════════════════════════════
# EXTRACCIÓN DE DATOS
# ══════════════════════════════════════

def cargar_senales(db: DatabaseManager, simbolo_filter: str = None) -> list:
    """Carga todas las señales cerradas (no ACTIVA) de la BD."""
    if simbolo_filter:
        query = """
        SELECT id, timestamp, simbolo, direccion, precio_entrada,
               tp1, tp2, tp3, sl, score, estado, fecha_cierre,
               tp1_alcanzado, beneficio_final_pct
        FROM senales
        WHERE estado NOT IN ('ACTIVA')
          AND simbolo LIKE ?
        ORDER BY timestamp DESC
        """
        result = db.ejecutar_query(query, (f'%{simbolo_filter}%',))
    else:
        query = """
        SELECT id, timestamp, simbolo, direccion, precio_entrada,
               tp1, tp2, tp3, sl, score, estado, fecha_cierre,
               tp1_alcanzado, beneficio_final_pct
        FROM senales
        WHERE estado NOT IN ('ACTIVA')
        ORDER BY timestamp DESC
        """
        result = db.ejecutar_query(query)

    return result.rows if result and result.rows else []


def cargar_todas(db: DatabaseManager) -> list:
    """Carga TODAS las señales incluyendo activas (para resumen global)."""
    query = """
    SELECT id, timestamp, simbolo, direccion, precio_entrada,
           tp1, tp2, tp3, sl, score, estado, fecha_cierre,
           tp1_alcanzado, beneficio_final_pct
    FROM senales
    ORDER BY timestamp DESC
    """
    result = db.ejecutar_query(query)
    return result.rows if result and result.rows else []


# ══════════════════════════════════════
# CÁLCULO DE MÉTRICAS
# ══════════════════════════════════════

def calcular_metricas(senales: list) -> dict:
    """Calcula métricas de rendimiento sobre una lista de señales cerradas."""
    if not senales:
        return {}

    total         = len(senales)
    tp1_count     = sum(1 for s in senales if s.get('estado') in ('TP1', 'TP2', 'TP3'))
    tp2_count     = sum(1 for s in senales if s.get('estado') in ('TP2', 'TP3'))
    tp3_count     = sum(1 for s in senales if s.get('estado') == 'TP3')
    sl_count      = sum(1 for s in senales if s.get('estado') == 'SL')
    canceladas    = sum(1 for s in senales if s.get('estado') == 'CANCELADA')

    # PnL estimado (si disponible)
    pnl_values = []
    for s in senales:
        pnl = s.get('beneficio_final_pct')
        if pnl is not None:
            try:
                pnl_values.append(float(pnl))
            except (ValueError, TypeError):
                pass

    pnl_total   = sum(pnl_values) if pnl_values else None
    pnl_medio   = (pnl_total / len(pnl_values)) if pnl_values else None

    return {
        'total':          total,
        'tp1_count':      tp1_count,
        'tp2_count':      tp2_count,
        'tp3_count':      tp3_count,
        'sl_count':       sl_count,
        'canceladas':     canceladas,
        'win_rate_tp1':   round(tp1_count / total * 100, 1) if total else 0,
        'win_rate_tp2':   round(tp2_count / total * 100, 1) if total else 0,
        'win_rate_tp3':   round(tp3_count / total * 100, 1) if total else 0,
        'loss_rate':      round(sl_count / total * 100, 1) if total else 0,
        'pnl_total_pct':  round(pnl_total, 2) if pnl_total is not None else 'N/A',
        'pnl_medio_pct':  round(pnl_medio, 2) if pnl_medio is not None else 'N/A',
    }


def agrupar_por_campo(senales: list, campo: str) -> dict:
    """Agrupa señales por un campo (simbolo, direccion, score, etc.)."""
    grupos = {}
    for s in senales:
        key = s.get(campo, 'desconocido')
        # Convertir score a int si es posible
        if campo == 'score':
            try:
                key = int(float(key))
            except (ValueError, TypeError):
                key = 0
        if key not in grupos:
            grupos[key] = []
        grupos[key].append(s)
    return grupos


# ══════════════════════════════════════
# ANÁLISIS POR SCORE THRESHOLD
# ══════════════════════════════════════

def analizar_score_threshold(senales: list) -> list:
    """
    Para cada posible score mínimo (1–20), calcula cuántas señales
    quedarían y cuál sería el win rate.
    Útil para optimizar el umbral de señal.
    """
    resultados = []
    max_score = 20

    for umbral in range(1, max_score + 1):
        filtradas = [s for s in senales if _safe_int(s.get('score', 0)) >= umbral]
        if not filtradas:
            break
        metricas = calcular_metricas(filtradas)
        resultados.append({
            'score_min':    umbral,
            'n_senales':    metricas['total'],
            'win_tp1_pct':  metricas['win_rate_tp1'],
            'win_tp2_pct':  metricas['win_rate_tp2'],
            'loss_pct':     metricas['loss_rate'],
        })

    return resultados


def _safe_int(val) -> int:
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


# ══════════════════════════════════════
# FORMATEO Y SALIDA
# ══════════════════════════════════════

def imprimir_metricas(titulo: str, metricas: dict):
    print(f"\n{'═'*50}")
    print(f"  {titulo}")
    print(f"{'═'*50}")
    if not metricas:
        print("  Sin señales cerradas.")
        return
    total = metricas['total']
    print(f"  Señales cerradas:  {total}")
    print(f"  TP1+ alcanzado:    {metricas['tp1_count']} ({metricas['win_rate_tp1']}%)")
    print(f"  TP2+ alcanzado:    {metricas['tp2_count']} ({metricas['win_rate_tp2']}%)")
    print(f"  TP3  alcanzado:    {metricas['tp3_count']} ({metricas['win_rate_tp3']}%)")
    print(f"  SL tocado:         {metricas['sl_count']} ({metricas['loss_rate']}%)")
    print(f"  Canceladas:        {metricas['canceladas']}")
    if metricas['pnl_total_pct'] != 'N/A':
        print(f"  PnL total (%):     {metricas['pnl_total_pct']}%")
        print(f"  PnL medio/señal:   {metricas['pnl_medio_pct']}%")


def imprimir_tabla_score(tabla: list):
    if not tabla:
        return
    print(f"\n{'─'*60}")
    print(f"  {'Score≥':>6}  {'Señales':>7}  {'WinTP1%':>8}  {'WinTP2%':>8}  {'Loss%':>7}")
    print(f"{'─'*60}")
    for row in tabla:
        print(f"  {row['score_min']:>6}  {row['n_senales']:>7}  "
              f"{row['win_tp1_pct']:>8.1f}  {row['win_tp2_pct']:>8.1f}  "
              f"{row['loss_pct']:>7.1f}")
    print(f"{'─'*60}")


def exportar_csv(senales: list, filepath: str):
    """Exporta las señales a CSV para análisis externo."""
    try:
        import csv
        if not senales:
            print("⚠️ Sin señales para exportar.")
            return

        campos = ['id', 'timestamp', 'simbolo', 'direccion', 'precio_entrada',
                  'tp1', 'tp2', 'tp3', 'sl', 'score', 'estado',
                  'fecha_cierre', 'tp1_alcanzado', 'beneficio_final_pct']

        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=campos, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(senales)

        print(f"\n✅ Exportado a: {filepath} ({len(senales)} señales)")
    except Exception as e:
        print(f"❌ Error al exportar CSV: {e}")


# ══════════════════════════════════════
# MAIN
# ══════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='Análisis de rendimiento de señales históricas')
    parser.add_argument('--simbolo',  help='Filtrar por símbolo (ej: XAUUSD_1H)', default=None)
    parser.add_argument('--export',   help='Exportar a CSV (ej: resultados.csv)',  default=None)
    args = parser.parse_args()

    print("\n📊 BackTest Signals — Análisis de rendimiento XAUUSD")
    print(f"   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

    try:
        db = DatabaseManager()
    except Exception as e:
        print(f"❌ No se pudo conectar a la BD: {e}")
        sys.exit(1)

    # ── Cargar señales ──
    todas_las_senales = cargar_todas(db)
    senales_cerradas  = cargar_senales(db, simbolo_filter=args.simbolo)

    activas    = [s for s in todas_las_senales if s.get('estado') == 'ACTIVA']
    canceladas = [s for s in senales_cerradas  if s.get('estado') == 'CANCELADA']

    print(f"  Total en BD:       {len(todas_las_senales)}")
    print(f"  Activas:           {len(activas)}")
    print(f"  Cerradas:          {len(senales_cerradas)}")
    if args.simbolo:
        print(f"  Filtro aplicado:   {args.simbolo}")

    # ── Métricas globales ──
    metricas_global = calcular_metricas(senales_cerradas)
    imprimir_metricas("GLOBAL — Todas las señales cerradas", metricas_global)

    # ── Métricas por símbolo (TF) ──
    grupos_simbolo = agrupar_por_campo(senales_cerradas, 'simbolo')
    if len(grupos_simbolo) > 1:
        print(f"\n\n{'═'*50}")
        print("  POR SÍMBOLO / TIMEFRAME")
        print(f"{'═'*50}")
        for sym, sigs in sorted(grupos_simbolo.items()):
            m = calcular_metricas(sigs)
            print(f"\n  {sym}:")
            print(f"    Señales: {m['total']}  |  "
                  f"TP1: {m['win_rate_tp1']}%  |  "
                  f"TP2: {m['win_rate_tp2']}%  |  "
                  f"SL: {m['loss_rate']}%")

    # ── Métricas por dirección ──
    grupos_dir = agrupar_por_campo(senales_cerradas, 'direccion')
    print(f"\n\n{'═'*50}")
    print("  POR DIRECCIÓN")
    print(f"{'═'*50}")
    for direccion, sigs in sorted(grupos_dir.items()):
        m = calcular_metricas(sigs)
        print(f"\n  {direccion}:")
        print(f"    Señales: {m['total']}  |  "
              f"TP1: {m['win_rate_tp1']}%  |  "
              f"SL: {m['loss_rate']}%")

    # ── Análisis por score threshold ──
    print(f"\n\n{'═'*50}")
    print("  OPTIMIZACIÓN DE SCORE MÍNIMO")
    print(f"{'═'*50}")
    tabla_score = analizar_score_threshold(senales_cerradas)
    imprimir_tabla_score(tabla_score)
    if tabla_score:
        # Señalar el score con mejor win rate TP1
        mejor = max(tabla_score, key=lambda r: r['win_tp1_pct'])
        print(f"\n  ★ Score óptimo TP1: ≥{mejor['score_min']} "
              f"({mejor['win_tp1_pct']}% win rate, {mejor['n_senales']} señales)")

    # ── Señales activas en curso ──
    if activas:
        print(f"\n\n{'═'*50}")
        print(f"  SEÑALES ACTIVAS EN CURSO ({len(activas)})")
        print(f"{'═'*50}")
        for s in activas:
            print(f"  [{s.get('simbolo')}] {s.get('direccion')} @ {s.get('precio_entrada')}"
                  f"  score={s.get('score')}  |  {s.get('timestamp', '')[:16]}")

    # ── Exportar CSV ──
    if args.export:
        exportar_csv(todas_las_senales, args.export)

    print(f"\n✅ Análisis completado.\n")


if __name__ == "__main__":
    main()
