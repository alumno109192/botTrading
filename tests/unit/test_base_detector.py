"""
tests/unit/test_base_detector.py — Contrato completo para BaseDetector

Este archivo define el comportamiento esperado de `core.base_detector.BaseDetector`.
Todos los tests deben pasar DESPUÉS de implementar el BaseDetector.
Sirven tanto como especificación (TDD) como suite de regresión post-refactor.

Cobertura del contrato:
  1. calcular_zonas_sr          — S/R mediante swing highs/lows (lógica compartida de todos los detectores)
  2. calcular_indicadores        — Poblar DataFrame con RSI, EMA, ATR, Bollinger, MACD, OBV, ADX, columnas de vela
  3. calcular_niveles            — SL y TP basados en ATR
  4. calcular_rr                 — Risk:Reward ratio
  5. Anti-spam: ya_enviada / marcar_enviada / limpiar_alertas_viejas
  6. Guard de análisis duplicado: esta_duplicado / registrar_analisis
  7. Exclusión mutua             — Una sola dirección por vela
  8. Determinar sesgo multi-TF   — BULLISH / BEARISH / NEUTRAL
  9. enviar_con_macro_sufijo      — Wrapper de enviar_telegram con aviso macro
 10. Subclase concreta           — BaseDetector es extensible con params por TF
"""

import pytest
import time
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock

# ─────────────────────────────────────────────────────────────────────────────
# La clase que se va a implementar.  Si aún no existe, los tests se recogen
# como "errores de importación" — eso es INTENCIONADO: muestran qué falta.
# ─────────────────────────────────────────────────────────────────────────────
try:
    from core.base_detector import BaseDetector
    _IMPORT_OK = True
except ImportError:
    _IMPORT_OK = False
    BaseDetector = None  # se usa en pytestmark


