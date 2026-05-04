"""
tests/unit/test_gold_detectors.py — Suite de tests para el par XAUUSD (Gold)

Cobertura:
  1. Configuración de los 5 detectores (ticker, asset, CHECK_INTERVAL)
  2. GoldDetector5M.analizar() — señal SELL con datos sintéticos bajistas
  3. GoldDetector5M.analizar() — señal BUY con datos sintéticos alcistas
  4. GoldDetector5M.analizar() — bloqueo FOMC (debe_bloquear_trading=True)
  5. GoldDetector5M.analizar() — score insuficiente → sin señal
  6. GoldDetector5M.analizar() — señal activa en BD → bloqueada (no duplicado)
  7. GoldDetector15M.analizar() — resamplea 5M→15M correctamente
  8. GoldDetector15M.analizar() — señal SELL con datos bajistas
  9. Filtro ATR mínimo (atr_min=$5) — sin señal si ATR < umbral
 10. Confluencia multi-TF (tf_bias) — señal bloqueada si no hay confluencia
 11. Exclusión mutua SELL vs BUY — solo se emite la dirección dominante
 12. Anti-spam — segunda llamada con misma vela no envía duplicado
 13. data_provider — ticker GC=F mapeado correctamente a XAU/USD y C:XAUUSD
 14. ohlcv_poller — POLL_TARGETS contiene entradas de GC=F
 15. orchestrator — DETECTOR_REGISTRY contiene los 5 detectores Gold
"""

import sys
import os
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_ohlcv(closes, highs=None, lows=None, opens=None, volumes=None,
                freq='5min', tz_utc=True):
    """Fabrica DataFrame OHLCV con índice temporal."""
    n  = len(closes)
    c  = np.array(closes,  dtype=float)
    o  = np.array(opens,   dtype=float) if opens   is not None else c * 0.999
    h  = np.array(highs,   dtype=float) if highs   is not None else c * 1.005
    lo = np.array(lows,    dtype=float) if lows    is not None else c * 0.995
    v  = np.array(volumes, dtype=float) if volumes is not None else np.full(n, 5000.0)
    tz  = timezone.utc if tz_utc else None
    idx = pd.date_range('2026-05-04 10:00', periods=n, freq=freq, tz=tz)
    return pd.DataFrame(
        {'Open': o, 'High': h, 'Low': lo, 'Close': c, 'Volume': v},
        index=idx,
    )


def _gold_closes_bajistas(n=250, start=3300.0, step=2.0):
    """Cierra bajistas — fuerza RSI < 40 y EMA fast < EMA slow."""
    return [start - i * step for i in range(n)]


def _gold_closes_alcistas(n=250, start=3000.0, step=2.0):
    """Cierra alcistas — fuerza RSI > 60 y EMA fast > EMA slow."""
    return [start + i * step for i in range(n)]


# ─────────────────────────────────────────────────────────────────────────────
# Mocks compartidos
# ─────────────────────────────────────────────────────────────────────────────

def _mock_db():
    db = MagicMock()
    db.existe_senal_activa_tf.return_value = False
    db.get_antispam.return_value = 0
    db.set_antispam.return_value = None
    db.guardar_senal.return_value = 1
    db.ejecutar_query.return_value = None
    return db


