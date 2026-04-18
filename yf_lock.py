"""
yf_lock.py — Lock compartido para serializar llamadas a yfinance

yfinance NO es thread-safe: comparte estado interno global entre threads.
Importar este lock en TODOS los módulos que llamen a:
  - yf.download()
  - yf.Ticker().history()

app.py parchea yf.download() con este lock.
signal_monitor.py usa este lock directamente en yf.Ticker().history().
"""
import threading

_yf_lock = threading.Lock()
