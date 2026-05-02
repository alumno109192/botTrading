"""
tests/unit/test_yield_bias.py — Tests unitarios para services/yield_bias.py

Cubre la lógica pura de ajustar_score_por_yield (sin llamadas a yfinance).
La correlación inversa Gold/Yields:
    BEARISH (yields subiendo) → -2 en score_buy, +1 en score_sell
    BULLISH (yields bajando)  → +1 en score_buy, -2 en score_sell
    NEUTRAL / None            → sin cambio
"""
import pytest
from services.yield_bias import ajustar_score_por_yield


class TestAjustarScorePorYield:
    def test_bearish_penaliza_buy_y_refuerza_sell(self):
        buy, sell = ajustar_score_por_yield(score_buy=8, score_sell=6, bias="BEARISH")
        assert buy  == 6   # 8 - 2
        assert sell == 7   # 6 + 1

    def test_bullish_refuerza_buy_y_penaliza_sell(self):
        buy, sell = ajustar_score_por_yield(score_buy=8, score_sell=6, bias="BULLISH")
        assert buy  == 9   # 8 + 1
        assert sell == 4   # 6 - 2

    def test_neutral_sin_cambio(self):
        buy, sell = ajustar_score_por_yield(score_buy=8, score_sell=6, bias="NEUTRAL")
        assert buy  == 8
        assert sell == 6

    def test_none_sin_cambio(self):
        buy, sell = ajustar_score_por_yield(score_buy=5, score_sell=5, bias=None)
        assert buy  == 5
        assert sell == 5

    def test_bearish_no_baja_buy_de_cero(self):
        """score_buy no puede ser negativo."""
        buy, sell = ajustar_score_por_yield(score_buy=1, score_sell=0, bias="BEARISH")
        assert buy  >= 0

    def test_bullish_no_baja_sell_de_cero(self):
        """score_sell no puede ser negativo."""
        buy, sell = ajustar_score_por_yield(score_buy=0, score_sell=1, bias="BULLISH")
        assert sell >= 0

    def test_bearish_con_buy_cero(self):
        """score_buy=0 sigue en 0 (max de 0 y -2)."""
        buy, sell = ajustar_score_por_yield(score_buy=0, score_sell=5, bias="BEARISH")
        assert buy  == 0
        assert sell == 6

    def test_bullish_con_sell_cero(self):
        buy, sell = ajustar_score_por_yield(score_buy=5, score_sell=0, bias="BULLISH")
        assert buy  == 6
        assert sell == 0

    def test_retorna_tupla_de_dos_ints(self):
        result = ajustar_score_por_yield(10, 10, "BEARISH")
        assert len(result) == 2
        assert isinstance(result[0], int)
        assert isinstance(result[1], int)

    def test_ajuste_simetrico_bearish(self):
        """Ajuste BEARISH: delta_buy = -2, delta_sell = +1."""
        buy_in, sell_in = 10, 10
        buy, sell = ajustar_score_por_yield(buy_in, sell_in, "BEARISH")
        assert buy  == buy_in - 2
        assert sell == sell_in + 1

    def test_ajuste_simetrico_bullish(self):
        """Ajuste BULLISH: delta_buy = +1, delta_sell = -2."""
        buy_in, sell_in = 10, 10
        buy, sell = ajustar_score_por_yield(buy_in, sell_in, "BULLISH")
        assert buy  == buy_in + 1
        assert sell == sell_in - 2

    def test_bias_desconocido_sin_cambio(self):
        """Bias no reconocido actúa como NEUTRAL."""
        buy, sell = ajustar_score_por_yield(7, 3, bias="UNKNOWN")
        assert buy  == 7
        assert sell == 3