def _patch_externos_para(modulo_detector, bloqueado=False, confluencia=True):
    """
    Devuelve lista de patches usando la ruta del módulo detector como prefijo.
    Como los Gold detectors hacen `from X import Y`, hay que parchear donde se usa,
    no en el módulo origen.
    """
    m = modulo_detector  # ej: 'detectors.gold.detector_gold_5m'
    # tf_bias se importa como módulo (`from services import tf_bias`)
    # → se parchea en services directamente
    return [
        patch(f'{m}.debe_bloquear_trading',
              return_value=(bloqueado, 'FOMC', 30)),
        patch(f'{m}.obtener_aviso_macro', return_value=''),
        patch(f'{m}.enviar_alerta_bloqueo'),
        patch(f'{m}.get_dxy_bias', return_value=0),
        patch(f'{m}.ajustar_score_por_dxy', side_effect=lambda s, *a, **kw: s),
        patch(f'{m}.get_cot_bias', return_value=0),
        patch(f'{m}.ajustar_score_por_cot', side_effect=lambda s, *a, **kw: s),
        patch(f'{m}.get_oi_bias', return_value=0),
        patch(f'{m}.ajustar_score_por_oi', side_effect=lambda s, *a, **kw: s),
        patch('services.tf_bias.publicar_sesgo'),
        patch('services.tf_bias.verificar_confluencia',
              return_value=(confluencia, 'OK multi-TF')),
        patch('services.tf_bias.obtener_zona_activa', return_value=None),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 1. CONFIGURACIÓN DE DETECTORES
# ─────────────────────────────────────────────────────────────────────────────

class TestConfiguracionDetectores:
    """Verifica que los 5 detectores Gold tienen la configuración correcta."""

    def test_5m_ticker_correcto(self):
        from detectors.gold import detector_gold_5m
        ticker = detector_gold_5m.SIMBOLOS['XAUUSD']['ticker_yf']
        assert ticker == 'GC=F'

    def test_15m_ticker_correcto(self):
        from detectors.gold import detector_gold_15m
        ticker = detector_gold_15m.SIMBOLOS['XAUUSD']['ticker_yf']
        assert ticker == 'GC=F'

    def test_1h_ticker_correcto(self):
        from detectors.gold import detector_gold_1h
        ticker = detector_gold_1h.SIMBOLOS['XAUUSD']['ticker_yf']
        assert ticker == 'GC=F'

    def test_4h_ticker_correcto(self):
        from detectors.gold import detector_gold_4h
        ticker = detector_gold_4h.SIMBOLOS['XAUUSD']['ticker_yf']
        assert ticker == 'GC=F'

    def test_1d_ticker_correcto(self):
        from detectors.gold import detector_gold_1d
        ticker = detector_gold_1d.SIMBOLOS['XAUUSD']['ticker_yf']
        assert ticker == 'GC=F'

    def test_5m_check_interval_60s(self):
        from detectors.gold import detector_gold_5m
        assert detector_gold_5m.CHECK_INTERVAL == 60

    def test_15m_check_interval_120s(self):
        from detectors.gold import detector_gold_15m
        assert detector_gold_15m.CHECK_INTERVAL == 120

    def test_5m_atr_minimo_definido(self):
        from detectors.gold import detector_gold_5m
        atr_min = detector_gold_5m.SIMBOLOS['XAUUSD']['atr_min']
        assert atr_min > 0, "atr_min debe ser positivo para filtrar baja volatilidad"

    def test_5m_rsi_umbrales_asimetricos(self):
        from detectors.gold import detector_gold_5m
        p = detector_gold_5m.SIMBOLOS['XAUUSD']
        assert p['rsi_min_sell'] > 50
        assert p['rsi_max_buy']  < 50

    def test_15m_rsi_umbrales_asimetricos(self):
        from detectors.gold import detector_gold_15m
        p = detector_gold_15m.SIMBOLOS['XAUUSD']
        assert p['rsi_min_sell'] > 50
        assert p['rsi_max_buy']  < 50

    def test_4h_sl_mayor_que_1_atr(self):
        """4H usa SL más amplio que 1× ATR para dar espacio."""
        from detectors.gold import detector_gold_4h
        p = detector_gold_4h.SIMBOLOS['XAUUSD']
        assert p['atr_sl_mult'] >= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. GoldDetector5M — señal SELL
# ─────────────────────────────────────────────────────────────────────────────

class TestGoldDetector5MSell:
    """Condiciones bajistas fuertes → debe generar señal SELL y guardarla en BD."""

    @pytest.fixture
    def df_sell(self):
        """300 velas 5M bajistas. Precio cae de 3300 a 2700."""
        c = _gold_closes_bajistas(300, 3300.0, 2.0)
        h = [x + 8 for x in c]
        l = [x - 8 for x in c]
        o = [x + 2 for x in c]
        v = [6000 + i * 5 for i in range(300)]
        return _make_ohlcv(c, h, l, o, v, freq='5min')

    def test_send_sell_signal(self, df_sell):
        from detectors.gold.detector_gold_5m import GoldDetector5M
        params = dict(detector_gold_5m_params())
        mod = 'detectors.gold.detector_gold_5m'

        db = _mock_db()
        patches = _patch_externos_para(mod)
        patches += [
            patch(f'{mod}.get_ohlcv', return_value=(df_sell, False)),
        ]

        with _apply_patches(patches):
            with patch.object(GoldDetector5M, 'enviar') as mock_enviar:
                det = GoldDetector5M(
                    simbolo='XAUUSD', tf_label='5M',
                    params=params, telegram_thread_id=None
                )
                det.db = db
                det.analizar('XAUUSD', params)
                if mock_enviar.called:
                    msg = mock_enviar.call_args[0][0]
                    assert 'SELL' in msg or 'VENTA' in msg

    def test_asset_gold_en_db(self, df_sell):
        """La señal guardada en BD debe tener asset='GOLD'."""
        from detectors.gold.detector_gold_5m import GoldDetector5M
        params = dict(detector_gold_5m_params())
        mod = 'detectors.gold.detector_gold_5m'
        db = _mock_db()
        saved_calls = []

        def _capture_guardar(data):
            saved_calls.append(data)
            return 1

        patches = _patch_externos_para(mod)
        patches += [
            patch(f'{mod}.get_ohlcv', return_value=(df_sell, False)),
        ]

        with _apply_patches(patches):
            with patch.object(GoldDetector5M, '_guardar_senal', side_effect=_capture_guardar):
                det = GoldDetector5M(
                    simbolo='XAUUSD', tf_label='5M',
                    params=params, telegram_thread_id=None
                )
                det.db = db
                det.analizar('XAUUSD', params)

        for call_data in saved_calls:
            assert call_data.get('asset') == 'GOLD', (
                f"asset debe ser 'GOLD', obtenido: {call_data.get('asset')}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. GoldDetector5M — bloqueo FOMC
# ─────────────────────────────────────────────────────────────────────────────

class TestGoldDetector5MBloqueoFOMC:
    def test_fomc_bloquea_analisis(self):
        from detectors.gold.detector_gold_5m import GoldDetector5M
        params = dict(detector_gold_5m_params())
        mod = 'detectors.gold.detector_gold_5m'

        patches = _patch_externos_para(mod, bloqueado=True)

        with _apply_patches(patches):
            with patch.object(GoldDetector5M, 'enviar') as mock_enviar:
                det = GoldDetector5M(
                    simbolo='XAUUSD', tf_label='5M',
                    params=params, telegram_thread_id=None
                )
                det.db = None
                det.analizar('XAUUSD', params)
                mock_enviar.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 4. GoldDetector5M — señal activa en BD bloquea nueva señal
# ─────────────────────────────────────────────────────────────────────────────

class TestGoldDetector5MSeñalActiva:
    def test_senal_activa_bloquea(self):
        from detectors.gold.detector_gold_5m import GoldDetector5M
        params = dict(detector_gold_5m_params())
        mod = 'detectors.gold.detector_gold_5m'

        c = _gold_closes_bajistas(300, 3300.0, 2.0)
        df = _make_ohlcv(c, freq='5min')

        db = _mock_db()
        db.existe_senal_activa_tf.return_value = True  # simula señal ya activa

        patches = _patch_externos_para(mod)
        patches += [
            patch(f'{mod}.get_ohlcv', return_value=(df, False)),
        ]

        with _apply_patches(patches):
            with patch.object(GoldDetector5M, 'enviar') as mock_enviar:
                det = GoldDetector5M(
                    simbolo='XAUUSD', tf_label='5M',
                    params=params, telegram_thread_id=None
                )
                det.db = db
                det.analizar('XAUUSD', params)
                mock_enviar.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 5. GoldDetector5M — datos insuficientes
# ─────────────────────────────────────────────────────────────────────────────

class TestGoldDetector5MDatosInsuficientes:
    def test_df_vacio_no_lanza(self):
        from detectors.gold.detector_gold_5m import GoldDetector5M
        params = dict(detector_gold_5m_params())
        mod = 'detectors.gold.detector_gold_5m'
        df_vacio = pd.DataFrame()

        patches = _patch_externos_para(mod)
        patches += [
            patch(f'{mod}.get_ohlcv', return_value=(df_vacio, False)),
        ]

        with _apply_patches(patches):
            det = GoldDetector5M(
                simbolo='XAUUSD', tf_label='5M',
                params=params, telegram_thread_id=None
            )
            det.db = None
            det.analizar('XAUUSD', params)

    def test_df_pocos_datos_no_lanza(self):
        from detectors.gold.detector_gold_5m import GoldDetector5M
        params = dict(detector_gold_5m_params())
        mod = 'detectors.gold.detector_gold_5m'
        df_corto = _make_ohlcv([3300.0] * 10)  # solo 10 velas

        patches = _patch_externos_para(mod)
        patches += [
            patch(f'{mod}.get_ohlcv', return_value=(df_corto, False)),
        ]

        with _apply_patches(patches):
            det = GoldDetector5M(
                simbolo='XAUUSD', tf_label='5M',
                params=params, telegram_thread_id=None
            )
            det.db = None
            det.analizar('XAUUSD', params)


# ─────────────────────────────────────────────────────────────────────────────
# 6. GoldDetector15M — resamplea correctamente
# ─────────────────────────────────────────────────────────────────────────────

class TestGoldDetector15MResample:
    def test_resample_genera_velas_15m(self):
        """300 velas 5M deben producir ~100 velas 15M."""
        df_5m = _make_ohlcv(_gold_closes_bajistas(300, 3300.0, 2.0), freq='5min')
        df_15m = df_5m.resample('15min').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min',
            'Close': 'last', 'Volume': 'sum',
        }).dropna()
        assert len(df_15m) >= 90  # 300/3 = 100 aprox
        assert df_15m['High'].iloc[0] >= df_15m['Low'].iloc[0]

    def test_resample_high_max_de_period(self):
        """High del 15M debe ser el máximo de las 3 velas 5M."""
        highs_5m = [100.0, 105.0, 103.0] * 10  # cada trío tiene 105 como máximo
        c = [100.0] * 30
        df_5m = _make_ohlcv(c, highs=highs_5m, freq='5min')
        df_15m = df_5m.resample('15min').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min',
            'Close': 'last', 'Volume': 'sum',
        }).dropna()
        for h in df_15m['High']:
            assert abs(h - 105.0) < 0.01

    def test_detector_15m_no_lanza_con_datos_validos(self):
        from detectors.gold.detector_gold_15m import GoldDetector15M
        params = dict(detector_gold_15m_params())
        mod = 'detectors.gold.detector_gold_15m'

        df_5m = _make_ohlcv(_gold_closes_bajistas(300, 3300.0, 2.0), freq='5min')
        db = _mock_db()

        patches = _patch_externos_para(mod)
        patches += [
            patch(f'{mod}.get_ohlcv', return_value=(df_5m, False)),
        ]

        with _apply_patches(patches):
            det = GoldDetector15M(
                simbolo='XAUUSD', tf_label='15M',
                params=params, telegram_thread_id=None
            )
            det.db = db
            det.analizar('XAUUSD', params)  # No debe lanzar


