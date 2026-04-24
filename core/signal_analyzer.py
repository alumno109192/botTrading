"""
signal_analyzer.py — Analizador de señales XAUUSD basado en ratios Riesgo/Beneficio

Calcula los niveles TP a partir de Entry, SL y los RR de cada TP.
Determina el resultado de la operación comparando el precio de cierre con dichos niveles.
Opcional: recibe medias móviles para detectar obstáculos entre entry y cada TP.

Uso:
    from core.signal_analyzer import analizar_senal

    resultado = analizar_senal(
        direccion="LONG",
        entry=3310.0,
        sl=3295.0,
        tp1_rr=1.5,
        tp2_rr=2.5,
        tp3_rr=4.0,
        cierre=3335.0,
        medias={"EMA20": 3318.0, "EMA50": 3325.5, "EMA200": 3290.0},
    )
"""

from __future__ import annotations
import json


# Tolerancia (% del precio) para considerar que el cierre ocurrió "sobre" una media
_TOLERANCIA_MEDIA_PCT = 0.0015   # 0.15% → ~5 USD en XAUUSD a 3300


def _medias_entre(nivel_a: float, nivel_b: float,
                  medias: dict[str, float]) -> list[dict]:
    """Devuelve las MAs cuyos niveles están entre nivel_a y nivel_b (exclusivo),
    ordenadas en la dirección de movimiento (de más cercana a más lejana)."""
    lo, hi = (nivel_a, nivel_b) if nivel_a < nivel_b else (nivel_b, nivel_a)
    obstaculos = [
        {"nombre": nombre, "nivel": round(nivel, 5)}
        for nombre, nivel in medias.items()
        if lo < nivel < hi
    ]
    # Ordenar en dirección de viaje: ascendente si nivel_b > nivel_a, desc si no
    obstaculos.sort(key=lambda x: x["nivel"],
                    reverse=(nivel_b < nivel_a))
    return obstaculos


