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


# ─────────────────────────────────────────────────────────────────────────────
# Sección 2 — Análisis de obstáculos por medias móviles
# analizar_obstaculos() — cálculo puro, sin acceso a BD ni Telegram
# ─────────────────────────────────────────────────────────────────────────────

# Clasificación de MAs por tipo (determina la severidad base)
_MA_TIPO: dict[str, str] = {
    # Tendencia → severidad base CRÍTICO
    'EMA200': 'tendencia', 'EMA400': 'tendencia', 'EMA55': 'tendencia',
    # Lenta → severidad base MODERADO
    'EMA21': 'lenta',  'EMA42': 'lenta',  'EMA13': 'lenta',
    # Rápida → severidad base LEVE
    'EMA9':  'rapida', 'EMA18': 'rapida', 'EMA5':  'rapida',
}

_SEV_BASE: dict[str, int] = {
    'tendencia': 2,   # índice → CRÍTICO
    'lenta':     1,   # índice → MODERADO
    'rapida':    0,   # índice → LEVE
}

_SEV_NOMBRES: list[str]  = ['LEVE', 'MODERADO', 'CRÍTICO']
_TF_SCALPING: frozenset  = frozenset({'5M', '15M'})

_IMPACTO_DE_SEV: dict = {
    None:       'BAJO',
    'LEVE':     'MEDIO',
    'MODERADO': 'ALTO',
    'CRÍTICO':  'BLOQUEADO',
}


def _sev_clip(idx: int) -> str:
    """Convierte un índice entero a nombre de severidad con clipping [0-2]."""
    return _SEV_NOMBRES[max(0, min(2, idx))]


def _zona_de_ma(
    ma_val: float,
    entry: float, tp1: float, tp2: float, tp3: float,
    direccion: str, atr: float,
) -> str | None:
    """
    Determina en qué zona del trade se encuentra la MA.

    Devuelve: 'ENTRY' (±0.5 ATR del entry), 'TP1', 'TP2', 'TP3', o None (fuera de rango).
    """
    if abs(ma_val - entry) <= 0.5 * atr:
        return 'ENTRY'
    if direccion == 'LONG':
        if entry < ma_val <= tp1:  return 'TP1'
        if tp1   < ma_val <= tp2:  return 'TP2'
        if tp2   < ma_val <= tp3:  return 'TP3'
    else:   # SHORT: tp1 < tp2 < tp3 en valor, todos < entry
        if tp1 <= ma_val < entry:  return 'TP1'
        if tp2 <= ma_val < tp1:    return 'TP2'
        if tp3 <= ma_val < tp2:    return 'TP3'
    return None


def _pendiente_str(pendiente: float, atr: float, umbral_frac: float) -> str:
    """Clasifica la pendiente como 'alcista', 'bajista' o 'plana'."""
    if atr > 0 and abs(pendiente) < umbral_frac * atr:
        return 'plana'
    return 'alcista' if pendiente >= 0 else 'bajista'


