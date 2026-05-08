import importlib.util
from pathlib import Path


_SPEC = importlib.util.spec_from_file_location(
    'train_predictor',
    Path(__file__).resolve().parents[2] / 'scripts' / 'train_predictor.py',
)
train_predictor = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(train_predictor)


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def ejecutar_query(self, _q, _params=()):
        return _FakeResult(self._rows)


def test_cargar_datos_entrenamiento_combina_bd_y_backtest(monkeypatch):
    rows = [
        {
            'timestamp': '2026-01-01T00:00:00Z',
            'estado': 'TP1',
            'indicadores': '{"en_zona_soporte": true, "rsi_nivel": 0.4}',
        }
    ]
    db = _FakeDB(rows)

    monkeypatch.setattr(
        train_predictor.GoldPredictor,
        'generar_muestras_backtest',
        lambda *args, **kwargs: [
            {'en_zona_soporte': 1, 'rsi_nivel': 0.3, 'win': 1},
            {'en_zona_soporte': 0, 'rsi_nivel': 0.7, 'win': 0},
        ],
    )

    df = train_predictor.cargar_datos_entrenamiento(db, tf='1H', direccion='COMPRA', backtest_period='3mo')

    assert len(df) == 3
    assert (df['source'] == 'bd').sum() == 1
    assert (df['source'] == 'backtest').sum() == 2
    assert float(df[df['source'] == 'bd']['weight'].iloc[0]) == 2.0
    assert all(df[df['source'] == 'backtest']['weight'] == 1.0)


def test_cargar_datos_entrenamiento_force_backtest(monkeypatch):
    rows = [
        {
            'timestamp': '2026-01-01T00:00:00Z',
            'estado': 'TP1',
            'indicadores': '{"en_zona_soporte": true, "rsi_nivel": 0.4}',
        }
        for _ in range(35)
    ]
    db = _FakeDB(rows)

    calls = {'count': 0}

    def _bt(*args, **kwargs):
        calls['count'] += 1
        return [{'en_zona_soporte': 1, 'rsi_nivel': 0.5, 'win': 1}]

    monkeypatch.setattr(train_predictor.GoldPredictor, 'generar_muestras_backtest', _bt)

    df = train_predictor.cargar_datos_entrenamiento(
        db,
        tf='1H',
        direccion='COMPRA',
        backtest_period='3mo',
        force_backtest=True,
    )

    assert calls['count'] == 1
    assert (df['source'] == 'backtest').sum() == 1
