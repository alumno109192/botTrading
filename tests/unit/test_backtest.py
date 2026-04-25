"""
tests/unit/test_backtest.py — Tests for scripts/backtest.py

Tests use mock data to avoid real API calls.
"""
import sys
import os
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.backtest import (
    _generar_senal_simple,
    evaluar_senal,
    calcular_metricas,
    ejecutar_backtest,
)


def _make_df(n=200, start_price=3300.0, trend='flat'):
    """Creates a synthetic OHLCV DataFrame for testing."""
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC')

    if trend == 'flat':
        closes = start_price + np.random.randn(n).cumsum() * 0.5
    elif trend == 'up':
        closes = start_price + np.arange(n) * 0.5 + np.random.randn(n)
    elif trend == 'down':
        closes = start_price - np.arange(n) * 0.5 + np.random.randn(n)
    else:
        closes = np.full(n, start_price)

    opens = closes * (1 + np.random.randn(n) * 0.0001)
    highs = np.maximum(closes, opens) + abs(np.random.randn(n)) * 0.3
    lows = np.minimum(closes, opens) - abs(np.random.randn(n)) * 0.3
    volumes = np.random.randint(1000, 5000, n).astype(float)

    return pd.DataFrame({
        'Open': opens,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': volumes,
    }, index=dates)


def _make_df_rsi_high(n=200, rsi_trigger=75):
    """Creates DataFrame where RSI will be high (bearish signal conditions)."""
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC')
    # Strong uptrend to generate high RSI
    closes = 3300.0 + np.arange(n) * 2.0
    opens = closes - 0.5
    highs = closes + 1.0
    lows = closes - 1.5
    volumes = np.random.randint(1000, 5000, n).astype(float)
    return pd.DataFrame({
        'Open': opens, 'High': highs, 'Low': lows, 'Close': closes, 'Volume': volumes,
    }, index=dates)


def _make_df_rsi_low(n=200):
    """Creates DataFrame where RSI will be low (bullish signal conditions)."""
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC')
    # Strong downtrend to generate low RSI
    closes = 3300.0 - np.arange(n) * 2.0
    opens = closes + 0.5
    highs = closes + 1.5
    lows = closes - 1.0
    volumes = np.random.randint(1000, 5000, n).astype(float)
    return pd.DataFrame({
        'Open': opens, 'High': highs, 'Low': lows, 'Close': closes, 'Volume': volumes,
    }, index=dates)


class TestSinSenales:
    """test_sin_senales: No signals generated → metrics show 0 trades."""

    def test_sin_senales_metricas_cero(self):
        """When no data available, metrics should show 0 trades."""
        with patch('scripts.backtest.cargar_datos') as mock_load:
            mock_load.return_value = pd.DataFrame()  # Empty DF → no signals

            resultado = ejecutar_backtest(
                simbolo='XAUUSD',
                tf='1h',
                desde='2024-01-01',
                hasta='2024-06-30',
            )

        assert resultado['metricas']['total_senales'] == 0
        assert resultado['metricas']['win_rate'] == 0.0
        assert resultado['resultados'] == []

    def test_calcular_metricas_lista_vacia(self):
        """calcular_metricas([]) should return all zeros."""
        metricas = calcular_metricas([])

        assert metricas['total_senales'] == 0
        assert metricas['win_rate'] == 0.0
        assert metricas['profit_factor'] == 0.0
        assert metricas['mejor_racha'] == 0
        assert metricas['peor_racha'] == 0


class TestSenalSL:
    """test_senal_sl: One signal that hits SL → win_rate=0%."""

    def test_senal_sell_hits_sl(self):
        """A SELL signal where price goes UP (hits SL) → win_rate=0%."""
        senal = {
            'tipo': 'SELL',
            'direccion': 'SHORT',
            'entry': 3300.0,
            'sl': 3315.0,   # SL is above entry for SHORT
            'tp1': 3285.0,
            'tp2': 3270.0,
            'tp3': 3255.0,
        }

        # Future bars: price goes up hitting SL
        dates = pd.date_range('2024-01-02', periods=5, freq='1h', tz='UTC')
        df_futuro = pd.DataFrame({
            'Open':  [3301, 3305, 3310, 3316, 3320],
            'High':  [3305, 3310, 3315, 3320, 3325],  # High[3] = 3320 > sl 3315
            'Low':   [3299, 3303, 3308, 3313, 3317],
            'Close': [3303, 3308, 3314, 3318, 3321],
            'Volume':[100, 100, 100, 100, 100],
        }, index=dates)

        resultado = evaluar_senal(senal, df_futuro)
        assert resultado == 'SL'

    def test_metricas_con_solo_sl(self):
        """With only SL results, win_rate=0%, profit_factor=0."""
        resultados = [{
            'bar': 0, 'fecha': '2024-01-01', 'tipo': 'SELL',
            'resultado': 'SL', 'entry': 3300.0, 'sl': 3315.0,
            'tp1': 3285.0, 'tp2': 3270.0, 'tp3': 3255.0,
            'riesgo': 15.0, 'tp1_rr': 1.0, 'tp2_rr': 2.0, 'tp3_rr': 3.0,
        }]

        metricas = calcular_metricas(resultados)

        assert metricas['total_senales'] == 1
        assert metricas['win_rate'] == 0.0
        assert metricas['sl_pct'] == 100.0
        assert metricas['profit_factor'] == 0.0