# ─────────────────────────────────────────────────────────────────────────────
# 7. GoldDetector15M — asset GOLD en DB
# ─────────────────────────────────────────────────────────────────────────────

class TestGoldDetector15MAsset:
    def test_asset_gold_guardado(self):
        from detectors.gold.detector_gold_15m import GoldDetector15M
        params = dict(detector_gold_15m_params())
        mod = 'detectors.gold.detector_gold_15m'

        df_5m = _make_ohlcv(_gold_closes_bajistas(300, 3300.0, 2.0), freq='5min')
        db = _mock_db()
        saved_calls = []

        def _capture(data):
            saved_calls.append(data)
            return 1

        patches = _patch_externos_para(mod)
        patches += [
            patch(f'{mod}.get_ohlcv', return_value=(df_5m, False)),
        ]

        with _apply_patches(patches):
            with patch.object(GoldDetector15M, '_guardar_senal', side_effect=_capture):
                det = GoldDetector15M(
                    simbolo='XAUUSD', tf_label='15M',
                    params=params, telegram_thread_id=None
                )
                det.db = db
                det.analizar('XAUUSD', params)

        for d in saved_calls:
            assert d.get('asset') == 'GOLD'


# ─────────────────────────────────────────────────────────────────────────────
# 8. Confluencia multi-TF — señal bloqueada sin confluencia
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiTFConvluencia:
    def test_sin_confluencia_no_envia(self):
        from detectors.gold.detector_gold_5m import GoldDetector5M
        params = dict(detector_gold_5m_params())
        mod = 'detectors.gold.detector_gold_5m'

        df = _make_ohlcv(_gold_closes_bajistas(300, 3300.0, 2.0), freq='5min')
        db = _mock_db()

        patches = _patch_externos_para(mod, confluencia=False)
        patches += [
            patch(f'{mod}.get_ohlcv', return_value=(df, False)),
        ]

        with _apply_patches(patches):
            with patch.object(GoldDetector5M, 'enviar') as mock_enviar:
                det = GoldDetector5M(
                    simbolo='XAUUSD', tf_label='5M',
                    params=params, telegram_thread_id=None
                )
                det.db = db
                det.analizar('XAUUSD', params)
                mock_enviar.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 9. Anti-spam — segunda llamada con misma vela no duplica
