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
import bisect
from datetime import datetime, timezone, timedelta

# ══════════════════════════════════════════════════════════════════
# EVENTOS USD DE ALTO IMPACTO
# Formato: (año, mes, día, hora_utc, minuto_utc, descripción)
# Actualizar cada mes — mantener al menos 45 días adelante
# Última actualización: 18 abril 2026
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

    # ── JUNIO 2026 ──────────────────────────────────────────────
    (2026, 6,  5, 12, 30, "NFP — Nóminas no agrícolas EEUU (mayo)"),
    (2026, 6,  5, 12, 30, "Tasa desempleo EEUU"),
    (2026, 6, 11, 12, 30, "CPI EEUU — Inflación mensual (mayo)"),
    (2026, 6, 12, 12, 30, "PPI EEUU — Inflación productor"),
    (2026, 6, 17, 14, 0,  "Ventas minoristas EEUU"),
    (2026, 6, 18, 18, 0,  "FOMC — Decisión tipos Fed"),
    (2026, 6, 18, 18, 30, "FOMC — Conferencia de prensa Powell"),
    (2026, 6, 26, 12, 30, "PCE inflación EEUU (mayo)"),
    (2026, 6, 26, 12, 30, "PIB EEUU Q1 final"),

    # ── JULIO 2026 ──────────────────────────────────────────────
    (2026, 7,  2, 12, 30, "NFP — Nóminas no agrícolas EEUU (junio)"),
    (2026, 7,  2, 12, 30, "Tasa desempleo EEUU"),
    (2026, 7,  9, 12, 30, "CPI EEUU — Inflación mensual (junio)"),
    (2026, 7, 15, 14, 0,  "Ventas minoristas EEUU"),
    (2026, 7, 17, 14, 0,  "Solicitudes desempleo semanal"),
    (2026, 7, 24, 12, 30, "PCE inflación EEUU (junio)"),
    (2026, 7, 29, 18, 0,  "FOMC — Decisión tipos Fed"),
    (2026, 7, 29, 18, 30, "FOMC — Conferencia de prensa Powell"),

    # ── AGOSTO 2026 ─────────────────────────────────────────────
    (2026, 8,  7, 12, 30, "NFP — Nóminas no agrícolas EEUU (julio)"),
    (2026, 8,  7, 12, 30, "Tasa desempleo EEUU"),
    (2026, 8, 13, 12, 30, "CPI EEUU — Inflación mensual (julio)"),
    (2026, 8, 14, 12, 30, "PPI EEUU — Inflación productor"),
    (2026, 8, 19, 14, 0,  "Ventas minoristas EEUU"),
    (2026, 8, 26, 14, 0,  "Discurso Powell — Jackson Hole"),
    (2026, 8, 28, 12, 30, "PCE inflación EEUU (julio)"),

    # ── SEPTIEMBRE 2026 ─────────────────────────────────────────
    (2026, 9,  4, 12, 30, "NFP — Nóminas no agrícolas EEUU (agosto)"),
    (2026, 9,  4, 12, 30, "Tasa desempleo EEUU"),
    (2026, 9, 10, 12, 30, "CPI EEUU — Inflación mensual (agosto)"),
    (2026, 9, 11, 12, 30, "PPI EEUU — Inflación productor"),
    (2026, 9, 17, 14, 0,  "Ventas minoristas EEUU"),
    (2026, 9, 16, 18, 0,  "FOMC — Decisión tipos Fed"),
    (2026, 9, 16, 18, 30, "FOMC — Conferencia de prensa Powell"),
    (2026, 9, 25, 12, 30, "PCE inflación EEUU (agosto)"),
    (2026, 9, 25, 12, 30, "PIB EEUU Q2 final"),

    # ── OCTUBRE 2026 ────────────────────────────────────────────
    (2026, 10,  2, 12, 30, "NFP — Nóminas no agrícolas EEUU (septiembre)"),
    (2026, 10,  2, 12, 30, "Tasa desempleo EEUU"),
    (2026, 10,  9, 12, 30, "CPI EEUU — Inflación mensual (septiembre)"),
    (2026, 10, 15, 14, 0,  "Ventas minoristas EEUU"),
    (2026, 10, 16, 14, 0,  "Solicitudes desempleo semanal"),
    (2026, 10, 25, 12, 30, "PCE inflación EEUU (septiembre)"),
    (2026, 10, 29, 18, 0,  "FOMC — Decisión tipos Fed"),
    (2026, 10, 29, 18, 30, "FOMC — Conferencia de prensa Powell"),

    # ── NOVIEMBRE 2026 ──────────────────────────────────────────
    (2026, 11,  6, 12, 30, "NFP — Nóminas no agrícolas EEUU (octubre)"),
    (2026, 11,  6, 12, 30, "Tasa desempleo EEUU"),
    (2026, 11, 12, 12, 30, "CPI EEUU — Inflación mensual (octubre)"),
    (2026, 11, 13, 12, 30, "PPI EEUU — Inflación productor"),
    (2026, 11, 18, 14, 0,  "Ventas minoristas EEUU"),
    (2026, 11, 25, 12, 30, "PCE inflación EEUU (octubre)"),
    (2026, 11, 26, 14, 0,  "PIB EEUU Q3 preliminar"),

    # ── DICIEMBRE 2026 ──────────────────────────────────────────
    (2026, 12,  4, 12, 30, "NFP — Nóminas no agrícolas EEUU (noviembre)"),
    (2026, 12,  4, 12, 30, "Tasa desempleo EEUU"),
    (2026, 12, 10, 12, 30, "CPI EEUU — Inflación mensual (noviembre)"),
    (2026, 12, 11, 12, 30, "PPI EEUU — Inflación productor"),
    (2026, 12, 16, 18, 0,  "FOMC — Decisión tipos Fed"),
    (2026, 12, 16, 18, 30, "FOMC — Conferencia de prensa Powell"),
    (2026, 12, 18, 14, 0,  "Ventas minoristas EEUU"),
    (2026, 12, 23, 12, 30, "PCE inflación EEUU (noviembre)"),
    (2026, 12, 23, 12, 30, "PIB EEUU Q3 final"),
]

