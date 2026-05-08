from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.database import get_db
from core.predictor import ESTADOS_LOSS, ESTADOS_WIN, GoldPredictor

MIN_MUESTRAS_BD = 30
DEFAULT_FEATURE_FILL = 1.0  # fallback consistente para features NaN (incl. volumen plano en XAU/USD)


def cargar_senales_bd(db, tf: str, direccion: str, feature_cols: list[str]) -> list[dict]:
    if db is None:
        return []

    query = """
    SELECT timestamp, direccion, estado, indicadores
    FROM senales
    WHERE estado IN ('TP1','TP2','TP3','BREAKEVEN','SL')
      AND timeframe = ?
      AND direccion = ?
      AND indicadores IS NOT NULL
    ORDER BY timestamp ASC
    """
    result = db.ejecutar_query(query, (tf.upper(), direccion.upper()))

    rows = []
    for row in result.rows:
        raw = row.get('indicadores') or '{}'
        try:
            ind = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            continue
        estado = (row.get('estado') or '').upper()
        if estado not in ESTADOS_WIN | ESTADOS_LOSS:
            continue

        sample = {f: ind.get(f) for f in feature_cols}
        sample['win'] = 1 if estado in ESTADOS_WIN else 0
        sample['timestamp'] = row.get('timestamp')
        rows.append(sample)

    return rows


def cargar_datos_entrenamiento(
    db,
    tf: str,
    direccion: str,
    backtest_period: str = '3mo',
    force_backtest: bool = False,
) -> pd.DataFrame:
    """
    Estrategia híbrida:
    1. Carga señales cerradas de BD Turso
    2. Si hay pocas, genera muestras históricas desde Twelve Data
    3. Combina ambas fuentes, priorizando BD (peso x2)
    """
    predictor_tmp = GoldPredictor(tf=tf, direccion=direccion)

    senales_bd = cargar_senales_bd(db, tf, direccion, predictor_tmp.FEATURE_COLUMNS)
    print(f"  📊 Señales BD: {len(senales_bd)}")

    muestras_bt = []
    if force_backtest or len(senales_bd) < MIN_MUESTRAS_BD:
        if not force_backtest:
            print(f"  ⚠️  Pocas señales en BD ({len(senales_bd)} < {MIN_MUESTRAS_BD})")
        print("  📡 Generando muestras históricas desde Twelve Data...")

        interval_map = {'1H': '1h', '4H': '4h', '1D': '1d'}
        interval = interval_map.get(tf.upper(), '1h')

        try:
            muestras_bt = predictor_tmp.generar_muestras_backtest(
                ticker_yf='GC=F',
                tf=interval,
                period=backtest_period,
                direccion=direccion,
            )
        except Exception as exc:
            print(f"  ⚠️  Warning: fallo generando backtest ({exc})")
            muestras_bt = []

        print(f"  📈 Muestras backtest generadas: {len(muestras_bt)}")

    df_bd = pd.DataFrame(senales_bd) if senales_bd else pd.DataFrame()
    df_bt = pd.DataFrame(muestras_bt) if muestras_bt else pd.DataFrame()

    if not df_bd.empty:
        df_bd['source'] = 'bd'
        df_bd['weight'] = 2.0
    if not df_bt.empty:
        df_bt['source'] = 'backtest'
        df_bt['weight'] = 1.0

    frames = [f for f in [df_bd, df_bt] if not f.empty]
    if not frames:
        raise ValueError('Sin datos de entrenamiento disponibles')

    df_combined = pd.concat(frames, ignore_index=True)
    wins = int(pd.to_numeric(df_combined['win'], errors='coerce').fillna(0).sum())
    losses = int((pd.to_numeric(df_combined['win'], errors='coerce').fillna(0) == 0).sum())
    bd_count = len(df_bd) if not df_bd.empty else 0
    bt_count = len(df_bt) if not df_bt.empty else 0
    print(
        f"  ✅ Dataset final: {len(df_combined)} muestras "
        f"(BD: {bd_count} | Backtest: {bt_count}) | "
        f"WIN: {wins} | LOSS: {losses}"
    )
    return df_combined