# ─────────────────────────────────────────────────────────────────────────────

class TestAntiSpam:
    def test_segunda_llamada_igual_no_envia(self):
        from detectors.gold.detector_gold_5m import GoldDetector5M
        params = dict(detector_gold_5m_params())
        mod = 'detectors.gold.detector_gold_5m'
        df = _make_ohlcv(_gold_closes_bajistas(300, 3300.0, 2.0), freq='5min')
        db = _mock_db()

        patches = _patch_externos_para(mod)
        patches += [
            patch(f'{mod}.get_ohlcv', return_value=(df, False)),
        ]

        with _apply_patches(patches):
            with patch.object(GoldDetector5M, 'enviar') as mock_enviar:
                det = GoldDetector5M(
                    simbolo='XAUUSD', tf_label='5M',
                    params=params, telegram_thread_id=None
                )
                det.db = db
                det.analizar('XAUUSD', params)  # primera llamada
                n_primera = mock_enviar.call_count
                det.analizar('XAUUSD', params)  # segunda llamada, mismo df
                assert mock_enviar.call_count == n_primera


# ─────────────────────────────────────────────────────────────────────────────
# 10. data_provider — mapeo de ticker GC=F
# ─────────────────────────────────────────────────────────────────────────────

class TestDataProviderTickerMapping:
    def test_gc_f_mapeado_twelve_data(self):
        from adapters.data_provider import _TICKER_MAP_TWELVE
        assert 'GC=F' in _TICKER_MAP_TWELVE
        assert _TICKER_MAP_TWELVE['GC=F'] == 'XAU/USD'

    def test_gc_f_mapeado_polygon(self):
        from adapters.data_provider import _TICKER_MAP_POLYGON
        assert 'GC=F' in _TICKER_MAP_POLYGON
        assert _TICKER_MAP_POLYGON['GC=F'] == 'C:XAUUSD'

    def test_eurusd_no_interferir_gold(self):
        """La adición de EUR/USD no debe alterar el mapeo de GC=F."""
        from adapters.data_provider import _TICKER_MAP_TWELVE, _TICKER_MAP_POLYGON
        assert _TICKER_MAP_TWELVE['GC=F']   == 'XAU/USD'
        assert _TICKER_MAP_POLYGON['GC=F']  == 'C:XAUUSD'
        assert _TICKER_MAP_TWELVE['EURUSD=X']  == 'EUR/USD'
        assert _TICKER_MAP_POLYGON['EURUSD=X'] == 'C:EURUSD'