class TestSenalTP2:
    """test_senal_tp2: One signal that hits TP2 → win_rate=100%, result='TP2'."""

    def test_senal_buy_hits_tp2(self):
        """A BUY signal where price hits TP2 → resultado='TP2'."""
        entry = 3300.0
        sl = 3285.0     # SL is below entry for LONG
        tp1 = 3315.0    # +1.0R
        tp2 = 3330.0    # +2.0R
        tp3 = 3345.0    # +3.0R

        senal = {
            'tipo': 'BUY',
            'direccion': 'LONG',
            'entry': entry,
            'sl': sl,
            'tp1': tp1,
            'tp2': tp2,
            'tp3': tp3,
        }

        # Future bars: price goes up to TP2 but not TP3
        # Bars 0-2 must stay below tp1=3315; bar 3 hits tp2=3330 (not tp3=3345)
        dates = pd.date_range('2024-01-02', periods=5, freq='1h', tz='UTC')
        df_futuro = pd.DataFrame({
            'Open':  [3302, 3305, 3308, 3325, 3335],
            'High':  [3308, 3310, 3312, 3332, 3340],  # High[0-2] < tp1=3315; High[3]=3332 >= tp2=3330 < tp3=3345
            'Low':   [3299, 3303, 3306, 3323, 3330],
            'Close': [3305, 3308, 3310, 3330, 3338],
            'Volume':[100, 100, 100, 100, 100],
        }, index=dates)

        resultado = evaluar_senal(senal, df_futuro)
        assert resultado == 'TP2'

    def test_metricas_con_tp2(self):
        """With TP2 result, win_rate=100%."""
        resultados = [{
            'bar': 0, 'fecha': '2024-01-01', 'tipo': 'BUY',
            'resultado': 'TP2', 'entry': 3300.0, 'sl': 3285.0,
            'tp1': 3315.0, 'tp2': 3330.0, 'tp3': 3345.0,
            'riesgo': 15.0, 'tp1_rr': 1.0, 'tp2_rr': 2.0, 'tp3_rr': 3.0,
        }]

        metricas = calcular_metricas(resultados)

        assert metricas['total_senales'] == 1
        assert metricas['win_rate'] == 100.0
        assert metricas['tp2_pct'] == 100.0
        assert metricas['profit_factor'] > 0


class TestMultiplesSenalesSuperpuestas:
    """test_multiples_senales_superpuestas: Multiple overlapping signals handled correctly."""

    def test_multiples_senales_metricas_correctas(self):
        """Multiple signals with mixed results compute correct aggregate metrics."""
        resultados = [
            {
                'bar': 0, 'fecha': '2024-01-01', 'tipo': 'SELL',
                'resultado': 'TP1', 'entry': 3300.0, 'sl': 3315.0,
                'tp1': 3285.0, 'tp2': 3270.0, 'tp3': 3255.0,
                'riesgo': 15.0, 'tp1_rr': 1.0, 'tp2_rr': 2.0, 'tp3_rr': 3.0,
            },
            {
                'bar': 5, 'fecha': '2024-01-01 05:00', 'tipo': 'BUY',
                'resultado': 'SL', 'entry': 3290.0, 'sl': 3275.0,
                'tp1': 3305.0, 'tp2': 3320.0, 'tp3': 3335.0,
                'riesgo': 15.0, 'tp1_rr': 1.0, 'tp2_rr': 2.0, 'tp3_rr': 3.0,
            },
            {
                'bar': 10, 'fecha': '2024-01-01 10:00', 'tipo': 'BUY',
                'resultado': 'TP2', 'entry': 3295.0, 'sl': 3280.0,
                'tp1': 3310.0, 'tp2': 3325.0, 'tp3': 3340.0,
                'riesgo': 15.0, 'tp1_rr': 1.0, 'tp2_rr': 2.0, 'tp3_rr': 3.0,
            },
        ]

        metricas = calcular_metricas(resultados)

        assert metricas['total_senales'] == 3
        # 2 wins (TP1 + TP2), 1 loss (SL)
        assert metricas['win_rate'] == pytest.approx(66.7, abs=0.1)
        assert metricas['sl_pct'] == pytest.approx(33.3, abs=0.1)
        assert metricas['buy_count'] == 2
        assert metricas['sell_count'] == 1
        assert metricas['profit_factor'] > 1.0

    def test_ejecutar_backtest_con_datos_sinteticos(self):
        """ejecutar_backtest processes correctly when data is available."""
        df = _make_df(n=250, trend='flat')

        with patch('scripts.backtest.cargar_datos') as mock_load:
            mock_load.return_value = df

            resultado = ejecutar_backtest(
                simbolo='XAUUSD',
                tf='1h',
                desde='2024-01-01',
                hasta='2024-06-30',
            )

        # Verify structure
        assert 'metricas' in resultado
        assert 'resultados' in resultado
        assert isinstance(resultado['metricas']['total_senales'], int)
        assert 0.0 <= resultado['metricas']['win_rate'] <= 100.0

        # Verify each result has required keys
        for r in resultado['resultados']:
            assert 'resultado' in r
            assert r['resultado'] in ('SL', 'TP1', 'TP2', 'TP3', 'EN_CURSO')
            assert 'tipo' in r
            assert r['tipo'] in ('BUY', 'SELL')
