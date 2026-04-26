"""
scripts/backtest.py — Backtester walk-forward para señales de trading

Uso:
    python scripts/backtest.py --simbolo XAUUSD --tf 1h --desde 2024-01-01 --hasta 2024-06-30
    python scripts/backtest.py --simbolo XAUUSD --tf 1h --desde 2024-01-01 --plot
"""
import argparse
import json
import os
import sys
from datetime import datetime, date
from typing import Optional, List

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.signal_analyzer import analizar_senal

TICKER_MAP = {
    'XAUUSD': 'GC=F',
    'XAGUSD': 'SI=F',
    'BTC': 'BTC-USD',
    'SPX': '^GSPC',
    'NAS100': '^NDX',
}

def cargar_datos(simbolo: str, tf: str, desde: str, hasta: str) -> pd.DataFrame:
    """Downloads OHLCV data using adapters.data_provider."""
    ticker = TICKER_MAP.get(simbolo.upper(), simbolo)

    # Convert tf to yfinance interval and period
    tf_map = {
        '1h': ('1h', '60d'),
        '4h': ('4h', '60d'),
        '1d': ('1d', '2y'),
        '15m': ('5m', '7d'),   # download 5m, resample to 15m
        '5m': ('5m', '7d'),
    }
    interval, period = tf_map.get(tf.lower(), ('1h', '60d'))

    try:
        from adapters.data_provider import get_ohlcv
        df, _ = get_ohlcv(ticker, period=period, interval=interval)

        if tf.lower() == '15m':
            df = df.resample('15min').agg({
                'Open': 'first', 'High': 'max', 'Low': 'min',
                'Close': 'last', 'Volume': 'sum'
            }).dropna()

        # Filter by date range
        if desde:
            df = df[df.index >= pd.Timestamp(desde, tz='UTC')]
        if hasta:
            df = df[df.index <= pd.Timestamp(hasta, tz='UTC')]

        return df
    except Exception:
        # Return empty DataFrame if download fails
        return pd.DataFrame()


def _generar_senal_simple(df_slice: pd.DataFrame, params: dict) -> Optional[dict]:
    """
    Generates a simple signal based on RSI and EMA for backtest purposes.
    Uses only past data (no lookahead bias).

    Returns signal dict or None.
    """
    if len(df_slice) < 20:
        return None

    try:
        close = df_slice['Close']

        # Basic RSI (14 periods)
        rsi_length = params.get('rsi_length', 14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(rsi_length).mean()
        avg_loss = loss.rolling(rsi_length).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1])

        if pd.isna(rsi_val):
            return None

        # EMA fast/slow
        ema_fast_len = params.get('ema_fast_len', 9)
        ema_slow_len = params.get('ema_slow_len', 21)
        ema_fast = float(close.ewm(span=ema_fast_len).mean().iloc[-1])
        ema_slow = float(close.ewm(span=ema_slow_len).mean().iloc[-1])

        # ATR (14 periods)
        atr_length = params.get('atr_length', 14)
        high = df_slice['High']
        low = df_slice['Low']
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr = float(tr.rolling(atr_length).mean().iloc[-1])

        if pd.isna(atr) or atr <= 0:
            return None

        entry = float(close.iloc[-1])
        atr_sl_mult = params.get('atr_sl_mult', 1.5)
        atr_tp1_mult = params.get('atr_tp1_mult', 1.5)
        atr_tp2_mult = params.get('atr_tp2_mult', 2.5)
        atr_tp3_mult = params.get('atr_tp3_mult', 4.0)

        # SELL signal: RSI > 70 AND ema_fast < ema_slow (bearish)
        if rsi_val > 70 and ema_fast < ema_slow:
            sl = round(entry + atr * atr_sl_mult, 2)
            tp1 = round(entry - atr * atr_tp1_mult, 2)
            tp2 = round(entry - atr * atr_tp2_mult, 2)
            tp3 = round(entry - atr * atr_tp3_mult, 2)
            return {
                'tipo': 'SELL', 'direccion': 'SHORT',
                'entry': entry, 'sl': sl,
                'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
                'rsi': rsi_val, 'atr': atr,
            }

        # BUY signal: RSI < 30 AND ema_fast > ema_slow (bullish)
        if rsi_val < 30 and ema_fast > ema_slow:
            sl = round(entry - atr * atr_sl_mult, 2)
            tp1 = round(entry + atr * atr_tp1_mult, 2)
            tp2 = round(entry + atr * atr_tp2_mult, 2)
            tp3 = round(entry + atr * atr_tp3_mult, 2)
            return {
                'tipo': 'BUY', 'direccion': 'LONG',
                'entry': entry, 'sl': sl,
                'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
                'rsi': rsi_val, 'atr': atr,
            }

        return None
    except Exception:
        return None


