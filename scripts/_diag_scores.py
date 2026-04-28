"""Diagnóstico rápido: llama analizar() una vez en cada TF, sin loop."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

# Silenciar inicios de Telegram
import adapters.telegram as _tg
_tg.enviar_telegram = lambda *a, **kw: None

import importlib

TFS = ['5m', '15m', '1h', '4h', '1d']
for tf in TFS:
    print(f"\n{'='*55}")
    print(f"  GOLD {tf.upper()}")
    print('='*55)
    try:
        mod = importlib.import_module(f'detectors.gold.detector_gold_{tf}')
        # 5M usa analizar_simbolo, el resto analizar
        fn = getattr(mod, 'analizar', None) or getattr(mod, 'analizar_simbolo', None)
        for simbolo, params in mod.SIMBOLOS.items():
            fn(simbolo, params)
    except KeyboardInterrupt:
        break
    except Exception as e:
        import traceback
        print(f"  ERROR: {e}")
        traceback.print_exc()