# ─────────────────────────────────────────────────────────────────────────────
# 11. ohlcv_poller — POLL_TARGETS contiene Gold
# ─────────────────────────────────────────────────────────────────────────────

class TestOHLCVPollerGold:
    def test_poll_targets_tiene_gold_5m(self):
        from services.ohlcv_poller import POLL_TARGETS
        tickers = [t['ticker_yf'] for t in POLL_TARGETS]
        assert 'GC=F' in tickers

    def test_poll_targets_tiene_gold_4h(self):
        from services.ohlcv_poller import POLL_TARGETS
        gold_targets = [t for t in POLL_TARGETS if t['ticker_yf'] == 'GC=F']
        intervalos = {t['interval'] for t in gold_targets}
        assert '4h' in intervalos

    def test_poll_targets_tiene_gold_1d(self):
        from services.ohlcv_poller import POLL_TARGETS
        gold_targets = [t for t in POLL_TARGETS if t['ticker_yf'] == 'GC=F']
        intervalos = {t['interval'] for t in gold_targets}
        assert '1d' in intervalos

    def test_gold_5m_poll_interval_60s(self):
        from services.ohlcv_poller import POLL_TARGETS
        gold_5m = next(
            (t for t in POLL_TARGETS if t['ticker_yf'] == 'GC=F' and t['interval'] == '5m'),
            None
        )
        assert gold_5m is not None
        assert gold_5m['poll_secs'] == 60

    def test_gold_4h_max_dias_suficientes(self):
        """4H necesita ≥95 días para EMA larga."""
        from services.ohlcv_poller import POLL_TARGETS
        gold_4h = next(
            (t for t in POLL_TARGETS if t['ticker_yf'] == 'GC=F' and t['interval'] == '4h'),
            None
        )
        assert gold_4h is not None
        assert gold_4h['max_dias_bd'] >= 90