def analizar_obstaculos(
    senal: dict,
    medias: dict[str, float],
    pendientes: dict[str, float] | None = None,
    umbral_plana_atr: float = 0.15,
) -> dict:
    """
    Analiza qué medias móviles actúan como obstáculo entre el entry y cada TP.

    Args:
        senal    : Dict con id, simbolo, timeframe, direccion, precio_entrada,
                   sl, tp1, tp2, tp3, atr.
        medias   : {nombre_MA: valor_actual}. Ej. {"EMA18": 3310.5, "EMA42": 3295.0}.
        pendientes: {nombre_MA: (ema[-1]-ema[-5])/5}. None → todas planas.
        umbral_plana_atr: Fracción del ATR bajo la cual la pendiente es «plana».

    Returns:
        Dict con: senal_id, tiene_obstaculos, obstaculos, impacto_tp1/2/3,
                  recomendacion, todas_a_favor, referencias_lejanas, mensaje_telegram.
    """
    pendientes = pendientes or {}

    senal_id  = senal.get('id', 0)
    simbolo   = str(senal.get('simbolo', 'XAUUSD'))
    timeframe = str(senal.get('timeframe', '')).upper()
    direccion = str(senal.get('direccion', 'LONG')).upper()
    if direccion not in ('LONG', 'SHORT'):
        raise ValueError(f"direccion debe ser 'LONG' o 'SHORT', recibido: '{direccion}'")

    entry = float(senal['precio_entrada'])
    sl    = float(senal['sl'])
    tp1   = float(senal['tp1'])
    tp2   = float(senal['tp2'])
    tp3   = float(senal['tp3'])
    atr   = max(float(senal.get('atr', 1.0)), 0.001)

    es_scalping = timeframe in _TF_SCALPING
    obstaculos_raw: list[dict] = []     # incluye campo interno _zona
    referencias_lejanas: list[str] = []

    for nombre_ma, valor_ma in medias.items():
        tipo     = _MA_TIPO.get(nombre_ma, 'lenta')
        pend_val = float(pendientes.get(nombre_ma, 0.0))
        pend_str = _pendiente_str(pend_val, atr, umbral_plana_atr)

        # Caso especial 3: EMA tendencia muy lejos (>3 ATR del entry) → solo referencia
        if tipo == 'tendencia' and abs(valor_ma - entry) > 3.0 * atr:
            referencias_lejanas.append(f"{nombre_ma}@{round(valor_ma, 2)}")
            continue

        zona = _zona_de_ma(valor_ma, entry, tp1, tp2, tp3, direccion, atr)
        if zona is None:
            continue   # fuera del rango del trade → ignorar

        # ── Severidad ─────────────────────────────────────────────────────────
        if zona == 'ENTRY':
            # Caso especial 1: MA en entry ±0.5 ATR → CRÍTICO forzado
            sev_idx = 2
        else:
            sev_idx = _SEV_BASE.get(tipo, 1)

            # Ajuste por posición en el trade
            if   zona == 'TP1': sev_idx += 1
            elif zona == 'TP3': sev_idx -= 1
            # zona == 'TP2': sin cambio

            # Ajuste por pendiente (ENTRY ya es forzado CRÍTICO, no ajustar)
            contra  = (
                (direccion == 'LONG'  and pend_str == 'bajista') or
                (direccion == 'SHORT' and pend_str == 'alcista')
            )
            a_favor = (
                (direccion == 'LONG'  and pend_str == 'alcista') or
                (direccion == 'SHORT' and pend_str == 'bajista')
            )
            if contra:   sev_idx += 1
            elif a_favor: sev_idx -= 1

        # Caso especial 4: scalping → reducir severidad un nivel
        if es_scalping:
            sev_idx -= 1

        severidad  = _sev_clip(sev_idx)
        contra_dir = (
            (direccion == 'LONG'  and pend_str == 'bajista') or
            (direccion == 'SHORT' and pend_str == 'alcista')
        )

        if zona == 'ENTRY':
            zone_desc = 'en la zona de entry (obstáculo inmediato)'
        else:
            zone_desc = f'entre entry y {zona}'
        desc = f"{nombre_ma} en {round(valor_ma, 2)} {zone_desc} con pendiente {pend_str}"

        obstaculos_raw.append({
            'media':            nombre_ma,
            'valor':            round(valor_ma, 5),
            'entre_entry_y':    'TP1' if zona == 'ENTRY' else zona,
            '_zona':            zona,        # campo interno — se elimina del output público
            'severidad':        severidad,
            'pendiente':        pend_str,
            'contra_direccion': contra_dir,
            'descripcion':      desc,
        })

    # ── Impacto por TP ──────────────────────────────────────────────────────
    def _max_sev(*zonas: str) -> str | None:
        sevs = [o['severidad'] for o in obstaculos_raw if o['_zona'] in zonas]
        return max(sevs, key=lambda s: _SEV_NOMBRES.index(s)) if sevs else None

    impacto_tp1 = _IMPACTO_DE_SEV[_max_sev('ENTRY', 'TP1')]
    impacto_tp2 = _IMPACTO_DE_SEV[_max_sev('TP2')]
    impacto_tp3 = _IMPACTO_DE_SEV[_max_sev('TP3')]

    # ── Recomendación ───────────────────────────────────────────────────────
    criticos_tp1  = [o for o in obstaculos_raw
                     if o['_zona'] in ('ENTRY', 'TP1') and o['severidad'] == 'CRÍTICO']
    criticos_tp12 = [o for o in obstaculos_raw
                     if o['_zona'] in ('ENTRY', 'TP1', 'TP2') and o['severidad'] == 'CRÍTICO']
    mods_tp1      = [o for o in obstaculos_raw
                     if o['_zona'] in ('ENTRY', 'TP1') and o['severidad'] == 'MODERADO']

    razon: str | None = None
    if criticos_tp1:
        recomendacion = 'NO_OPERAR'
        razon = f"{criticos_tp1[0]['media']} bloquea el camino a TP1"
    elif criticos_tp12 or len(mods_tp1) >= 2:
        recomendacion = 'OPERAR_CON_CAUTELA'
        if criticos_tp12:
            razon = f"{criticos_tp12[0]['media']} es CRÍTICO entre TP1 y TP2"
        else:
            razon = f"{len(mods_tp1)} obstáculos MODERADO entre entry y TP1"
    else:
        recomendacion = 'OPERAR_NORMAL'

    # ── Todas las medias alineadas a favor ──────────────────────────────────
    tiene_obstaculos = bool(obstaculos_raw)
    todas_a_favor = (
        bool(medias) and not tiene_obstaculos and
        all(
            (direccion == 'LONG'  and pendientes.get(k, 0.0) >= 0) or
            (direccion == 'SHORT' and pendientes.get(k, 0.0) <= 0)
            for k in medias
        )
    )

    # ── Mensaje Telegram ────────────────────────────────────────────────────
    sev_emoji  = {'CRÍTICO': '🔴', 'MODERADO': '🟡', 'LEVE': '🟢'}
    sev_orden  = {'CRÍTICO': 0, 'MODERADO': 1, 'LEVE': 2}
    zona_orden = {'ENTRY': 0, 'TP1': 1, 'TP2': 2, 'TP3': 3}

    msg: list[str] = [
        f"⚠️ <b>ANÁLISIS DE OBSTÁCULOS — {simbolo} {timeframe}</b>",
        "",
        f"📍 Entry: {round(entry, 2)} | Dirección: {direccion}",
        f"📊 ATR actual: {round(atr, 2)}",
    ]

    if todas_a_favor:
        msg += ["", "✅ <b>Medias alineadas a favor — camino despejado</b>"]
    elif not tiene_obstaculos:
        msg += ["", "✅ Sin obstáculos detectados en el camino hacia los TPs"]
    else:
        msg += ["", "🚧 <b>OBSTÁCULOS DETECTADOS:</b>", ""]
        sorted_obs = sorted(
            obstaculos_raw,
            key=lambda o: (sev_orden.get(o['severidad'], 3), zona_orden.get(o['_zona'], 4))
        )
        for obs in sorted_obs:
            emoji    = sev_emoji.get(obs['severidad'], '⚪')
            zona_txt = (
                "Zona de entry (obstáculo inmediato)" if obs['_zona'] == 'ENTRY'
                else f"Entre entry y {obs['entre_entry_y']}"
            )
            pend_extra = " (contra el trade)" if obs['contra_direccion'] else ""
            msg += [
                f"{emoji} <b>{obs['severidad']}</b> — {obs['media']} @ {obs['valor']:.2f}",
                f"   └ {zona_txt}",
                f"   └ Pendiente {obs['pendiente']}{pend_extra}",
                "",
            ]

    if referencias_lejanas:
        msg += ["", f"📌 Referencia lejana: {', '.join(referencias_lejanas)}"]

    dir_arrow = '📈' if direccion == 'LONG' else '📉'
    msg += [
        "",
        f"{dir_arrow} <b>Niveles:</b>",
        f"   TP1: {round(tp1, 2)}  →  impacto: {impacto_tp1}",
        f"   TP2: {round(tp2, 2)}  →  impacto: {impacto_tp2}",
        f"   TP3: {round(tp3, 2)}  →  impacto: {impacto_tp3}",
        "",
        f"🤖 <b>Recomendación: {recomendacion.replace('_', ' ')}</b>",
    ]
    if razon:
        msg.append(f"   Razón: {razon}")

    # Limpiar campo interno _zona antes de exponer obstáculos
    obstaculos_publicos = [
        {k: v for k, v in o.items() if not k.startswith('_')}
        for o in obstaculos_raw
    ]

    return {
        'senal_id':            senal_id,
        'tiene_obstaculos':    tiene_obstaculos,
        'obstaculos':          obstaculos_publicos,
        'impacto_tp1':         impacto_tp1,
        'impacto_tp2':         impacto_tp2,
        'impacto_tp3':         impacto_tp3,
        'recomendacion':       recomendacion,
        'todas_a_favor':       todas_a_favor,
        'referencias_lejanas': referencias_lejanas,
        'mensaje_telegram':    '\n'.join(msg),
    }


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
