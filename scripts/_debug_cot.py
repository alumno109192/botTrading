"""Investiga la estructura del CSV del COT para encontrar el nombre correcto de columna."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import requests, io, zipfile, pandas as pd
from datetime import datetime

year = datetime.now().year
url = f"https://www.cftc.gov/files/dea/history/deacot{year}.zip"
print(f"Descargando: {url}")
resp = requests.get(url, timeout=20)
resp.raise_for_status()

with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
    name = z.namelist()[0]
    print(f"Archivo en zip: {name}")
    with z.open(name) as f:
        df = pd.read_csv(f, low_memory=False)

print(f"\nColumnas ({len(df.columns)}):")
for c in df.columns[:15]:
    print(f"  {c!r}")

print(f"\nPrimera columna con valores:")
print(df.iloc[0, :5])

# Buscar filas de Gold
gold_mask = df.iloc[:, 0].astype(str).str.upper().str.contains('GOLD', na=False)
print(f"\nFilas con 'GOLD': {gold_mask.sum()}")
if gold_mask.sum() > 0:
    print(df[gold_mask].iloc[0, :5])
