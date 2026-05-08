from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.database import get_db
from core.predictor import GoldPredictor


def _train_one(db, tf: str, direction: str) -> bool:
    predictor = GoldPredictor(tf=tf, direccion=direction)
    ok = predictor.reentrenar_desde_bd(db)
    model_path = Path('models') / f"predictor_gold_{tf}_{'compra' if direction == 'COMPRA' else 'venta'}.pkl"

    if ok:
        metrics = predictor.last_metrics or {}
        print(f"✅ Modelo entrenado: TF={tf} DIR={direction}")
        print(f"   💾 {model_path}")
        print(
            "   📊 "
            f"accuracy={metrics.get('accuracy', 0.0):.3f} "
            f"precision={metrics.get('precision', 0.0):.3f} "
            f"recall={metrics.get('recall', 0.0):.3f}"
        )
    else:
        print(f"⚠️  No se pudo entrenar TF={tf} DIR={direction} (muestras insuficientes o error)")

    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description='Entrena predictor ML anticipado para GOLD')
    parser.add_argument('--tf', choices=['1H', '4H'], help='Timeframe a entrenar')
    parser.add_argument('--direction', choices=['COMPRA', 'VENTA'], help='Dirección a entrenar')
    args = parser.parse_args()

    db = get_db()
    Path('models').mkdir(parents=True, exist_ok=True)

    tfs = [args.tf] if args.tf else ['1H', '4H']
    dirs = [args.direction] if args.direction else ['COMPRA', 'VENTA']

    ok_any = False
    for tf in tfs:
        for direction in dirs:
            ok_any = _train_one(db, tf, direction) or ok_any

    return 0 if ok_any else 1


if __name__ == '__main__':
    raise SystemExit(main())
