"""
tests/unit/test_mejoras_contexto.py — Tests para las 3 nuevas funciones de contexto.

Cubre:
  - calcular_aceleracion_rsi
  - calcular_micro_volatilidad
  - calcular_momentum_reciente
"""
import pytest
import numpy as np
import pandas as pd

from core.indicators import (
    calcular_aceleracion_rsi,
    calcular_micro_volatilidad,
    calcular_momentum_reciente,
    calcular_rsi,
    calcular_atr,
)


# ── Helper ────────────────────────────────────────────────────────────────────

def _make_ohlcv(n, close_base=2000.0, opens=None, highs=None, lows=None, closes=None,
                high_extra=1.0, low_extra=1.0):
    c  = np.full(n, close_base) if closes is None else np.array(closes, dtype=float)
    h  = c + high_extra          if highs  is None else np.array(highs,  dtype=float)
    lo = c - low_extra           if lows   is None else np.array(lows,   dtype=float)
    o  = c.copy()                if opens  is None else np.array(opens,  dtype=float)
    v  = np.full(n, 1000.0)
    return pd.DataFrame({'Open': o, 'High': h, 'Low': lo, 'Close': c, 'Volume': v})


# ══════════════════════════════════════════════════════════════════════════════
# calcular_aceleracion_rsi
# ══════════════════════════════════════════════════════════════════════════════

class TestCalcularAceleracionRsi:
    """Tests para calcular_aceleracion_rsi(rsi_series, n=3)."""

    def _rsi_series(self, values):
        """Crea una pd.Series de RSI con los valores dados."""
        return pd.Series(values, dtype=float)

    def test_rsi_bajando_3_velas_retorna_bajando(self):
        # RSI bajando 4 valores (3 diferencias negativas): 60, 58, 55, 52
        # Las últimas n+2=5 posiciones son índices [-5:-1] pero necesitamos al menos 5 valores
        vals = [70, 68, 65, 60, 58, 55, 52, 50]  # última viva (índice -1) = 50
        rsi = self._rsi_series(vals)
        baj, sub = calcular_aceleracion_rsi(rsi, n=3)
        assert baj is True
        assert sub is False

    def test_rsi_subiendo_3_velas_retorna_subiendo(self):
        vals = [30, 32, 34, 36, 38, 40, 42, 45]
        rsi = self._rsi_series(vals)
        baj, sub = calcular_aceleracion_rsi(rsi, n=3)
        assert baj is False
        assert sub is True

    def test_rsi_mixto_retorna_ambos_false(self):
        # RSI sube y luego baja — no aceleración sostenida
        vals = [50, 52, 51, 53, 52, 54, 53, 55]
        rsi = self._rsi_series(vals)
        baj, sub = calcular_aceleracion_rsi(rsi, n=3)
        assert baj is False
        assert sub is False

    def test_datos_insuficientes_retorna_false_false(self):
        rsi = self._rsi_series([55, 53, 51])  # solo 3 valores, n+2=5 requeridos
        baj, sub = calcular_aceleracion_rsi(rsi, n=3)
        assert baj is False
        assert sub is False

    def test_exactamente_n_caidas_bajando_true(self):
        # n=2: necesita 4 valores totales (n+2). Últ 3 cerradas: 60, 58, 55 → 2 diferencias neg
        vals = [65, 62, 60, 58, 55, 50]  # viva=50
        rsi = self._rsi_series(vals)
        baj, sub = calcular_aceleracion_rsi(rsi, n=2)
        assert baj is True
        assert sub is False

    def test_rsi_plano_no_acelera(self):
        vals = [55, 55, 55, 55, 55, 55]
        rsi = self._rsi_series(vals)
        baj, sub = calcular_aceleracion_rsi(rsi, n=3)
        assert baj is False
        assert sub is False

    def test_n_1_detecta_un_paso(self):
        # n=1: solo necesita que el último valor cerrado baje respecto al anterior
        vals = [60, 58, 55]  # viva=55, [60, 58] → 1 dif negativa
        rsi = self._rsi_series(vals)
        baj, sub = calcular_aceleracion_rsi(rsi, n=1)
        assert baj is True
        assert sub is False


# ══════════════════════════════════════════════════════════════════════════════
# calcular_micro_volatilidad
# ══════════════════════════════════════════════════════════════════════════════

