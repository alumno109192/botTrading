from __future__ import annotations

import json
import logging
import pickle
from datetime import datetime, timedelta
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

ESTADOS_WIN = {'TP1', 'TP2', 'TP3', 'BREAKEVEN'}
ESTADOS_LOSS = {'SL'}
ESTADOS_VALIDOS = ESTADOS_WIN | ESTADOS_LOSS
MIN_MUESTRAS_BD = 30


class GoldPredictor:
    """Motor predictivo para GOLD por timeframe y dirección."""

    FEATURE_COLUMNS = [
        'en_zona_soporte',
        'en_zona_resist',
        'dist_soporte_pct',
        'dist_resist_pct',
        'rsi_nivel',
        'macd_hist_mejorando',
        'mecha_inferior_pct',
        'mecha_superior_pct',
        'atr_contrayendo',
        'vol_relativo_3v',
        'close_vs_ema20_pct',
        'adx_nivel',
    ]

    def __init__(self, tf: str = '1H', direccion: str = 'COMPRA', model_dir: str = 'models'):
        self.tf = (tf or '1H').upper()
        self.direccion = (direccion or 'COMPRA').upper()
        self.model_dir = Path(model_dir)
        self.model_path = self.model_dir / f'predictor_gold_{self.tf}_{self.direccion}.pkl'
        self.model: Any = None
        self.feature_columns: list[str] = list(self.FEATURE_COLUMNS)

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
        out['ema20'] = calcular_ema(out['Close'], 20)
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

    def calcular_features_predictivos(
        self,
        df: pd.DataFrame,
        soportes: list[float] | None = None,
        resistencias: list[float] | None = None,
    ) -> dict[str, Any]:
        if df is None or df.empty:
            return {}

        row = df.iloc[-1]
        close = float(row['Close'])
        atr = float(row.get('atr', np.nan))
        atr = atr if np.isfinite(atr) and atr > 0 else np.nan

        soportes = soportes or []
        resistencias = resistencias or []
        soporte_cercano = max([s for s in soportes if s < close], default=np.nan)
        resistencia_cercana = min([r for r in resistencias if r > close], default=np.nan)

        dist_soporte_pct = ((close - soporte_cercano) / close * 100.0) if np.isfinite(soporte_cercano) and close > 0 else np.nan
        dist_resist_pct = ((resistencia_cercana - close) / close * 100.0) if np.isfinite(resistencia_cercana) and close > 0 else np.nan

        zona_mult = 0.5
        en_zona_soporte = bool(np.isfinite(soporte_cercano) and np.isfinite(atr) and abs(close - soporte_cercano) <= atr * zona_mult)
        en_zona_resist = bool(np.isfinite(resistencia_cercana) and np.isfinite(atr) and abs(resistencia_cercana - close) <= atr * zona_mult)

        o = float(row['Open'])
        h = float(row['High'])
        l = float(row['Low'])
        c = float(row['Close'])
        vela_rango = max(h - l, 1e-9)
        mecha_inferior = min(o, c) - l
        mecha_superior = h - max(o, c)

        atr_roll = pd.to_numeric(df['atr'].tail(4), errors='coerce').dropna()
        atr_contrayendo = bool(len(atr_roll) >= 3 and float(atr_roll.iloc[-1]) < float(atr_roll.iloc[:-1].mean()))

        macd_hist = pd.to_numeric(df['macd_hist'].tail(2), errors='coerce')
        macd_hist_mejorando = bool(len(macd_hist.dropna()) == 2 and float(macd_hist.iloc[-1]) > float(macd_hist.iloc[-2]))

        rsi = float(row.get('rsi', np.nan))
        adx = float(row.get('adx', np.nan))
        ema20 = float(row.get('ema20', np.nan))

        return {
            'en_zona_soporte': int(en_zona_soporte),
            'en_zona_resist': int(en_zona_resist),
            'dist_soporte_pct': float(dist_soporte_pct) if np.isfinite(dist_soporte_pct) else np.nan,
            'dist_resist_pct': float(dist_resist_pct) if np.isfinite(dist_resist_pct) else np.nan,
            'rsi_nivel': float(rsi / 100.0) if np.isfinite(rsi) else np.nan,
            'macd_hist_mejorando': int(macd_hist_mejorando),
            'mecha_inferior_pct': float(mecha_inferior / vela_rango),
            'mecha_superior_pct': float(mecha_superior / vela_rango),
            'atr_contrayendo': int(atr_contrayendo),
            'vol_relativo_3v': self._vol_relativo_3v(df['Volume']),
            'close_vs_ema20_pct': float((close - ema20) / close * 100.0) if np.isfinite(ema20) and close > 0 else np.nan,
            'adx_nivel': float(adx / 100.0) if np.isfinite(adx) else np.nan,
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
        """
        Genera muestras de entrenamiento sobre histórico OHLCV de Twelve Data.
        """
        try:
            df_raw, _ = get_ohlcv(ticker_yf, period, tf)
            df = self._normalizar_ohlcv(df_raw)
            if df.empty or len(df) < (50 + velas_forward + 1):
                return []

            direccion = (direccion or self.direccion).upper()
            muestras: list[dict] = []

            for i in range(50, len(df) - velas_forward):
                parcial = df.iloc[: i + 1].copy()
                atr = float(parcial['atr'].iloc[-1]) if pd.notna(parcial['atr'].iloc[-1]) else np.nan
                if not np.isfinite(atr) or atr <= 0:
                    continue

                soportes, resistencias = calcular_sr_multiples(parcial, atr)
                features = self.calcular_features_predictivos(parcial, soportes, resistencias)
                if not features:
                    continue
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
                muestras.append({**features, 'win': int(win)})

            return muestras
        except Exception as exc:
            logger.warning(f"⚠️ No se pudieron generar muestras backtest ({self.tf}/{self.direccion}): {exc}")
            return []

    def _cargar_senales_bd(self, db) -> list[dict]:
        if db is None:
            return []

        q = """
        SELECT timestamp, direccion, estado, indicadores
        FROM senales
        WHERE estado IN ('TP1','TP2','TP3','BREAKEVEN','SL')
          AND timeframe = ?
          AND direccion = ?
          AND indicadores IS NOT NULL
        ORDER BY timestamp ASC
        """
        result = db.ejecutar_query(q, (self.tf, self.direccion))

        filas: list[dict] = []
        for row in result.rows:
            estado = (row.get('estado') or '').upper()
            if estado not in ESTADOS_VALIDOS:
                continue
            raw = row.get('indicadores') or '{}'
            try:
                ind = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except Exception:
                continue

            muestra = {f: ind.get(f, np.nan) for f in self.FEATURE_COLUMNS}
            muestra['win'] = 1 if estado in ESTADOS_WIN else 0
            filas.append(muestra)

        return filas

    def entrenar(self, df: pd.DataFrame) -> bool:
        if df is None or df.empty or len(df) < 5:
            logger.warning(f"⚠️ Dataset insuficiente para entrenar ({self.tf}/{self.direccion})")
            return False

        try:
            from sklearn.ensemble import RandomForestClassifier
        except Exception as exc:
            logger.warning(f"⚠️ sklearn no disponible para entrenar predictor: {exc}")
            return False

        X = df[[c for c in self.FEATURE_COLUMNS if c in df.columns]].copy()
        for col in X.columns:
            X[col] = pd.to_numeric(X[col], errors='coerce')
        X = X.fillna(1.0)
        y = pd.to_numeric(df['win'], errors='coerce').fillna(0).astype(int)

        rf = RandomForestClassifier(
            n_estimators=300,
            random_state=42,
            max_depth=8,
            min_samples_leaf=3,
            class_weight='balanced_subsample',
        )

        sample_weight = None
        if 'weight' in df.columns:
            sample_weight = pd.to_numeric(df['weight'], errors='coerce').fillna(1.0).values

        rf.fit(X, y, sample_weight=sample_weight)

        self.model_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            'model': rf,
            'feature_columns': list(X.columns),
            'tf': self.tf,
            'direccion': self.direccion,
            'trained_at': datetime.utcnow().isoformat(),
        }
        with self.model_path.open('wb') as f:
            pickle.dump(payload, f)

        self.model = rf
        self.feature_columns = list(X.columns)
        return True

    def reentrenar_desde_bd_o_backtest(self, db, ticker_yf: str = 'GC=F') -> bool:
        """
        Reentrenamiento automático desde detectores.
        Usa BD si hay suficientes señales, sino complementa con Twelve Data.
        """
        senales_bd = self._cargar_senales_bd(db)
        muestras_bt: list[dict] = []

        if len(senales_bd) < MIN_MUESTRAS_BD:
            interval_map = {'1H': '1h', '4H': '4h', '1D': '1d'}
            period_map = {'1H': '3mo', '4H': '6mo', '1D': '6mo'}
            interval = interval_map.get(self.tf, '1h')
            period = period_map.get(self.tf, '3mo')
            logger.info(
                f"⚠️ Pocas señales BD ({len(senales_bd)}<{MIN_MUESTRAS_BD}) — complementando con backtest Twelve Data"
            )
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
            logger.warning("⚠️ Sin datos para reentrenar predictor")
            return False

        return self.entrenar(pd.concat(frames, ignore_index=True))

    def reentrenar_desde_bd(self, db) -> bool:
        """Compatibilidad retroactiva: redirige al método híbrido."""
        return self.reentrenar_desde_bd_o_backtest(db)

    def proximo_reentrenamiento(self, dias: int = 30) -> str:
        return (datetime.utcnow() + timedelta(days=dias)).strftime('%Y-%m-%d')
