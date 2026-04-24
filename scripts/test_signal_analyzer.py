"""
test_signal_analyzer.py — Batería de pruebas del analizador de señales

Ejecuta ~30 escenarios representativos para XAUUSD (LONG y SHORT) y registra
los resultados exclusivamente en el log.  Sin acceso a BD ni Telegram.

Escenarios cubiertos:
  - TP1 / TP2 / TP3 alcanzados
  - SL activado
  - Precio en territorio favorable pero sin TP (EN_CURSO normal)
  - Precio retrocedido sin tocar SL → segunda entrada POSIBLE / ARRIESGADA
  - Precio exactamente en TP1 / SL (bordes)
  - Sin precio actual (señal recién emitida)
  - Medias móviles como obstáculos entre entry y TP
  - Cierre sobre una media móvil (rechazo en MA)
  - Múltiples MAs bloqueando distintos tramos

Uso:
    python scripts/test_signal_analyzer.py
"""

import sys
import os
import logging
from datetime import datetime, timezone

# ── path para importar módulos del proyecto ──────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.signal_analyzer import analizar_senal

# ── configurar logging solo a archivo y consola ──────────────────────────────
LOG_FILE = os.path.join(ROOT, 'logfile.txt')

logger = logging.getLogger('test_signal_analyzer')
logger.setLevel(logging.DEBUG)

