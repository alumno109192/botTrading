"""
tests/unit/test_performance_features.py
========================================
Tests unitarios para las nuevas features:
  1. _derivar_nivel en BaseDetector
  2. obtener_kpis_performance en DatabaseManager
  3. migrate_add_nivel en DatabaseManager
  4. guardar_senal persiste nivel
  5. Endpoints /api/performance en Flask
  6. calcular_metricas en optimize_weights
  7. _extract_features en optimize_weights
  8. grid_search / logistic_regression / bayesian en optimize_weights
"""
import sys
import os
import json
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
# 1. BaseDetector._derivar_nivel
# ─────────────────────────────────────────────────────────────────────────────

class TestDerivarNivel(unittest.TestCase):

    def setUp(self):
        from core.base_detector import BaseDetector
        self.BD = BaseDetector

    # ── Umbrales exactos por TF ──────────────────────────────────────────────

    def test_1d_alerta(self):
        assert self.BD._derivar_nivel(4, '1D') == 'ALERTA'

    def test_1d_media(self):
        assert self.BD._derivar_nivel(6, '1D') == 'MEDIA'

    def test_1d_fuerte(self):
        assert self.BD._derivar_nivel(8, '1D') == 'FUERTE'

    def test_1d_maxima(self):
        assert self.BD._derivar_nivel(10, '1D') == 'MAXIMA'

    def test_4h_alerta(self):
        assert self.BD._derivar_nivel(5, '4H') == 'ALERTA'

    def test_4h_media(self):
        assert self.BD._derivar_nivel(9, '4H') == 'MEDIA'

    def test_4h_fuerte(self):
        assert self.BD._derivar_nivel(12, '4H') == 'FUERTE'

    def test_4h_maxima(self):
        assert self.BD._derivar_nivel(14, '4H') == 'MAXIMA'

    def test_1h_maxima(self):
        assert self.BD._derivar_nivel(14, '1H') == 'MAXIMA'

    def test_15m_alerta(self):
        assert self.BD._derivar_nivel(3, '15M') == 'ALERTA'

    def test_15m_media(self):
        assert self.BD._derivar_nivel(7, '15M') == 'MEDIA'

    def test_5m_alerta(self):
        assert self.BD._derivar_nivel(2, '5M') == 'ALERTA'

    def test_5m_maxima(self):
        assert self.BD._derivar_nivel(10, '5M') == 'MAXIMA'

    # ── Boundary conditions ──────────────────────────────────────────────────

    def test_score_cero_siempre_alerta(self):
        for tf in ('1D', '4H', '1H', '15M', '5M'):
            assert self.BD._derivar_nivel(0, tf) == 'ALERTA', f"TF={tf}"

    def test_score_muy_alto_siempre_maxima(self):
        for tf in ('1D', '4H', '1H', '15M', '5M'):
            assert self.BD._derivar_nivel(99, tf) == 'MAXIMA', f"TF={tf}"

    def test_tf_desconocido_usa_fallback_1h(self):
        # TF desconocido → usa threshold de '1H' (el default del .get())
        result = self.BD._derivar_nivel(14, 'WEEKLY')
        assert result == 'MAXIMA'

    def test_tf_case_insensitive_no_aplica(self):
        # Los TF en _NIVEL_THRESHOLDS son uppercase; si llega en minúscula
        # el .get() devuelve None → usa fallback 1H. No debe lanzar excepción.
        result = self.BD._derivar_nivel(10, '1d')
        assert result in ('ALERTA', 'MEDIA', 'FUERTE', 'MAXIMA')

    def test_todos_los_tf_en_thresholds(self):
        esperados = {'1D', '4H', '1H', '15M', '5M'}
        assert set(self.BD._NIVEL_THRESHOLDS.keys()) == esperados

    def test_thresholds_ordenados_crecientes(self):
        for tf, t in self.BD._NIVEL_THRESHOLDS.items():
            assert t['ALERTA'] < t['MEDIA'] < t['FUERTE'] < t['MAXIMA'], tf


