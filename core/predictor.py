from __future__ import annotations

import json
import logging
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from adapters.data_provider import get_ohlcv
from core.indicators import (
    calcular_adx,
    calcular_atr,
    calcular_bollinger_bands,
    calcular_ema,
    calcular_macd,
    calcular_obv,
    calcular_rsi,
    calcular_sr_multiples,
)

logger = logging.getLogger('bottrading')

ESTADOS_WIN = {'TP1', 'TP2', 'TP3', 'BREAKEVEN', 'IN TP1', 'IN TP2', 'IN TP3'}
ESTADOS_LOSS = {'SL'}
ESTADOS_VALIDOS = ESTADOS_WIN | ESTADOS_LOSS
MIN_MUESTRAS_BD = 30
DEFAULT_FEATURE_FILL = 1.0  # XAU/USD suele traer volumen plano (1.0) en Twelve Data


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

    def __init__(
        self,
        tf: str = '1H',
        direccion: str = 'COMPRA',
        model_path: str | None = None,
        model_dir: str = 'models',
    ):
        self.tf = (tf or '1H').upper()
        self.direccion = (direccion or 'COMPRA').upper()

        self.modelo_rf: Any = None
        self.modelo_lr: Any = None
        self.scaler: Any = None
        self.last_metrics: dict[str, float] = {}
        self.n_muestras = 0
        self.trained_at: datetime | None = None

        self.model_dir = Path(model_dir)
        dir_slug = 'compra' if self.direccion == 'COMPRA' else 'venta'
        default_model = self.model_dir / f'predictor_gold_{self.tf}_{dir_slug}.pkl'
        self.model_path = Path(model_path) if model_path else default_model

        self.cargar_modelo(str(self.model_path))

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

    @staticmethod
    def _normalizar_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        out = df.copy()
        if 'Volume' not in out.columns:
            out['Volume'] = 1.0
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            out[col] = pd.to_numeric(out[col], errors='coerce')
        out = out.dropna(subset=['Open', 'High', 'Low', 'Close']).copy()
        if out.empty:
            return out

        out['atr'] = calcular_atr(out, 14)
        out['rsi'] = calcular_rsi(out['Close'], 14)
        out['ema_fast'] = calcular_ema(out['Close'], 20)
        out['ema50'] = calcular_ema(out['Close'], 50)
        _, _, macd_hist = calcular_macd(out['Close'])
        out['macd_hist'] = macd_hist
        out['obv'] = calcular_obv(out)
        adx, _, _ = calcular_adx(out, 14)
        out['adx'] = adx
        bb_up, _, bb_low, _ = calcular_bollinger_bands(out['Close'], 20, 2)
        out['bb_upper'] = bb_up
        out['bb_lower'] = bb_low
        return out

    @staticmethod
    def _vol_relativo_3v(vol_serie: pd.Series) -> float | None:
        ult = pd.to_numeric(vol_serie.tail(3), errors='coerce').dropna()
        if len(ult) < 3:
            return None
        if np.isclose(float(ult.max()), float(ult.min())):
            return None
        base = float(ult.iloc[:-1].mean())
        if base <= 0:
            return None
        return float(ult.iloc[-1] / base)

    def _normalizar_features(self, features: dict[str, Any]) -> list[float]:
        return [self._to_float(features.get(col, DEFAULT_FEATURE_FILL), DEFAULT_FEATURE_FILL) for col in self.FEATURE_COLUMNS]

    def _estado_a_target(self, estado: str) -> int | None:
        estado = str(estado or '').upper().strip()
        if estado in ESTADOS_WIN:
            return 1
        if estado in ESTADOS_LOSS:
            return 0
        return None

    def _fit_models(self, X_train: pd.DataFrame, y_train: pd.Series, sample_weight: np.ndarray | None = None):
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
        except Exception as e:
            logger.warning(f"[ML {self.tf} {self.direccion}] sklearn no disponible: {e}")
            return None, None, None

        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)

        rf = RandomForestClassifier(
            n_estimators=300,
            max_depth=8,
            min_samples_leaf=3,
            class_weight='balanced_subsample',
            random_state=42,
        )
        lr = LogisticRegression(max_iter=500, class_weight='balanced', random_state=42)

        rf.fit(X_train, y_train, sample_weight=sample_weight)
        lr.fit(X_train_scaled, y_train, sample_weight=sample_weight)
        return rf, lr, scaler

    def calcular_features_predictivos(
        self,
        df: pd.DataFrame,
        zsl: float | None = None,
        zsh: float | None = None,
        zrl: float | None = None,
        zrh: float | None = None,
        atr: float | None = None,
        soportes: list[float] | None = None,
        resistencias: list[float] | None = None,
    ) -> dict[str, Any]:
        if df is None or df.empty:
            return {k: 0.0 for k in self.FEATURE_COLUMNS} | {'en_zona_soporte': 0, 'en_zona_resist': 0}

        _df = df.copy()
        idx = -1
        close = self._to_float(_df['Close'].iloc[idx])
        low = self._to_float(_df['Low'].iloc[idx])
        high = self._to_float(_df['High'].iloc[idx])
        open_ = self._to_float(_df['Open'].iloc[idx])

        atr_val = self._to_float(atr, self._to_float(_df['atr'].iloc[idx] if 'atr' in _df.columns else 0.0, 0.0))
        atr_val = max(atr_val, 1e-9)

        if (zsl is None or zsh is None or zrl is None or zrh is None) and (soportes or resistencias):
            soportes = soportes or []
            resistencias = resistencias or []
            zsh = max([s for s in soportes if s < close], default=close)
            zsl = zsh - atr_val * 0.5
            zrl = min([r for r in resistencias if r > close], default=close)
            zrh = zrl + atr_val * 0.5

        zsl = self._to_float(zsl, close)
        zsh = self._to_float(zsh, close)
        zrl = self._to_float(zrl, close)
        zrh = self._to_float(zrh, close)

        rsi_series = _df['rsi'].dropna() if 'rsi' in _df.columns else pd.Series(dtype=float)
        rsi_direccion_3v = 0
        if len(rsi_series) >= 4:
            r0 = self._to_float(rsi_series.iloc[-1])
            r1 = self._to_float(rsi_series.iloc[-2])
            r2 = self._to_float(rsi_series.iloc[-3])
            if r0 < r1 < r2:
                rsi_direccion_3v = 1
            elif r0 > r1 > r2:
                rsi_direccion_3v = -1

        rsi_nivel = self._to_float(_df['rsi'].iloc[idx], 50.0) if 'rsi' in _df.columns else 50.0

        vol_relativo_3v = self._vol_relativo_3v(_df['Volume'])
        if vol_relativo_3v is None:
            vol_relativo_3v = DEFAULT_FEATURE_FILL

        macd_hist_mejorando = 0
        if 'macd_hist' in _df.columns and len(_df['macd_hist'].dropna()) >= 3:
            h0 = self._to_float(_df['macd_hist'].iloc[-1])
            h1 = self._to_float(_df['macd_hist'].iloc[-2])
            h2 = self._to_float(_df['macd_hist'].iloc[-3])
            mejora_buy = h2 < 0 and h1 > h2 and h0 > h1
            mejora_sell = h2 > 0 and h1 < h2 and h0 < h1
            macd_hist_mejorando = 1 if (mejora_buy or mejora_sell) else 0

        mecha_inferior = max(0.0, min(close, open_) - low)
        mecha_superior = max(0.0, high - max(close, open_))
        mecha_inferior_pct = mecha_inferior / atr_val
        mecha_superior_pct = mecha_superior / atr_val

        atr_contrayendo = 0
        if 'atr' in _df.columns and len(_df) >= 3:
            atr_tail = pd.to_numeric(_df['atr'].tail(3), errors='coerce').dropna()
            if len(atr_tail) >= 3 and float(atr_tail.iloc[-1]) < float(atr_tail.iloc[:-1].mean()):
                atr_contrayendo = 1

        obv_divergencia = 0
        if 'obv' in _df.columns and len(_df) >= 8:
            sub = _df.iloc[-6:]
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
        recent = _df.tail(21)
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

        en_zona_soporte = int(low <= zsh and high >= zsl)
        en_zona_resist = int(low <= zrh and high >= zrl)

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
            'en_zona_soporte': en_zona_soporte,
            'en_zona_resist': en_zona_resist,
        }

    def generar_muestras_backtest(
        self,
        ticker_yf: str = 'GC=F',
        tf: str = '1h',
        period: str = '3mo',
        direccion: str = 'COMPRA',
        atr_tp_mult: float = 1.5,
        atr_sl_mult: float = 1.0,
        velas_forward: int = 3,
    ) -> list[dict]:
        try:
            df_raw, _ = get_ohlcv(ticker_yf, period, tf)
            df = self._normalizar_ohlcv(df_raw)
            if df.empty or len(df) < (50 + velas_forward + 1):
                return []

            direccion = (direccion or self.direccion).upper()
            muestras: list[dict] = []

            for i in range(50, len(df) - velas_forward):
                parcial = df.iloc[: i + 1].copy()
                atr = self._to_float(parcial['atr'].iloc[-1], 0.0)
                if atr <= 0:
                    continue

                soportes, resistencias = calcular_sr_multiples(parcial, atr)
                features = self.calcular_features_predictivos(parcial, atr=atr, soportes=soportes, resistencias=resistencias)
                if not (features.get('en_zona_soporte') or features.get('en_zona_resist')):
                    continue

                close = float(parcial['Close'].iloc[-1])
                futuro = df.iloc[i + 1: i + 1 + velas_forward]
                future_high = float(futuro['High'].max())
                future_low = float(futuro['Low'].min())

                win: int | None
                if direccion == 'VENTA':
                    tp = close - atr * atr_tp_mult
                    sl = close + atr * atr_sl_mult
                    if future_low <= tp and future_high < sl:
                        win = 1
                    elif future_high >= sl:
                        win = 0
                    else:
                        win = None
                else:
                    tp = close + atr * atr_tp_mult
                    sl = close - atr * atr_sl_mult
                    if future_high >= tp and future_low > sl:
                        win = 1
                    elif future_low <= sl:
                        win = 0
                    else:
                        win = None

                if win is None:
                    continue

                sample = {k: features.get(k, DEFAULT_FEATURE_FILL) for k in self.FEATURE_COLUMNS}
                sample['win'] = int(win)
                muestras.append(sample)

            return muestras
        except Exception as exc:
            logger.warning(f"⚠️ No se pudieron generar muestras backtest ({self.tf}/{self.direccion}): {exc}")
            return []

    def _cargar_senales_bd(self, db) -> list[dict]:
        if db is None:
            return []

        query = """
        SELECT id, timestamp, timestamp_entry, direccion, indicadores, estado, timeframe, asset, simbolo
        FROM senales
        WHERE direccion = ?
          AND timeframe = ?
          AND estado IN ('TP1','TP2','TP3','SL','BREAKEVEN','IN TP1','IN TP2','IN TP3')
          AND indicadores IS NOT NULL
        ORDER BY timestamp ASC
        """
        result = db.ejecutar_query(query, (self.direccion, self.tf))

        rows: list[dict] = []
        for row in result.rows or []:
            target = self._estado_a_target(row.get('estado'))
            if target is None:
                continue
            indicadores = self._parse_indicadores(row.get('indicadores'))
            sample = {f: self._to_float(indicadores.get(f), DEFAULT_FEATURE_FILL) for f in self.FEATURE_COLUMNS}
            sample['win'] = int(target)
            sample['timestamp'] = row.get('timestamp') or row.get('timestamp_entry')
            rows.append(sample)
        return rows

    def entrenar(self, direccion_or_df, df_senales: list[dict] | None = None) -> bool:
        if isinstance(direccion_or_df, pd.DataFrame):
            df = direccion_or_df.copy()
        else:
            self.direccion = (str(direccion_or_df or self.direccion)).upper()
            samples = []
            for row in df_senales or []:
                ind = self._parse_indicadores(row.get('indicadores'))
                target = self._estado_a_target(row.get('estado'))
                if target is None:
                    continue
                sample = {'timestamp': self._parse_timestamp(row.get('timestamp') or row.get('timestamp_entry'))}
                for col in self.FEATURE_COLUMNS:
                    sample[col] = self._to_float(ind.get(col), DEFAULT_FEATURE_FILL)
                sample['win'] = int(target)
                samples.append(sample)
            df = pd.DataFrame(samples)

        if df is None or df.empty:
            logger.warning(f"[ML {self.tf} {self.direccion}] Dataset vacío")
            return False

        if 'win' not in df.columns:
            logger.warning(f"[ML {self.tf} {self.direccion}] Dataset sin columna 'win'")
            return False

        self.n_muestras = len(df)
        if self.n_muestras < 30:
            self.modelo_rf = None
            self.modelo_lr = None
            self.scaler = None
            self.last_metrics = {}
            logger.warning(f"[ML {self.tf} {self.direccion}] Muestras insuficientes ({self.n_muestras} < 30)")
            return False

        X = df[[c for c in self.FEATURE_COLUMNS if c in df.columns]].copy()
        for col in X.columns:
            X[col] = pd.to_numeric(X[col], errors='coerce')
        X = X.fillna(DEFAULT_FEATURE_FILL)
        y = pd.to_numeric(df['win'], errors='coerce').fillna(0).astype(int)

        split_idx = max(1, int(len(df) * 0.8))
        split_idx = min(split_idx, len(df) - 1)
        X_train = X.iloc[:split_idx]
        X_test = X.iloc[split_idx:]
        y_train = y.iloc[:split_idx]
        y_test = y.iloc[split_idx:]

        sample_weight = None
        if 'weight' in df.columns:
            sample_weight = pd.to_numeric(df['weight'].iloc[:split_idx], errors='coerce').fillna(1.0).values

        rf, lr, scaler = self._fit_models(X_train, y_train, sample_weight)
        if rf is None or lr is None or scaler is None:
            return False

        try:
            from sklearn.metrics import accuracy_score, precision_score, recall_score
            rf_prob = rf.predict_proba(X_test)[:, 1]
            lr_prob = lr.predict_proba(scaler.transform(X_test))[:, 1]
            avg_prob = (rf_prob + lr_prob) / 2.0
            y_pred = (avg_prob >= 0.5).astype(int)
            metrics = {
                'accuracy': float(accuracy_score(y_test, y_pred)),
                'precision': float(precision_score(y_test, y_pred, zero_division=0)),
                'recall': float(recall_score(y_test, y_pred, zero_division=0)),
                'samples_train': int(len(X_train)),
                'samples_test': int(len(X_test)),
            }
        except Exception:
            metrics = {}

        self.modelo_rf = rf
        self.modelo_lr = lr
        self.scaler = scaler
        self.trained_at = datetime.now(timezone.utc)
        self.last_metrics = metrics
        self.guardar_modelo(str(self.model_path))
        return True

    def predecir(self, features: dict) -> tuple[float, str]:
        if self.modelo_rf is None:
            return 0.0, 'NEUTRO'

        try:
            x = pd.DataFrame([self._normalizar_features(features)], columns=self.FEATURE_COLUMNS, dtype=float)
            prob_rf = float(self.modelo_rf.predict_proba(x)[0][1])
            if self.modelo_lr is not None and self.scaler is not None:
                prob_lr = float(self.modelo_lr.predict_proba(self.scaler.transform(x))[0][1])
                prob = max(0.0, min(1.0, (prob_rf + prob_lr) / 2.0))
            else:
                prob = max(0.0, min(1.0, prob_rf))

            if prob >= 0.55:
                etiqueta = 'BUY' if self.direccion == 'COMPRA' else 'SELL'
            else:
                etiqueta = 'NEUTRO'
            return prob, etiqueta
        except Exception as e:
            logger.debug(f"[ML {self.tf} {self.direccion}] Predicción no disponible: {e}")
            return 0.0, 'NEUTRO'

    def guardar_modelo(self, path: str) -> None:
        if self.modelo_rf is None:
            logger.warning(f"[ML {self.tf} {self.direccion}] Modelo no entrenado, no se guarda")
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

        try:
            import joblib
            joblib.dump(payload, target)
        except Exception:
            with target.open('wb') as f:
                pickle.dump(payload, f)

        self.model_path = target

    def cargar_modelo(self, path: str) -> bool:
        target = Path(path)
        if not target.exists():
            return False

        payload = None
        try:
            import joblib
            payload = joblib.load(target)
        except Exception:
            try:
                with target.open('rb') as f:
                    payload = pickle.load(f)
            except Exception as e:
                logger.warning(f"[ML {self.tf} {self.direccion}] No se pudo cargar modelo {path}: {e}")

        if not isinstance(payload, dict):
            self.modelo_rf = None
            self.modelo_lr = None
            self.scaler = None
            return False

        # Compatibilidad con payloads previos de una sola clave 'model'
        self.modelo_rf = payload.get('modelo_rf') or payload.get('model')
        self.modelo_lr = payload.get('modelo_lr')
        self.scaler = payload.get('scaler')
        self.n_muestras = int(payload.get('n_muestras') or 0)
        self.last_metrics = payload.get('metrics') or {}

        trained_at_raw = payload.get('trained_at')
        if trained_at_raw:
            try:
                self.trained_at = pd.to_datetime(trained_at_raw, utc=True).to_pydatetime()
            except Exception:
                self.trained_at = None

        self.model_path = target
        return self.modelo_rf is not None

    def necesita_reentrenamiento(self, min_muestras: int = 30) -> bool:
        target = Path(self.model_path)
        if not target.exists():
            return True
        if not self.cargar_modelo(str(self.model_path)):
            return True
        if self.n_muestras and self.n_muestras < min_muestras:
            return True
        if self.trained_at is None:
            return True
        age_days = (datetime.now(timezone.utc) - self.trained_at).days
        return age_days >= 30

    def reentrenar_desde_bd_o_backtest(
        self,
        db,
        ticker_yf: str = 'GC=F',
        backtest_period: str | None = None,
        force_backtest: bool = False,
        min_muestras: int = MIN_MUESTRAS_BD,
    ) -> bool:
        senales_bd = self._cargar_senales_bd(db)
        muestras_bt: list[dict] = []

        if force_backtest or len(senales_bd) < min_muestras:
            interval_map = {'1H': '1h', '4H': '4h', '1D': '1d'}
            period_map = {'1H': '3mo', '4H': '6mo', '1D': '6mo'}
            interval = interval_map.get(self.tf, '1h')
            period = backtest_period or period_map.get(self.tf, '3mo')
            logger.info(f"[ML {self.tf} {self.direccion}] Complementando dataset con backtest Twelve Data")
            muestras_bt = self.generar_muestras_backtest(
                ticker_yf=ticker_yf,
                tf=interval,
                period=period,
                direccion=self.direccion,
            )

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
            logger.warning(f"[ML {self.tf} {self.direccion}] Sin datos para reentrenar")
            return False

        return self.entrenar(pd.concat(frames, ignore_index=True))

    def reentrenar_desde_bd(self, db, min_muestras: int = MIN_MUESTRAS_BD) -> bool:
        return self.reentrenar_desde_bd_o_backtest(db, min_muestras=min_muestras)

    def proximo_reentrenamiento(self, dias: int = 30) -> str:
        return (datetime.now(timezone.utc) + timedelta(days=dias)).strftime('%Y-%m-%d')