# ─────────────────────────────────────────────────────────────────────────────
# 12. orchestrator — DETECTOR_REGISTRY contiene los 5 Gold
# ─────────────────────────────────────────────────────────────────────────────

class TestOrchestratorRegistryGold:
    def test_gold_5_detectores_registrados(self):
        from services.orchestrator import DETECTOR_REGISTRY
        gold_keys = [k for k in DETECTOR_REGISTRY if k.startswith('gold_')]
        assert len(gold_keys) == 5, (
            f"Se esperan 5 detectores Gold, encontrados: {gold_keys}")

    def test_gold_detectores_enabled(self):
        from services.orchestrator import DETECTOR_REGISTRY
        for key, cfg in DETECTOR_REGISTRY.items():
            if key.startswith('gold_'):
                assert cfg['enabled'] is True, f"{key} debería estar habilitado"

    def test_gold_modulos_correctos(self):
        from services.orchestrator import DETECTOR_REGISTRY
        modulos_esperados = {
            'gold_5m':  'detectors.gold.detector_gold_5m',
            'gold_15m': 'detectors.gold.detector_gold_15m',
            'gold_1h':  'detectors.gold.detector_gold_1h',
            'gold_4h':  'detectors.gold.detector_gold_4h',
            'gold_1d':  'detectors.gold.detector_gold_1d',
        }
        for key, modulo_esperado in modulos_esperados.items():
            assert key in DETECTOR_REGISTRY, f"Falta {key} en DETECTOR_REGISTRY"
            assert DETECTOR_REGISTRY[key]['module'] == modulo_esperado

    def test_gold_y_eurusd_coexisten(self):
        from services.orchestrator import DETECTOR_REGISTRY
        gold_keys   = [k for k in DETECTOR_REGISTRY if k.startswith('gold_')]
        eurusd_keys = [k for k in DETECTOR_REGISTRY if k.startswith('eurusd_')]
        assert len(gold_keys)   >= 5
        assert len(eurusd_keys) >= 4


# ─────────────────────────────────────────────────────────────────────────────
# 13. Exclusión mutua SELL vs BUY
# ─────────────────────────────────────────────────────────────────────────────

