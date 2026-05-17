"""services/market_state.py — Estado de apertura/cierre del mercado.

Expone un flag thread-safe que bloquea la generación de señales cuando
el mercado está cerrado (fin de semana o aviso explícito de cierre).

Uso típico
----------
    # En signal_monitor.py — al detectar CIERRE DE MERCADOS:
    from services.market_state import set_mercado_abierto
    set_mercado_abierto(False, origen="CIERRE DE MERCADOS")

    # En signal_monitor.py — al detectar APERTURA DE MERCADOS:
    from services.market_state import set_mercado_abierto
    set_mercado_abierto(True, origen="APERTURA DE MERCADOS")

    # En base_detector.py — antes de guardar/enviar señal:
    from services.market_state import is_mercado_abierto
    if not is_mercado_abierto():
        return  # señal bloqueada
"""

import threading
import logging
from datetime import datetime, timezone

logger = logging.getLogger('bottrading')

_lock = threading.Lock()


def _calcular_estado_inicial() -> bool:
    """Determina si el mercado está abierto en el momento de arrancar.

    Mercado cerrado:
      - Sábado (weekday == 5)
      - Viernes >= 21:00 UTC (weekday == 4 y hour >= 21)
    """
    ahora = datetime.now(timezone.utc)
    weekday = ahora.weekday()  # 0=Lun … 4=Vie, 5=Sáb, 6=Dom
    if weekday == 5:
        return False
    if weekday == 4 and ahora.hour >= 21:
        return False
    return True


_mercado_abierto: bool = _calcular_estado_inicial()

# Registrar estado inicial al importar el módulo
_estado_inicial_txt = "ABIERTO 🟢" if _mercado_abierto else "CERRADO 🔴"
logger.info(f"[market_state] Estado inicial del mercado: {_estado_inicial_txt}")


def is_mercado_abierto() -> bool:
    """Retorna True si los mercados están abiertos (señales permitidas)."""
    with _lock:
        return _mercado_abierto


def set_mercado_abierto(valor: bool, origen: str = "manual") -> None:
    """Actualiza el estado de apertura del mercado.

    Args:
        valor:  True  → mercado abierto  (señales ON)
                False → mercado cerrado  (señales OFF)
        origen: etiqueta de quién actualizó (para trazabilidad en logs).
    """
    global _mercado_abierto
    with _lock:
        anterior = _mercado_abierto
        _mercado_abierto = valor

    if anterior != valor:
        estado_txt = "ABIERTO 🟢 — señales ACTIVADAS" if valor else "CERRADO 🔴 — señales DESACTIVADAS"
        logger.info(f"[market_state] Mercado {estado_txt}  (origen: {origen})")
    else:
        logger.debug(f"[market_state] set_mercado_abierto({valor}) sin cambio  (origen: {origen})")
