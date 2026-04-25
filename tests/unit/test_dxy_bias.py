"""
tests/unit/test_dxy_bias.py — Tests unitarios para services/dxy_bias.py

Cubre:
  - ajustar_score_por_dxy: lógica pura sin llamadas a yfinance
"""
import pytest
from services.dxy_bias import ajustar_score_por_dxy


class TestAjustarScorePorDXY:
    def test_bullish_penaliza_buy_y_refuerza_sell(self):
        buy, sell = ajustar_score_por_dxy(score_buy=8, score_sell=6, bias="BULLISH")
        assert buy  == 6   # 8 - 2
        assert sell == 7   # 6 + 1

    def test_bearish_refuerza_buy_y_penaliza_sell(self):
        buy, sell = ajustar_score_por_dxy(score_buy=8, score_sell=6, bias="BEARISH")
        assert buy  == 9   # 8 + 1
        assert sell == 4   # 6 - 2

    def test_neutral_sin_cambio(self):
        buy, sell = ajustar_score_por_dxy(score_buy=8, score_sell=6, bias="NEUTRAL")
        assert buy  == 8
        assert sell == 6

    def test_none_sin_cambio(self):
        buy, sell = ajustar_score_por_dxy(score_buy=5, score_sell=5, bias=None)
        assert buy  == 5
        assert sell == 5

    def test_bullish_no_baja_de_cero(self):
        """score_buy no puede ser negativo."""
        buy, sell = ajustar_score_por_dxy(score_buy=1, score_sell=0, bias="BULLISH")
        assert buy  >= 0

    def test_bearish_no_baja_sell_de_cero(self):
        """score_sell no puede ser negativo."""
        buy, sell = ajustar_score_por_dxy(score_buy=0, score_sell=1, bias="BEARISH")
        assert sell >= 0

    def test_retorna_tupla_de_dos_ints(self):
        result = ajustar_score_por_dxy(10, 10, "BULLISH")
        assert len(result) == 2
        assert isinstance(result[0], int)
        assert isinstance(result[1], int)

    def test_bullish_con_buy_cero(self):
        buy, sell = ajustar_score_por_dxy(score_buy=0, score_sell=5, bias="BULLISH")
        assert buy == 0
        assert sell == 6

    def test_bearish_con_sell_cero(self):
        buy, sell = ajustar_score_por_dxy(score_buy=5, score_sell=0, bias="BEARISH")
        assert buy  == 6
        assert sell == 0