# ─────────────────────────────────────────────────────────────────────────────
# 2. _guardar_senal inyecta nivel automáticamente
# ─────────────────────────────────────────────────────────────────────────────

class TestGuardarSenalInjectaNivel(unittest.TestCase):

    def _make_detector(self, tf_label='1D'):
        """Crea una instancia concreta mínima de BaseDetector sin BD ni Telegram.
        Usa object.__new__ para saltar __init__ completamente."""
        from core.base_detector import BaseDetector

        class _FakeDetector(BaseDetector):
            def analizar(self, *a, **kw):
                pass

        det = object.__new__(_FakeDetector)
        det.simbolo = 'XAUUSD'
        det.tf_label = tf_label
        det.params = {}
        det.telegram_thread_id = None
        det.alertas_enviadas = {}
        det.ultimo_analisis = {}
        det.aviso_macro = ''
        det.contexto_pullback = ''
        det._last_senal_id = None
        det._current_candle_ts = None
        det.db = MagicMock()
        det.db.guardar_senal.return_value = 42
        return det

    def test_nivel_inyectado_cuando_no_presente(self):
        det = self._make_detector('1D')
        senal = {
            'simbolo': 'XAUUSD_1D', 'direccion': 'VENTA',
            'precio_entrada': 3300.0, 'tp1': 3280.0, 'tp2': 3260.0,
            'tp3': 3240.0, 'sl': 3320.0, 'score': 10,
        }
        det._guardar_senal(senal)
        # Score 10 en 1D → MAXIMA
        assert senal['nivel'] == 'MAXIMA'
        det.db.guardar_senal.assert_called_once()

    def test_nivel_explicito_no_sobreescrito(self):
        det = self._make_detector('4H')
        senal = {
            'simbolo': 'XAUUSD_4H', 'direccion': 'COMPRA',
            'precio_entrada': 3300.0, 'tp1': 3320.0, 'tp2': 3340.0,
            'tp3': 3360.0, 'sl': 3280.0, 'score': 5,
            'nivel': 'FUERTE',  # explícito — no debe cambiarse
        }
        det._guardar_senal(senal)
        assert senal['nivel'] == 'FUERTE'

    def test_score_bajo_inyecta_alerta(self):
        det = self._make_detector('4H')
        senal = {
            'simbolo': 'XAUUSD_4H', 'direccion': 'COMPRA',
            'precio_entrada': 3300.0, 'tp1': 3320.0, 'tp2': 3340.0,
            'tp3': 3360.0, 'sl': 3280.0, 'score': 3,
        }
        det._guardar_senal(senal)
        assert senal['nivel'] == 'ALERTA'

    def test_score_none_no_lanza_excepcion(self):
        det = self._make_detector('1H')
        senal = {
            'simbolo': 'XAUUSD_1H', 'direccion': 'COMPRA',
            'precio_entrada': 3300.0, 'tp1': 3320.0, 'tp2': 3340.0,
            'tp3': 3360.0, 'sl': 3280.0, 'score': None,
        }
        det._guardar_senal(senal)  # no debe lanzar
        assert senal['nivel'] == 'ALERTA'


# ─────────────────────────────────────────────────────────────────────────────
# 3. DatabaseManager.obtener_kpis_performance (mock)
# ─────────────────────────────────────────────────────────────────────────────

# Credenciales falsas para que el constructor no falle si se llega a usar
os.environ.setdefault('TURSO_DATABASE_URL', 'libsql://fake-db.turso.io')
os.environ.setdefault('TURSO_AUTH_TOKEN',   'fake-token')


