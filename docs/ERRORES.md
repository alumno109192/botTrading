Análisis de problemas encontrados
CRÍTICOS (causan crash o datos incorrectos)
#	Archivo	Problema
1	run_detectors.py:12	Importa detector_gold_copy que no existe → crash al ejecutar
2	db_manager.py:55	Parámetro float enviado como valor raw a Turso en vez de string → posible fallo JSON
3	db_manager.py:120-130	Race condition: INSERT + last_insert_rowid() no son atómicos entre threads
4	detector_gold_5m.py:126	Cálculo ADX incorrecto: calcular_atr(df,1) * length no es Wilder smoothing
5	detector_gold_5m.py:530	perdidas_consecutivas es variable local en main(), no global → protección de pérdidas consecutivas rota
6	signal_monitor.py:23	Thread IDs de Telegram cargados como strings, la API requiere int → mensajes van al topic equivocado
MEDIOS (funcionalidad degradada o seguridad)
#	Archivo	Problema
7	signal_monitor.py:15	Lock de yfinance propio, no comparte con app.py → posibles problemas de thread-safety
8	economic_calendar.py:22	Eventos hardcodeados hasta junio 2026, después se desactiva silenciosamente el filtro de seguridad
9	app.py:465	CRON_TOKEN aceptado por query string → se expone en logs
10	gold_news_monitor.py	XML parsing con ET.fromstring() vulnerable a XML entity expansion (billion laughs)
11	Detectores legacy	Zonas S/R hardcodeadas totalmente desactualizadas (gold $4900, SPX 6100)
12	Detectores legacy	Usan yf.download() directo en vez de data_provider → sin fallback ni thread-safety
13	Múltiples archivos	Dict alertas_enviadas crece sin límite → memory leak lento
14	detector_gold.py, detector_bitcoin.py	Score documentado como "/15" pero máximo real es ~26 → umbrales permisivos
15	10+ archivos	Funciones de indicadores copy-paste (RSI, EMA, ATR, ADX, Bollinger, MACD...) → bugs no se propagan
16	run_detectors.py	import sys duplicado + path manipulation frágil
BAJOS (mantenimiento y edge cases)
#	Problema
17	estado_sistema['detectores'] incompleto en app.py
18	Cache de dxy_bias.py no es thread-safe
19-20	Detectores SPX/EURUSD 15M sin filtro de sesión ni calendario económico
21	Column renaming frágil en data_provider.py
22	int() sin try/except en gold_news_monitor.py → crash si env var no numérica
23	strftime en SQL puede fallar con timestamps con timezone
24	DatabaseManager() se inicializa al importar → si Turso caído, app no arranca
25	signal_monitor.py: si 