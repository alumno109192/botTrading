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


# ── Stop Hunt / Falsa Ruptura + Recuperación ────────────────

def detectar_stop_hunt_alcista(df: pd.DataFrame, lookback: int = 20) -> bool:
    """
    Stop Hunt alcista: el precio perfora el mínimo de las últimas N velas
    (activa stops de compradores) pero cierra POR ENCIMA de ese nivel,
    con una mecha inferior larga que evidencia el rechazo.

    Condiciones:
      1. Low actual < mínimo de las últimas `lookback` velas  (ruptura)
      2. Close actual > ese mínimo                            (reclaim)
      3. Mecha inferior > cuerpo  O  mecha inferior > 50% del rango total

    Uso en 5M:  lookback=20 (~100 min de historia)
    Uso en 15M: lookback=20 (~5 h de historia)
    """
    if len(df) < lookback + 2:
        return False

    vela       = df.iloc[-1]
    swing_low  = float(df['Low'].iloc[-lookback - 1:-1].min())

    ruptura    = float(vela['Low'])   < swing_low
    reclaim    = float(vela['Close']) > swing_low

    body        = abs(float(vela['Close']) - float(vela['Open']))
    lower_wick  = min(float(vela['Close']), float(vela['Open'])) - float(vela['Low'])
    total_range = float(vela['High']) - float(vela['Low'])

    mecha_larga = (lower_wick > body) or (total_range > 0 and lower_wick / total_range > 0.5)

    return ruptura and reclaim and mecha_larga


def detectar_stop_hunt_bajista(df: pd.DataFrame, lookback: int = 20) -> bool:
    """
    Stop Hunt bajista: el precio perfora el máximo de las últimas N velas
    (activa stops de vendedores) pero cierra POR DEBAJO de ese nivel,
    con una mecha superior larga que evidencia el rechazo.

    Condiciones:
      1. High actual > máximo de las últimas `lookback` velas  (ruptura)
      2. Close actual < ese máximo                             (reclaim)
      3. Mecha superior > cuerpo  O  mecha superior > 50% del rango total
    """
    if len(df) < lookback + 2:
        return False

    vela       = df.iloc[-1]
    swing_high = float(df['High'].iloc[-lookback - 1:-1].max())

    ruptura    = float(vela['High'])  > swing_high
    reclaim    = float(vela['Close']) < swing_high

    body        = abs(float(vela['Close']) - float(vela['Open']))
    upper_wick  = float(vela['High']) - max(float(vela['Close']), float(vela['Open']))
    total_range = float(vela['High']) - float(vela['Low'])

    mecha_larga = (upper_wick > body) or (total_range > 0 and upper_wick / total_range > 0.5)

    return ruptura and reclaim and mecha_larga


# ── Roturas de Nivel (Breakouts) ─────────────────────────────

def detectar_rotura_alcista(df: pd.DataFrame, zrh: float, atr: float,
                             vol_mult: float = 1.2) -> bool:
    """
    Rotura alcista: la última vela cerrada supera la resistencia (zrh)
    con cuerpo de impulso y volumen elevado.

    Condiciones:
      1. close > zrh  (cerró por encima de la resistencia)
      2. is_bullish   (vela alcista — no solo una mecha)
      3. body > atr * 0.3  (impulso real, no ruido)
      4. volumen > media × vol_mult  (participación institucional)

    Nota: usa df.iloc[-2] como vela cerrada (convención de los detectores).
    """
    if len(df) < 25:
        return False

    vela     = df.iloc[-2]
    close    = float(vela['Close'])
    open_    = float(vela['Open'])
    body     = abs(close - open_)
    is_bull  = close > open_

    vol      = float(vela['Volume'])
    vol_avg  = float(df['Volume'].iloc[-22:-2].mean())

    return (close > zrh and is_bull and body > atr * 0.3
            and vol > vol_avg * vol_mult)


def detectar_rotura_bajista(df: pd.DataFrame, zsl: float, atr: float,
                             vol_mult: float = 1.2) -> bool:
    """
    Rotura bajista: la última vela cerrada rompe el soporte (zsl)
    con cuerpo de impulso y volumen elevado.

    Condiciones:
      1. close < zsl  (cerró por debajo del soporte)
      2. is_bearish   (vela bajista)
      3. body > atr * 0.3
      4. volumen > media × vol_mult

    Nota: usa df.iloc[-2] como vela cerrada.
    """
    if len(df) < 25:
        return False

    vela     = df.iloc[-2]
    close    = float(vela['Close'])
    open_    = float(vela['Open'])
    body     = abs(close - open_)
    is_bear  = close < open_

    vol      = float(vela['Volume'])
    vol_avg  = float(df['Volume'].iloc[-22:-2].mean())

    return (close < zsl and is_bear and body > atr * 0.3
            and vol > vol_avg * vol_mult)


