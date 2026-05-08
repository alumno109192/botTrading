from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger('bottrading')


class GoldPredictor:
    FEATURE_COLUMNS = [
        'dist_soporte_pct',
        'dist_resist_pct',
        'rsi_direccion_3v',
        'rsi_nivel',
        'vol_relativo_3v',
        'macd_hist_mejorando',
        'mecha_inferior_pct',
        'mecha_superior_pct',
        'atr_contrayendo',
        'obv_divergencia',
        'precio_vs_ema_fast_pct',
        'velas_en_zona',
    ]

    def __init__(self, tf: str, direccion: str, model_path: str | None = None):
        self.tf = (tf or '').upper()
        self.direccion = (direccion or '').upper()
        self.modelo_rf = None
        self.modelo_lr = None
        self.scaler = None
        self.last_metrics: dict[str, float] = {}
        self.n_muestras = 0
        self.trained_at: datetime | None = None

        dir_slug = 'compra' if self.direccion == 'COMPRA' else 'venta'
        default_path = Path('models') / f'predictor_gold_{self.tf}_{dir_slug}.pkl'
        self.model_path = str(Path(model_path) if model_path else default_path)

        self.cargar_modelo(self.model_path)

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_timestamp(value: Any) -> pd.Timestamp:
        if value is None:
            return pd.Timestamp.min
        try:
            return pd.to_datetime(value, utc=True)
        except Exception:
            return pd.Timestamp.min

    @staticmethod
    def _parse_indicadores(raw: Any) -> dict:
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def _normalizar_features(self, features: dict[str, Any]) -> list[float]:
        return [self._to_float(features.get(col, 0.0)) for col in self.FEATURE_COLUMNS]

    def _estado_a_target(self, estado: str) -> int | None:
        estado = str(estado or '').upper().strip()
        if estado in {'TP1', 'TP2', 'TP3', 'BREAKEVEN'} or estado.startswith('IN TP'):
            return 1
        if estado == 'SL':
            return 0
        return None

    def entrenar(self, direccion: str, df_senales: list[dict]) -> None:
        self.direccion = (direccion or self.direccion or '').upper()

        samples: list[dict[str, Any]] = []
        for row in df_senales or []:
            indicadores = self._parse_indicadores(row.get('indicadores'))
            target = self._estado_a_target(row.get('estado'))
            if target is None:
                continue
            sample = {
                'timestamp': self._parse_timestamp(row.get('timestamp') or row.get('timestamp_entry')),
                'target': target,
            }
            for col in self.FEATURE_COLUMNS:
                sample[col] = self._to_float(indicadores.get(col, 0.0))
            samples.append(sample)

        self.n_muestras = len(samples)
        if self.n_muestras < 30:
            self.modelo_rf = None
            self.modelo_lr = None
            self.scaler = None
            self.last_metrics = {}
            logger.warning(
                f"[ML {self.tf} {self.direccion}] Muestras insuficientes ({self.n_muestras} < 30). "
                "Predicción deshabilitada"
            )
            return

        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.linear_model import LogisticRegression
            from sklearn.metrics import accuracy_score, precision_score, recall_score
            from sklearn.preprocessing import StandardScaler
        except Exception as e:
            logger.warning(f"[ML {self.tf} {self.direccion}] sklearn no disponible: {e}")
            return

        df = pd.DataFrame(samples).sort_values('timestamp').reset_index(drop=True)
        split_idx = int(len(df) * 0.8)
        split_idx = min(max(split_idx, 1), len(df) - 1)

        X = df[self.FEATURE_COLUMNS].astype(float)
        y = df['target'].astype(int)

        X_train = X.iloc[:split_idx]
        X_test = X.iloc[split_idx:]
        y_train = y.iloc[:split_idx]
        y_test = y.iloc[split_idx:]

        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)

        rf = RandomForestClassifier(
            n_estimators=200,
            max_depth=8,
            min_samples_leaf=3,
            class_weight='balanced_subsample',
            random_state=42,
        )
        lr = LogisticRegression(
            max_iter=500,
            class_weight='balanced',
            random_state=42,
        )

        rf.fit(X_train, y_train)
        lr.fit(X_train_scaled, y_train)

        rf_prob = rf.predict_proba(X_test)[:, 1]
        lr_prob = lr.predict_proba(X_test_scaled)[:, 1]
        avg_prob = (rf_prob + lr_prob) / 2.0
        y_pred = (avg_prob >= 0.5).astype(int)

        self.modelo_rf = rf
        self.modelo_lr = lr
        self.scaler = scaler
        self.trained_at = datetime.now(timezone.utc)
        self.last_metrics = {
            'accuracy': float(accuracy_score(y_test, y_pred)),
            'precision': float(precision_score(y_test, y_pred, zero_division=0)),
            'recall': float(recall_score(y_test, y_pred, zero_division=0)),
            'samples_train': int(len(X_train)),
            'samples_test': int(len(X_test)),
            'split_idx': int(split_idx),
        }

    def predecir(self, features: dict) -> tuple[float, str]:
        if self.modelo_rf is None or self.modelo_lr is None or self.scaler is None:
            return 0.0, 'NEUTRO'

        try:
            x = pd.DataFrame([self._normalizar_features(features)], columns=self.FEATURE_COLUMNS, dtype=float)
            prob_rf = float(self.modelo_rf.predict_proba(x)[0][1])
            prob_lr = float(self.modelo_lr.predict_proba(self.scaler.transform(x))[0][1])
            prob = max(0.0, min(1.0, (prob_rf + prob_lr) / 2.0))
            if prob >= 0.55:
                etiqueta = 'BUY' if self.direccion == 'COMPRA' else 'SELL'
            else:
                etiqueta = 'NEUTRO'
            return prob, etiqueta
        except Exception as e:
            logger.debug(f"[ML {self.tf} {self.direccion}] Predicción no disponible: {e}")
            return 0.0, 'NEUTRO'

    def calcular_features_predictivos(self, df: pd.DataFrame,
                                      zsl: float, zsh: float,
                                      zrl: float, zrh: float,
                                      atr: float) -> dict:
        if df is None or df.empty:
            return {k: 0.0 for k in self.FEATURE_COLUMNS}

        _df = df.copy()
        idx = -2 if len(_df) >= 2 else -1

        close = self._to_float(_df['Close'].iloc[idx])
        low = self._to_float(_df['Low'].iloc[idx])
        high = self._to_float(_df['High'].iloc[idx])
        open_ = self._to_float(_df['Open'].iloc[idx])

        rsi_series = _df['rsi'].dropna() if 'rsi' in _df.columns else pd.Series(dtype=float)
        rsi_direccion_3v = 0
        if len(rsi_series) >= 4:
            r0 = self._to_float(rsi_series.iloc[-2])
            r1 = self._to_float(rsi_series.iloc[-3])
            r2 = self._to_float(rsi_series.iloc[-4])
            if r0 < r1 < r2:
                rsi_direccion_3v = 1
            elif r0 > r1 > r2:
                rsi_direccion_3v = -1
        rsi_nivel = self._to_float(_df['rsi'].iloc[idx], 50.0) if 'rsi' in _df.columns else 50.0

        vol_relativo_3v = 1.0
        if 'Volume' in _df.columns and len(_df) >= 21:
            vol_3 = _df['Volume'].iloc[idx-2:idx+1].mean()
            vol_20 = _df['Volume'].iloc[idx-19:idx+1].mean()
            vol_relativo_3v = self._to_float(vol_3 / vol_20, 1.0) if vol_20 else 1.0

        macd_hist_mejorando = 0
        if 'macd_hist' in _df.columns and len(_df['macd_hist'].dropna()) >= 4:
            h0 = self._to_float(_df['macd_hist'].iloc[idx])
            h1 = self._to_float(_df['macd_hist'].iloc[idx-1])
            h2 = self._to_float(_df['macd_hist'].iloc[idx-2])
            mejora_buy = h2 < 0 and h1 > h2 and h0 > h1
            mejora_sell = h2 > 0 and h1 < h2 and h0 < h1
            macd_hist_mejorando = 1 if (mejora_buy or mejora_sell) else 0

        atr_val = max(self._to_float(atr, 0.0), 1e-9)
        mecha_inferior = max(0.0, min(close, open_) - low)
        mecha_superior = max(0.0, high - max(close, open_))
        mecha_inferior_pct = mecha_inferior / atr_val
        mecha_superior_pct = mecha_superior / atr_val

        atr_contrayendo = 0
        if 'atr' in _df.columns and len(_df) >= 21:
            atr_actual = self._to_float(_df['atr'].iloc[idx], atr_val)
            atr_media_20 = self._to_float(_df['atr'].iloc[idx-19:idx+1].mean(), atr_actual)
            atr_contrayendo = 1 if atr_actual < atr_media_20 else 0

        obv_divergencia = 0
        if 'obv' in _df.columns and len(_df) >= 8:
            sub = _df.iloc[idx-5:idx+1]
            if len(sub) >= 3:
                low_prev = self._to_float(sub['Low'].iloc[:-1].min())
                high_prev = self._to_float(sub['High'].iloc[:-1].max())
                obv_prev_min = self._to_float(sub['obv'].iloc[:-1].min())
                obv_prev_max = self._to_float(sub['obv'].iloc[:-1].max())
                low_curr = self._to_float(sub['Low'].iloc[-1])
                high_curr = self._to_float(sub['High'].iloc[-1])
                obv_curr = self._to_float(sub['obv'].iloc[-1])
                if low_curr < low_prev and obv_curr >= obv_prev_min:
                    obv_divergencia = 1
                elif high_curr > high_prev and obv_curr <= obv_prev_max:
                    obv_divergencia = -1

        ema_fast = self._to_float(_df['ema_fast'].iloc[idx], close) if 'ema_fast' in _df.columns else close

        velas_en_zona = 0
        recent = _df.iloc[max(0, idx - 20):idx + 1]
        for _, candle in recent.iloc[::-1].iterrows():
            candle_low = self._to_float(candle.get('Low'))
            candle_high = self._to_float(candle.get('High'))
            toca_soporte = candle_low <= zsh and candle_high >= zsl
            toca_resistencia = candle_low <= zrh and candle_high >= zrl
            if toca_soporte or toca_resistencia:
                velas_en_zona += 1
            else:
                break

        dist_soporte_pct = self._to_float(((close - zsh) / close * 100.0) if close else 0.0)
        dist_resist_pct = self._to_float(((zrl - close) / close * 100.0) if close else 0.0)
        precio_vs_ema_fast_pct = self._to_float(((close - ema_fast) / close * 100.0) if close else 0.0)

        return {
            'dist_soporte_pct': dist_soporte_pct,
            'dist_resist_pct': dist_resist_pct,
            'rsi_direccion_3v': float(rsi_direccion_3v),
            'rsi_nivel': rsi_nivel,
            'vol_relativo_3v': vol_relativo_3v,
            'macd_hist_mejorando': float(macd_hist_mejorando),
            'mecha_inferior_pct': mecha_inferior_pct,
            'mecha_superior_pct': mecha_superior_pct,
            'atr_contrayendo': float(atr_contrayendo),
            'obv_divergencia': float(obv_divergencia),
            'precio_vs_ema_fast_pct': precio_vs_ema_fast_pct,
            'velas_en_zona': float(velas_en_zona),
        }

    def guardar_modelo(self, path: str) -> None:
        if self.modelo_rf is None or self.modelo_lr is None or self.scaler is None:
            logger.warning(f"[ML {self.tf} {self.direccion}] Modelo no entrenado, no se guarda")
            return

        try:
            import joblib
        except Exception as e:
            logger.warning(f"[ML {self.tf} {self.direccion}] joblib no disponible: {e}")
            return

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'modelo_rf': self.modelo_rf,
            'modelo_lr': self.modelo_lr,
            'scaler': self.scaler,
            'feature_columns': self.FEATURE_COLUMNS,
            'tf': self.tf,
            'direccion': self.direccion,
            'trained_at': (self.trained_at or datetime.now(timezone.utc)).isoformat(),
            'n_muestras': self.n_muestras,
            'metrics': self.last_metrics,
        }
        joblib.dump(payload, target)
        self.model_path = str(target)

    def cargar_modelo(self, path: str) -> bool:
        target = Path(path)
        if not target.exists():
            return False

        try:
            import joblib
            payload = joblib.load(target)
            self.modelo_rf = payload.get('modelo_rf')
            self.modelo_lr = payload.get('modelo_lr')
            self.scaler = payload.get('scaler')
            self.n_muestras = int(payload.get('n_muestras') or 0)
            self.last_metrics = payload.get('metrics') or {}
            trained_at_raw = payload.get('trained_at')
            if trained_at_raw:
                self.trained_at = pd.to_datetime(trained_at_raw, utc=True).to_pydatetime()
            self.model_path = str(target)
            return self.modelo_rf is not None and self.modelo_lr is not None and self.scaler is not None
        except Exception as e:
            logger.warning(f"[ML {self.tf} {self.direccion}] No se pudo cargar modelo {path}: {e}")
            self.modelo_rf = None
            self.modelo_lr = None
            self.scaler = None
            return False

    def necesita_reentrenamiento(self, min_muestras: int = 30) -> bool:
        target = Path(self.model_path)
        if not target.exists():
            return True
        if not self.cargar_modelo(self.model_path):
            return True
        if self.n_muestras < min_muestras:
            return True
        if self.trained_at is None:
            return True
        age_days = (datetime.now(timezone.utc) - self.trained_at).days
        return age_days >= 30

    def reentrenar_desde_bd(self, db, min_muestras: int = 30) -> bool:
        if db is None:
            logger.warning(f"[ML {self.tf} {self.direccion}] BD no disponible para reentrenar")
            return False

        query = """
        SELECT id, timestamp, timestamp_entry, direccion, indicadores, estado, timeframe, asset
        FROM senales
        WHERE direccion = ?
          AND timeframe = ?
          AND (asset = 'GOLD' OR simbolo LIKE 'XAUUSD%')
          AND estado IN ('TP1','TP2','TP3','SL','BREAKEVEN','IN TP1','IN TP2','IN TP3')
          AND indicadores IS NOT NULL
        ORDER BY timestamp ASC
        """
        result = db.ejecutar_query(query, (self.direccion, self.tf))
        rows = list(result.rows or [])

        if len(rows) < min_muestras:
            logger.warning(
                f"[ML {self.tf} {self.direccion}] No hay suficientes señales cerradas "
                f"({len(rows)} < {min_muestras})"
            )
            return False

        self.entrenar(self.direccion, rows)
        if self.modelo_rf is None:
            return False
        self.guardar_modelo(self.model_path)
        return True
