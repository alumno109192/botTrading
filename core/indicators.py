"""
shared_indicators.py — Indicadores técnicos compartidos

Fuente canónica de todas las funciones de indicadores usadas por los detectores.
Importar desde aquí en lugar de duplicar en cada archivo.

Funciones disponibles:
    calcular_rsi(series, length)
    calcular_ema(series, length)
    calcular_atr(df, length)
    calcular_bollinger_bands(series, length=20, std_dev=2)
    calcular_macd(series, fast=12, slow=26, signal=9)
    calcular_obv(df)
    calcular_adx(df, length=14)
    detectar_evening_star(df, idx)
    detectar_morning_star(df, idx)
"""
import pandas as pd
import numpy as np


def calcular_rsi(series: pd.Series, length: int) -> pd.Series:
    """RSI usando suavizado Wilder (EWM com=length-1)."""
    delta  = series.diff()
    gain   = delta.clip(lower=0)
    loss   = -delta.clip(upper=0)
    avg_g  = gain.ewm(com=length - 1, min_periods=length).mean()
    avg_l  = loss.ewm(com=length - 1, min_periods=length).mean()
    rs     = avg_g / avg_l
    return 100 - (100 / (1 + rs))


def calcular_ema(series: pd.Series, length: int) -> pd.Series:
    """EMA estándar (span=length)."""
    return series.ewm(span=length, adjust=False).mean()


