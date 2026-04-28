"""Ejecutar solo detectores Gold 1H y 4H en modo debug para ver scores"""
import os, sys, logging
os.environ['PYTHONIOENCODING'] = 'utf-8'
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])

print("="*60)
print(f"SKIP_SESSION_FILTER = {os.getenv('SKIP_SESSION_FILTER')}")
print("="*60)

# --- Detector 1H ---
print("\n>>> GOLD 1H <<<")
try:
    from detectors.gold.detector_gold_1h import analizar as analizar_1h, SIMBOLOS as S1H
    analizar_1h('XAUUSD', S1H['XAUUSD'])
except Exception as e:
    print(f"ERROR 1H: {e}")
    import traceback; traceback.print_exc()

# --- Detector 4H ---
print("\n>>> GOLD 4H <<<")
try:
    from detectors.gold.detector_gold_4h import analizar as analizar_4h, SIMBOLOS as S4H
    analizar_4h('XAUUSD', S4H['XAUUSD'])
except Exception as e:
    print(f"ERROR 4H: {e}")
    import traceback; traceback.print_exc()


