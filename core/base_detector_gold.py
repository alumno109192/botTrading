"""
core/base_detector_gold.py — Clase base para detectores de Gold/metales preciosos.

Hereda toda la lógica de BaseDetector sin cambios:
  - Sesión óptima: 08:00–21:00 UTC (Londres + NY)
  - Precisión numérica: 2 decimales (precio ~3 000)
  - Todas las demás herramientas (SR, indicadores, SL/TP, anti-spam…)
"""

from core.base_detector import BaseDetector


class GoldBaseDetector(BaseDetector):
    """
    Clase base para detectores de XAUUSD (Gold).

    Reutiliza íntegramente la lógica de BaseDetector, que ya fue diseñada
    pensando en Gold:
      - Sesión 08–21 UTC
      - Redondeo a 2 decimales en SL/TP
      - _SR_WING = 3, _SR_MIN_DIST_ATR = 0.3
    """
    pass