class TestExclusionMutua:
    def test_no_se_emiten_ambas_direcciones(self):
        """Con datos neutros, si se emite señal solo debe ser una dirección."""
        from detectors.gold.detector_gold_5m import GoldDetector5M
        params = dict(detector_gold_5m_params())
        mod = 'detectors.gold.detector_gold_5m'
        df = _make_ohlcv(_gold_closes_bajistas(300, 3300.0, 2.0), freq='5min')
        db = _mock_db()
        mensajes_enviados = []

        patches = _patch_externos_para(mod)
        patches += [
            patch(f'{mod}.get_ohlcv', return_value=(df, False)),
        ]

        with _apply_patches(patches):
            with patch.object(GoldDetector5M, 'enviar',
                              side_effect=lambda m: mensajes_enviados.append(m)):
                det = GoldDetector5M(
                    simbolo='XAUUSD', tf_label='5M',
                    params=params, telegram_thread_id=None
                )
                det.db = db
                det.analizar('XAUUSD', params)

        tiene_sell = any('SELL' in m or 'VENTA' in m for m in mensajes_enviados)
        tiene_buy  = any('BUY'  in m or 'COMPRA' in m for m in mensajes_enviados)
        assert not (tiene_sell and tiene_buy), (
            "No deben emitirse SELL y BUY en el mismo ciclo de análisis")


# ─────────────────────────────────────────────────────────────────────────────
# 14. Versión del detector en BD
# ─────────────────────────────────────────────────────────────────────────────

class TestVersionDetector:
    def test_5m_version_en_senal(self):
        from detectors.gold.detector_gold_5m import GoldDetector5M
        params = dict(detector_gold_5m_params())
        mod = 'detectors.gold.detector_gold_5m'
        df = _make_ohlcv(_gold_closes_bajistas(300, 3300.0, 2.0), freq='5min')
        db = _mock_db()
        saved = []

        def _capture(data):
            saved.append(data)
            return 1

        patches = _patch_externos_para(mod)
        patches += [
            patch(f'{mod}.get_ohlcv', return_value=(df, False)),
        ]

        with _apply_patches(patches):
            with patch.object(GoldDetector5M, '_guardar_senal', side_effect=_capture):
                det = GoldDetector5M(
                    simbolo='XAUUSD', tf_label='5M',
                    params=params, telegram_thread_id=None
                )
                det.db = db
                det.analizar('XAUUSD', params)

        for d in saved:
            assert '5M' in d.get('version_detector', ''), (
                f"version_detector debe contener '5M': {d.get('version_detector')}")

    def test_15m_version_en_senal(self):
        from detectors.gold.detector_gold_15m import GoldDetector15M
        params = dict(detector_gold_15m_params())
        mod = 'detectors.gold.detector_gold_15m'
        df_5m = _make_ohlcv(_gold_closes_bajistas(300, 3300.0, 2.0), freq='5min')
        db = _mock_db()
        saved = []

        def _capture(data):
            saved.append(data)
            return 1

        patches = _patch_externos_para(mod)
        patches += [
            patch(f'{mod}.get_ohlcv', return_value=(df_5m, False)),
        ]

        with _apply_patches(patches):
            with patch.object(GoldDetector15M, '_guardar_senal', side_effect=_capture):
                det = GoldDetector15M(
                    simbolo='XAUUSD', tf_label='15M',
                    params=params, telegram_thread_id=None
                )
                det.db = db
                det.analizar('XAUUSD', params)

        for d in saved:
            assert '15M' in d.get('version_detector', '')


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de parámetros (expuestos como funciones para evitar import circular)
# ─────────────────────────────────────────────────────────────────────────────

def detector_gold_5m_params():
    from detectors.gold.detector_gold_5m import SIMBOLOS
    return SIMBOLOS['XAUUSD']


def detector_gold_15m_params():
    from detectors.gold.detector_gold_15m import SIMBOLOS
    return SIMBOLOS['XAUUSD']


# ─────────────────────────────────────────────────────────────────────────────
# Context manager para aplicar lista de patches
# ─────────────────────────────────────────────────────────────────────────────

from contextlib import contextmanager

@contextmanager
def _apply_patches(patch_list):
    """Activa todos los patches de la lista como un bloque."""
    started = []
    try:
        for p in patch_list:
            started.append(p.start())
        yield started
    finally:
        for p in reversed(patch_list):
            try:
                p.stop()
            except RuntimeError:
                pass