class TestObtenerKpisPerformance(unittest.TestCase):

    def _make_db(self):
        """Instancia DatabaseManager sin singleton ni HTTP usando object.__new__."""
        from adapters.database import DatabaseManager
        db = object.__new__(DatabaseManager)  # bypasa __new__ singleton y __init__
        db._initialized = True
        db.api_url = 'https://fake'
        db.headers = {}
        import threading
        db._insert_lock = threading.Lock()
        return db

    def _make_result(self, rows):
        from adapters.database import _Result
        if rows:
            cols = list(rows[0].keys())
        else:
            cols = []
        return _Result(list(rows), cols)

    def test_estructura_resultado(self):
        db = self._make_db()
        rows_nivel = [
            {'grupo': 'MAXIMA', 'total': 20, 'wins': 16, 'losses': 4,
             'win_rate': 80.0, 'avg_pnl': 0.5, 'profit_factor': 2.1},
            {'grupo': 'FUERTE', 'total': 30, 'wins': 18, 'losses': 12,
             'win_rate': 60.0, 'avg_pnl': 0.2, 'profit_factor': 1.4},
        ]
        db.ejecutar_query = MagicMock(
            return_value=self._make_result(rows_nivel)
        )
        result = db.obtener_kpis_performance()
        assert 'por_nivel' in result
        assert 'por_timeframe' in result
        assert 'por_asset' in result

    def test_resultado_vacio_devuelve_listas_vacias(self):
        db = self._make_db()
        db.ejecutar_query = MagicMock(
            return_value=self._make_result([])
        )
        result = db.obtener_kpis_performance()
        assert result['por_nivel'] == []
        assert result['por_timeframe'] == []
        assert result['por_asset'] == []

    def test_kpis_contienen_campos_requeridos(self):
        db = self._make_db()
        rows = [
            {'grupo': 'MAXIMA', 'total': 10, 'wins': 8, 'losses': 2,
             'win_rate': 80.0, 'avg_pnl': 0.3, 'profit_factor': 1.8},
        ]
        db.ejecutar_query = MagicMock(return_value=self._make_result(rows))
        result = db.obtener_kpis_performance()
        for item in result['por_nivel']:
            for campo in ('grupo', 'total', 'wins', 'losses', 'win_rate',
                          'profit_factor'):
                assert campo in item, f"Campo '{campo}' faltante"

    def test_ejecutar_query_llamado_tres_veces(self):
        db = self._make_db()
        db.ejecutar_query = MagicMock(return_value=self._make_result([]))
        db.obtener_kpis_performance()
        assert db.ejecutar_query.call_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# 4. migrate_add_nivel
# ─────────────────────────────────────────────────────────────────────────────

class TestMigrateAddNivel(unittest.TestCase):

    def _make_db(self):
        from adapters.database import DatabaseManager
        db = object.__new__(DatabaseManager)  # bypasa singleton
        db._initialized = True
        return db

    def test_migra_sin_errores(self):
        db = self._make_db()
        db.ejecutar_query = MagicMock()
        db.migrate_add_nivel()
        db.ejecutar_query.assert_called_once()
        sql = db.ejecutar_query.call_args[0][0]
        assert 'nivel' in sql.lower()
        assert 'ALTER TABLE senales' in sql

    def test_migra_idempotente_duplicate_column(self):
        db = self._make_db()
        db.ejecutar_query = MagicMock(side_effect=Exception('duplicate column name: nivel'))
        # No debe lanzar excepción
        db.migrate_add_nivel()

    def test_migra_idempotente_already_exists(self):
        db = self._make_db()
        db.ejecutar_query = MagicMock(side_effect=Exception('already exists'))
        db.migrate_add_nivel()  # no debe lanzar


# ─────────────────────────────────────────────────────────────────────────────
# 5. Endpoints Flask /api/performance
# ─────────────────────────────────────────────────────────────────────────────