# ── Dobles Techos y Suelos ────────────────────────────────────

def detectar_doble_techo(df: pd.DataFrame, atr: float,
                          lookback: int = 40,
                          tol_mult: float = 0.6) -> tuple:
    """
    Doble Techo (patrón M): dos swing highs consecutivos a nivel similar
    separados por un valle. Confirmado cuando el precio rompe por debajo
    del cuello (neckline = mínimo entre ambos techos).

    Retorna: (detectado: bool, nivel_techo: float, neckline: float)

    Condiciones:
      1. Exactamente dos swing highs recientes a nivel similar (±tol_mult×ATR)
      2. Valle entre ellos (neckline)
      3. La última vela cerrada (df.iloc[-2]) cierra bajo la neckline
    """
    if len(df) < lookback + 5:
        return False, 0.0, 0.0

    tolerancia = atr * tol_mult
    wing       = 2

    highs = df['High'].iloc[-lookback - 2: -2]
    lows  = df['Low'].iloc[-lookback - 2: -2]

    swing_highs = []
    for i in range(wing, len(highs) - wing):
        val = float(highs.iloc[i])
        if (all(val >= float(highs.iloc[i - j]) for j in range(1, wing + 1)) and
                all(val >= float(highs.iloc[i + j]) for j in range(1, wing + 1))):
            swing_highs.append((i, val))

    if len(swing_highs) < 2:
        return False, 0.0, 0.0

    # Dos techos más recientes
    h1_idx, h1_val = swing_highs[-2]
    h2_idx, h2_val = swing_highs[-1]

    if h1_idx >= h2_idx or abs(h1_val - h2_val) > tolerancia:
        return False, 0.0, 0.0

    # Neckline = mínimo del Low entre los dos techos
    valley = lows.iloc[h1_idx: h2_idx + 1]
    if valley.empty:
        return False, 0.0, 0.0
    neckline = float(valley.min())

    # Confirmación: última vela cerrada bajo la neckline
    close = float(df['Close'].iloc[-2])
    if close >= neckline:
        return False, 0.0, 0.0

    nivel_techo = round(max(h1_val, h2_val), 2)
    return True, nivel_techo, round(neckline, 2)


def detectar_doble_suelo(df: pd.DataFrame, atr: float,
                          lookback: int = 40,
                          tol_mult: float = 0.6) -> tuple:
    """
    Doble Suelo (patrón W): dos swing lows consecutivos a nivel similar
    separados por un pico. Confirmado cuando el precio supera la neckline
    (= máximo entre los dos suelos).

    Retorna: (detectado: bool, nivel_suelo: float, neckline: float)

    Condiciones:
      1. Dos swing lows recientes a nivel similar (±tol_mult×ATR)
      2. Pico entre ellos (neckline)
      3. La última vela cerrada (df.iloc[-2]) cierra sobre la neckline
    """
    if len(df) < lookback + 5:
        return False, 0.0, 0.0

    tolerancia = atr * tol_mult
    wing       = 2

    lows  = df['Low'].iloc[-lookback - 2: -2]
    highs = df['High'].iloc[-lookback - 2: -2]

    swing_lows = []
    for i in range(wing, len(lows) - wing):
        val = float(lows.iloc[i])
        if (all(val <= float(lows.iloc[i - j]) for j in range(1, wing + 1)) and
                all(val <= float(lows.iloc[i + j]) for j in range(1, wing + 1))):
            swing_lows.append((i, val))

    if len(swing_lows) < 2:
        return False, 0.0, 0.0

    # Dos suelos más recientes
    l1_idx, l1_val = swing_lows[-2]
    l2_idx, l2_val = swing_lows[-1]

    if l1_idx >= l2_idx or abs(l1_val - l2_val) > tolerancia:
        return False, 0.0, 0.0

    # Neckline = máximo del High entre los dos suelos
    peak = highs.iloc[l1_idx: l2_idx + 1]
    if peak.empty:
        return False, 0.0, 0.0
    neckline = float(peak.max())

    # Confirmación: última vela cerrada sobre la neckline
    close = float(df['Close'].iloc[-2])
    if close <= neckline:
        return False, 0.0, 0.0

    nivel_suelo = round(min(l1_val, l2_val), 2)
    return True, nivel_suelo, round(neckline, 2)