def analizar_senal(
    direccion: str,         # "LONG" o "SHORT"
    entry: float,
    sl: float,
    tp1_rr: float,
    tp2_rr: float,
    tp3_rr: float,
    cierre: float | None = None,  # None → "EN_CURSO"
    medias: dict[str, float] | None = None,  # ej. {"EMA20": 3318.0, "EMA50": 3325.5}
) -> dict:
    """
    Calcula los niveles TP y determina el resultado de la operación.

    Args:
        direccion : "LONG" o "SHORT" (insensible a mayúsculas)
        entry     : Precio de entrada
        sl        : Stop Loss
        tp1_rr    : Ratio R:R para TP1 (ej. 1.5)
        tp2_rr    : Ratio R:R para TP2 (ej. 2.5)
        tp3_rr    : Ratio R:R para TP3 (ej. 4.0)
        cierre    : Precio al que se cerró / precio actual. None si sigue en curso.
        medias    : Dict nombre→nivel de medias móviles activas (EMA20, EMA50, etc.).
                    El analizador detecta cuáles actúan como obstáculo entre entry
                    y cada TP, y si el cierre ocurrió sobre alguna de ellas.

    Returns:
        dict con entry, sl, riesgo_pts, tp1, tp2, tp3, cierre, resultado, pnl_pts,
        segunda_entrada, medias_obstaculos, cierre_en_media
    """
    direccion = direccion.upper()
    if direccion not in ('LONG', 'SHORT'):
        raise ValueError(f"direccion debe ser 'LONG' o 'SHORT', recibido: '{direccion}'")

    riesgo_pts = abs(entry - sl)
    if riesgo_pts == 0:
        raise ValueError("Entry y SL no pueden ser iguales (riesgo = 0)")

    signo = 1 if direccion == 'LONG' else -1

    tp1 = round(entry + signo * riesgo_pts * tp1_rr, 5)
    tp2 = round(entry + signo * riesgo_pts * tp2_rr, 5)
    tp3 = round(entry + signo * riesgo_pts * tp3_rr, 5)

    resultado = "EN_CURSO"
    pnl_pts   = 0.0

    if cierre is not None:
        if direccion == 'LONG':
            if cierre <= sl:
                resultado = "SL"
                pnl_pts   = round(cierre - entry, 5)
            elif cierre >= tp3:
                resultado = "TP3"
                pnl_pts   = round(cierre - entry, 5)
            elif cierre >= tp2:
                resultado = "TP2"
                pnl_pts   = round(cierre - entry, 5)
            elif cierre >= tp1:
                resultado = "TP1"
                pnl_pts   = round(cierre - entry, 5)
            else:
                resultado = "EN_CURSO"
                pnl_pts   = round(cierre - entry, 5)
        else:  # SHORT
            if cierre >= sl:
                resultado = "SL"
                pnl_pts   = round(entry - cierre, 5)
            elif cierre <= tp3:
                resultado = "TP3"
                pnl_pts   = round(entry - cierre, 5)
            elif cierre <= tp2:
                resultado = "TP2"
                pnl_pts   = round(entry - cierre, 5)
            elif cierre <= tp1:
                resultado = "TP1"
                pnl_pts   = round(entry - cierre, 5)
            else:
                resultado = "EN_CURSO"
                pnl_pts   = round(entry - cierre, 5)

    # ── Detección de segunda entrada ─────────────────────────────────────────
    segunda_entrada = "NO"
    if resultado == "EN_CURSO" and cierre is not None:
        if direccion == 'LONG' and sl < cierre < entry:
            pct_margen = (cierre - sl) / riesgo_pts
            segunda_entrada = "POSIBLE" if pct_margen > 0.3 else "ARRIESGADA"
        elif direccion == 'SHORT' and entry < cierre < sl:
            pct_margen = (sl - cierre) / riesgo_pts
            segunda_entrada = "POSIBLE" if pct_margen > 0.3 else "ARRIESGADA"

    # ── Medias móviles como obstáculos ───────────────────────────────────────
    medias_obstaculos: dict = {
        "antes_tp1":      [],
        "entre_tp1_tp2":  [],
        "entre_tp2_tp3":  [],
    }
    cierre_en_media: str | None = None   # nombre de la MA si el cierre está sobre ella

    if medias:
        medias_obstaculos["antes_tp1"]     = _medias_entre(entry, tp1, medias)
        medias_obstaculos["entre_tp1_tp2"] = _medias_entre(tp1,   tp2, medias)
        medias_obstaculos["entre_tp2_tp3"] = _medias_entre(tp2,   tp3, medias)

        # ¿El cierre ocurrió sobre alguna media? (dentro de tolerancia)
        if cierre is not None:
            tol = cierre * _TOLERANCIA_MEDIA_PCT
            for nombre, nivel in medias.items():
                if abs(cierre - nivel) <= tol:
                    cierre_en_media = nombre
                    break   # primera coincidencia es suficiente

    return {
        "entry":              round(entry, 5),
        "sl":                 round(sl, 5),
        "riesgo_pts":         round(riesgo_pts, 5),
        "tp1":                tp1,
        "tp2":                tp2,
        "tp3":                tp3,
        "cierre":             round(cierre, 5) if cierre is not None else None,
        "resultado":          resultado,
        "pnl_pts":            pnl_pts,
        "segunda_entrada":    segunda_entrada,
        "medias_obstaculos":  medias_obstaculos,
        "cierre_en_media":    cierre_en_media,
    }


def analizar_senal_json(
    direccion: str,
    entry: float,
    sl: float,
    tp1_rr: float,
    tp2_rr: float,
    tp3_rr: float,
    cierre: float | None = None,
    medias: dict[str, float] | None = None,
) -> str:
    """Igual que analizar_senal pero devuelve JSON serializado."""
    return json.dumps(
        analizar_senal(direccion, entry, sl, tp1_rr, tp2_rr, tp3_rr, cierre, medias),
        indent=2
    )


# ─── CLI rápido ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys

    ejemplos = [
        # LONG con EMA20 bloqueando antes de TP1, EMA50 entre TP1 y TP2
        dict(direccion="LONG",  entry=3310.0, sl=3295.0, tp1_rr=1.5, tp2_rr=2.5, tp3_rr=4.0,
             cierre=3320.0,
             medias={"EMA20": 3318.5, "EMA50": 3338.0, "EMA200": 3280.0}),
        # SHORT que hace SL (sin medias)
        dict(direccion="SHORT", entry=3310.0, sl=3325.0, tp1_rr=1.5, tp2_rr=2.5, tp3_rr=4.0,
             cierre=3328.0),
        # LONG: cierre exactamente sobre EMA50
        dict(direccion="LONG",  entry=3310.0, sl=3295.0, tp1_rr=1.5, tp2_rr=2.5, tp3_rr=4.0,
             cierre=3338.1,
             medias={"EMA20": 3318.5, "EMA50": 3338.0}),
    ]

    for i, ej in enumerate(ejemplos, 1):
        print(f"\n── Ejemplo {i} ──")
        print(analizar_senal_json(**ej))