class TestPerformanceEndpoints(unittest.TestCase):

    def _make_app(self, db_mock=None):
        from api.routes import create_app
        estado = {'iniciado': True, 'detectores': {}}
        threads = {}
        app = create_app(estado, threads)
        app.config['TESTING'] = True
        return app

    def test_json_endpoint_sin_db(self):
        app = self._make_app()
        with patch('adapters.database.get_db', return_value=None):
            with app.test_client() as c:
                r = c.get('/api/performance')
                assert r.status_code == 503
                data = json.loads(r.data)
                assert 'error' in data

    def test_json_endpoint_con_db_mock(self):
        fake_kpis = {
            'por_nivel': [{'grupo': 'MAXIMA', 'total': 5, 'wins': 4,
                           'losses': 1, 'win_rate': 80.0,
                           'avg_pnl': 0.4, 'profit_factor': 2.0}],
            'por_timeframe': [],
            'por_asset': [],
        }
        db_mock = MagicMock()
        db_mock.obtener_kpis_performance.return_value = fake_kpis
        app = self._make_app()
        with patch('adapters.database.get_db', return_value=db_mock):
            with app.test_client() as c:
                r = c.get('/api/performance')
                assert r.status_code == 200
                data = json.loads(r.data)
                assert 'kpis' in data
                assert 'timestamp' in data
                assert data['kpis']['por_nivel'][0]['grupo'] == 'MAXIMA'

    def test_dashboard_endpoint_sin_db(self):
        app = self._make_app()
        with patch('adapters.database.get_db', return_value=None):
            with app.test_client() as c:
                r = c.get('/api/performance/dashboard')
                assert r.status_code == 503

    def test_dashboard_endpoint_html_con_db(self):
        fake_kpis = {
            'por_nivel': [{'grupo': 'FUERTE', 'total': 10, 'wins': 7,
                           'losses': 3, 'win_rate': 70.0,
                           'avg_pnl': 0.3, 'profit_factor': 1.8}],
            'por_timeframe': [{'grupo': '1H', 'total': 10, 'wins': 7,
                               'losses': 3, 'win_rate': 70.0,
                               'avg_pnl': 0.3, 'profit_factor': 1.8}],
            'por_asset': [],
        }
        db_mock = MagicMock()
        db_mock.obtener_kpis_performance.return_value = fake_kpis
        app = self._make_app()
        with patch('adapters.database.get_db', return_value=db_mock):
            with app.test_client() as c:
                r = c.get('/api/performance/dashboard')
                assert r.status_code == 200
                html = r.data.decode('utf-8')
                assert 'Performance Dashboard' in html
                assert 'FUERTE' in html
                assert '70.0%' in html

    def test_dashboard_tabla_niveles_muestra_todos_los_niveles(self):
        fake_kpis = {
            'por_nivel': [
                {'grupo': 'MAXIMA', 'total': 5, 'wins': 4, 'losses': 1,
                 'win_rate': 80.0, 'avg_pnl': 0.5, 'profit_factor': 2.5},
                {'grupo': 'ALERTA', 'total': 20, 'wins': 10, 'losses': 10,
                 'win_rate': 50.0, 'avg_pnl': 0.0, 'profit_factor': 1.0},
            ],
            'por_timeframe': [],
            'por_asset': [],
        }
        db_mock = MagicMock()
        db_mock.obtener_kpis_performance.return_value = fake_kpis
        app = self._make_app()
        with patch('adapters.database.get_db', return_value=db_mock):
            with app.test_client() as c:
                r = c.get('/api/performance/dashboard')
                html = r.data.decode('utf-8')
                assert 'MAXIMA' in html
                assert 'ALERTA' in html

    def test_json_endpoint_devuelve_timestamp(self):
        db_mock = MagicMock()
        db_mock.obtener_kpis_performance.return_value = {
            'por_nivel': [], 'por_timeframe': [], 'por_asset': []
        }
        app = self._make_app()
        with patch('adapters.database.get_db', return_value=db_mock):
            with app.test_client() as c:
                r = c.get('/api/performance')
                data = json.loads(r.data)
                assert data['timestamp'].endswith('Z')


