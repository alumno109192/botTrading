"""Diagnóstico rápido: ejecuta los 5 detectores Gold una vez y muestra scores."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

TFS = ['5m', '15m', '1h', '4h', '1d']
for tf in TFS:
    print(f"\n{'='*50}")
    print(f"  GOLD {tf.upper()}")
    print('='*50)
    try:
        import importlib
        mod = importlib.import_module(f'detectors.gold.detector_gold_{tf}')
        mod.main()
    except Exception as e:
        print(f"  ERROR: {e}")