def evaluar_senal(senal: dict, df_futuro: pd.DataFrame) -> str:
    """
    Evaluates a signal against future price bars.
    Returns: 'SL', 'TP1', 'TP2', 'TP3', or 'EN_CURSO'
    """
    if df_futuro.empty:
        return 'EN_CURSO'

    entry = senal['entry']
    sl = senal['sl']
    tp1 = senal['tp1']
    tp2 = senal['tp2']
    tp3 = senal['tp3']
    direccion = senal['direccion']

    riesgo = abs(entry - sl)
    if riesgo == 0:
        return 'EN_CURSO'

    for _, row in df_futuro.iterrows():
        high = float(row['High'])
        low = float(row['Low'])

        if direccion == 'SHORT':
            # Check SL (price goes up)
            if high >= sl:
                return 'SL'
            # Check TPs (price goes down)
            if low <= tp3:
                return 'TP3'
            if low <= tp2:
                return 'TP2'
            if low <= tp1:
                return 'TP1'
        else:  # LONG
            # Check SL (price goes down)
            if low <= sl:
                return 'SL'
            # Check TPs (price goes up)
            if high >= tp3:
                return 'TP3'
            if high >= tp2:
                return 'TP2'
            if high >= tp1:
                return 'TP1'

    return 'EN_CURSO'


def calcular_metricas(resultados: List[dict]) -> dict:
    """Calculates backtest performance metrics."""
    total = len(resultados)
    if total == 0:
        return {
            'total_senales': 0,
            'win_rate': 0.0,
            'sl_pct': 0.0,
            'tp1_pct': 0.0,
            'tp2_pct': 0.0,
            'tp3_pct': 0.0,
            'profit_factor': 0.0,
            'max_drawdown_r': 0.0,
            'mejor_racha': 0,
            'peor_racha': 0,
            'buy_count': 0,
            'sell_count': 0,
        }

    sl_count = sum(1 for r in resultados if r['resultado'] == 'SL')
    tp1_count = sum(1 for r in resultados if r['resultado'] == 'TP1')
    tp2_count = sum(1 for r in resultados if r['resultado'] == 'TP2')
    tp3_count = sum(1 for r in resultados if r['resultado'] == 'TP3')

    wins = tp1_count + tp2_count + tp3_count
    win_rate = wins / total * 100 if total > 0 else 0.0

    # R-multiples
    r_multiples = []
    for r in resultados:
        resultado = r['resultado']
        tp1_rr = r['tp1_rr']
        tp2_rr = r['tp2_rr']
        tp3_rr = r['tp3_rr']

        if resultado == 'SL':
            r_multiples.append(-1.0)
        elif resultado == 'TP1':
            r_multiples.append(tp1_rr)
        elif resultado == 'TP2':
            r_multiples.append(tp2_rr)
        elif resultado == 'TP3':
            r_multiples.append(tp3_rr)
        else:
            r_multiples.append(0.0)

    total_wins_r = sum(r for r in r_multiples if r > 0)
    total_loss_r = abs(sum(r for r in r_multiples if r < 0))
    profit_factor = total_wins_r / total_loss_r if total_loss_r > 0 else (float('inf') if total_wins_r > 0 else 0.0)

    # Max drawdown
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in r_multiples:
        equity += r
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    # Streaks
    mejor_racha = 0
    peor_racha = 0
    curr_win = 0
    curr_loss = 0
    for r in r_multiples:
        if r > 0:
            curr_win += 1
            curr_loss = 0
            mejor_racha = max(mejor_racha, curr_win)
        elif r < 0:
            curr_loss += 1
            curr_win = 0
            peor_racha = max(peor_racha, curr_loss)
        else:
            curr_win = 0
            curr_loss = 0

    buy_count = sum(1 for r in resultados if r.get('tipo') == 'BUY')
    sell_count = sum(1 for r in resultados if r.get('tipo') == 'SELL')

    return {
        'total_senales': total,
        'win_rate': round(win_rate, 1),
        'sl_pct': round(sl_count / total * 100, 1),
        'tp1_pct': round(tp1_count / total * 100, 1),
        'tp2_pct': round(tp2_count / total * 100, 1),
        'tp3_pct': round(tp3_count / total * 100, 1),
        'profit_factor': round(profit_factor, 2) if profit_factor != float('inf') else 999.99,
        'max_drawdown_r': round(max_dd, 2),
        'mejor_racha': mejor_racha,
        'peor_racha': peor_racha,
        'buy_count': buy_count,
        'sell_count': sell_count,
    }


