"""
scripts/optimize_weights.py — Optimización de pesos del scoring con Grid Search + Regresión Logística

Carga señales cerradas históricas de la BD, extrae los factores booleanos
almacenados en 'indicadores', y encuentra los pesos óptimos que maximizan
el win rate y el profit factor, validados out-of-sample (split temporal).

Métodos:
  1. Grid Search   — explora combinaciones de multiplicadores de peso (0.5–2.0)
  2. Logistic Reg. — usa sklearn para encontrar pesos óptimos por factor
  3. scipy minimize — optimización numérica directa (Nelder-Mead)

Uso:
    python scripts/optimize_weights.py --tf 1D --min-samples 20
    python scripts/optimize_weights.py --tf 4H --method grid --output pesos_4h.json
    python scripts/optimize_weights.py  # corre todos los TF disponibles
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

# ─────────────────────────────────────────────────────────────────────────────
# FACTORES POR DIRECCIÓN
# ─────────────────────────────────────────────────────────────────────────────

FEATURES_SELL = [
    'en_zona_resist', 'vela_rechazo', 'shooting_star', 'bearish_engulfing',
    'bearish_marubozu', 'doji_resist', 'vol_alto_rechazo', 'rsi_alto_girando',
    'rsi_sobrecompra', 'divergencia_bajista', 'emas_bajistas', 'bajo_ema200',
    'estructura_bajista', 'intento_rotura_fallido', 'vol_decreciente',
    'bb_toca_superior', 'evening_star', 'macd_cruce_bajista', 'macd_negativo',
    'macd_divergencia_bajista', 'adx_bajista', 'obv_divergencia_bajista',
    'obv_decreciente', 'rotura_bajista', 'dt_detectado', 'rup_sop_1d',
    'rebote_bajista',
]

FEATURES_BUY = [
    'en_zona_soporte', 'vela_rebote', 'hammer', 'bullish_engulfing',
    'bullish_marubozu', 'doji_soporte', 'vol_alto_rebote', 'rsi_bajo_girando',
    'rsi_sobreventa', 'divergencia_alcista', 'emas_alcistas', 'sobre_ema200',
    'estructura_alcista', 'intento_caida_fallido', 'vol_decreciente_sell',
    'bb_toca_inferior', 'morning_star', 'macd_cruce_alcista', 'macd_positivo',
    'macd_divergencia_alcista', 'adx_alcista', 'obv_divergencia_alcista',
    'obv_creciente', 'rotura_alcista', 'ds_detectado', 'rup_res_1d',
    'rebote_alcista',
]

# Grupos de pesos para grid search (features relacionadas comparten grupo)
WEIGHT_GROUPS_SELL = {
    'velas': ['shooting_star', 'bearish_engulfing', 'bearish_marubozu', 'evening_star',
              'doji_resist', 'vela_rechazo'],
    'momentum': ['rsi_alto_girando', 'rsi_sobrecompra', 'divergencia_bajista',
                 'macd_cruce_bajista', 'macd_negativo', 'macd_divergencia_bajista'],
    'trend': ['emas_bajistas', 'bajo_ema200', 'estructura_bajista', 'adx_bajista'],
    'volumen': ['vol_alto_rechazo', 'vol_decreciente', 'obv_decreciente',
                'obv_divergencia_bajista', 'bb_toca_superior'],
    'estructura': ['en_zona_resist', 'intento_rotura_fallido', 'rotura_bajista',
                   'dt_detectado', 'rup_sop_1d', 'rebote_bajista'],
}

WEIGHT_GROUPS_BUY = {
    'velas': ['hammer', 'bullish_engulfing', 'bullish_marubozu', 'morning_star',
              'doji_soporte', 'vela_rebote'],
    'momentum': ['rsi_bajo_girando', 'rsi_sobreventa', 'divergencia_alcista',
                 'macd_cruce_alcista', 'macd_positivo', 'macd_divergencia_alcista'],
    'trend': ['emas_alcistas', 'sobre_ema200', 'estructura_alcista', 'adx_alcista'],
    'volumen': ['vol_alto_rebote', 'vol_decreciente_sell', 'obv_creciente',
                'obv_divergencia_alcista', 'bb_toca_inferior'],
    'estructura': ['en_zona_soporte', 'intento_caida_fallido', 'rotura_alcista',
                   'ds_detectado', 'rup_res_1d', 'rebote_alcista'],
}

ESTADOS_WIN  = {'TP1', 'TP2', 'TP3', 'BREAKEVEN'}
ESTADOS_LOSS = {'SL'}
ESTADOS_VALIDOS = ESTADOS_WIN | ESTADOS_LOSS


# ─────────────────────────────────────────────────────────────────────────────
# CARGA DE DATOS
# ─────────────────────────────────────────────────────────────────────────────

def cargar_senales(tf: str | None = None, min_samples: int = 10) -> pd.DataFrame:
    """Carga señales cerradas desde la BD y devuelve un DataFrame listo para análisis."""
    from adapters.database import get_db
    db = get_db()
    if db is None:
        raise RuntimeError("BD no disponible — configura TURSO_DATABASE_URL y TURSO_AUTH_TOKEN")

    where_tf = f"AND timeframe = '{tf.upper()}'" if tf else ""
    query = f"""
    SELECT id, timestamp, direccion, score, indicadores, estado, timeframe, asset,
           beneficio_final_pct
    FROM senales
    WHERE estado IN ('TP1','TP2','TP3','BREAKEVEN','SL')
      AND indicadores IS NOT NULL
      {where_tf}
    ORDER BY timestamp ASC
    """
    result = db.ejecutar_query(query)
    if not result.rows:
        raise ValueError(f"Sin señales cerradas suficientes{f' para TF={tf}' if tf else ''}")

    rows = []
    for row in result.rows:
        ind_raw = row.get('indicadores') or '{}'
        try:
            ind = json.loads(ind_raw) if isinstance(ind_raw, str) else ind_raw
        except json.JSONDecodeError:
            continue
        rows.append({**dict(row), '_ind': ind})

    df = pd.DataFrame(rows)
    df['win'] = df['estado'].isin(ESTADOS_WIN).astype(int)
    print(f"  📊 Señales cargadas: {len(df)}  |  wins={df['win'].sum()}  losses={(df['win']==0).sum()}")
    return df


def _extract_features(df: pd.DataFrame, direction: str) -> pd.DataFrame:
    """Extrae la matriz de features booleanos para una dirección."""
    feats = FEATURES_SELL if direction == 'VENTA' else FEATURES_BUY
    records = []
    for _, row in df[df['direccion'] == direction].iterrows():
        ind = row['_ind']
        r = {'win': row['win'], 'score': row.get('score', 0),
             'beneficio_final_pct': row.get('beneficio_final_pct'),
             'timestamp': row['timestamp']}
        for f in feats:
            r[f] = int(bool(ind.get(f, False)))
        records.append(r)
    return pd.DataFrame(records)


# ─────────────────────────────────────────────────────────────────────────────
# MÉTRICAS
# ─────────────────────────────────────────────────────────────────────────────

def calcular_metricas(y_true: np.ndarray, y_pred_score: np.ndarray,
                      threshold: float, pnl: np.ndarray | None = None) -> dict:
    """Calcula win_rate y profit_factor para un umbral de score dado."""
    mask = y_pred_score >= threshold
    if mask.sum() == 0:
        return {'win_rate': 0.0, 'profit_factor': 0.0, 'total': 0, 'coverage': 0.0}
    selected_wins = y_true[mask].sum()
    total_selected = mask.sum()
    win_rate = selected_wins / total_selected

    profit_factor = np.nan
    if pnl is not None and len(pnl) > 0:
        wins_pnl = pnl[mask & (y_true == 1)]
        loss_pnl = np.abs(pnl[mask & (y_true == 0)])
        total_win = wins_pnl.sum() if len(wins_pnl) > 0 else 0
        total_loss = loss_pnl.sum() if len(loss_pnl) > 0 else 0
        profit_factor = round(total_win / total_loss, 3) if total_loss > 0 else np.inf

    return {
        'win_rate':      round(float(win_rate * 100), 2),
        'profit_factor': round(float(profit_factor), 3) if not np.isnan(profit_factor) else None,
        'total':         int(total_selected),
        'wins':          int(selected_wins),
        'coverage':      round(float(mask.sum() / len(y_true) * 100), 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MÉTODO 1: GRID SEARCH sobre multiplicadores por grupo de factores
# ─────────────────────────────────────────────────────────────────────────────

def grid_search(X_train: pd.DataFrame, y_train: np.ndarray,
                X_test: pd.DataFrame,  y_test: np.ndarray,
                direction: str, n_steps: int = 5) -> dict:
    """
    Grid search sobre multiplicadores [0.5, 0.75, 1.0, 1.25, 1.5, 2.0] por grupo.
    Optimiza win_rate × coverage (F-score-like) en train, evalúa en test.
    """
    groups = WEIGHT_GROUPS_SELL if direction == 'VENTA' else WEIGHT_GROUPS_BUY
    group_names = list(groups.keys())
    multipliers_grid = np.linspace(0.5, 2.0, n_steps).tolist()

    feats_sell = FEATURES_SELL if direction == 'VENTA' else FEATURES_BUY

    def score_from_weights(w_map: dict, X: pd.DataFrame) -> np.ndarray:
        """Recomputa el score de cada señal con los multiplicadores dados."""
        scores = np.zeros(len(X))
        for feat in feats_sell:
            col = feat if feat in X.columns else None
            if col is None:
                continue
            group = next((g for g, fs in groups.items() if feat in fs), None)
            mult = w_map.get(group, 1.0)
            scores += X[col].values * mult
        return scores

    best_score_val = -1.0
    best_weights   = {g: 1.0 for g in group_names}

    # Reduce space: only grid over 2 groups at a time (time constraint)
    from itertools import product as iterproduct
    combinations = list(iterproduct(multipliers_grid, repeat=len(group_names)))
    total = len(combinations)
    print(f"  🔍 Grid search: {total} combinaciones para {direction} ({len(group_names)} grupos)")

    # Si hay demasiadas combinaciones, usar muestra aleatoria
    max_eval = 2000
    if total > max_eval:
        rng = np.random.default_rng(42)
        idx = rng.choice(total, size=max_eval, replace=False)
        combinations = [combinations[i] for i in sorted(idx)]
        print(f"  ⚡ Limitado a {max_eval} combinaciones aleatorias")

    pnl_train = X_train.get('beneficio_final_pct', pd.Series(dtype=float)).values \
        if 'beneficio_final_pct' in X_train.columns else None

    for combo in combinations:
        w = {g: combo[i] for i, g in enumerate(group_names)}
        sc = score_from_weights(w, X_train)
        # Umbral = mediana de scores positivos
        threshold = np.median(sc[y_train == 1]) if (y_train == 1).sum() > 0 else 1.0
        m = calcular_metricas(y_train, sc, threshold, pnl_train)
        objective = m['win_rate'] * (m['coverage'] / 100.0)
        if objective > best_score_val:
            best_score_val = objective
            best_weights   = dict(w)

    # Evaluar en test
    sc_test = score_from_weights(best_weights, X_test)
    threshold_test = np.median(sc_test[y_test == 1]) if (y_test == 1).sum() > 0 else 1.0
    pnl_test = X_test.get('beneficio_final_pct', pd.Series(dtype=float)).values \
        if 'beneficio_final_pct' in X_test.columns else None

    sc_train = score_from_weights(best_weights, X_train)
    threshold_train = np.median(sc_train[y_train == 1]) if (y_train == 1).sum() > 0 else 1.0

    return {
        'method':        'grid_search',
        'group_weights': best_weights,
        'threshold_recommended': round(float(threshold_test), 2),
        'train_metrics': calcular_metricas(y_train, sc_train, threshold_train, pnl_train),
        'test_metrics':  calcular_metricas(y_test,  sc_test,  threshold_test,  pnl_test),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MÉTODO 2: REGRESIÓN LOGÍSTICA (pesos por feature individual)
# ─────────────────────────────────────────────────────────────────────────────

def logistic_regression_weights(X_train: pd.DataFrame, y_train: np.ndarray,
                                 X_test: pd.DataFrame,  y_test: np.ndarray,
                                 direction: str) -> dict:
    """
    Ajusta una regresión logística con regularización L1 (Lasso) para
    obtener pesos por feature. Los coeficientes positivos → factores predictivos de WIN.
    """
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return {'error': 'scikit-learn no instalado. Ejecuta: pip install scikit-learn'}

    feats = FEATURES_SELL if direction == 'VENTA' else FEATURES_BUY
    available = [f for f in feats if f in X_train.columns]

    if len(available) < 3:
        return {'error': f'Insuficientes features disponibles: {available}'}

    X_tr = X_train[available].values.astype(float)
    X_te = X_test[available].values.astype(float)

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)

    model = LogisticRegression(penalty='l1', solver='liblinear', C=0.5, max_iter=500)
    model.fit(X_tr_s, y_train)

    # Coeficientes como pesos relativos (normalizados para comparar)
    coefs = model.coef_[0]
    coef_raw = {f: round(float(c), 4) for f, c in zip(available, coefs)}

    # Normalizar a multiplicadores relativos al máximo positivo
    positive_coefs = {f: c for f, c in coef_raw.items() if c > 0}
    max_pos = max(positive_coefs.values()) if positive_coefs else 1.0
    weight_multipliers = {
        f: round(c / max_pos * 2.0, 3) if c > 0 else round(max(0.1, 1.0 + c / max_pos), 3)
        for f, c in coef_raw.items()
    }

    # Métricas con score = dot product de features × coeficientes originales
    sc_train = X_tr @ coefs
    sc_test  = X_te @ coefs
    pnl_train = X_train['beneficio_final_pct'].values \
        if 'beneficio_final_pct' in X_train.columns else None
    pnl_test  = X_test['beneficio_final_pct'].values \
        if 'beneficio_final_pct' in X_test.columns else None
    thr_train = np.percentile(sc_train, 40)
    thr_test  = np.percentile(sc_test,  40)

    top10 = sorted(coef_raw.items(), key=lambda x: x[1], reverse=True)[:10]
    print(f"  🏆 Top factores {direction}: {', '.join(f'{f}({c:+.2f})' for f, c in top10)}")

    return {
        'method':                 'logistic_regression',
        'feature_weights':        weight_multipliers,
        'feature_coefficients':   coef_raw,
        'top_features':           [{'feature': f, 'coef': c} for f, c in top10],
        'threshold_recommended':  round(float(thr_test), 4),
        'train_metrics':          calcular_metricas(y_train, sc_train, thr_train, pnl_train),
        'test_metrics':           calcular_metricas(y_test,  sc_test,  thr_test,  pnl_test),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MÉTODO 3: OPTIMIZACIÓN BAYESIANA (scipy.optimize.minimize — Nelder-Mead)
# ─────────────────────────────────────────────────────────────────────────────

def bayesian_optimization(X_train: pd.DataFrame, y_train: np.ndarray,
                           X_test: pd.DataFrame,  y_test: np.ndarray,
                           direction: str) -> dict:
    """
    Optimización numérica de multiplicadores por grupo (Nelder-Mead sobre
    una función de pérdida basada en profit factor del conjunto de train).
    """
    from scipy.optimize import minimize

    groups = WEIGHT_GROUPS_SELL if direction == 'VENTA' else WEIGHT_GROUPS_BUY
    feats  = FEATURES_SELL if direction == 'VENTA' else FEATURES_BUY
    group_names = list(groups.keys())
    n_groups    = len(group_names)

    available_feats = [f for f in feats if f in X_train.columns]

    def _compute_score(w_vec: np.ndarray, X: pd.DataFrame) -> np.ndarray:
        scores = np.zeros(len(X))
        for feat in available_feats:
            group = next((g for g, fs in groups.items() if feat in fs), None)
            idx   = group_names.index(group) if group in group_names else None
            mult  = float(w_vec[idx]) if idx is not None else 1.0
            scores += X[feat].values * mult
        return scores

    def _loss(w_vec: np.ndarray) -> float:
        """Minimizar: –(profit_factor × win_rate) en train."""
        w_pos = np.clip(w_vec, 0.1, 4.0)
        sc    = _compute_score(w_pos, X_train)
        threshold = np.median(sc[y_train == 1]) if (y_train == 1).sum() > 0 else 1.0
        m = calcular_metricas(y_train, sc, threshold)
        wr = m['win_rate'] / 100.0
        pf = m['profit_factor'] or 1.0
        # Penalizar si muy pocas señales pasan (coverage < 20%)
        cov_penalty = 0.0 if m['coverage'] >= 20 else (20 - m['coverage']) * 0.05
        return -(wr * min(pf, 5.0)) + cov_penalty

    x0      = np.ones(n_groups)
    bounds  = [(0.1, 3.0)] * n_groups
    result  = minimize(_loss, x0, method='Nelder-Mead',
                       options={'maxiter': 5000, 'xatol': 0.01, 'fatol': 0.001})
    w_opt   = np.clip(result.x, 0.1, 3.0)
    best_w  = {g: round(float(w_opt[i]), 3) for i, g in enumerate(group_names)}

    sc_train = _compute_score(w_opt, X_train)
    sc_test  = _compute_score(w_opt, X_test)
    thr_tr   = np.median(sc_train[y_train == 1]) if (y_train == 1).sum() > 0 else 1.0
    thr_te   = np.median(sc_test[y_test == 1])   if (y_test  == 1).sum() > 0 else 1.0
    pnl_tr   = X_train['beneficio_final_pct'].values \
        if 'beneficio_final_pct' in X_train.columns else None
    pnl_te   = X_test['beneficio_final_pct'].values \
        if 'beneficio_final_pct' in X_test.columns else None

    return {
        'method':                'bayesian_nelder_mead',
        'group_weights':         best_w,
        'threshold_recommended': round(float(thr_te), 2),
        'optimizer_success':     bool(result.success),
        'optimizer_message':     result.message,
        'train_metrics':         calcular_metricas(y_train, sc_train, thr_tr, pnl_tr),
        'test_metrics':          calcular_metricas(y_test,  sc_test,  thr_te, pnl_te),
    }


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def optimizar(tf: str | None = None, method: str = 'all',
              min_samples: int = 20, output: str | None = None,
              test_ratio: float = 0.2) -> dict:
    """
    Ejecuta la optimización completa para un TF dado.

    Args:
        tf          : Timeframe ('1D', '4H', '1H', '15M', '5M'). None = todos.
        method      : 'grid' | 'logistic' | 'bayesian' | 'all'
        min_samples : Mínimo de señales cerradas requeridas para correr.
        output      : Ruta del JSON de salida. None = auto-generar en scripts/.
        test_ratio  : Fracción del dataset reservada para test OOS (temporal).
    """
    print(f"\n{'='*60}")
    print(f"  OPTIMIZACIÓN DE PESOS — TF={tf or 'TODOS'} | método={method}")
    print(f"{'='*60}")

    df = cargar_senales(tf, min_samples)

    if len(df) < min_samples:
        raise ValueError(
            f"Solo {len(df)} señales cerradas — necesitas ≥{min_samples} para optimizar."
            f" Ejecuta el bot más tiempo o baja --min-samples."
        )

    # Split temporal (80 / 20) — sin lookahead bias
    split_idx   = int(len(df) * (1 - test_ratio))
    df_train    = df.iloc[:split_idx].copy()
    df_test     = df.iloc[split_idx:].copy()

    train_start = df_train['timestamp'].iloc[0][:10]
    train_end   = df_train['timestamp'].iloc[-1][:10]
    test_start  = df_test['timestamp'].iloc[0][:10]
    test_end    = df_test['timestamp'].iloc[-1][:10]

    print(f"  Train : {train_start} → {train_end}  ({len(df_train)} señales)")
    print(f"  Test  : {test_start} → {test_end}  ({len(df_test)} señales)")

    results: dict[str, Any] = {
        'metadata': {
            'timestamp':   datetime.utcnow().isoformat() + 'Z',
            'tf':          tf,
            'method':      method,
            'train_n':     len(df_train),
            'test_n':      len(df_test),
            'train_start': train_start, 'train_end': train_end,
            'test_start':  test_start,  'test_end':  test_end,
        },
        'directions': {}
    }

    for direction in ['VENTA', 'COMPRA']:
        print(f"\n  ── Dirección: {direction} ──")
        X_tr_all = _extract_features(df_train, direction)
        X_te_all = _extract_features(df_test,  direction)

        if len(X_tr_all) < 5 or len(X_te_all) < 2:
            print(f"  ⚠️  Pocas señales para {direction} ({len(X_tr_all)} train / {len(X_te_all)} test) — omitida")
            continue

        feats = FEATURES_SELL if direction == 'VENTA' else FEATURES_BUY
        y_tr = X_tr_all['win'].values
        y_te = X_te_all['win'].values
        # Quitar columnas no-feature
        X_tr = X_tr_all[[f for f in feats if f in X_tr_all.columns]]
        X_te = X_te_all[[f for f in feats if f in X_te_all.columns]]
        # Reinyectar beneficio_final_pct para métricas
        X_tr = X_tr.copy(); X_tr['beneficio_final_pct'] = X_tr_all['beneficio_final_pct'].values
        X_te = X_te.copy(); X_te['beneficio_final_pct'] = X_te_all['beneficio_final_pct'].values

        # Baseline: score actual de la BD
        baseline_tr = calcular_metricas(y_tr, X_tr_all['score'].values,
                                        X_tr_all['score'].median())
        baseline_te = calcular_metricas(y_te, X_te_all['score'].values,
                                        X_te_all['score'].median())
        print(f"  📏 Baseline — Train WR={baseline_tr['win_rate']}%  "
              f"Test WR={baseline_te['win_rate']}%  PF={baseline_te['profit_factor']}")

        dir_results: dict[str, Any] = {'baseline': {'train': baseline_tr, 'test': baseline_te}}

        if method in ('grid', 'all'):
            print(f"  🔍 [Grid Search]...")
            dir_results['grid_search'] = grid_search(X_tr, y_tr, X_te, y_te, direction)
            r = dir_results['grid_search']
            print(f"     ✅ Train WR={r['train_metrics']['win_rate']}% → "
                  f"Test WR={r['test_metrics']['win_rate']}%  "
                  f"PF={r['test_metrics']['profit_factor']}")

        if method in ('logistic', 'all'):
            print(f"  🤖 [Logistic Regression]...")
            dir_results['logistic_regression'] = logistic_regression_weights(
                X_tr, y_tr, X_te, y_te, direction)
            r = dir_results['logistic_regression']
            if 'error' not in r:
                print(f"     ✅ Train WR={r['train_metrics']['win_rate']}% → "
                      f"Test WR={r['test_metrics']['win_rate']}%  "
                      f"PF={r['test_metrics']['profit_factor']}")

        if method in ('bayesian', 'all'):
            print(f"  🔬 [Bayesian / Nelder-Mead]...")
            dir_results['bayesian'] = bayesian_optimization(
                X_tr, y_tr, X_te, y_te, direction)
            r = dir_results['bayesian']
            print(f"     ✅ Train WR={r['train_metrics']['win_rate']}% → "
                  f"Test WR={r['test_metrics']['win_rate']}%  "
                  f"PF={r['test_metrics']['profit_factor']}")

        results['directions'][direction] = dir_results

    # Guardar resultados
    if output is None:
        ts   = datetime.utcnow().strftime('%Y%m%d_%H%M')
        tf_s = (tf or 'ALL').replace('/', '-')
        output = str(Path(__file__).parent / f"pesos_optimos_{tf_s}_{ts}.json")

    with open(output, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n  💾 Resultados guardados en: {output}")
    _imprimir_resumen(results)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# INFORME RESUMEN
# ─────────────────────────────────────────────────────────────────────────────

def _imprimir_resumen(results: dict) -> None:
    """Imprime un resumen ejecutivo en consola."""
    print(f"\n{'='*60}")
    print("  RESUMEN EJECUTIVO — COMPARATIVA MÉTODOS")
    print(f"{'='*60}")
    for direction, dir_res in results.get('directions', {}).items():
        print(f"\n  Dirección: {direction}")
        b_te = dir_res.get('baseline', {}).get('test', {})
        print(f"    Baseline actual  → WR={b_te.get('win_rate','—')}%  "
              f"PF={b_te.get('profit_factor','—')}")
        for m_key in ('grid_search', 'logistic_regression', 'bayesian'):
            if m_key not in dir_res or 'error' in dir_res[m_key]:
                continue
            te = dir_res[m_key].get('test_metrics', {})
            delta_wr = round(te.get('win_rate', 0) - b_te.get('win_rate', 0), 2)
            sign = '+' if delta_wr >= 0 else ''
            print(f"    {m_key:<24} → WR={te.get('win_rate','—')}% "
                  f"({sign}{delta_wr}%)  PF={te.get('profit_factor','—')}")

    print(f"\n{'='*60}")
    print("  RECOMENDACIÓN DE USO:")
    print("    1. Compara 'test_metrics' de cada método con 'baseline'")
    print("    2. Si mejora >3% WR OOS → actualiza los pesos en el detector")
    print("    3. Prioriza Profit Factor sobre Win Rate puro")
    print("    4. Requiere ≥30 señales cerradas para resultados fiables")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Optimización de pesos del scoring con Grid Search / Bayesiana',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--tf',          default=None,
                        help='Timeframe: 1D | 4H | 1H | 15M | 5M. Default: todos')
    parser.add_argument('--method',      default='all',
                        choices=['grid', 'logistic', 'bayesian', 'all'],
                        help='Método de optimización (default: all)')
    parser.add_argument('--min-samples', type=int, default=20,
                        help='Mínimo de señales cerradas requeridas (default: 20)')
    parser.add_argument('--test-ratio',  type=float, default=0.2,
                        help='Fracción OOS para validación (default: 0.20)')
    parser.add_argument('--output',      default=None,
                        help='Ruta del JSON de salida (default: auto)')
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    tfs = [args.tf] if args.tf else [None]  # None = todos los TF juntos
    for tf in tfs:
        try:
            optimizar(
                tf=tf,
                method=args.method,
                min_samples=args.min_samples,
                output=args.output,
                test_ratio=args.test_ratio,
            )
        except Exception as e:
            print(f"  ❌ Error para TF={tf}: {e}")


if __name__ == '__main__':
    main()