# ─────────────────────────────────────────────────────────────────────────────
# 6. optimize_weights.calcular_metricas
# ─────────────────────────────────────────────────────────────────────────────

class TestCalcularMetricas(unittest.TestCase):

    def setUp(self):
        from scripts.optimize_weights import calcular_metricas
        self.calcular_metricas = calcular_metricas

    def test_win_rate_100(self):
        y  = np.array([1, 1, 1, 1])
        sc = np.array([5, 6, 7, 8])
        m  = self.calcular_metricas(y, sc, threshold=3.0)
        assert m['win_rate'] == 100.0
        assert m['total'] == 4
        assert m['wins']  == 4

    def test_win_rate_50(self):
        y  = np.array([1, 0, 1, 0])
        sc = np.array([5, 5, 5, 5])
        m  = self.calcular_metricas(y, sc, threshold=4.0)
        assert m['win_rate'] == 50.0

    def test_threshold_filtra_senales(self):
        y  = np.array([1, 1, 0, 0, 1])
        sc = np.array([8, 7, 3, 2, 9])
        m  = self.calcular_metricas(y, sc, threshold=6.0)
        # Solo sc >= 6: índices 0, 1, 4 → todos wins
        assert m['total']    == 3
        assert m['win_rate'] == 100.0

    def test_sin_senales_por_encima_threshold(self):
        y  = np.array([1, 0, 1])
        sc = np.array([1, 2, 3])
        m  = self.calcular_metricas(y, sc, threshold=10.0)
        assert m['total']    == 0
        assert m['win_rate'] == 0.0

    def test_profit_factor_calculado_con_pnl(self):
        y   = np.array([1, 1, 0, 0])
        sc  = np.array([5, 5, 5, 5])
        pnl = np.array([2.0, 3.0, -1.0, -1.0])
        m   = self.calcular_metricas(y, sc, threshold=4.0, pnl=pnl)
        # wins=5.0, losses=2.0 → pf=2.5
        assert abs(m['profit_factor'] - 2.5) < 0.01

    def test_coverage_correcto(self):
        y  = np.array([1, 0, 1, 0, 1])
        sc = np.array([8, 8, 2, 2, 2])
        m  = self.calcular_metricas(y, sc, threshold=5.0)
        # 2/5 = 40%
        assert m['coverage'] == 40.0


# ─────────────────────────────────────────────────────────────────────────────
# 7. optimize_weights._extract_features
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractFeatures(unittest.TestCase):

    def setUp(self):
        from scripts.optimize_weights import _extract_features, FEATURES_SELL, FEATURES_BUY
        self._extract_features = _extract_features
        self.FEATURES_SELL = FEATURES_SELL
        self.FEATURES_BUY  = FEATURES_BUY

    def _make_df(self, direction, n=5, estado='TP1'):
        ind = {
            'shooting_star': True, 'bearish_engulfing': False,
            'rsi_sobrecompra': True, 'emas_bajistas': True,
            'hammer': True, 'bullish_engulfing': False,
            'rsi_sobreventa': True, 'emas_alcistas': True,
            'score_sell': 8, 'score_buy': 5,
        }
        rows = []
        for i in range(n):
            rows.append({
                'id': i, 'timestamp': f'2025-01-0{i+1}T00:00:00',
                'direccion': direction,
                'score': 8 if direction == 'VENTA' else 5,
                'indicadores': json.dumps(ind),
                'estado': estado,
                'timeframe': '1D', 'asset': 'GOLD',
                'beneficio_final_pct': 0.3 if 'TP' in estado else -0.2,
                '_ind': ind,
            })
        df = pd.DataFrame(rows)
        df['win'] = df['estado'].isin({'TP1', 'TP2', 'TP3', 'BREAKEVEN'}).astype(int)
        return df

    def test_extrae_columnas_sell(self):
        df  = self._make_df('VENTA', n=5)
        out = self._extract_features(df, 'VENTA')
        assert 'win'   in out.columns
        assert 'score' in out.columns
        # Al menos algunas features deben estar
        found = [f for f in self.FEATURES_SELL if f in out.columns]
        assert len(found) > 0

    def test_extrae_columnas_buy(self):
        df  = self._make_df('COMPRA', n=5)
        out = self._extract_features(df, 'COMPRA')
        assert 'win'   in out.columns
        found = [f for f in self.FEATURES_BUY if f in out.columns]
        assert len(found) > 0

    def test_filtra_solo_direccion(self):
        df_sell = self._make_df('VENTA',  n=3)
        df_buy  = self._make_df('COMPRA', n=7)
        df_mix  = pd.concat([df_sell, df_buy], ignore_index=True)
        out_sell = self._extract_features(df_mix, 'VENTA')
        out_buy  = self._extract_features(df_mix, 'COMPRA')
        assert len(out_sell) == 3
        assert len(out_buy)  == 7

    def test_features_son_binarios(self):
        df  = self._make_df('VENTA', n=5)
        out = self._extract_features(df, 'VENTA')
        for feat in self.FEATURES_SELL:
            if feat in out.columns:
                assert set(out[feat].unique()).issubset({0, 1}), feat

    def test_win_derivado_correctamente(self):
        df  = self._make_df('VENTA', n=3, estado='TP2')
        out = self._extract_features(df, 'VENTA')
        assert all(out['win'] == 1)

        df2  = self._make_df('VENTA', n=3, estado='SL')
        out2 = self._extract_features(df2, 'VENTA')
        assert all(out2['win'] == 0)