# ──────────────────────────────────────────────────────────────────────────────
# CANAL ROTO + S/R MÚLTIPLES (compartido entre 1H y 4H)
# ──────────────────────────────────────────────────────────────────────────────

def detectar_canal_roto(df: pd.DataFrame, atr: float,
                        lookback: int = 40, wing: int = 3) -> tuple:
    """
    Detecta si el precio acaba de romper un canal alcista o bajista.

    Ajusta una línea de tendencia sobre swing-lows (canal alcista) y otra
    sobre swing-highs (canal bajista) mediante regresión lineal.
    Considera roto si el cierre actual está por debajo/encima del valor
    proyectado en más de 0.3×ATR.

    Returns:
        (canal_alcista_roto, canal_bajista_roto,
         valor_linea_soporte, valor_linea_resist)
    """
    sub   = df.iloc[-lookback:]
    highs = sub['High'].values
    lows  = sub['Close'].values
    n     = len(sub)

    idx_lows, idx_highs = [], []
    for i in range(wing, n - wing):
        if all(lows[i]  <= lows[i-j]  for j in range(1, wing+1)) and \
           all(lows[i]  <= lows[i+j]  for j in range(1, wing+1)):
            idx_lows.append(i)
        if all(highs[i] >= highs[i-j] for j in range(1, wing+1)) and \
           all(highs[i] >= highs[i+j] for j in range(1, wing+1)):
            idx_highs.append(i)

    canal_alcista_roto = False
    canal_bajista_roto = False
    linea_soporte_val  = float(lows[-1])
    linea_resist_val   = float(highs[-1])
    close_actual       = float(df['Close'].iloc[-2])  # vela cerrada (igual que el resto del detector)

    if len(idx_lows) >= 2:
        xs = np.array(idx_lows)
        m, b = np.polyfit(xs, lows[idx_lows], 1)
        if m > 0:
            linea_soporte_val = m * (n - 1) + b
            if close_actual < linea_soporte_val - 0.3 * atr:
                canal_alcista_roto = True

    if len(idx_highs) >= 2:
        xs = np.array(idx_highs)
        m, b = np.polyfit(xs, highs[idx_highs], 1)
        if m < 0:
            linea_resist_val = m * (n - 1) + b
            if close_actual > linea_resist_val + 0.3 * atr:
                canal_bajista_roto = True

    return canal_alcista_roto, canal_bajista_roto, linea_soporte_val, linea_resist_val


def calcular_sr_multiples(df: pd.DataFrame, atr: float,
                          lookback: int = 60, zone_mult: float = 0.5,
                          n_niveles: int = 5) -> tuple:
    """
    Devuelve hasta n_niveles de soporte y resistencia ordenados por proximidad
    al precio actual. Âncla los TPs a zonas reales del mercado.

    Returns:
        (soportes, resistencias) — listas de float, más cercano primero.
    """
    close    = float(df['Close'].iloc[-1])
    wing     = 3
    min_dist = atr * 0.5
    highs    = df['High'].iloc[-lookback-1:-1]
    lows_s   = df['Low'].iloc[-lookback-1:-1]

    raw_highs, raw_lows = [], []
    for i in range(wing, len(highs) - wing):
        val = float(highs.iloc[i])
        if all(val >= float(highs.iloc[i-j]) for j in range(1, wing+1)) and \
           all(val >= float(highs.iloc[i+j]) for j in range(1, wing+1)):
            raw_highs.append(val)
    for i in range(wing, len(lows_s) - wing):
        val = float(lows_s.iloc[i])
        if all(val <= float(lows_s.iloc[i-j]) for j in range(1, wing+1)) and \
           all(val <= float(lows_s.iloc[i+j]) for j in range(1, wing+1)):
            raw_lows.append(val)

    filtrados = []
    for v in sorted(set(raw_highs + raw_lows)):
        if not filtrados or abs(v - filtrados[-1]) > min_dist:
            filtrados.append(v)

    soportes     = sorted([v for v in filtrados if v < close - atr * 0.2], reverse=True)[:n_niveles]
    resistencias = sorted([v for v in filtrados if v > close + atr * 0.2])[:n_niveles]
    return soportes, resistencias