# Pre-computar lista ordenada de datetimes para búsqueda O(log n) con bisect
_EVENTOS_DT: list[datetime] = sorted(
    datetime(a, m, d, h, mi, tzinfo=timezone.utc)
    for (a, m, d, h, mi, _) in EVENTOS_ALTO_IMPACTO
)
_EVENTOS_SORTED: list[tuple] = sorted(
    EVENTOS_ALTO_IMPACTO,
    key=lambda e: datetime(e[0], e[1], e[2], e[3], e[4], tzinfo=timezone.utc)
)

_calendar_alerta_enviada = False


def _alertar_calendario_expirado():
    """Envía alerta Telegram una sola vez cuando el calendario expira."""
    global _calendar_alerta_enviada
    if _calendar_alerta_enviada:
        return
    _calendar_alerta_enviada = True
    try:
        from telegram_utils import enviar_telegram
        enviar_telegram(
            "⛔ <b>CALENDARIO EXPIRADO</b>\n"
            "El calendario de eventos económicos ha expirado.\n"
            "Actualizar EVENTOS_ALTO_IMPACTO en economic_calendar.py"
        )
    except Exception as e:
        print(f"⚠️ No se pudo enviar alerta de calendario: {e}")


def hay_evento_impacto(ventana_minutos: int = 60) -> tuple:
    """
    Comprueba si hay un evento USD de alto impacto en los próximos o últimos
    N minutos respecto al momento actual (UTC).

    Usa bisect para O(log n) en lugar de recorrer la lista completa.

    Args:
        ventana_minutos: Minutos antes/después del evento a bloquear.
                         Recomendado: 60 (1 hora antes y después).

    Returns:
        (hay_evento: bool, descripcion: str)
        - hay_evento=True  → bloquear señal
        - hay_evento=False → operar con normalidad

    Raises:
        RuntimeError: Si el calendario ha expirado (ningún evento futuro).
    """
    ahora   = datetime.now(timezone.utc)
    ventana = timedelta(minutes=ventana_minutos)

    # Verificar expiración ANTES de operar
    ultimo_evento_dt = _EVENTOS_DT[-1] if _EVENTOS_DT else None
    if ultimo_evento_dt and ahora > ultimo_evento_dt + ventana:
        _alertar_calendario_expirado()
        raise RuntimeError(
            "⛔ [CALENDAR] Calendario de eventos EXPIRADO — "
            "actualizar EVENTOS_ALTO_IMPACTO en economic_calendar.py"
        )

    # Bisect: encontrar índice del primer evento >= ahora - ventana
    limite_inf = ahora - ventana
    idx = bisect.bisect_left(_EVENTOS_DT, limite_inf)

    # Comprobar el evento en idx y los inmediatamente adyacentes
    for i in range(max(0, idx - 1), min(len(_EVENTOS_SORTED), idx + 3)):
        año, mes, dia, hora, minuto, descripcion = _EVENTOS_SORTED[i]
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

    return False, ""


def proximos_eventos(n: int = 5) -> list:
    """
    Retorna los próximos N eventos programados a partir de ahora.

    Returns:
        Lista de tuples (evento_dt, descripcion)
    """
    ahora   = datetime.now(timezone.utc)
    futuros = [
        (datetime(a, m, d, h, mi, tzinfo=timezone.utc), desc)
        for (a, m, d, h, mi, desc) in _EVENTOS_SORTED
        if datetime(a, m, d, h, mi, tzinfo=timezone.utc) > ahora
    ]
    return futuros[:n]