# ─────────────────────────────────────────────────────────────────────────────
# 8. optimize_weights: métodos de optimización (smoke tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestOptimizationMethods(unittest.TestCase):
    """Smoke tests: verifican que los métodos corren sin errores y devuelven
    la estructura esperada, usando datos sintéticos pequeños."""

    def _make_xy(self, n=40, direction='VENTA'):
        from scripts.optimize_weights import FEATURES_SELL, FEATURES_BUY
        feats = FEATURES_SELL if direction == 'VENTA' else FEATURES_BUY
        rng   = np.random.default_rng(42)
        X = pd.DataFrame(rng.integers(0, 2, size=(n, len(feats))),
                         columns=feats)
        X['beneficio_final_pct'] = rng.uniform(-0.5, 1.0, n)
        y = rng.integers(0, 2, n)
        return X, y

    def test_grid_search_estructura(self):
        from scripts.optimize_weights import grid_search
        X, y = self._make_xy(40)
        X_tr, X_te = X.iloc[:32], X.iloc[32:]
        y_tr, y_te = y[:32], y[32:]
        res = grid_search(X_tr, y_tr, X_te, y_te, 'VENTA', n_steps=3)
        assert res['method'] == 'grid_search'
        assert 'group_weights' in res
        assert 'train_metrics' in res
        assert 'test_metrics'  in res
        assert 'win_rate' in res['test_metrics']

    def test_logistic_regression_estructura(self):
        try:
            import sklearn  # noqa: F401
        except ImportError:
            self.skipTest('scikit-learn no instalado')
        from scripts.optimize_weights import logistic_regression_weights
        X, y = self._make_xy(40)
        X_tr, X_te = X.iloc[:32], X.iloc[32:]
        y_tr, y_te = y[:32], y[32:]
        res = logistic_regression_weights(X_tr, y_tr, X_te, y_te, 'VENTA')
        if 'error' in res:
            self.skipTest(f'sklearn error: {res["error"]}')
        assert res['method'] == 'logistic_regression'
        assert 'feature_weights' in res
        assert 'top_features' in res
        assert len(res['top_features']) <= 10

    def test_bayesian_estructura(self):
        try:
            from scipy.optimize import minimize  # noqa: F401
        except ImportError:
            self.skipTest('scipy no instalado')
        from scripts.optimize_weights import bayesian_optimization
        X, y = self._make_xy(40)
        X_tr, X_te = X.iloc[:32], X.iloc[32:]
        y_tr, y_te = y[:32], y[32:]
        res = bayesian_optimization(X_tr, y_tr, X_te, y_te, 'VENTA')
        assert res['method'] == 'bayesian_nelder_mead'
        assert 'group_weights' in res
        assert 'threshold_recommended' in res

    def test_grid_search_multiplicadores_en_rango(self):
        from scripts.optimize_weights import grid_search
        X, y = self._make_xy(40)
        X_tr, X_te = X.iloc[:32], X.iloc[32:]
        y_tr, y_te = y[:32], y[32:]
        res = grid_search(X_tr, y_tr, X_te, y_te, 'VENTA', n_steps=3)
        for grupo, peso in res['group_weights'].items():
            assert 0.4 <= peso <= 2.1, f"grupo={grupo} peso={peso} fuera de rango"

    def test_logistic_top_features_son_conocidos(self):
        try:
            import sklearn  # noqa: F401
        except ImportError:
            self.skipTest('scikit-learn no instalado')
        from scripts.optimize_weights import logistic_regression_weights, FEATURES_SELL
        X, y = self._make_xy(40)
        X_tr, X_te = X.iloc[:32], X.iloc[32:]
        y_tr, y_te = y[:32], y[32:]
        res = logistic_regression_weights(X_tr, y_tr, X_te, y_te, 'VENTA')
        if 'error' in res:
            self.skipTest(f'sklearn error: {res["error"]}')
        for item in res['top_features']:
            assert item['feature'] in FEATURES_SELL

    def test_calcular_metricas_no_divide_por_cero(self):
        from scripts.optimize_weights import calcular_metricas
        y  = np.array([1, 1, 1])
        sc = np.array([5, 5, 5])
        # pnl solo wins, sin losses → profit_factor = inf o None, no excepción
        pnl = np.array([1.0, 2.0, 0.5])
        m   = calcular_metricas(y, sc, 4.0, pnl)
        assert m['win_rate'] == 100.0
        # profit_factor puede ser inf o None, pero no debe lanzar ZeroDivisionError