def ejecutar_backtest(simbolo: str, tf: str, desde: str, hasta: str,
                      params: Optional[dict] = None) -> dict:
    """
    Main backtest function. Walk-forward: at each bar, only uses past data.
    Returns dict with 'resultados' list and 'metricas' dict.
    """
    if params is None:
        params = {
            'rsi_length': 14,
            'ema_fast_len': 9,
            'ema_slow_len': 21,
            'atr_length': 14,
            'atr_sl_mult': 1.5,
            'atr_tp1_mult': 1.5,
            'atr_tp2_mult': 2.5,
            'atr_tp3_mult': 4.0,
        }

    df = cargar_datos(simbolo, tf, desde, hasta)

    if df.empty or len(df) < 100:
        return {
            'simbolo': simbolo,
            'tf': tf,
            'desde': desde,
            'hasta': hasta,
            'resultados': [],
            'metricas': calcular_metricas([]),
        }

    resultados = []
    senales_activas = []  # (bar_idx, senal_dict)
    WARMUP = 100  # minimum bars needed
    MAX_BARS_ESPERA = 50  # max bars to wait for signal resolution

    for i in range(WARMUP, len(df)):
        df_slice = df.iloc[:i + 1]

        # Evaluate open signals
        nuevas_activas = []
        for (bar_inicio, senal) in senales_activas:
            bars_esperados = i - bar_inicio
            if bars_esperados >= MAX_BARS_ESPERA:
                resultado = 'EN_CURSO'
            else:
                df_futuro = df.iloc[bar_inicio + 1:i + 1]
                resultado = evaluar_senal(senal, df_futuro)

            if resultado != 'EN_CURSO':
                riesgo = abs(senal['entry'] - senal['sl'])
                resultados.append({
                    'bar': bar_inicio,
                    'fecha': str(df.index[bar_inicio]),
                    'tipo': senal['tipo'],
                    'resultado': resultado,
                    'entry': senal['entry'],
                    'sl': senal['sl'],
                    'tp1': senal['tp1'],
                    'tp2': senal['tp2'],
                    'tp3': senal['tp3'],
                    'riesgo': riesgo,
                    'tp1_rr': abs(senal['tp1'] - senal['entry']) / riesgo if riesgo > 0 else 0,
                    'tp2_rr': abs(senal['tp2'] - senal['entry']) / riesgo if riesgo > 0 else 0,
                    'tp3_rr': abs(senal['tp3'] - senal['entry']) / riesgo if riesgo > 0 else 0,
                })
            else:
                nuevas_activas.append((bar_inicio, senal))
        senales_activas = nuevas_activas

        # Generate new signal at this bar
        senal = _generar_senal_simple(df_slice, params)
        if senal is not None:
            senales_activas.append((i, senal))

    # Remaining open signals are excluded from metrics (EN_CURSO)

    metricas = calcular_metricas(resultados)

    return {
        'simbolo': simbolo,
        'tf': tf,
        'desde': desde,
        'hasta': hasta,
        'resultados': resultados,
        'metricas': metricas,
    }