fmt = logging.Formatter('%(asctime)s [TEST] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
fh.setFormatter(fmt)
logger.addHandler(fh)

ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(fmt)
logger.addHandler(ch)

# ─────────────────────────────────────────────────────────────────────────────
# Definición de casos de prueba
# Cada caso: descripción, parámetros de señal, precio actual/cierre
# ─────────────────────────────────────────────────────────────────────────────
# Base LONG:  entry=3310, SL=3295 (riesgo=15)
#   TP1=3332.5 (RR1.5) | TP2=3347.5 (RR2.5) | TP3=3370.0 (RR4.0)
# Base SHORT: entry=3310, SL=3325 (riesgo=15)
#   TP1=3287.5 (RR1.5) | TP2=3272.5 (RR2.5) | TP3=3250.0 (RR4.0)

LONG_BASE  = dict(direccion="LONG",  entry=3310.0, sl=3295.0, tp1_rr=1.5, tp2_rr=2.5, tp3_rr=4.0)
SHORT_BASE = dict(direccion="SHORT", entry=3310.0, sl=3325.0, tp1_rr=1.5, tp2_rr=2.5, tp3_rr=4.0)

CASOS = [
    # ── LONG ─────────────────────────────────────────────────────────────────
    {
        "id": "L-01",
        "desc": "LONG | Precio supera TP1 → cerrar 1/3 + mover SL a BE",
        "params": {**LONG_BASE, "cierre": 3335.0},
        "esperado": "TP1",
    },
    {
        "id": "L-02",
        "desc": "LONG | Precio supera TP2 → cerrar 2/3 + trailing SL",
        "params": {**LONG_BASE, "cierre": 3350.0},
        "esperado": "TP2",
    },
    {
        "id": "L-03",
        "desc": "LONG | Precio supera TP3 → cerrar todo, operación completa",
        "params": {**LONG_BASE, "cierre": 3375.0},
        "esperado": "TP3",
    },
    {
        "id": "L-04",
        "desc": "LONG | SL activado → cerrar posición con pérdida",
        "params": {**LONG_BASE, "cierre": 3290.0},
        "esperado": "SL",
    },
    {
        "id": "L-05",
        "desc": "LONG | Precio exactamente en TP1 (borde exacto)",
        "params": {**LONG_BASE, "cierre": 3332.5},
        "esperado": "TP1",
    },
    {
        "id": "L-06",
        "desc": "LONG | Precio exactamente en SL (borde exacto)",
        "params": {**LONG_BASE, "cierre": 3295.0},
        "esperado": "SL",
    },
    {
        "id": "L-07",
        "desc": "LONG | Precio avanza en dirección correcta, aún sin TP (EN_CURSO favorable)",
        "params": {**LONG_BASE, "cierre": 3322.0},
        "esperado": "EN_CURSO",
    },
    {
        "id": "L-08",
        "desc": "LONG | Precio retrocedió lejos del SL → segunda entrada POSIBLE",
        "params": {**LONG_BASE, "cierre": 3304.0},
        "esperado": "EN_CURSO",
        "segunda_esperada": "POSIBLE",
    },
    {
        "id": "L-09",
        "desc": "LONG | Precio muy cerca del SL (retroceso peligroso) → segunda entrada ARRIESGADA",
        "params": {**LONG_BASE, "cierre": 3296.5},
        "esperado": "EN_CURSO",
        "segunda_esperada": "ARRIESGADA",
    },
    {
        "id": "L-10",
        "desc": "LONG | Señal recién emitida, sin precio actual (EN_CURSO sin cierre)",
        "params": {**LONG_BASE, "cierre": None},
        "esperado": "EN_CURSO",
    },
    # ── SHORT ─────────────────────────────────────────────────────────────────
    {
        "id": "S-01",
        "desc": "SHORT | Precio cae hasta TP1 → cerrar 1/3 + mover SL a BE",
        "params": {**SHORT_BASE, "cierre": 3285.0},
        "esperado": "TP1",
    },
    {
        "id": "S-02",
        "desc": "SHORT | Precio cae hasta TP2 → cerrar 2/3 + trailing SL",
        "params": {**SHORT_BASE, "cierre": 3268.0},
        "esperado": "TP2",
    },
    {
        "id": "S-03",
        "desc": "SHORT | Precio cae hasta TP3 → cerrar todo, operación completa",
        "params": {**SHORT_BASE, "cierre": 3248.0},
        "esperado": "TP3",
    },
    {
        "id": "S-04",
        "desc": "SHORT | SL activado → cerrar posición con pérdida",
        "params": {**SHORT_BASE, "cierre": 3328.0},
        "esperado": "SL",
    },
    {
        "id": "S-05",
        "desc": "SHORT | Precio exactamente en TP1 (borde exacto)",
        "params": {**SHORT_BASE, "cierre": 3287.5},
        "esperado": "TP1",
    },
    {
        "id": "S-06",
        "desc": "SHORT | Precio exactamente en SL (borde exacto)",
        "params": {**SHORT_BASE, "cierre": 3325.0},
        "esperado": "SL",
    },
    {
        "id": "S-07",
        "desc": "SHORT | Precio avanza en dirección correcta, aún sin TP (EN_CURSO favorable)",
        "params": {**SHORT_BASE, "cierre": 3298.0},
        "esperado": "EN_CURSO",
    },
    {
        "id": "S-08",
        "desc": "SHORT | Precio retrocedió lejos del SL → segunda entrada POSIBLE",
        "params": {**SHORT_BASE, "cierre": 3316.0},
        "esperado": "EN_CURSO",
        "segunda_esperada": "POSIBLE",
    },
    {
        "id": "S-09",
        "desc": "SHORT | Precio muy cerca del SL (retroceso peligroso) → segunda entrada ARRIESGADA",
        "params": {**SHORT_BASE, "cierre": 3323.5},
        "esperado": "EN_CURSO",
        "segunda_esperada": "ARRIESGADA",
    },
    {
        "id": "S-10",
        "desc": "SHORT | Señal recién emitida, sin precio actual (EN_CURSO sin cierre)",
        "params": {**SHORT_BASE, "cierre": None},
        "esperado": "EN_CURSO",
    },
    # ── LONG con medias móviles ───────────────────────────────────────────────
    # MAs LONG:  EMA20=3318.5 (antes TP1) | EMA50=3338.0 (entre TP1 y TP2) | EMA200=3280.0 (bajo SL)
    {
        "id": "LM-01",
        "desc": "LONG+MA | EMA20(3318.5) bloquea antes de TP1 → precio frena en MA",
        "params": {**LONG_BASE, "cierre": 3319.0,
                   "medias": {"EMA20": 3318.5, "EMA50": 3338.0, "EMA200": 3280.0}},
        "esperado": "EN_CURSO",
    },
    {
        "id": "LM-02",
        "desc": "LONG+MA | Cierre sobre EMA20 (rechazo en media) → cierre_en_media='EMA20'",
        "params": {**LONG_BASE, "cierre": 3318.5,
                   "medias": {"EMA20": 3318.5, "EMA50": 3338.0, "EMA200": 3280.0}},
        "esperado": "EN_CURSO",
        "cierre_media_esperada": "EMA20",
    },
    {
        "id": "LM-03",
        "desc": "LONG+MA | Precio rompe EMA20 y EMA50, llega a TP2",
        "params": {**LONG_BASE, "cierre": 3350.0,
                   "medias": {"EMA20": 3318.5, "EMA50": 3338.0, "EMA200": 3280.0}},
        "esperado": "TP2",
    },
    {
        "id": "LM-04",
        "desc": "LONG+MA | EMA50(3338.0) entre TP1 y TP2 → precio supera TP1 pero frena en EMA50",
        "params": {**LONG_BASE, "cierre": 3339.0,
                   "medias": {"EMA20": 3318.5, "EMA50": 3338.0, "EMA200": 3280.0}},
        "esperado": "TP1",
    },
    {
        "id": "LM-05",
        "desc": "LONG+MA | Solo EMA200 por debajo de SL → ningún obstáculo en el camino",
        "params": {**LONG_BASE, "cierre": 3335.0,
                   "medias": {"EMA200": 3280.0}},
        "esperado": "TP1",
    },
    # ── SHORT con medias móviles ──────────────────────────────────────────────
    # MAs SHORT: EMA20=3301.5 (antes TP1) | EMA50=3277.0 (entre TP1 y TP2) | EMA200=3340.0 (sobre SL)
    {
        "id": "SM-01",
        "desc": "SHORT+MA | EMA20(3301.5) bloquea antes de TP1 → precio frena en MA",
        "params": {**SHORT_BASE, "cierre": 3302.0,
                   "medias": {"EMA20": 3301.5, "EMA50": 3277.0, "EMA200": 3340.0}},
        "esperado": "EN_CURSO",
    },
    {
        "id": "SM-02",
        "desc": "SHORT+MA | Cierre sobre EMA20 (rechazo en media) → cierre_en_media='EMA20'",
        "params": {**SHORT_BASE, "cierre": 3301.5,
                   "medias": {"EMA20": 3301.5, "EMA50": 3277.0, "EMA200": 3340.0}},
        "esperado": "EN_CURSO",
        "cierre_media_esperada": "EMA20",
    },
    {
        "id": "SM-03",
        "desc": "SHORT+MA | Precio rompe EMA20 y EMA50, llega a TP2",
        "params": {**SHORT_BASE, "cierre": 3268.0,
                   "medias": {"EMA20": 3301.5, "EMA50": 3277.0, "EMA200": 3340.0}},
        "esperado": "TP2",
    },
    {
        "id": "SM-04",
        "desc": "SHORT+MA | EMA50(3277.0) entre TP1 y TP2 → precio supera TP1 pero frena en EMA50",
        "params": {**SHORT_BASE, "cierre": 3276.0,
                   "medias": {"EMA20": 3301.5, "EMA50": 3277.0, "EMA200": 3340.0}},
        "esperado": "TP1",
    },
    {
        "id": "SM-05",
        "desc": "SHORT+MA | Múltiples MAs en camino, precio supera todas y llega a TP3",
        "params": {**SHORT_BASE, "cierre": 3248.0,
                   "medias": {"EMA20": 3301.5, "EMA50": 3277.0, "EMA200": 3340.0}},
        "esperado": "TP3",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Acciones recomendadas según resultado
# ─────────────────────────────────────────────────────────────────────────────
ACCIONES = {
    "TP1":      "✅ ACCIÓN: Cerrar 33% de la posición. Mover SL a breakeven ({entry:.2f}).",
    "TP2":      "✅ ACCIÓN: Cerrar 33% más (total 66%). Mover SL a TP1 ({tp1:.2f}).",
    "TP3":      "🏆 ACCIÓN: Cerrar el 100% restante. Operación completada.",
    "SL":       "❌ ACCIÓN: Cerrar el 100% de la posición. Pérdida asumida.",
    "EN_CURSO": "⏳ ACCIÓN: Mantener posición. Siguiente objetivo: TP1 ({tp1:.2f}).",
}

ACCIONES_SEGUNDA = {
    "POSIBLE":    "🔄 SEGUNDA ENTRADA: Zona válida para re-entrada. "
                  "Precio retrocedió pero SL intacto. Vigilar setup de continuación.",
    "ARRIESGADA": "⚠️  SEGUNDA ENTRADA: Retroceso peligroso (cerca de SL). "
                  "Re-entrada solo con confirmación fuerte. Reducir tamaño.",
    "NO":         "",
}


def _accion(resultado: str, r: dict) -> str:
    template = ACCIONES.get(resultado, "")
    return template.format(
        entry=r["entry"],
        tp1=r["tp1"],
        tp2=r["tp2"],
        tp3=r["tp3"],
        sl=r["sl"],
    )


def ejecutar_bateria():
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    logger.info("=" * 70)
    logger.info(f"BATERÍA DE PRUEBAS — signal_analyzer  [{ts}]")
    logger.info(f"Total de casos: {len(CASOS)}")
    logger.info("=" * 70)

    pasados = 0
    fallidos = 0
    resumen = []

    for caso in CASOS:
        cid    = caso["id"]
        desc   = caso["desc"]
        params = caso["params"]
        esperado        = caso.get("esperado")
        segunda_esperada = caso.get("segunda_esperada")  # puede ser None (no se comprueba)

        try:
            r = analizar_senal(**params)
        except Exception as exc:
            logger.error(f"[{cid}] ERROR al analizar: {exc}")
            fallidos += 1
            resumen.append(f"  {cid}  FALLO(excepción)  {desc[:55]}")
            continue

        ok_resultado = (r["resultado"] == esperado)
        ok_segunda   = (segunda_esperada is None) or (r["segunda_entrada"] == segunda_esperada)
        cierre_media_esperada = caso.get("cierre_media_esperada")  # solo informativo
        ok           = ok_resultado and ok_segunda

        estado_str = "✔ PASS" if ok else "✘ FAIL"
        if ok:
            pasados += 1
        else:
            fallidos += 1

        logger.info("-" * 70)
        logger.info(f"[{cid}] {estado_str}  |  {desc}")
        logger.info(
            f"       Señal  : {params['direccion']} "
            f"entry={r['entry']:.2f}  SL={r['sl']:.2f}  riesgo={r['riesgo_pts']:.2f}pts"
        )
        logger.info(
            f"       Niveles: TP1={r['tp1']:.2f}  TP2={r['tp2']:.2f}  TP3={r['tp3']:.2f}"
        )
        cierre_str = f"{r['cierre']:.2f}" if r['cierre'] is not None else "—"
        logger.info(
            f"       Precio : {cierre_str}  →  resultado={r['resultado']}  "
            f"pnl={r['pnl_pts']:+.2f}pts  segunda_entrada={r['segunda_entrada']}"
        )

        # ─ Medias móviles ───────────────────────────────────────────────
        obs = r.get("medias_obstaculos", {})
        antes_tp1     = obs.get("antes_tp1", [])
        entre_tp1_tp2 = obs.get("entre_tp1_tp2", [])
        entre_tp2_tp3 = obs.get("entre_tp2_tp3", [])
        tiene_mas = antes_tp1 or entre_tp1_tp2 or entre_tp2_tp3

        if tiene_mas:
            def _fmt(lista): return ", ".join(f"{x['nombre']}={x['nivel']:.2f}" for x in lista)
            logger.info(f"       📊 Medias obstáculo:")
            if antes_tp1:
                logger.info(f"          ⚠️  Antes TP1     : {_fmt(antes_tp1)}  ← puede frenar aquí")
            if entre_tp1_tp2:
                logger.info(f"          ⚠️  Entre TP1→TP2: {_fmt(entre_tp1_tp2)}  ← resistencia/soporte dinámico")
            if entre_tp2_tp3:
                logger.info(f"          ⚠️  Entre TP2→TP3: {_fmt(entre_tp2_tp3)}  ← vigilar")
        else:
            if params.get("medias"):
                logger.info("       📊 Medias: ninguna en el camino hacia los TPs")

        cen = r.get("cierre_en_media")
        if cen:
            logger.info(f"       🔄 Cierre sobre {cen} — posible rechazo/rebote dinámico en media")
        if cierre_media_esperada and cen != cierre_media_esperada:
            logger.warning(f"       ⚡ cierre_en_media esperada={cierre_media_esperada}, obtenida={cen}")

        # Resultado esperado vs obtenido
        if not ok_resultado:
            logger.warning(
                f"       ⚡ resultado esperado={esperado}, obtenido={r['resultado']}"
            )
        if not ok_segunda:
            logger.warning(
                f"       ⚡ segunda_entrada esperada={segunda_esperada}, "
                f"obtenida={r['segunda_entrada']}"
            )

        # Acción recomendada
        accion = _accion(r["resultado"], r)
        if accion:
            logger.info(f"       {accion}")

        # Aviso de segunda entrada
        aviso_segunda = ACCIONES_SEGUNDA.get(r["segunda_entrada"], "")
        if aviso_segunda:
            logger.info(f"       {aviso_segunda}")

        resumen.append(
            f"  {cid}  {estado_str}  resultado={r['resultado']}  "
            f"segunda={r['segunda_entrada']}  pnl={r['pnl_pts']:+.2f}pts"
        )

    # ── Resumen final ────────────────────────────────────────────────────────
    logger.info("=" * 70)
    logger.info(f"RESUMEN FINAL: {pasados}/{len(CASOS)} pasados  |  {fallidos} fallidos")
    logger.info("=" * 70)
    for linea in resumen:
        logger.info(linea)
    logger.info("=" * 70)

    return pasados, fallidos


if __name__ == '__main__':
    pasados, fallidos = ejecutar_bateria()
    sys.exit(0 if fallidos == 0 else 1)
