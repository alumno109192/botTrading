"""
core/base_detector_eurusd.py — Clase base para detectores de EUR/USD y pares Forex.

Hereda de BaseDetector y sobreescribe las partes específicas de Forex:
  - Sesión óptima: 07:00–22:00 UTC (Londres + NY completo)
  - calcular_niveles(): redondeo a 5 decimales (precio ~1.17, ATR ~0.0005)
  - calcular_zonas_sr(): ya usa precisión dinámica (heredado de BaseDetector)
"""

import os
import pandas as pd
from core.base_detector import BaseDetector


class EURUSDBaseDetector(BaseDetector):
    """
    Clase base para detectores de EUR/USD (Forex).

    Diferencias respecto a GoldBaseDetector / BaseDetector estándar:

    1. Sesión óptima 07:00–22:00 UTC
       EUR/USD tiene máximo volumen en apertura Londres (07:00) y cierre
       NY (22:00 aprox.). Gold solo usa 08:00–21:00.

    2. calcular_niveles() con 5 decimales
       Para precios ~1.17000, round(..., 2) colapsaría SL y TP al mismo
       valor que el entry. Se necesitan al menos 5 decimales.
    """

    # ─────────────────────────────────────────────────────────────────────────
    # Sesión óptima EUR/USD
    # ─────────────────────────────────────────────────────────────────────────

    def en_sesion_optima(self) -> bool:
        """
        Devuelve True si la hora UTC actual está dentro de la sesión óptima
        de EUR/USD: 07:00–22:00 UTC (Londres completo + solape con NY).
        Poner SKIP_SESSION_FILTER=1 en .env para saltar este filtro (pruebas).
        """
        if os.getenv('SKIP_SESSION_FILTER', '0') == '1':
            return True
        from datetime import datetime, timezone
        hora = datetime.now(timezone.utc).hour
        return 7 <= hora < 22

    # ─────────────────────────────────────────────────────────────────────────
    # SL / TP con precisión Forex (5 decimales)
    # ─────────────────────────────────────────────────────────────────────────

    def calcular_niveles(self, sell_limit: float, buy_limit: float,
                         atr: float) -> tuple:
        """
        Calcula SL y TPs para EUR/USD con 5 decimales de precisión.

        Para Forex (precio ~1.17, ATR ~0.0005) round(..., 2) colapsaría todos
        los niveles al mismo valor. Se usan 5 decimales aquí.

        Returns: (sl_venta, sl_compra, tp1_v, tp2_v, tp3_v, tp1_c, tp2_c, tp3_c)
        """
        p = self.params
        asm = p['atr_sl_mult']

        sl_venta  = round(sell_limit + atr * asm,              5)
        sl_compra = round(buy_limit  - atr * asm,              5)

        tp1_v = round(sell_limit - atr * p['atr_tp1_mult'], 5)
        tp2_v = round(sell_limit - atr * p['atr_tp2_mult'], 5)
        tp3_v = round(sell_limit - atr * p['atr_tp3_mult'], 5)

        tp1_c = round(buy_limit  + atr * p['atr_tp1_mult'], 5)
        tp2_c = round(buy_limit  + atr * p['atr_tp2_mult'], 5)
        tp3_c = round(buy_limit  + atr * p['atr_tp3_mult'], 5)

        return sl_venta, sl_compra, tp1_v, tp2_v, tp3_v, tp1_c, tp2_c, tp3_c