def calcular_atr(df: pd.DataFrame, length: int) -> pd.Series:
    """ATR usando suavizado Wilder (EWM com=length-1)."""
    high, low, close_prev = df['High'], df['Low'], df['Close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low  - close_prev).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(com=length - 1, min_periods=length).mean()


def calcular_bollinger_bands(series: pd.Series, length: int = 20,
                             std_dev: float = 2) -> tuple:
    """
    Bandas de Bollinger.
    Retorna: (bb_upper, bb_mid, bb_lower, bb_width)
    bb_width = (upper - lower) / mid  (ancho normalizado)
    """
    bb_mid   = series.rolling(window=length).mean()
    std      = series.rolling(window=length).std()
    bb_upper = bb_mid + (std * std_dev)
    bb_lower = bb_mid - (std * std_dev)
    bb_width = (bb_upper - bb_lower) / bb_mid
    return bb_upper, bb_mid, bb_lower, bb_width


def calcular_macd(series: pd.Series, fast: int = 12, slow: int = 26,
                  signal: int = 9) -> tuple:
    """
    MACD (Moving Average Convergence Divergence).
    Retorna: (macd_line, signal_line, histogram)
    """
    ema_fast    = series.ewm(span=fast,   adjust=False).mean()
    ema_slow    = series.ewm(span=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


def calcular_obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume (vectorizado)."""
    direction = np.sign(df['Close'].diff()).fillna(0)
    return (direction * df['Volume']).cumsum()


def calcular_adx(df: pd.DataFrame, length: int = 14) -> tuple:
    """
    ADX (Average Directional Index) usando suavizado Wilder.
    Retorna: (adx, di_plus, di_minus)
    """
    high  = df['High']
    low   = df['Low']
    close = df['Close']

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs()
    ], axis=1).max(axis=1)

    up_move   = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm  = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    plus_dm[(up_move > down_move) & (up_move > 0)]     = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)]  = down_move

    atr      = tr.ewm(com=length - 1, min_periods=length).mean()
    plus_di  = 100 * (plus_dm.ewm(com=length - 1, min_periods=length).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(com=length - 1, min_periods=length).mean() / atr)

    dx  = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    adx = dx.ewm(com=length - 1, min_periods=length).mean()

    return adx, plus_di, minus_di


def detectar_evening_star(df: pd.DataFrame, idx: int) -> bool:
    """
    Evening Star: patrón de reversión bajista (3 velas).
    V1: alcista grande | V2: pequeña con gap alcista | V3: bajista grande.
    """
    if idx < 2:
        return False

    v1 = df.iloc[idx - 2]
    v2 = df.iloc[idx - 1]
    v3 = df.iloc[idx]

    v1_body        = abs(v1['Close'] - v1['Open'])
    v1_range       = v1['High'] - v1['Low']
    v2_body        = abs(v2['Close'] - v2['Open'])
    v2_range       = v2['High'] - v2['Low']
    v3_body        = abs(v3['Close'] - v3['Open'])
    v3_range       = v3['High'] - v3['Low']

    v1_bullish     = v1['Close'] > v1['Open']
    v1_large_body  = v1_body > v1_range * 0.6
    v2_small       = v2_body < v2_range * 0.3 if v2_range > 0 else True
    v2_gap_up      = v2['Open'] > v1['Close']
    v3_bearish     = v3['Close'] < v3['Open']
    v3_large_body  = v3_body > v3_range * 0.6
    v3_closes_in_v1 = v3['Close'] < (v1['Open'] + v1['Close']) / 2

    return (v1_bullish and v1_large_body and v2_small and v2_gap_up
            and v3_bearish and v3_large_body and v3_closes_in_v1)


def detectar_morning_star(df: pd.DataFrame, idx: int) -> bool:
    """
    Morning Star: patrón de reversión alcista (3 velas).
    V1: bajista grande | V2: pequeña con gap bajista | V3: alcista grande.
    """
    if idx < 2:
        return False

    v1 = df.iloc[idx - 2]
    v2 = df.iloc[idx - 1]
    v3 = df.iloc[idx]

    v1_body        = abs(v1['Close'] - v1['Open'])
    v1_range       = v1['High'] - v1['Low']
    v2_body        = abs(v2['Close'] - v2['Open'])
    v2_range       = v2['High'] - v2['Low']
    v3_body        = abs(v3['Close'] - v3['Open'])
    v3_range       = v3['High'] - v3['Low']

    v1_bearish     = v1['Close'] < v1['Open']
    v1_large_body  = v1_body > v1_range * 0.6
    v2_small       = v2_body < v2_range * 0.3 if v2_range > 0 else True
    v2_gap_down    = v2['Open'] < v1['Close']
    v3_bullish     = v3['Close'] > v3['Open']
    v3_large_body  = v3_body > v3_range * 0.6
    v3_closes_in_v1 = v3['Close'] > (v1['Open'] + v1['Close']) / 2

    return (v1_bearish and v1_large_body and v2_small and v2_gap_down
            and v3_bullish and v3_large_body and v3_closes_in_v1)


# ── Patrones de velas simples ────────────────────────────────

def patron_envolvente_alcista(df: pd.DataFrame) -> bool:
    """Patrón envolvente alcista (bullish engulfing)."""
    c1_bear = df['Close'].iloc[-2] < df['Open'].iloc[-2]
    c2_bull = df['Close'].iloc[-1] > df['Open'].iloc[-1]
    c2_envuelve = (df['Close'].iloc[-1] > df['Open'].iloc[-2] and
                   df['Open'].iloc[-1] < df['Close'].iloc[-2])
    return c1_bear and c2_bull and c2_envuelve


def patron_envolvente_bajista(df: pd.DataFrame) -> bool:
    """Patrón envolvente bajista (bearish engulfing)."""
    c1_bull = df['Close'].iloc[-2] > df['Open'].iloc[-2]
    c2_bear = df['Close'].iloc[-1] < df['Open'].iloc[-1]
    c2_envuelve = (df['Close'].iloc[-1] < df['Open'].iloc[-2] and
                   df['Open'].iloc[-1] > df['Close'].iloc[-2])
    return c1_bull and c2_bear and c2_envuelve


def patron_doji(df: pd.DataFrame) -> bool:
    """Detectar vela Doji (indecisión)."""
    body = abs(df['Close'].iloc[-1] - df['Open'].iloc[-1])
    range_vela = df['High'].iloc[-1] - df['Low'].iloc[-1]
    return body < (range_vela * 0.1) if range_vela > 0 else False
