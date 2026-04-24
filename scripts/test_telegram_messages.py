"""
test_telegram_messages.py — Batería de pruebas con mensajes Telegram simulados

Para cada escenario usa signal_analyzer para calcular el resultado y luego genera
el mensaje exacto que se enviaría a Telegram, volcándolo al log.

Sin acceso a BD ni API real de Telegram.

Escenarios cubiertos (34 casos):
  L-01..L-10  — LONG sin medias
  S-01..S-10  — SHORT sin medias
  LM-01..LM-05 — LONG con medias móviles
  SM-01..SM-05 — SHORT con medias móviles
  B-01..B-04  — BREAKEVEN (TP1 previamente alcanzado → SL convertido en entrada)

Uso:
    python scripts/test_telegram_messages.py
"""

import sys
import os
import logging
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.signal_analyzer import analizar_senal

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_FILE = os.path.join(ROOT, 'logfile.txt')

logger = logging.getLogger('test_tg_messages')
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter('%(asctime)s [TG-SIM] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
fh.setFormatter(fmt)
logger.addHandler(fh)

ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(fmt)
logger.addHandler(ch)

# ─────────────────────────────────────────────────────────────────────────────
# Casos de prueba
# ─────────────────────────────────────────────────────────────────────────────
SIMBOLO = "XAUUSD"

# Base LONG:  entry=3310, SL=3295 (riesgo=15)
#   TP1=3332.5 (RR1.5) | TP2=3347.5 (RR2.5) | TP3=3370.0 (RR4.0)
# Base SHORT: entry=3310, SL=3325 (riesgo=15)
#   TP1=3287.5 (RR1.5) | TP2=3272.5 (RR2.5) | TP3=3250.0 (RR4.0)
LONG_BASE  = dict(direccion="LONG",  entry=3310.0, sl=3295.0, tp1_rr=1.5, tp2_rr=2.5, tp3_rr=4.0)
SHORT_BASE = dict(direccion="SHORT", entry=3310.0, sl=3325.0, tp1_rr=1.5, tp2_rr=2.5, tp3_rr=4.0)

LONG_MAS  = {"EMA20": 3318.5, "EMA50": 3338.0, "EMA200": 3280.0}
SHORT_MAS = {"EMA20": 3301.5, "EMA50": 3277.0, "EMA200": 3340.0}

CASOS = [
    # ── LONG ─────────────────────────────────────────────────────────────────
    {
        "id": "L-01",
        "desc": "LONG | Precio supera TP1 → cerrar 1/3 + mover SL a BE",
        "params": {**LONG_BASE, "cierre": 3335.0},
    },
    {
        "id": "L-02",
        "desc": "LONG | Precio supera TP2 → cerrar 2/3 + trailing SL",
        "params": {**LONG_BASE, "cierre": 3350.0},
    },
    {
        "id": "L-03",
        "desc": "LONG | Precio supera TP3 → cerrar todo, operación completa",
        "params": {**LONG_BASE, "cierre": 3375.0},
    },
    {
        "id": "L-04",
        "desc": "LONG | SL activado → cerrar posición con pérdida",
        "params": {**LONG_BASE, "cierre": 3290.0},
    },
    {
        "id": "L-05",
        "desc": "LONG | Precio exactamente en TP1 (borde exacto)",
        "params": {**LONG_BASE, "cierre": 3332.5},
    },
    {
        "id": "L-06",
        "desc": "LONG | Precio exactamente en SL (borde exacto)",
        "params": {**LONG_BASE, "cierre": 3295.0},
    },
    {
        "id": "L-07",
        "desc": "LONG | Precio avanza en dirección correcta >50% hacia TP1 → aviso de progreso",
        "params": {**LONG_BASE, "cierre": 3322.0},
    },
    {
        "id": "L-08",
        "desc": "LONG | Precio retrocedió lejos del SL → segunda entrada POSIBLE",
        "params": {**LONG_BASE, "cierre": 3304.0},
    },
    {
        "id": "L-09",
        "desc": "LONG | Precio muy cerca del SL (retroceso peligroso) → segunda entrada ARRIESGADA",
        "params": {**LONG_BASE, "cierre": 3296.5},
    },
    {
        "id": "L-10",
        "desc": "LONG | Señal recién emitida, sin precio actual → mensaje de nueva señal",
        "params": {**LONG_BASE, "cierre": None},
    },
    # ── SHORT ─────────────────────────────────────────────────────────────────
    {
        "id": "S-01",
        "desc": "SHORT | Precio cae hasta TP1 → cerrar 1/3 + mover SL a BE",
        "params": {**SHORT_BASE, "cierre": 3285.0},
    },
    {
        "id": "S-02",
        "desc": "SHORT | Precio cae hasta TP2 → cerrar 2/3 + trailing SL",
        "params": {**SHORT_BASE, "cierre": 3268.0},
    },
    {
        "id": "S-03",
        "desc": "SHORT | Precio cae hasta TP3 → cerrar todo, operación completa",
        "params": {**SHORT_BASE, "cierre": 3248.0},
    },
    {
        "id": "S-04",
        "desc": "SHORT | SL activado → cerrar posición con pérdida",
        "params": {**SHORT_BASE, "cierre": 3328.0},
    },
    {
        "id": "S-05",
        "desc": "SHORT | Precio exactamente en TP1 (borde exacto)",
        "params": {**SHORT_BASE, "cierre": 3287.5},
    },
    {
        "id": "S-06",
        "desc": "SHORT | Precio exactamente en SL (borde exacto)",
        "params": {**SHORT_BASE, "cierre": 3325.0},
    },
    {
        "id": "S-07",
        "desc": "SHORT | Precio avanza en dirección correcta >50% hacia TP1 → aviso de progreso",
        "params": {**SHORT_BASE, "cierre": 3298.0},
    },
    {
        "id": "S-08",
        "desc": "SHORT | Precio retrocedió lejos del SL → segunda entrada POSIBLE",
        "params": {**SHORT_BASE, "cierre": 3316.0},
    },
    {
        "id": "S-09",
        "desc": "SHORT | Precio muy cerca del SL (retroceso peligroso) → segunda entrada ARRIESGADA",
        "params": {**SHORT_BASE, "cierre": 3323.5},
    },
    {
        "id": "S-10",
        "desc": "SHORT | Señal recién emitida, sin precio actual → mensaje de nueva señal",
        "params": {**SHORT_BASE, "cierre": None},
    },
    # ── LONG con medias móviles ───────────────────────────────────────────────
    {
        "id": "LM-01",
        "desc": "LONG+MA | EMA20(3318.5) bloquea antes de TP1 — precio frena justo encima",
        "params": {**LONG_BASE, "cierre": 3319.0, "medias": LONG_MAS},
    },
    {
        "id": "LM-02",
        "desc": "LONG+MA | Cierre sobre EMA20 → cierre_en_media detectado",
        "params": {**LONG_BASE, "cierre": 3318.5, "medias": LONG_MAS},
    },
    {
        "id": "LM-03",
        "desc": "LONG+MA | Precio rompe EMA20 y EMA50, llega a TP2",
        "params": {**LONG_BASE, "cierre": 3350.0, "medias": LONG_MAS},
    },
    {
        "id": "LM-04",
        "desc": "LONG+MA | EMA50(3338.0) entre TP1 y TP2 — precio alcanza TP1 pero EMA50 avisa",
        "params": {**LONG_BASE, "cierre": 3339.0, "medias": LONG_MAS},
    },
    {
        "id": "LM-05",
        "desc": "LONG+MA | Solo EMA200 bajo el SL — camino libre hasta TP1",
        "params": {**LONG_BASE, "cierre": 3335.0, "medias": {"EMA200": 3280.0}},
    },
    # ── SHORT con medias móviles ──────────────────────────────────────────────
    {
        "id": "SM-01",
        "desc": "SHORT+MA | EMA20(3301.5) bloquea antes de TP1 — precio frena justo debajo",
        "params": {**SHORT_BASE, "cierre": 3302.0, "medias": SHORT_MAS},
    },
    {
        "id": "SM-02",
        "desc": "SHORT+MA | Cierre sobre EMA20 → cierre_en_media detectado",
        "params": {**SHORT_BASE, "cierre": 3301.5, "medias": SHORT_MAS},
    },
    {
        "id": "SM-03",
        "desc": "SHORT+MA | Precio rompe EMA20 y EMA50, llega a TP2",
        "params": {**SHORT_BASE, "cierre": 3268.0, "medias": SHORT_MAS},
    },
    {
        "id": "SM-04",
        "desc": "SHORT+MA | EMA50(3277.0) entre TP1 y TP2 — precio alcanza TP1 pero EMA50 avisa",
        "params": {**SHORT_BASE, "cierre": 3276.0, "medias": SHORT_MAS},
    },
    {
        "id": "SM-05",
        "desc": "SHORT+MA | Múltiples MAs en camino, precio las supera todas hasta TP3",
        "params": {**SHORT_BASE, "cierre": 3248.0, "medias": SHORT_MAS},
    },
    # ── BREAKEVEN (TP1 previamente alcanzado → SL = entrada) ─────────────────
    {
        "id": "B-01",
        "desc": "BREAKEVEN LONG | TP1 previo alcanzado, precio vuelve a entrada → sin pérdida",
        "params": {**LONG_BASE, "cierre": 3295.0},
        "tp1_previo": True,
    },
    {
        "id": "B-02",
        "desc": "BREAKEVEN SHORT | TP1 previo alcanzado, precio vuelve a entrada → sin pérdida",
        "params": {**SHORT_BASE, "cierre": 3325.0},
        "tp1_previo": True,
    },
    {
        "id": "B-03",
        "desc": "BREAKEVEN LONG | TP1 previo + MAs en camino → breakeven con aviso MA",
        "params": {**LONG_BASE, "cierre": 3295.0, "medias": LONG_MAS},
        "tp1_previo": True,
    },
    {
        "id": "B-04",
        "desc": "BREAKEVEN SHORT | TP1 previo + MAs en camino → breakeven con aviso MA",
        "params": {**SHORT_BASE, "cierre": 3325.0, "medias": SHORT_MAS},
        "tp1_previo": True,
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Generación de mensajes Telegram
# ─────────────────────────────────────────────────────────────────────────────

def _dir_str(d: str) -> str:
    return "COMPRA" if d == "LONG" else "VENTA"


def _flecha(d: str) -> str:
    return "📈" if d == "LONG" else "📉"


def _bloque_mas(r: dict) -> str:
    """Genera bloque de advertencia de medias móviles si hay obstáculos o rechazo."""
    obs       = r.get("medias_obstaculos", {})
    antes     = obs.get("antes_tp1", [])
    tp1_tp2   = obs.get("entre_tp1_tp2", [])
    tp2_tp3   = obs.get("entre_tp2_tp3", [])
    cen       = r.get("cierre_en_media")

    lines = []
    if antes:
        nombres = ", ".join(f"{x['nombre']} ${x['nivel']:.2f}" for x in antes)
        lines.append(f"⚠️ Antes de TP1:     {nombres}  ← puede frenar aquí")
    if tp1_tp2:
        nombres = ", ".join(f"{x['nombre']} ${x['nivel']:.2f}" for x in tp1_tp2)
        lines.append(f"⚠️ Entre TP1→TP2:    {nombres}  ← resistencia/soporte dinámico")
    if tp2_tp3:
        nombres = ", ".join(f"{x['nombre']} ${x['nivel']:.2f}" for x in tp2_tp3)
        lines.append(f"⚠️ Entre TP2→TP3:    {nombres}  ← vigilar")
    if cen:
        lines.append(f"🔄 Cierre sobre {cen} — posible rechazo/rebote dinámico en media")

    if lines:
        return "\n📊 <b>Medias Móviles:</b>\n" + "\n".join(lines)
    return ""


def generar_mensajes(r: dict, params: dict, tp1_previo: bool = False) -> list[str]:
    """
    Genera los mensajes Telegram para el resultado del análisis.
    Devuelve lista de mensajes (normalmente uno, a veces dos si hay aviso MA adicional).
    """
    d       = params["direccion"]
    dir_s   = _dir_str(d)
    flecha  = _flecha(d)
    precio  = f"${r['cierre']:.2f}" if r["cierre"] is not None else "—"
    bene    = r["pnl_pts"]
    bene_pct = bene / r["entry"] * 100

    resultado = r["resultado"]
    msgs = []

    if resultado == "TP3":
        msg = (
            f"🏆🏆🏆 <b>TP3 ALCANZADO!</b>\n\n"
            f"📊 {SIMBOLO} | {dir_s}\n"
            f"💰 Entrada: ${r['entry']:.2f}\n"
            f"✅ TP3: ${r['tp3']:.2f}\n"
            f"{flecha} Actual: {precio}\n"
            f"💵 Beneficio: +{bene:.2f} pts  ({bene_pct:.3f}%)\n\n"
            f"📋 <b>ACCIÓN RECOMENDADA:</b>\n"
            f"🔴 Cerrar el 100% restante de la posición\n"
            f"🏆 ¡Operación completada con éxito!"
        ) + _bloque_mas(r)
        msgs.append(msg)

    elif resultado == "TP2":
        msg = (
            f"🎯🎯 <b>TP2 ALCANZADO</b>\n\n"
            f"📊 {SIMBOLO} | {dir_s}\n"
            f"💰 Entrada: ${r['entry']:.2f}\n"
            f"✅ TP2: ${r['tp2']:.2f}\n"
            f"{flecha} Actual: {precio}\n"
            f"💵 Beneficio: +{bene:.2f} pts  ({bene_pct:.3f}%)\n\n"
            f"📋 <b>ACCIÓN RECOMENDADA:</b>\n"
            f"🔴 Cerrar 33% de la posición\n"
            f"🔒 Mover SL a TP1 (${r['tp1']:.2f})\n"
            f"⏳ Dejar correr hacia TP3 (${r['tp3']:.2f})"
        ) + _bloque_mas(r)
        msgs.append(msg)

    elif resultado == "TP1":
        msg = (
            f"🎯 <b>TP1 ALCANZADO</b>\n\n"
            f"📊 {SIMBOLO} | {dir_s}\n"
            f"💰 Entrada: ${r['entry']:.2f}\n"
            f"✅ TP1: ${r['tp1']:.2f}\n"
            f"{flecha} Actual: {precio}\n"
            f"💵 Beneficio: +{bene:.2f} pts  ({bene_pct:.3f}%)\n\n"
            f"📋 <b>ACCIÓN RECOMENDADA:</b>\n"
            f"🔴 Cerrar 33% de la posición\n"
            f"🔒 Mover SL a breakeven (${r['entry']:.2f})\n"
            f"⏳ Dejar correr hacia TP2 (${r['tp2']:.2f})"
        ) + _bloque_mas(r)
        msgs.append(msg)

    elif resultado == "SL":
        if tp1_previo:
            msg = (
                f"🔄 <b>BREAKEVEN — {SIMBOLO}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 {dir_s} | TP1 alcanzado previamente\n"
                f"💰 Entrada: ${r['entry']:.2f}\n"
                f"📍 Precio tocó breakeven: ${r['sl']:.2f}\n"
                f"{flecha} Actual: {precio}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Trade cerrado en <b>0% de pérdida</b>\n"
                f"🔍 El bot buscará nueva oportunidad de entrada"
            ) + _bloque_mas(r)
        else:
            msg = (
                f"❌ <b>STOP LOSS ACTIVADO</b>\n\n"
                f"📊 {SIMBOLO} | {dir_s}\n"
                f"💰 Entrada: ${r['entry']:.2f}\n"
                f"🛑 SL: ${r['sl']:.2f}\n"
                f"{flecha} Actual: {precio}\n"
                f"💸 Pérdida: {bene:.2f} pts  ({bene_pct:.3f}%)\n\n"
                f"📋 <b>ACCIÓN RECOMENDADA:</b>\n"
                f"🔴 Cerrar el 100% de la posición"
            )
        msgs.append(msg)

    elif resultado == "EN_CURSO":
        if r["cierre"] is None:
            # ── Nueva señal (recién emitida por el detector) ───────────────
            msg = (
                f"⚡ <b>SEÑAL {'BUY' if d == 'LONG' else 'SELL'} — ORO ({SIMBOLO})</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 {SIMBOLO} | {dir_s}\n"
                f"💰 Entrada: ${r['entry']:.2f}\n"
                f"🛑 Stop Loss: ${r['sl']:.2f}  (riesgo: ${r['riesgo_pts']:.2f} pts)\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 TP1: ${r['tp1']:.2f}  (+${abs(r['tp1'] - r['entry']):.2f} pts)\n"
                f"🎯 TP2: ${r['tp2']:.2f}  (+${abs(r['tp2'] - r['entry']):.2f} pts)\n"
                f"🎯 TP3: ${r['tp3']:.2f}  (+${abs(r['tp3'] - r['entry']):.2f} pts)"
            ) + _bloque_mas(r)
            msgs.append(msg)

        else:
            segunda = r["segunda_entrada"]
            dist_tp1   = abs(r["tp1"] - r["entry"])
            favorable  = (
                (d == "LONG"  and r["cierre"] > r["entry"]) or
                (d == "SHORT" and r["cierre"] < r["entry"])
            )
            dist_rec   = abs(r["cierre"] - r["entry"]) if favorable else 0
            pct_camino = dist_rec / dist_tp1 * 100 if dist_tp1 > 0 else 0

            if segunda in ("POSIBLE", "ARRIESGADA"):
                # ── Segunda entrada (retroceso) ────────────────────────────
                emoji = "🟡" if segunda == "POSIBLE" else "🟠"
                if segunda == "POSIBLE":
                    accion = (
                        "✅ Zona válida para re-entrada\n"
                        "🔍 Busca setup de continuación (pin bar, engulfing)\n"
                        f"🎯 Objetivo: TP1 ${r['tp1']:.2f}"
                    )
                else:
                    accion = (
                        "⚠️ Retroceso peligroso — muy cerca del SL\n"
                        "🔍 Re-entrada solo con confirmación muy fuerte\n"
                        "⬇️ Si entras, reduce el tamaño de posición"
                    )
                msg = (
                    f"{emoji} <b>SEGUNDA ENTRADA — {segunda}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 {SIMBOLO} | {dir_s}  (retroceso tras señal activa)\n"
                    f"💰 Entrada original: ${r['entry']:.2f}\n"
                    f"📍 Precio actual: {precio}  ({bene:+.2f} pts)\n"
                    f"🛑 SL intacto: ${r['sl']:.2f}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"{accion}"
                ) + _bloque_mas(r)
                msgs.append(msg)

            elif pct_camino >= 50:
                # ── 50% o más hacia TP1 → notificación de progreso ────────
                msg = (
                    f"⚡ <b>Trade avanzando — {pct_camino:.0f}% hacia TP1</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 {SIMBOLO} | {dir_s}\n"
                    f"💰 Entrada: ${r['entry']:.2f}\n"
                    f"📍 Actual: {precio}  ({pct_camino:.0f}% del camino)\n"
                    f"🎯 TP1: ${r['tp1']:.2f}  |  Faltan ${abs(r['tp1'] - r['cierre']):.2f} pts\n"
                    f"💵 P&L actual: {bene:+.2f} pts\n"
                    f"🔒 Considera mover SL a breakeven (${r['entry']:.2f})"
                ) + _bloque_mas(r)
                msgs.append(msg)

            else:
                # ── Trade en curso sin eventos notables ────────────────────
                msg = (
                    f"📊 <b>Trade en curso — {SIMBOLO} | {dir_s}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 Entrada: ${r['entry']:.2f}\n"
                    f"📍 Actual: {precio}  ({bene:+.2f} pts)\n"
                    f"🎯 Siguiente objetivo: TP1 ${r['tp1']:.2f}\n"
                    f"🛑 SL: ${r['sl']:.2f}"
                ) + _bloque_mas(r)
                msgs.append(msg)

    return msgs


# ─────────────────────────────────────────────────────────────────────────────
# Mock enviar_telegram: vuelca el mensaje al log
# ─────────────────────────────────────────────────────────────────────────────

def enviar_telegram_sim(msg: str, caso_id: str):
    logger.info(f"[{caso_id}] 📨 TELEGRAM MENSAJE →")
    for linea in msg.strip().splitlines():
        logger.info(f"   │ {linea}")
    logger.info(f"   └─ ✅ Enviado (simulado)")


# ─────────────────────────────────────────────────────────────────────────────
# Ejecución de la batería
# ─────────────────────────────────────────────────────────────────────────────

def ejecutar_bateria():
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    logger.info("=" * 72)
    logger.info(f"BATERÍA TG-SIM — Mensajes Telegram simulados  [{ts}]")
    logger.info(f"Total de casos: {len(CASOS)}")
    logger.info("=" * 72)

    resumen = []

    for caso in CASOS:
        cid      = caso["id"]
        desc     = caso["desc"]
        params   = caso["params"]
        tp1_prev = caso.get("tp1_previo", False)

        logger.info("-" * 72)
        logger.info(f"[{cid}] {desc}")

        try:
            r = analizar_senal(**params)
        except Exception as exc:
            logger.error(f"[{cid}] ERROR en analizar_senal: {exc}")
            resumen.append(f"  {cid}  ✘ ERROR  {exc}")
            continue

        # ── Resumen del análisis ─────────────────────────────────────────
        logger.info(
            f"       Señal  : {params['direccion']} "
            f"entry=${r['entry']:.2f}  SL=${r['sl']:.2f}  riesgo={r['riesgo_pts']:.2f}pts"
        )
        logger.info(
            f"       Niveles: TP1=${r['tp1']:.2f}  TP2=${r['tp2']:.2f}  TP3=${r['tp3']:.2f}"
        )
        cierre_s = f"${r['cierre']:.2f}" if r["cierre"] is not None else "—"
        logger.info(
            f"       Precio : {cierre_s}  →  resultado={r['resultado']}  "
            f"pnl={r['pnl_pts']:+.2f}pts  segunda={r['segunda_entrada']}"
        )
        if tp1_prev:
            logger.info("       ⚠️  tp1_previo=True → se emitirá BREAKEVEN en lugar de SL")

        # ── Generar y «enviar» mensajes ──────────────────────────────────
        msgs = generar_mensajes(r, params, tp1_previo=tp1_prev)
        for i, msg in enumerate(msgs, 1):
            sufijo = f" ({i}/{len(msgs)})" if len(msgs) > 1 else ""
            enviar_telegram_sim(msg, f"{cid}{sufijo}")

        tipo_msg = (
            "BREAKEVEN" if (tp1_prev and r["resultado"] == "SL") else r["resultado"]
        )
        resumen.append(f"  {cid}  resultado={tipo_msg}  segunda={r['segunda_entrada']}  pnl={r['pnl_pts']:+.2f}pts")

    # ── Resumen final ────────────────────────────────────────────────────────
    logger.info("=" * 72)
    logger.info(f"RESUMEN FINAL — {len(CASOS)} casos procesados")
    logger.info("=" * 72)
    for linea in resumen:
        logger.info(linea)
    logger.info("=" * 72)


if __name__ == '__main__':
    ejecutar_bateria()