def imprimir_tabla(metricas: dict, simbolo: str, tf: str, desde: str, hasta: str):
    """Prints ASCII results table."""
    print("\n" + "="*60)
    print(f"  BACKTEST RESULTS — {simbolo} {tf.upper()}")
    print(f"  Período: {desde} → {hasta}")
    print("="*60)
    print(f"  Total señales:    {metricas['total_senales']}")
    print(f"  Win Rate:         {metricas['win_rate']}%")
    print(f"  ─────────────────────────────────────────")
    print(f"  SL:               {metricas['sl_pct']}%")
    print(f"  TP1:              {metricas['tp1_pct']}%")
    print(f"  TP2:              {metricas['tp2_pct']}%")
    print(f"  TP3:              {metricas['tp3_pct']}%")
    print(f"  ─────────────────────────────────────────")
    print(f"  Profit Factor:    {metricas['profit_factor']}")
    print(f"  Max Drawdown (R): {metricas['max_drawdown_r']}")
    print(f"  Mejor racha:      {metricas['mejor_racha']} wins")
    print(f"  Peor racha:       {metricas['peor_racha']} losses")
    print(f"  ─────────────────────────────────────────")
    print(f"  BUY:  {metricas['buy_count']}  |  SELL: {metricas['sell_count']}")
    print("="*60 + "\n")


def guardar_resultados(resultado: dict, simbolo: str, tf: str):
    """Saves results to backtest_results/ directory."""
    os.makedirs('backtest_results', exist_ok=True)
    fecha_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    nombre = f"backtest_results/{simbolo}_{tf}_{fecha_str}.json"

    resultado_serializable = json.loads(
        json.dumps(resultado, default=str)
    )

    with open(nombre, 'w', encoding='utf-8') as f:
        json.dump(resultado_serializable, f, indent=2, ensure_ascii=False)

    print(f"  💾 Resultados guardados en: {nombre}")
    return nombre


def plot_equity_curve(resultados: List[dict]):
    """Generates equity curve plot (optional, requires matplotlib)."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("  ⚠️ matplotlib no instalado. Instala con: pip install matplotlib")
        return

    r_multiples = []
    for r in resultados:
        resultado = r['resultado']
        if resultado == 'SL':
            r_multiples.append(-1.0)
        elif resultado == 'TP1':
            r_multiples.append(r['tp1_rr'])
        elif resultado == 'TP2':
            r_multiples.append(r['tp2_rr'])
        elif resultado == 'TP3':
            r_multiples.append(r['tp3_rr'])
        else:
            r_multiples.append(0.0)

    equity = [0.0]
    for r in r_multiples:
        equity.append(equity[-1] + r)

    plt.figure(figsize=(12, 6))
    plt.plot(equity, label='Equity Curve (R-multiples)', color='blue')
    plt.axhline(y=0, color='red', linestyle='--', alpha=0.5)
    plt.title('Backtest Equity Curve')
    plt.xlabel('Trade #')
    plt.ylabel('Accumulated R')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig('backtest_results/equity_curve.png')
    plt.show()
    print("  📈 Curva de equidad guardada en backtest_results/equity_curve.png")


def main():
    parser = argparse.ArgumentParser(description='Backtester walk-forward para señales de trading')
    parser.add_argument('--simbolo', default='XAUUSD', help='Símbolo a analizar (default: XAUUSD)')
    parser.add_argument('--tf', default='1h', help='Timeframe: 1h, 4h, 1d, 15m, 5m (default: 1h)')
    parser.add_argument('--desde', required=True, help='Fecha inicio YYYY-MM-DD')
    parser.add_argument('--hasta', default=None, help='Fecha fin YYYY-MM-DD (default: hoy)')
    parser.add_argument('--plot', action='store_true', help='Generar curva de equidad')
    args = parser.parse_args()

    hasta = args.hasta or date.today().strftime('%Y-%m-%d')

    print(f"\n🔍 Iniciando backtest: {args.simbolo} {args.tf.upper()}")
    print(f"   Período: {args.desde} → {hasta}")

    resultado = ejecutar_backtest(
        simbolo=args.simbolo,
        tf=args.tf,
        desde=args.desde,
        hasta=hasta,
    )

    imprimir_tabla(resultado['metricas'], args.simbolo, args.tf, args.desde, hasta)
    guardar_resultados(resultado, args.simbolo, args.tf)

    if args.plot and resultado['resultados']:
        plot_equity_curve(resultado['resultados'])


if __name__ == '__main__':
    main()