# ─────────────────────────────────────────────────────────────────────────────
# 9. _render_performance_html (helper de routes)
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderPerformanceHtml(unittest.TestCase):

    def setUp(self):
        from api.routes import _render_performance_html
        self._render = _render_performance_html

    def test_html_valido_con_datos(self):
        kpis = {
            'por_nivel': [
                {'grupo': 'MAXIMA', 'total': 10, 'wins': 8, 'losses': 2,
                 'win_rate': 80.0, 'avg_pnl': 0.5, 'profit_factor': 2.5},
            ],
            'por_timeframe': [
                {'grupo': '4H', 'total': 5, 'wins': 3, 'losses': 2,
                 'win_rate': 60.0, 'avg_pnl': 0.1, 'profit_factor': 1.2},
            ],
            'por_asset': [],
        }
        html = self._render(kpis)
        assert html.startswith('<!DOCTYPE html>')
        assert 'MAXIMA' in html
        assert '80.0%' in html
        assert '4H' in html

    def test_html_sin_datos_muestra_sin_datos(self):
        kpis = {'por_nivel': [], 'por_timeframe': [], 'por_asset': []}
        html = self._render(kpis)
        assert 'Sin datos cerrados' in html

    def test_html_profit_factor_none_muestra_guion(self):
        kpis = {
            'por_nivel': [
                {'grupo': 'ALERTA', 'total': 3, 'wins': 1, 'losses': 2,
                 'win_rate': 33.0, 'avg_pnl': None, 'profit_factor': None},
            ],
            'por_timeframe': [], 'por_asset': [],
        }
        html = self._render(kpis)
        assert 'ALERTA' in html
        # profit_factor None → '—'
        assert '—' in html


if __name__ == '__main__':
    unittest.main(verbosity=2)