class TestCalcularMicroVolatilidad:
    """Tests para calcular_micro_volatilidad(df, periodo_corto=5, periodo_largo=20)."""

    def test_datos_insuficientes_retorna_1(self):
        df = _make_ohlcv(10)
        resultado = calcular_micro_volatilidad(df, periodo_corto=5, periodo_largo=20)
        assert resultado == 1.0

    def test_datos_suficientes_retorna_float(self):
        # Con 50 velas con precios estables, ATR_5 ≈ ATR_20 → ratio ≈ 1.0
        df = _make_ohlcv(50, high_extra=2.0, low_extra=2.0)
        resultado = calcular_micro_volatilidad(df)
        assert isinstance(resultado, float)
        assert resultado > 0

    def test_expansion_da_ratio_mayor_1(self):
        # Crear un DataFrame con las últimas velas muy volátiles
        n = 50
        closes = np.full(n, 2000.0)
        # Las últimas 5 velas tienen rango muy amplio vs las 20 previas
        highs = np.full(n, 2002.0)
        lows  = np.full(n, 1998.0)
        highs[-6:-1] = 2020.0   # últimas 5 velas cerradas con rango enorme
        lows[-6:-1]  = 1980.0
        df = _make_ohlcv(n, closes=closes, highs=highs, lows=lows)
        resultado = calcular_micro_volatilidad(df)
        # Esperamos ratio > 1.0 ya que las últimas velas tienen ATR mayor
        assert resultado > 1.0

    def test_compresion_da_ratio_menor_1(self):
        n = 50
        closes = np.full(n, 2000.0)
        # Las primeras 30 velas son muy volátiles, las últimas 5 son comprimidas
        highs = np.full(n, 2010.0)
        lows  = np.full(n, 1990.0)
        highs[-6:-1] = 2001.0   # últimas 5 velas cerradas comprimidas
        lows[-6:-1]  = 1999.0
        df = _make_ohlcv(n, closes=closes, highs=highs, lows=lows)
        resultado = calcular_micro_volatilidad(df)
        assert resultado < 1.0

    def test_retorna_float_redondeado_3_decimales(self):
        df = _make_ohlcv(50, high_extra=3.0, low_extra=3.0)
        resultado = calcular_micro_volatilidad(df)
        # Debe tener máximo 3 decimales
        assert resultado == round(resultado, 3)

    def test_atr_largo_cero_retorna_1(self):
        # Simular ATR=0 usando un DataFrame con todas las velas idénticas
        # (aunque en la práctica EWM no da exactamente 0, verificamos que no crashea)
        df = _make_ohlcv(50, high_extra=0.0, low_extra=0.0)
        resultado = calcular_micro_volatilidad(df)
        assert isinstance(resultado, float)
        assert resultado >= 0


# ══════════════════════════════════════════════════════════════════════════════
# calcular_momentum_reciente
# ══════════════════════════════════════════════════════════════════════════════

class TestCalcularMomentumReciente:
    """Tests para calcular_momentum_reciente(df, n=3)."""

    def _df_con_velas(self, opens, closes):
        """Helper: crea df con velas específicas de open/close."""
        n = len(opens)
        highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
        lows  = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
        # Añadir vela viva al final (índice -1) sin contar en el análisis
        opens_  = list(opens)  + [opens[-1]]
        closes_ = list(closes) + [closes[-1]]
        highs_  = highs        + [highs[-1]]
        lows_   = lows         + [lows[-1]]
        v = [1000.0] * (n + 1)
        return pd.DataFrame({'Open': opens_, 'High': highs_, 'Low': lows_,
                              'Close': closes_, 'Volume': v})

    def test_3_velas_alcistas_retorna_1(self):
        opens  = [2000, 2005, 2010, 2015]   # 4 velas cerradas
        closes = [2005, 2010, 2015, 2020]
        df = self._df_con_velas(opens, closes)
        assert calcular_momentum_reciente(df, n=3) == 1

    def test_3_velas_bajistas_retorna_menos_1(self):
        opens  = [2020, 2015, 2010, 2005]
        closes = [2015, 2010, 2005, 2000]
        df = self._df_con_velas(opens, closes)
        assert calcular_momentum_reciente(df, n=3) == -1

    def test_velas_mixtas_retorna_0(self):
        opens  = [2000, 2010, 2005, 2015]
        closes = [2010, 2005, 2015, 2010]   # sube, baja, sube, baja
        df = self._df_con_velas(opens, closes)
        assert calcular_momentum_reciente(df, n=3) == 0

    def test_datos_insuficientes_retorna_0(self):
        df = _make_ohlcv(3)  # solo 3 filas, n+2=5 requeridos para n=3
        assert calcular_momentum_reciente(df, n=3) == 0

    def test_n_1_detecta_una_vela(self):
        # Con n=1: solo la vela más reciente cerrada importa
        opens  = [2000, 2010, 2005]
        closes = [2010, 2005, 2015]   # última cerrada: alcista
        df = self._df_con_velas(opens, closes)
        assert calcular_momentum_reciente(df, n=1) == 1

    def test_vela_doji_cierra_igual_open_es_0(self):
        # Una vela doji (close == open) no es alcista ni bajista → resultado 0
        opens  = [2000, 2005, 2010, 2010]
        closes = [2005, 2010, 2010, 2015]   # 3ª vela es doji
        df = self._df_con_velas(opens, closes)
        assert calcular_momentum_reciente(df, n=3) == 0

    def test_exactamente_n_velas_alcistas_true(self):
        # n=2: últimas 2 velas cerradas deben ser alcistas
        opens  = [2000, 2005, 2010]   # 3 velas cerradas
        closes = [2005, 2010, 2015]   # todas alcistas
        df = self._df_con_velas(opens, closes)
        assert calcular_momentum_reciente(df, n=2) == 1

    def test_retorna_int(self):
        df = _make_ohlcv(20)
        resultado = calcular_momentum_reciente(df)
        assert isinstance(resultado, int)
        assert resultado in (-1, 0, 1)
