import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from core.predictor import GoldPredictor


def _sample_features(i: int, win: bool) -> dict:
    base = {
        'dist_soporte_pct': -0.05 if win else 0.7,
        'dist_resist_pct': 0.7 if win else -0.05,
        'rsi_direccion_3v': 1 if win else -1,
        'rsi_nivel': 38 if win else 66,
        'vol_relativo_3v': 0.8 if win else 1.3,
        'macd_hist_mejorando': 1 if win else 0,
        'mecha_inferior_pct': 0.6 if win else 0.1,
        'mecha_superior_pct': 0.1 if win else 0.7,
        'atr_contrayendo': 1 if win else 0,
        'obv_divergencia': 1 if win else -1,
        'precio_vs_ema_fast_pct': -0.1 if win else 0.3,
        'velas_en_zona': 3 if win else 1,
    }
    base['rsi_nivel'] += (i % 3) * 0.1
    return base


def _build_training_rows(n: int = 50, direccion: str = 'COMPRA'):
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(n):
        win = i % 2 == 0
        estado = 'TP1' if win else 'SL'
        rows.append({
            'timestamp': (now - timedelta(hours=n - i)).isoformat(),
            'direccion': direccion,
            'estado': estado,
            'indicadores': json.dumps(_sample_features(i, win)),
        })
    return rows


def _build_price_df() -> pd.DataFrame:
    idx = pd.date_range('2026-05-01', periods=30, freq='1h', tz='UTC')
    data = {
        'Open': [3300 + i * 0.5 for i in range(30)],
        'High': [3302 + i * 0.5 for i in range(30)],
        'Low': [3298 + i * 0.5 for i in range(30)],
        'Close': [3301 + i * 0.5 for i in range(30)],
        'Volume': [1000 + (i % 5) * 50 for i in range(30)],
        'rsi': [45 - (i % 4) for i in range(30)],
        'macd_hist': [-0.4 + i * 0.02 for i in range(30)],
        'atr': [12 - i * 0.05 for i in range(30)],
        'obv': [10000 + i * 100 for i in range(30)],
        'ema_fast': [3300 + i * 0.45 for i in range(30)],
    }
    return pd.DataFrame(data, index=idx)


def _df_backtest_base(n: int = 70) -> pd.DataFrame:
    idx = pd.date_range('2026-01-01', periods=n, freq='h')
    highs = [102.0 if i % 2 == 0 else 100.2 for i in range(n)]
    lows = [99.5 if i % 2 == 0 else 98.5 for i in range(n)]
    return pd.DataFrame(
        {
            'Open': [100.0] * n,
            'High': highs,
            'Low': lows,
            'Close': [100.0] * n,
            'Volume': [1.0] * n,
            'atr': [1.0] * n,
            'rsi': [50.0] * n,
            'ema_fast': [100.0] * n,
            'macd_hist': [0.1] * n,
            'obv': list(range(n)),
        },
        index=idx,
    )


def test_calcular_features_predictivos_devuelve_campos_esperados():
    predictor = GoldPredictor(tf='1H', direccion='COMPRA')
    features = predictor.calcular_features_predictivos(
        _build_price_df(), zsl=3288.0, zsh=3295.0, zrl=3315.0, zrh=3322.0, atr=12.0
    )

    for key in GoldPredictor.FEATURE_COLUMNS:
        assert key in features


def test_entrenar_y_predecir_con_datos_suficientes(tmp_path):
    model_path = tmp_path / 'predictor.pkl'
    predictor = GoldPredictor(tf='1H', direccion='COMPRA', model_path=str(model_path))
    predictor.entrenar('COMPRA', _build_training_rows(60, 'COMPRA'))

    assert predictor.modelo_rf is not None
    assert predictor.modelo_lr is not None
    assert predictor.last_metrics

    prob, label = predictor.predecir(_sample_features(99, True))
    assert 0.0 <= prob <= 1.0
    assert label in {'BUY', 'NEUTRO'}


def test_guardar_y_cargar_modelo(tmp_path):
    model_path = tmp_path / 'predictor.pkl'
    predictor = GoldPredictor(tf='4H', direccion='VENTA', model_path=str(model_path))
    predictor.entrenar('VENTA', _build_training_rows(60, 'VENTA'))
    predictor.guardar_modelo(str(model_path))

    nuevo = GoldPredictor(tf='4H', direccion='VENTA', model_path=str(model_path))
    assert nuevo.cargar_modelo(str(model_path)) is True
    assert nuevo.necesita_reentrenamiento(min_muestras=30) is False


def test_entrenar_con_datos_insuficientes_no_habilita_prediccion():
    predictor = GoldPredictor(tf='1H', direccion='COMPRA')
    predictor.entrenar('COMPRA', _build_training_rows(10, 'COMPRA'))

    prob, label = predictor.predecir(_sample_features(1, True))
    assert prob == 0.0
    assert label == 'NEUTRO'


def test_reentrenar_desde_bd_con_mock():
    class _Result:
        def __init__(self, rows):
            self.rows = rows

    class _DB:
        def __init__(self, rows):
            self._rows = rows

        def ejecutar_query(self, query, params=()):
            return _Result(self._rows)

    rows = _build_training_rows(50, 'COMPRA')
    predictor = GoldPredictor(tf='1H', direccion='COMPRA')
    ok = predictor.reentrenar_desde_bd(_DB(rows), min_muestras=30)

    assert ok is True
    assert predictor.modelo_rf is not None


def test_generar_muestras_backtest_compra_genera_win_y_loss(monkeypatch):
    predictor = GoldPredictor(tf='1H', direccion='COMPRA')
    df = _df_backtest_base()

    monkeypatch.setattr('core.predictor.get_ohlcv', lambda *args, **kwargs: (df, False))
    monkeypatch.setattr(GoldPredictor, '_normalizar_ohlcv', staticmethod(lambda x: x.copy()))
    monkeypatch.setattr('core.predictor.calcular_sr_multiples', lambda *_args, **_kwargs: ([99.8], [100.2]))

    muestras = predictor.generar_muestras_backtest(tf='1h', period='3mo', direccion='COMPRA', velas_forward=1)

    assert muestras
    wins = [m['win'] for m in muestras]
    assert 1 in wins
    assert 0 in wins


def test_generar_muestras_backtest_fallback_seguro_si_falla_fuente(monkeypatch):
    predictor = GoldPredictor(tf='1H', direccion='COMPRA')
    monkeypatch.setattr('core.predictor.get_ohlcv', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('boom')))

    muestras = predictor.generar_muestras_backtest()

    assert muestras == []
