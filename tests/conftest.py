"""
conftest.py — Fixtures compartidas para todos los tests de botTrading.
"""
import sys
import os
import pytest
import pandas as pd
import numpy as np

# Asegurar que el directorio raíz está en el path para importar los módulos del proyecto
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── Helpers para construir DataFrames OHLCV sintéticos ──────────────────────

def _make_ohlcv(closes, highs=None, lows=None, opens=None, volumes=None):
    """Crea un DataFrame OHLCV a partir de listas o arrays.

    Si no se proveen highs/lows/opens/volumes se derivan de closes.
    """
    n = len(closes)
    closes = np.array(closes, dtype=float)
    if opens   is None: opens   = closes * 0.999
    if highs   is None: highs   = closes * 1.005
    if lows    is None: lows    = closes * 0.995
    if volumes is None: volumes = np.full(n, 1000.0)

    df = pd.DataFrame({
        'Open':   np.array(opens,   dtype=float),
        'High':   np.array(highs,   dtype=float),
        'Low':    np.array(lows,    dtype=float),
        'Close':  closes,
        'Volume': np.array(volumes, dtype=float),
    })
    return df


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def trending_up_df():
    """100 velas con tendencia alcista sostenida."""
    closes  = [100.0 + i * 0.5 for i in range(100)]
    highs   = [c + 1.0 for c in closes]
    lows    = [c - 1.0 for c in closes]
    opens   = [c - 0.3 for c in closes]
    volumes = [1000.0 + i * 10 for i in range(100)]
    return _make_ohlcv(closes, highs, lows, opens, volumes)


@pytest.fixture
def trending_down_df():
    """100 velas con tendencia bajista sostenida."""
    closes  = [150.0 - i * 0.5 for i in range(100)]
    highs   = [c + 1.0 for c in closes]
    lows    = [c - 1.0 for c in closes]
    opens   = [c + 0.3 for c in closes]
    volumes = [1000.0 + i * 10 for i in range(100)]
    return _make_ohlcv(closes, highs, lows, opens, volumes)


@pytest.fixture
def flat_df():
    """100 velas laterales (precio casi constante)."""
    closes  = [100.0 + 0.1 * (i % 3) for i in range(100)]
    return _make_ohlcv(closes)


@pytest.fixture
def make_ohlcv():
    """Expone el helper _make_ohlcv como fixture para tests que necesiten datos custom."""
    return _make_ohlcv
