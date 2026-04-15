"""
economic_calendar.py — Filtro de eventos macro USD de alto impacto

Bloquea señales en ventana de ±ventana_minutos alrededor de eventos críticos
para Gold (XAUUSD): FOMC, NFP, CPI, PIB, discursos Powell, etc.

MANTENIMIENTO:
    Revisar y actualizar la lista EVENTOS_ALTO_IMPACTO cada primer lunes del mes.
    Fuente de referencia: https://www.forexfactory.com/calendar (filtrar: USD, Impact=High)

Uso:
    from economic_calendar import hay_evento_impacto
    bloqueado, descripcion = hay_evento_impacto(ventana_minutos=60)
    if bloqueado:
        print(f"⚠️ Señal bloqueada: evento próximo → {descripcion}")
        return
"""
from datetime import datetime, timezone, timedelta

# ══════════════════════════════════════════════════════════════════
# EVENTOS USD DE ALTO IMPACTO
# Formato: (año, mes, día, hora_utc, minuto_utc, descripción)
# Actualizar cada mes — mantener al menos 45 días adelante
# Última actualización: 15 abril 2026
# ══════════════════════════════════════════════════════════════════
EVENTOS_ALTO_IMPACTO = [
    # ── ABRIL 2026 ─────────────────────────────────────────────
    (2026, 4, 16, 12, 30, "Ventas minoristas EEUU (marzo)"),
    (2026, 4, 17, 14, 30, "Solicitudes desempleo semanal"),
    (2026, 4, 22, 14, 0,  "PMI manufacturero Flash EEUU"),
    (2026, 4, 24, 12, 30, "PIB EEUU Q1 preliminar"),
    (2026, 4, 25, 12, 30, "PCE inflación EEUU (marzo)"),
    (2026, 4, 29, 18, 0,  "FOMC — Decisión tipos Fed"),
    (2026, 4, 29, 18, 30, "FOMC — Conferencia de prensa Powell"),
    (2026, 4, 30, 14, 0,  "Discurso Powell post-FOMC"),

    # ── MAYO 2026 ───────────────────────────────────────────────
    (2026, 5,  1, 12, 30, "NFP — Nóminas no agrícolas EEUU (abril)"),
    (2026, 5,  1, 12, 30, "Tasa desempleo EEUU"),
    (2026, 5,  7, 12, 30, "CPI EEUU — Inflación mensual (abril)"),
    (2026, 5, 13, 12, 30, "PPI EEUU — Inflación productor (abril)"),
    (2026, 5, 15, 12, 30, "Ventas minoristas EEUU (abril)"),
    (2026, 5, 15, 14, 30, "Solicitudes desempleo semanal"),
    (2026, 5, 22, 14, 0,  "PMI manufacturero Flash EEUU"),
    (2026, 5, 28, 12, 30, "PIB EEUU Q1 2026 revisado"),
    (2026, 5, 29, 12, 30, "PCE inflación EEUU (abril)"),

    # ── JUNIO 2026 (aproximados — confirmar en ForexFactory) ────
    (2026, 6,  5, 12, 30, "NFP — Nóminas no agrícolas EEUU (mayo)"),
    (2026, 6,  5, 12, 30, "Tasa desempleo EEUU"),
    (2026, 6, 11, 12, 30, "CPI EEUU — Inflación mensual (mayo)"),
    (2026, 6, 12, 12, 30, "PPI EEUU — Inflación productor"),
    (2026, 6, 17, 14, 0,  "Ventas minoristas EEUU"),
    (2026, 6, 18, 18, 0,  "FOMC — Decisión tipos Fed"),
    (2026, 6, 18, 18, 30, "FOMC — Conferencia de prensa Powell"),
    (2026, 6, 26, 12, 30, "PCE inflación EEUU (mayo)"),
    (2026, 6, 26, 12, 30, "PIB EEUU Q1 final"),
]


def hay_evento_impacto(ventana_minutos: int = 60) -> tuple:
    """
    Comprueba si hay un evento USD de alto impacto en los próximos o últimos
    N minutos respecto al momento actual (UTC).

    Args:
        ventana_minutos: Minutos antes/después del evento a bloquear.
                         Recomendado: 60 (1 hora antes y después).

    Returns:
        (hay_evento: bool, descripcion: str)
        - hay_evento=True  → bloquear señal
        - hay_evento=False → operar con normalidad
    """
    ahora   = datetime.now(timezone.utc)
    ventana = timedelta(minutes=ventana_minutos)

    for (año, mes, dia, hora, minuto, descripcion) in EVENTOS_ALTO_IMPACTO:
        try:
            evento_dt  = datetime(año, mes, dia, hora, minuto, tzinfo=timezone.utc)
            diferencia = abs(ahora - evento_dt)
            if diferencia <= ventana:
                tiempo_restante = evento_dt - ahora
                if tiempo_restante.total_seconds() > 0:
                    mins = int(tiempo_restante.total_seconds() / 60)
                    print(f"  🚫 Evento próximo en {mins} min: {descripcion}")
                else:
                    mins = int(-tiempo_restante.total_seconds() / 60)
                    print(f"  🚫 Evento ocurrido hace {mins} min: {descripcion}")
                return True, descripcion
        except Exception:
            continue

    return False, ""


def proximos_eventos(n: int = 5) -> list:
    """
    Retorna los próximos N eventos programados a partir de ahora.
    Útil para logging informativo al inicio del detector.

    Returns:
        Lista de tuples (evento_dt, descripcion)
    """
    ahora    = datetime.now(timezone.utc)
    futuros  = []

    for (año, mes, dia, hora, minuto, descripcion) in EVENTOS_ALTO_IMPACTO:
        try:
            evento_dt = datetime(año, mes, dia, hora, minuto, tzinfo=timezone.utc)
            if evento_dt > ahora:
                futuros.append((evento_dt, descripcion))
        except Exception:
            continue

    futuros.sort(key=lambda x: x[0])
    return futuros[:n]