def entrenar_modelo(df: pd.DataFrame, predictor: GoldPredictor) -> dict:
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
    except Exception as exc:
        raise RuntimeError(f'sklearn no disponible: {exc}') from exc

    feature_cols = [c for c in predictor.FEATURE_COLUMNS if c in df.columns]
    if not feature_cols:
        raise ValueError('No hay columnas de features para entrenar')

    X = df[feature_cols].copy()
    for col in X.columns:
        X[col] = pd.to_numeric(X[col], errors='coerce')
    X = X.fillna(DEFAULT_FEATURE_FILL)
    y = pd.to_numeric(df['win'], errors='coerce').fillna(0).astype(int)

    split_idx = max(1, int(len(df) * 0.8))
    if split_idx >= len(df):
        split_idx = len(df) - 1
    if split_idx <= 0:
        raise ValueError('No hay suficientes muestras para split train/test')

    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    if X_test.empty:
        raise ValueError('No hay set de test OOS; añade más muestras')

    rf = RandomForestClassifier(
        n_estimators=300,
        random_state=42,
        max_depth=8,
        min_samples_leaf=3,
        class_weight='balanced_subsample',
    )

    sample_weight = None
    if 'weight' in df.columns:
        sample_weight = pd.to_numeric(df['weight'].iloc[:split_idx], errors='coerce').fillna(1.0).values

    rf.fit(X_train, y_train, sample_weight=sample_weight)

    y_pred = rf.predict(X_test)

    predictor.model_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        'model': rf,
        'feature_columns': feature_cols,
        'tf': predictor.tf,
        'direccion': predictor.direccion,
    }
    import pickle
    with predictor.model_path.open('wb') as f:
        pickle.dump(payload, f)

    feat_imp = sorted(
        [(feature_cols[i], float(v)) for i, v in enumerate(rf.feature_importances_)],
        key=lambda x: x[1],
        reverse=True,
    )

    return {
        'accuracy': float(accuracy_score(y_test, y_pred)),
        'precision': float(precision_score(y_test, y_pred, zero_division=0)),
        'recall': float(recall_score(y_test, y_pred, zero_division=0)),
        'f1': float(f1_score(y_test, y_pred, zero_division=0)),
        'top_features': feat_imp[:5],
        'train_size': len(X_train),
        'test_size': len(X_test),
        'model_path': str(predictor.model_path),
        'bd_count': int((df['source'] == 'bd').sum()) if 'source' in df.columns else 0,
        'bt_count': int((df['source'] == 'backtest').sum()) if 'source' in df.columns else 0,
    }


def imprimir_resumen(tf: str, direccion: str, stats: dict, predictor: GoldPredictor):
    print('═' * 52)
    print(f'  RESULTADO ENTRENAMIENTO — GOLD {tf.upper()} {direccion.upper()}')
    print('═' * 52)
    print(f"  Fuente datos : BD Turso: {stats['bd_count']} señales + Backtest TD: {stats['bt_count']} muestras")
    print(f"  Total train  : {stats['train_size']} muestras  |  Test OOS: {stats['test_size']} muestras")
    print('  ' + '─' * 48)
    print(f"  Accuracy     : {stats['accuracy'] * 100:.1f}%")
    print(f"  Precision    : {stats['precision'] * 100:.1f}%")
    print(f"  Recall       : {stats['recall'] * 100:.1f}%")
    print(f"  F1-score     : {stats['f1'] * 100:.1f}%")
    print('  ' + '─' * 48)
    print('  Top 5 features más importantes:')
    for idx, (name, imp) in enumerate(stats['top_features'], start=1):
        print(f'    {idx}. {name:<24} {imp:.3f}')
    print('  ' + '─' * 48)
    print(f"  Modelo guardado: {stats['model_path']}")
    print(f"  Próximo reentrenamiento recomendado: {predictor.proximo_reentrenamiento()}")
    print('═' * 52)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Entrena predictor ML GOLD con estrategia híbrida BD + backtest')
    parser.add_argument('--tf', default='1H', help='Timeframe (1H, 4H, 1D)')
    parser.add_argument('--direccion', default='COMPRA', choices=['COMPRA', 'VENTA'])
    parser.add_argument('--backtest-period', default='3mo', help='Periodo histórico para backtest Twelve Data (3mo, 6mo, ...)')
    parser.add_argument('--force-backtest', action='store_true', help='Forzar backtest aunque haya suficientes datos BD')
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    db = get_db()
    predictor = GoldPredictor(tf=args.tf, direccion=args.direccion)

    try:
        df = cargar_datos_entrenamiento(
            db=db,
            tf=args.tf,
            direccion=args.direccion,
            backtest_period=args.backtest_period,
            force_backtest=args.force_backtest,
        )
        stats = entrenar_modelo(df, predictor)
        imprimir_resumen(args.tf, args.direccion, stats, predictor)
        return 0
    except Exception as exc:
        print(f'❌ Error en entrenamiento: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
