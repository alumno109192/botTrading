"""Diagnóstico rápido del estado actual de todos los detectores Gold."""
import sys, logging
sys.path.insert(0, '.')

# Solo log a stdout, sin archivo
for h in logging.root.handlers[:]:
    logging.root.removeHandler(h)
logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
logger = logging.getLogger()

import yfinance as yf
import pandas as pd
from datetime import datetime, timezone

# ── Precio actual ──────────────────────────────────────
print("=" * 60)
print("📊 DIAGNÓSTICO GOLD — " + datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
print("=" * 60)

t = yf.Ticker('GC=F')
fast = t.fast_info
print(f"💰 Precio: ${fast.last_price:.2f}  |  Max: ${fast.day_high:.2f}  Min: ${fast.day_low:.2f}")
print()

# ── Sesgo COT + DXY + OI ───────────────────────────────
try:
    from services.cot_bias import get_cot_bias
    cot, ratio = get_cot_bias()
    print(f"📋 COT:  {cot}  ({ratio:.1%} longs)")
except Exception as e:
    print(f"⚠️  COT error: {e}")

try:
    from services.dxy_bias import get_dxy_bias
    dxy = get_dxy_bias()
    print(f"💵 DXY:  {dxy}")
except Exception as e:
    print(f"⚠️  DXY error: {e}")

try:
    from services.open_interest import get_oi_bias
    from services import open_interest as _oi_mod
    oi_bias = get_oi_bias()
    _det = _oi_mod._cache.get('detalle') or {}
    oi_val = _det.get('oi_disponible') or 0
    print(f"📈 OI:   {oi_bias}  ({oi_val:,.0f} contratos)")
except Exception as e:
    print(f"⚠️  OI error: {e}")

print()

# ── Ejecutar cada detector una vez ─────────────────────
def run_once(module_name, label, simbolo_key, tf_label, simbolo_yf):
    print(f"{'─'*60}")
    print(f"🔍 {label}")
    try:
        import importlib
        m = importlib.import_module(module_name)
        # Inyectar handler en el logger del módulo para capturar output
        mod_logger = logging.getLogger(module_name)
        mod_logger.setLevel(logging.INFO)
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter('  %(message)s'))
        mod_logger.addHandler(ch)
        mod_logger.propagate = False

        m.analizar(simbolo_key, m.SIMBOLOS[simbolo_key])
    except Exception as e:
        print(f"  ❌ Error: {e}")

# Cada TF
for mod, label in [
    ('detectors.gold.detector_gold_1d', 'GOLD 1D'),
    ('detectors.gold.detector_gold_4h', 'GOLD 4H'),
    ('detectors.gold.detector_gold_1h', 'GOLD 1H'),
    ('detectors.gold.detector_gold_15m', 'GOLD 15M'),
    ('detectors.gold.detector_gold_5m',  'GOLD 5M'),
]:
    run_once(mod, label, 'XAUUSD', '', 'GC=F')

print("=" * 60)
print("✅ Diagnóstico completo")