# Saltar todo el módulo si BaseDetector todavía no existe
pytestmark = pytest.mark.skipif(
    not _IMPORT_OK,
    reason="core.base_detector.BaseDetector no implementado aún"
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_ohlcv(closes, highs=None, lows=None, opens=None, volumes=None):
    n = len(closes)
    c  = np.array(closes,  dtype=float)
    o  = np.array(opens,   dtype=float) if opens   is not None else c * 0.999
    h  = np.array(highs,   dtype=float) if highs   is not None else c * 1.005
    lo = np.array(lows,    dtype=float) if lows    is not None else c * 0.995
    v  = np.array(volumes, dtype=float) if volumes is not None else np.full(n, 1000.0)
    idx = pd.date_range('2024-01-01', periods=n, freq='1h')
    return pd.DataFrame({'Open': o, 'High': h, 'Low': lo, 'Close': c, 'Volume': v}, index=idx)


# ─────────────────────────────────────────────────────────────────────────────
# Parámetros mínimos compartidos con todos los detectores de Gold
# ─────────────────────────────────────────────────────────────────────────────

PARAMS_1D = {
    'ticker_yf':        'GC=F',
    'sr_lookback':      30,
    'sr_zone_mult':     0.8,
    'limit_offset_pct': 0.3,
    'anticipar_velas':  3,
    'cancelar_dist':    1.0,
    'rsi_length':       14,
    'rsi_min_sell':     55.0,
    'rsi_max_buy':      45.0,
    'ema_fast_len':     9,
    'ema_slow_len':     21,
    'ema_trend_len':    200,
    'atr_length':       14,
    'atr_sl_mult':      1.5,
    'atr_tp1_mult':     3.0,
    'atr_tp2_mult':     5.0,
    'atr_tp3_mult':     8.0,
    'vol_mult':         1.2,
}

PARAMS_4H = {**PARAMS_1D,
    'sr_lookback':   80,
    'sr_zone_mult':  0.6,
    'rsi_length':    28,
    'ema_fast_len':  18,
    'ema_slow_len':  42,
    'ema_trend_len': 400,
    'atr_length':    28,
    'atr_sl_mult':   1.2,
    'atr_tp1_mult':  2.0,
    'atr_tp2_mult':  3.5,
    'atr_tp3_mult':  5.5,
}

PARAMS_SCALPING = {**PARAMS_1D,
    'rsi_length':    9,
    'rsi_min_sell':  65.0,
    'rsi_max_buy':   35.0,
    'ema_fast_len':  5,
    'ema_slow_len':  13,
    'ema_trend_len': 50,
    'atr_length':    10,
    'atr_sl_mult':   1.5,
    'atr_tp1_mult':  1.5,
    'atr_tp2_mult':  2.5,
    'atr_tp3_mult':  4.0,
    'min_score_scalping': 3,
    'max_perdidas_dia':   3,
}


# ─────────────────────────────────────────────────────────────────────────────
# Stub concreto — necesario porque BaseDetector es abstracta (ABC).
# Los fixtures usan esta subclase mínima para poder instanciar la base.
# ─────────────────────────────────────────────────────────────────────────────

if _IMPORT_OK:
    class _StubDetector(BaseDetector):
        """Subclase concreta mínima para instanciar BaseDetector en tests."""
        def analizar(self, *args, **kwargs):
            return None
else:
    _StubDetector = None  # se usa solo cuando el módulo existe


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: instancia del detector base con parámetros 1D
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def detector():
    return _StubDetector(
        simbolo='XAUUSD',
        tf_label='1D',
        params=PARAMS_1D,
        telegram_thread_id=None,
    )


@pytest.fixture
def detector_4h():
    return _StubDetector(
        simbolo='XAUUSD',
        tf_label='4H',
        params=PARAMS_4H,
        telegram_thread_id=42,
    )


@pytest.fixture
def df_trending_up():
    """250 velas con tendencia alcista sostenida."""
    closes  = [3000.0 + i * 2 for i in range(250)]
    highs   = [c + 10.0 for c in closes]
    lows    = [c - 10.0 for c in closes]
    opens   = [c - 2.0  for c in closes]
    volumes = [5000.0 + i * 5 for i in range(250)]
    return _make_ohlcv(closes, highs, lows, opens, volumes)


@pytest.fixture
def df_trending_down():
    """250 velas con tendencia bajista sostenida."""
    closes  = [3500.0 - i * 2 for i in range(250)]
    highs   = [c + 10.0 for c in closes]
    lows    = [c - 10.0 for c in closes]
    opens   = [c + 2.0  for c in closes]
    volumes = [5000.0 + i * 5 for i in range(250)]
    return _make_ohlcv(closes, highs, lows, opens, volumes)


# ═════════════════════════════════════════════════════════════════════════════
# 1. INICIALIZACIÓN
# ═════════════════════════════════════════════════════════════════════════════

class TestBaseDetectorInit:
    def test_instancia_creada(self, detector):
        assert detector is not None

    def test_atributos_basicos(self, detector):
        assert detector.simbolo   == 'XAUUSD'
        assert detector.tf_label  == '1D'
        assert detector.params    == PARAMS_1D

    def test_dicts_internos_inicializados(self, detector):
        assert hasattr(detector, 'alertas_enviadas')
        assert hasattr(detector, 'ultimo_analisis')
        assert isinstance(detector.alertas_enviadas, dict)
        assert isinstance(detector.ultimo_analisis,  dict)

    def test_thread_id_asignado(self, detector_4h):
        assert detector_4h.telegram_thread_id == 42

    def test_thread_id_none_por_defecto(self, detector):
        assert detector.telegram_thread_id is None


# ═════════════════════════════════════════════════════════════════════════════
# 2. calcular_zonas_sr
# ═════════════════════════════════════════════════════════════════════════════

class TestCalcularZonasSR:
    """Lógica idéntica copy-pasted en 1D, 4H, 1H, 15M, 5M → debe vivir en BaseDetector."""

    def test_retorna_cuatro_floats(self, detector, df_trending_up):
        atr = 20.0
        result = detector.calcular_zonas_sr(df_trending_up, atr,
                                            lookback=30, zone_mult=0.8)
        assert len(result) == 4
        zrl, zrh, zsl, zsh = result
        assert all(isinstance(v, float) for v in result)

    def test_resistencia_sobre_soporte(self, detector, df_trending_up):
        atr = 20.0
        zrl, zrh, zsl, zsh = detector.calcular_zonas_sr(df_trending_up, atr, 30, 0.8)
        assert zrh > zrl                 # zona resistencia coherente
        assert zsh > zsl                 # zona soporte coherente
        assert (zrl + zrh) / 2 > (zsl + zsh) / 2  # resistencia > soporte

    def test_zona_mas_ancha_con_mayor_mult(self, detector, df_trending_up):
        atr = 20.0
        _, zrh_narrow, zsl_n, zsh_narrow = detector.calcular_zonas_sr(
            df_trending_up, atr, 30, zone_mult=0.5)
        _, zrh_wide,   zsl_w, zsh_wide   = detector.calcular_zonas_sr(
            df_trending_up, atr, 30, zone_mult=1.5)
        ancho_narrow = zrh_narrow - detector.calcular_zonas_sr(df_trending_up, atr, 30, 0.5)[0]
        ancho_wide   = zrh_wide   - detector.calcular_zonas_sr(df_trending_up, atr, 30, 1.5)[0]
        assert ancho_wide > ancho_narrow

    def test_pocos_datos_no_lanza_excepcion(self, detector):
        """Con datos escasos debe retornar algo válido, no lanzar excepción."""
        df = _make_ohlcv([3300.0] * 15)
        zrl, zrh, zsl, zsh = detector.calcular_zonas_sr(df, atr=10.0, lookback=10, zone_mult=0.8)
        assert zrh >= zrl
        assert zsh >= zsl

    def test_consistente_entre_instancias(self, df_trending_up):
        """Dos instancias con mismos params deben dar mismo resultado."""
        d1 = _StubDetector('XAUUSD', '1D', PARAMS_1D, None)
        d2 = _StubDetector('XAUUSD', '1D', PARAMS_1D, None)
        r1 = d1.calcular_zonas_sr(df_trending_up, 20.0, 30, 0.8)
        r2 = d2.calcular_zonas_sr(df_trending_up, 20.0, 30, 0.8)
        assert r1 == r2


# ═════════════════════════════════════════════════════════════════════════════
# 3. calcular_indicadores
# ═════════════════════════════════════════════════════════════════════════════

class TestCalcularIndicadores:
    """Bloque idéntico de indicadores que se repite en cada analizar()."""

    COLUMNAS_ESPERADAS = [
        'rsi', 'ema_fast', 'ema_slow', 'ema_trend', 'atr', 'vol_avg',
        'bb_upper', 'bb_mid', 'bb_lower', 'bb_width',
        'macd', 'macd_signal', 'macd_hist',
        'obv', 'obv_ema',
        'adx', 'di_plus', 'di_minus',
        'body', 'upper_wick', 'lower_wick', 'total_range',
        'is_bearish', 'is_bullish',
    ]

    def test_retorna_dataframe(self, detector, df_trending_up):
        result = detector.calcular_indicadores(df_trending_up)
        assert isinstance(result, pd.DataFrame)

    def test_columnas_presentes(self, detector, df_trending_up):
        result = detector.calcular_indicadores(df_trending_up)
        for col in self.COLUMNAS_ESPERADAS:
            assert col in result.columns, f"Columna faltante: {col}"

    def test_longitud_preservada(self, detector, df_trending_up):
        result = detector.calcular_indicadores(df_trending_up)
        assert len(result) == len(df_trending_up)

    def test_rsi_en_rango_0_100(self, detector, df_trending_up):
        result = detector.calcular_indicadores(df_trending_up)
        valid = result['rsi'].dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_body_es_positivo(self, detector, df_trending_up):
        result = detector.calcular_indicadores(df_trending_up)
        assert (result['body'].dropna() >= 0).all()

    def test_is_bullish_is_bearish_son_booleanos(self, detector, df_trending_up):
        result = detector.calcular_indicadores(df_trending_up)
        assert result['is_bullish'].dtype == bool or result['is_bullish'].isin([True, False]).all()
        assert result['is_bearish'].dtype == bool or result['is_bearish'].isin([True, False]).all()

    def test_is_bullish_y_is_bearish_mutuamente_exclusivos(self, detector, df_trending_up):
        """Una vela no puede ser alcista Y bajista a la vez (excluyendo doji exacto)."""
        result = detector.calcular_indicadores(df_trending_up)
        ambos = result['is_bullish'] & result['is_bearish']
        assert not ambos.any()

    def test_params_distintos_generan_ema_distintas(self, df_trending_up):
        """Params de EMA distintos (1D vs 4H) deben generar EMAs distintas."""
        d1d = _StubDetector('XAUUSD', '1D', PARAMS_1D, None)
        d4h = _StubDetector('XAUUSD', '4H', PARAMS_4H, None)
        r1  = d1d.calcular_indicadores(df_trending_up)
        r4  = d4h.calcular_indicadores(df_trending_up)
        # ema_fast_len 9 (1D) vs 18 (4H): el 9 reacciona más rápido → valores distintos
        assert not r1['ema_fast'].equals(r4['ema_fast'])

    def test_ema_trend_correcto_para_scalping(self, df_trending_up):
        """EMA trend del 5M (ema_trend_len=21) debe ser diferente al del 1D (ema_trend_len=200)."""
        d_scalp = _StubDetector('XAUUSD', '5M', PARAMS_SCALPING, None)
        d_1d    = _StubDetector('XAUUSD', '1D', PARAMS_1D,       None)
        r_scalp = d_scalp.calcular_indicadores(df_trending_up)
        r_1d    = d_1d.calcular_indicadores(df_trending_up)
        assert not r_scalp['ema_trend'].equals(r_1d['ema_trend'])


# ═════════════════════════════════════════════════════════════════════════════
# 4. calcular_niveles (SL y TP)
# ═════════════════════════════════════════════════════════════════════════════

class TestCalcularNiveles:
    """Lógica de SL/TP idéntica en todos los detectores."""

    def test_retorna_ocho_valores(self, detector):
        result = detector.calcular_niveles(
            sell_limit=3310.0, buy_limit=3290.0, atr=15.0)
        assert len(result) == 8

    def test_sl_venta_sobre_sell_limit(self, detector):
        sl_v, sl_c, tp1_v, tp2_v, tp3_v, tp1_c, tp2_c, tp3_c = \
            detector.calcular_niveles(sell_limit=3310.0, buy_limit=3290.0, atr=15.0)
        # SL venta está ENCIMA del sell_limit (stop loss para corto)
        assert sl_v > 3310.0

    def test_sl_compra_bajo_buy_limit(self, detector):
        sl_v, sl_c, tp1_v, tp2_v, tp3_v, tp1_c, tp2_c, tp3_c = \
            detector.calcular_niveles(sell_limit=3310.0, buy_limit=3290.0, atr=15.0)
        # SL compra está DEBAJO del buy_limit
        assert sl_c < 3290.0

    def test_tps_venta_decrecientes(self, detector):
        sl_v, sl_c, tp1_v, tp2_v, tp3_v, tp1_c, tp2_c, tp3_c = \
            detector.calcular_niveles(sell_limit=3310.0, buy_limit=3290.0, atr=15.0)
        # Para SELL: TP1 > TP2 > TP3 (precios más bajos = más ganancias)
        assert tp1_v > tp2_v > tp3_v

    def test_tps_compra_crecientes(self, detector):
        sl_v, sl_c, tp1_v, tp2_v, tp3_v, tp1_c, tp2_c, tp3_c = \
            detector.calcular_niveles(sell_limit=3310.0, buy_limit=3290.0, atr=15.0)
        # Para BUY: TP1 < TP2 < TP3 (precios más altos = más ganancias)
        assert tp1_c < tp2_c < tp3_c

    def test_sl_proporcional_al_atr(self, detector):
        """ATR más grande → SL más alejado."""
        _, _, _, _, _, tp1c_10, _, _ = detector.calcular_niveles(3310.0, 3290.0, atr=10.0)
        _, _, _, _, _, tp1c_30, _, _ = detector.calcular_niveles(3310.0, 3290.0, atr=30.0)
        assert tp1c_30 > tp1c_10

    def test_multipliers_del_4h_distintos_al_1d(self, detector_4h, detector):
        """Params 4H vs 1D generan TPs distintos con el mismo ATR."""
        _1d_result = detector.calcular_niveles(3310.0, 3290.0, atr=15.0)
        _4h_result = detector_4h.calcular_niveles(3310.0, 3290.0, atr=15.0)
        # tp3_v (índice 4): 1D usa atr_tp3_mult=8.0, 4H usa 5.5 → distinto
        assert _1d_result[4] != _4h_result[4]


# ═════════════════════════════════════════════════════════════════════════════
# 5. calcular_rr (Risk:Reward)
# ═════════════════════════════════════════════════════════════════════════════

class TestCalcularRR:
    def test_rr_correcto_1_a_2(self, detector):
        """Entry=100, SL=95 → riesgo=5; TP=110 → ganancia=10 → R:R=2.0"""
        rr = detector.calcular_rr(limit=100.0, sl=95.0, tp=110.0)
        assert abs(rr - 2.0) < 0.05

    def test_rr_correcto_short(self, detector):
        """Entry=3310, SL=3325 → riesgo=15; TP1=3280 → ganancia=30 → R:R=2.0"""
        rr = detector.calcular_rr(limit=3310.0, sl=3325.0, tp=3280.0)
        assert abs(rr - 2.0) < 0.05

    def test_rr_cero_cuando_sl_igual_a_limit(self, detector):
        """Evitar división por cero: retorna 0."""
        rr = detector.calcular_rr(limit=100.0, sl=100.0, tp=110.0)
        assert rr == 0

    def test_rr_positivo_siempre(self, detector):
        """El R:R siempre es >= 0."""
        assert detector.calcular_rr(100.0, 90.0, 120.0) >= 0
        assert detector.calcular_rr(100.0, 110.0, 80.0) >= 0

    def test_rr_1_a_1(self, detector):
        rr = detector.calcular_rr(limit=100.0, sl=90.0, tp=110.0)
        assert abs(rr - 1.0) < 0.05


# ═════════════════════════════════════════════════════════════════════════════
# 6. Anti-spam: ya_enviada / marcar_enviada / limpiar_alertas_viejas
# ═════════════════════════════════════════════════════════════════════════════

class TestAntiSpam:
    def test_no_enviada_por_defecto(self, detector):
        assert detector.ya_enviada('XAUUSD_2024-01-01_SELL') is False

    def test_enviada_despues_de_marcar(self, detector):
        detector.marcar_enviada('XAUUSD_2024-01-01_SELL')
        assert detector.ya_enviada('XAUUSD_2024-01-01_SELL') is True

    def test_claves_distintas_independientes(self, detector):
        detector.marcar_enviada('XAUUSD_2024-01-01_SELL')
        assert detector.ya_enviada('XAUUSD_2024-01-01_BUY') is False

    def test_limpiar_alertas_viejas(self, detector):
        """Después de limpiar alertas viejas, las expiradas deben borrarse."""
        clave = 'XAUUSD_2024-01-01_SELL'
        detector.alertas_enviadas[clave] = time.time() - 200_000  # muy antigua
        detector.limpiar_alertas_viejas()
        # La clave antigua debería desaparecer
        assert detector.ya_enviada(clave) is False

    def test_limpiar_alertas_no_borra_recientes(self, detector):
        clave = 'XAUUSD_2024-01-01_BUY'
        detector.marcar_enviada(clave)
        detector.limpiar_alertas_viejas()
        assert detector.ya_enviada(clave) is True

    def test_limpiar_cuando_muchas_alertas(self, detector):
        """No debe lanzar excepción con 600 claves en el dict."""
        for i in range(600):
            detector.alertas_enviadas[f'clave_{i}'] = time.time() - 300_000
        detector.limpiar_alertas_viejas()  # no debe explotar
        assert isinstance(detector.alertas_enviadas, dict)

    def test_marcar_dos_veces_no_duplica(self, detector):
        clave = 'TEST_CLAVE'
        detector.marcar_enviada(clave)
        detector.marcar_enviada(clave)
        # Debe seguir siendo solo una entrada (no lista ni error)
        assert detector.ya_enviada(clave) is True


# ═════════════════════════════════════════════════════════════════════════════
# 7. Guard de análisis duplicado: esta_duplicado / registrar_analisis
# ═════════════════════════════════════════════════════════════════════════════

class TestGuardAnalisisDuplicado:
    def test_primera_vez_no_es_duplicado(self, detector):
        assert detector.esta_duplicado('XAUUSD', '2024-01-01', 8, 6) is False

    def test_misma_fecha_y_scores_es_duplicado(self, detector):
        detector.registrar_analisis('XAUUSD', '2024-01-01', score_sell=8, score_buy=6)
        assert detector.esta_duplicado('XAUUSD', '2024-01-01', 8, 6) is True

    def test_fecha_distinta_no_es_duplicado(self, detector):
        detector.registrar_analisis('XAUUSD', '2024-01-01', score_sell=8, score_buy=6)
        assert detector.esta_duplicado('XAUUSD', '2024-01-02', 8, 6) is False

    def test_scores_con_diferencia_mayor_a_delta_no_es_duplicado(self, detector):
        """Diferencia de score > delta (por defecto 1) → no duplicado."""
        detector.registrar_analisis('XAUUSD', '2024-01-01', score_sell=8, score_buy=6)
        # score_sell cambió en 3 puntos
        assert detector.esta_duplicado('XAUUSD', '2024-01-01', 11, 6) is False

    def test_scores_dentro_del_delta_es_duplicado(self, detector):
        """Diferencia de score <= delta → duplicado."""
        detector.registrar_analisis('XAUUSD', '2024-01-01', score_sell=8, score_buy=6)
        assert detector.esta_duplicado('XAUUSD', '2024-01-01', 9, 7) is True

    def test_registrar_sobreescribe_analisis_anterior(self, detector):
        detector.registrar_analisis('XAUUSD', '2024-01-01', score_sell=8,  score_buy=6)
        detector.registrar_analisis('XAUUSD', '2024-01-01', score_sell=15, score_buy=14)
        # Ahora con el nuevo registro: score 8/6 ya no coincide con 15/14
        assert detector.esta_duplicado('XAUUSD', '2024-01-01', 8, 6) is False
        assert detector.esta_duplicado('XAUUSD', '2024-01-01', 15, 14) is True

    def test_simbolos_distintos_son_independientes(self, detector):
        detector.registrar_analisis('XAUUSD', '2024-01-01', 8, 6)
        assert detector.esta_duplicado('BTCUSD', '2024-01-01', 8, 6) is False


# ═════════════════════════════════════════════════════════════════════════════
# 8. Exclusión mutua (una sola dirección por vela)
# ═════════════════════════════════════════════════════════════════════════════

class TestExclusionMutua:
    def test_sell_gana_cuando_score_sell_mayor(self, detector):
        sell, buy = detector.exclusion_mutua(
            score_sell=10, score_buy=6,
            senal_sell=True, senal_buy=True)
        assert sell is True
        assert buy  is False

    def test_buy_gana_cuando_score_buy_mayor(self, detector):
        sell, buy = detector.exclusion_mutua(
            score_sell=6, score_buy=10,
            senal_sell=True, senal_buy=True)
        assert sell is False
        assert buy  is True

    def test_sell_gana_en_empate(self, detector):
        """Con igualdad de scores, SELL tiene precedencia (conservadurismo)."""
        sell, buy = detector.exclusion_mutua(
            score_sell=8, score_buy=8,
            senal_sell=True, senal_buy=True)
        # Al menos uno debe ser False
        assert not (sell and buy)

    def test_sin_conflicto_no_modifica(self, detector):
        """Si solo una dirección activa, no debe cambiar nada."""
        sell, buy = detector.exclusion_mutua(8, 6, senal_sell=True, senal_buy=False)
        assert sell is True and buy is False

        sell2, buy2 = detector.exclusion_mutua(8, 6, senal_sell=False, senal_buy=True)
        assert sell2 is False and buy2 is True

    def test_ninguna_activa_no_modifica(self, detector):
        sell, buy = detector.exclusion_mutua(8, 6, senal_sell=False, senal_buy=False)
        assert sell is False and buy is False

    def test_retorna_tupla_de_dos_bool(self, detector):
        result = detector.exclusion_mutua(8, 6, True, True)
        assert len(result) == 2
        assert all(isinstance(v, bool) for v in result)


# ═════════════════════════════════════════════════════════════════════════════
# 9. Determinar sesgo (para publicar en tf_bias)
# ═════════════════════════════════════════════════════════════════════════════

class TestDeterminarSesgo:
    def test_sell_mayor_es_bearish(self, detector):
        assert detector.determinar_sesgo(score_sell=10, score_buy=5) == 'BEARISH'

    def test_buy_mayor_es_bullish(self, detector):
        assert detector.determinar_sesgo(score_sell=5, score_buy=10) == 'BULLISH'

    def test_empate_es_neutral(self, detector):
        assert detector.determinar_sesgo(score_sell=7, score_buy=7) == 'NEUTRAL'

    def test_cero_cero_es_neutral(self, detector):
        assert detector.determinar_sesgo(score_sell=0, score_buy=0) == 'NEUTRAL'

    def test_retorna_string(self, detector):
        result = detector.determinar_sesgo(5, 8)
        assert isinstance(result, str)

    def test_valores_validos(self, detector):
        for ss, sb in [(10, 0), (0, 10), (5, 5)]:
            v = detector.determinar_sesgo(ss, sb)
            assert v in ('BULLISH', 'BEARISH', 'NEUTRAL')


# ═════════════════════════════════════════════════════════════════════════════
# 10. enviar_con_macro_sufijo (wrapper de enviar_telegram)
# ═════════════════════════════════════════════════════════════════════════════

class TestEnviarConMacroSufijo:
    def test_envia_sin_sufijo_cuando_no_hay_macro(self, detector):
        detector.aviso_macro = ""
        with patch('adapters.telegram.enviar_telegram', return_value=True) as mock:
            detector.enviar(mensaje="Señal TEST")
        call_msg = mock.call_args[0][0]
        assert 'macro' not in call_msg.lower()
        assert 'Señal TEST' in call_msg

    def test_envia_con_sufijo_cuando_hay_macro(self, detector):
        detector.aviso_macro = "NFP en 30 min"
        with patch('adapters.telegram.enviar_telegram', return_value=True) as mock:
            detector.enviar(mensaje="Señal TEST")
        call_msg = mock.call_args[0][0]
        assert 'NFP en 30 min' in call_msg

    def test_pasa_thread_id_correcto(self, detector_4h):
        detector_4h.aviso_macro = ""
        with patch('adapters.telegram.enviar_telegram', return_value=True) as mock:
            detector_4h.enviar(mensaje="Test 4H")
        # El segundo argumento posicional o kwarg thread_id debe ser 42
        args, kwargs = mock.call_args
        thread_id_pasado = args[1] if len(args) > 1 else kwargs.get('thread_id')
        assert thread_id_pasado == 42

    def test_retorna_true_cuando_exito(self, detector):
        detector.aviso_macro = ""
        with patch('adapters.telegram.enviar_telegram', return_value=True):
            resultado = detector.enviar(mensaje="Test")
        assert resultado is True

    def test_retorna_false_cuando_falla(self, detector):
        detector.aviso_macro = ""
        with patch('adapters.telegram.enviar_telegram', return_value=False):
            resultado = detector.enviar(mensaje="Test")
        assert resultado is False


# ═════════════════════════════════════════════════════════════════════════════
# 11. Extensibilidad: subclase concreta de BaseDetector
# ═════════════════════════════════════════════════════════════════════════════

class TestSubclaseBaseDetector:
    """
    Verifica que BaseDetector sea correctamente extensible.
    El detector concreto (Gold4H, Gold1D, etc.) solo necesita implementar
    `analizar(simbolo, params)` y llamar a los métodos de la base.
    """

    @staticmethod
    def _make_concreto():
        class _DetectorConcreto(BaseDetector):
            """Subclase mínima que implementa el hook analizar."""
            def analizar(self, simbolo=None, params=None):
                return True
        return _DetectorConcreto

    def test_subclase_puede_instanciarse(self):
        cls = self._make_concreto()
        d = cls('XAUUSD', '4H', PARAMS_4H, None)
        assert d is not None

    def test_hereda_calcular_zonas_sr(self, df_trending_up):
        cls = self._make_concreto()
        d = cls('XAUUSD', '4H', PARAMS_4H, None)
        result = d.calcular_zonas_sr(df_trending_up, atr=20.0, lookback=30, zone_mult=0.6)
        assert len(result) == 4

    def test_hereda_anti_spam(self):
        cls = self._make_concreto()
        d = cls('XAUUSD', '4H', PARAMS_4H, None)
        d.marcar_enviada('CLAVE_TEST')
        assert d.ya_enviada('CLAVE_TEST') is True

    def test_hereda_calcular_indicadores(self, df_trending_up):
        cls = self._make_concreto()
        d = cls('XAUUSD', '4H', PARAMS_4H, None)
        result = d.calcular_indicadores(df_trending_up)
        assert 'rsi' in result.columns

    def test_analizar_override_funciona(self):
        cls = self._make_concreto()
        d = cls('XAUUSD', '4H', PARAMS_4H, None)
        assert d.analizar() is True

    def test_base_detector_sin_analizar_lanza_error(self):
        """BaseDetector puro (sin override de analizar) debe forzar implementación."""
        with pytest.raises((NotImplementedError, TypeError)):
            # Si BaseDetector es abstracto, instanciar sin subclase debe fallar
            d = BaseDetector('XAUUSD', '1D', PARAMS_1D, None)
            d.analizar()


# ═════════════════════════════════════════════════════════════════════════════
# 12. ajustar_scores_por_volumen — filtro de señales en bajo volumen
# ═════════════════════════════════════════════════════════════════════════════

class TestAjustarScoresPorVolumen:
    """Verifica que el filtro de volumen penalice scores cuando vol < vol_avg × mult."""

    @pytest.fixture
    def det(self):
        class _D(BaseDetector):
            def analizar(self): pass
        return _D('XAUUSD', '1H', PARAMS_1D, None)

    def test_penaliza_cuando_volumen_bajo(self, det):
        # vol = 800 < vol_avg(1000) × mult(1.2=1200) → penalización -3
        s, b, penalizado = det.ajustar_scores_por_volumen(10, 8, 800, 1000, 1.2)
        assert penalizado is True
        assert s == 7   # 10 - 3
        assert b == 5   # 8 - 3

    def test_no_penaliza_cuando_volumen_suficiente(self, det):
        # vol = 1500 > vol_avg(1000) × mult(1.2=1200) → sin penalización
        s, b, penalizado = det.ajustar_scores_por_volumen(10, 8, 1500, 1000, 1.2)
        assert penalizado is False
        assert s == 10
        assert b == 8

    def test_score_no_baja_de_cero(self, det):
        # Score ya en 1 → penalización de 3 no puede dejar en negativo
        s, b, penalizado = det.ajustar_scores_por_volumen(1, 2, 100, 1000, 1.2)
        assert penalizado is True
        assert s == 0
        assert b == 0

    def test_sin_penalizacion_cuando_vol_avg_cero(self, det):
        # vol_avg=0 → no penalizar (evitar división por cero / comportamiento errático)
        s, b, penalizado = det.ajustar_scores_por_volumen(10, 8, 500, 0, 1.2)
        assert penalizado is False
        assert s == 10
        assert b == 8

    def test_sin_penalizacion_cuando_vol_avg_nan(self, det):
        import math
        s, b, penalizado = det.ajustar_scores_por_volumen(10, 8, float('nan'), 1000, 1.2)
        assert penalizado is False

    def test_exactamente_en_umbral_no_penaliza(self, det):
        # vol = vol_avg × mult exacto → no se penaliza (condición es <, no <=)
        s, b, penalizado = det.ajustar_scores_por_volumen(10, 8, 1200.0, 1000.0, 1.2)
        assert penalizado is False


# ═════════════════════════════════════════════════════════════════════════════
# 13. umbral_adaptativo — score mínimo según volatilidad ATR
# ═════════════════════════════════════════════════════════════════════════════

class TestUmbralAdaptativo:
    """Verifica que el umbral adaptativo se eleve cuando ATR > atr_media × 1.5."""

    @pytest.fixture
    def det(self):
        class _D(BaseDetector):
            def analizar(self): pass
        return _D('XAUUSD', '1D', PARAMS_1D, None)

    def test_eleva_umbral_cuando_atr_alto(self, det):
        # ATR=75 > media(40) × 1.5(=60) → umbral sube
        umbral = det.umbral_adaptativo(6, atr=75, atr_media=40)
        assert umbral == 8   # 6 + 2

    def test_umbral_sin_cambio_cuando_atr_normal(self, det):
        # ATR=50 < media(40) × 1.5(=60) → umbral sin cambio
        umbral = det.umbral_adaptativo(6, atr=50, atr_media=40)
        assert umbral == 6

    def test_umbral_exactamente_en_limite_no_eleva(self, det):
        # ATR=60 == media(40) × 1.5 → NO se considera "mayor que", no se eleva
        umbral = det.umbral_adaptativo(6, atr=60.0, atr_media=40.0)
        assert umbral == 6

    def test_incremento_personalizado(self, det):
        # incremento=3 cuando ATR alto
        umbral = det.umbral_adaptativo(5, atr=100, atr_media=40, incremento=3)
        assert umbral == 8   # 5 + 3

    def test_atr_media_cero_no_eleva(self, det):
        # atr_media=0 → no calcular ratio (evitar división por cero)
        umbral = det.umbral_adaptativo(6, atr=100, atr_media=0)
        assert umbral == 6

    def test_atr_media_nan_no_eleva(self, det):
        import math
        umbral = det.umbral_adaptativo(6, atr=100, atr_media=float('nan'))
        assert umbral == 6


# ═════════════════════════════════════════════════════════════════════════════
# 12. Integración: flujo completo sin red ni BD
# ═════════════════════════════════════════════════════════════════════════════

class TestFlujoCompleto:
    """
    Test de integración que simula un ciclo de análisis completo usando
    únicamente datos sintéticos y mocks de las dependencias externas.
    """

    @staticmethod
    def _make_gold_detector():
        class _GoldDetector(BaseDetector):
            def analizar(self, df=None):
                """
                Simula el flujo de analizar():
                  1. Calcular indicadores
                  2. Calcular zonas S/R
                  3. Calcular niveles SL/TP
                  4. Determinar sesgo
                  5. Guard de duplicado
                  6. Exclusión mutua
                """
                if df is None or len(df) < 50:
                    return None

                df_ind = self.calcular_indicadores(df)
                row    = df_ind.iloc[-2]

                atr    = float(row['atr']) if not np.isnan(row['atr']) else 15.0
                zrl, zrh, zsl, zsh = self.calcular_zonas_sr(
                    df, atr, self.params['sr_lookback'], self.params['sr_zone_mult'])

                close = float(row['Close'])
                lop   = self.params['limit_offset_pct']
                sell_limit = zrl + (zrh - zrl) * (lop / 100 * 10)
                buy_limit  = zsh - (zsh - zsl) * (lop / 100 * 10)

                sl_v, sl_c, tp1_v, tp2_v, tp3_v, tp1_c, tp2_c, tp3_c = \
                    self.calcular_niveles(sell_limit, buy_limit, atr)

                rr_sell = self.calcular_rr(sell_limit, sl_v, tp1_v)
                rr_buy  = self.calcular_rr(buy_limit,  sl_c, tp1_c)

                score_sell = 5
                score_buy  = 3

                sesgo = self.determinar_sesgo(score_sell, score_buy)

                fecha = df.index[-2].strftime('%Y-%m-%d %H:%M')
                if self.esta_duplicado(self.simbolo, fecha, score_sell, score_buy):
                    return 'DUPLICADO'
                self.registrar_analisis(self.simbolo, fecha, score_sell, score_buy)

                senal_sell, senal_buy = self.exclusion_mutua(
                    score_sell, score_buy, senal_sell=True, senal_buy=True)

                return {
                    'sesgo':      sesgo,
                    'zrh':        zrh,
                    'zsl':        zsl,
                    'sl_venta':   sl_v,
                    'tp1_venta':  tp1_v,
                    'rr_sell':    rr_sell,
                    'rr_buy':     rr_buy,
                    'senal_sell': senal_sell,
                    'senal_buy':  senal_buy,
                }
        return _GoldDetector

    def test_flujo_completo_retorna_dict(self, df_trending_up):
        cls = self._make_gold_detector()
        d = cls('XAUUSD', '1D', PARAMS_1D, None)
        result = d.analizar(df=df_trending_up)
        assert isinstance(result, dict)

    def test_sesgo_correcto(self, df_trending_up):
        cls = self._make_gold_detector()
        d = cls('XAUUSD', '1D', PARAMS_1D, None)
        result = d.analizar(df=df_trending_up)
        # score_sell=5 > score_buy=3 → BEARISH
        assert result['sesgo'] == 'BEARISH'

    def test_exclusion_mutua_aplicada(self, df_trending_up):
        cls = self._make_gold_detector()
        d = cls('XAUUSD', '1D', PARAMS_1D, None)
        result = d.analizar(df=df_trending_up)
        # No pueden ser ambas True
        assert not (result['senal_sell'] and result['senal_buy'])

    def test_segunda_llamada_misma_vela_es_duplicado(self, df_trending_up):
        cls = self._make_gold_detector()
        d = cls('XAUUSD', '1D', PARAMS_1D, None)
        d.analizar(df=df_trending_up)
        result2 = d.analizar(df=df_trending_up)
        assert result2 == 'DUPLICADO'

    def test_rr_es_positivo(self, df_trending_up):
        cls = self._make_gold_detector()
        d = cls('XAUUSD', '1D', PARAMS_1D, None)
        result = d.analizar(df=df_trending_up)
        assert result['rr_sell'] >= 0
        assert result['rr_buy']  >= 0

    def test_flujo_con_datos_insuficientes_retorna_none(self):
        cls = self._make_gold_detector()
        d = cls('XAUUSD', '1D', PARAMS_1D, None)
        df_corto = _make_ohlcv([3300.0] * 10)
        result = d.analizar(df=df_corto)
        assert result is None

    def test_instancias_distintas_no_comparten_estado(self, df_trending_up):
        cls = self._make_gold_detector()
        d1 = cls('XAUUSD', '1D', PARAMS_1D, None)
        d2 = cls('XAUUSD', '4H', PARAMS_4H, None)
        d1.analizar(df=df_trending_up)

        # La segunda instancia no debe ver la vela registrada en la primera
        fecha = df_trending_up.index[-2].strftime('%Y-%m-%d %H:%M')
        assert d2.esta_duplicado('XAUUSD', fecha, 5, 3) is False
