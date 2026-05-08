import pandas as pd

from core.predictor import GoldPredictor


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
            'ema20': [100.0] * n,
            'ema50': [100.0] * n,
            'macd_hist': [0.1] * n,
            'obv': list(range(n)),
            'adx': [20.0] * n,
            'bb_upper': [101.0] * n,
            'bb_lower': [99.0] * n,
        },
        index=idx,
    )


def test_generar_muestras_backtest_compra_genera_win_y_loss(monkeypatch):
    predictor = GoldPredictor(tf='1H', direccion='COMPRA')
    df = _df_backtest_base()

    monkeypatch.setattr('core.predictor.get_ohlcv', lambda *args, **kwargs: (df, False))
    monkeypatch.setattr(GoldPredictor, '_normalizar_ohlcv', staticmethod(lambda x: x.copy()))
    monkeypatch.setattr('core.predictor.calcular_sr_multiples', lambda *_args, **_kwargs: ([99.8], [100.2]))
    monkeypatch.setattr(
        GoldPredictor,
        'calcular_features_predictivos',
        lambda *_args, **_kwargs: {
            'en_zona_soporte': 1,
            'en_zona_resist': 0,
            'dist_soporte_pct': 0.2,
            'dist_resist_pct': 0.2,
            'rsi_nivel': 0.5,
            'macd_hist_mejorando': 1,
            'mecha_inferior_pct': 0.2,
            'mecha_superior_pct': 0.2,
            'atr_contrayendo': 1,
            'vol_relativo_3v': None,
            'close_vs_ema20_pct': 0.0,
            'adx_nivel': 0.2,
        },
    )

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
