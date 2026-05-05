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

    # ─────────────────────────────────────────────────────────────────────────
    # Correlación USD: XAUUSD ↔ EURUSD
    # ─────────────────────────────────────────────────────────────────────────

    def ajustar_score_por_correlacion_gold(
            self, score_sell: int, score_buy: int, tf_label: str) -> tuple:
        """
        Ajusta los scores de EUR/USD según el sesgo publicado de XAUUSD.

        Fundamento: Gold y EUR/USD comparten el USD como denominador / divisor.
        Cuando el USD se debilita, ambos suben. Cuando el USD se fortalece,
        ambos bajan. La correlación es POSITIVA: misma dirección.

          Gold BULLISH + EUR/USD score_buy líder  → +2 BUY  (confirmación)
          Gold BEARISH + EUR/USD score_sell líder → +2 SELL (confirmación)
          Contradicción                           → -2 al score dominante (penalización)
          Gold NEUTRAL / sin datos / expirado     → sin ajuste

        El sesgo de Gold se considera expirado si tiene más de 2 horas sin actualizar.

        Returns: (score_sell, score_buy, desc)
            desc — cadena vacía si no hay ajuste, o descripción del ajuste aplicado.
        """
        from services import tf_bias as _tf_bias
        from datetime import datetime

        gold_sesgo = _tf_bias.obtener_sesgo('XAUUSD', tf_label)
        if gold_sesgo is None:
            return score_sell, score_buy, ''

        edad_seg = (datetime.now() - gold_sesgo['ts']).total_seconds()
        if edad_seg > 7200:   # sesgo de Gold expirado (>2 h sin nuevos datos)
            return score_sell, score_buy, ''

        bias_gold = gold_sesgo['bias']
        if bias_gold == _tf_bias.BIAS_NEUTRAL:
            return score_sell, score_buy, ''

        if bias_gold == _tf_bias.BIAS_BULLISH:
            if score_buy >= score_sell:
                score_buy = min(score_buy + 2, 50)
                return score_sell, score_buy, f'🥇 Gold BULLISH ({tf_label}) confirma → +2 BUY'
            else:
                score_sell = max(0, score_sell - 2)
                return score_sell, score_buy, f'⚠️ Gold BULLISH ({tf_label}) contradice SELL → -2'
        else:  # BEARISH
            if score_sell >= score_buy:
                score_sell = min(score_sell + 2, 50)
                return score_sell, score_buy, f'🥇 Gold BEARISH ({tf_label}) confirma → +2 SELL'
            else:
                score_buy = max(0, score_buy - 2)
                return score_sell, score_buy, f'⚠️ Gold BEARISH ({tf_label}) contradice BUY → -2'
