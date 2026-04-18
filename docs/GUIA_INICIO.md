# 🚀 Guía de Inicio Rápido

Pasos para instalar, configurar y ejecutar el sistema de detección de señales.

---

## 📋 Requisitos Previos

- **Python 3.8+** (recomendado 3.10+)
- **Bot de Telegram** (obtener en @BotFather)
- **Cuenta Turso** (base de datos SQLite en la nube) — [turso.tech](https://turso.tech)
- **Git** (para clonar el repo)

---

## 1️⃣ Clonar Repositorio

```bash
git clone https://github.com/alumno109192/botTrading.git
cd botTrading
```

---

## 2️⃣ Crear Entorno Virtual

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**En Linux/Mac:**
```bash
python3 -m venv venv
source venv/bin/activate
```

---

## 3️⃣ Instalar Dependencias

```powershell
pip install -r requirements.txt
```

**Dependencias principales:**
- `Flask 3.0.2` — Servidor web
- `yfinance` — Descarga datos de Yahoo Finance
- `libsql-client` — Cliente Turso
- `requests` — Envío HTTP (Telegram)
- `python-dotenv` — Variables de entorno
- `plotly`, `tabulate`, `schedule` — Visualización y scheduling

---

## 4️⃣ Configurar Variables de Entorno

### Crear archivo `.env`

Copia `.env.example` a `.env`:

```bash
cp .env.example .env
```

### Editar `.env` con tus credenciales

```env
# ═══════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════
TELEGRAM_TOKEN=123456789:ABCdefGHIjklmnoPQRstuvWXYZ_1a2b3c4d
TELEGRAM_CHAT_ID=987654321

# ═══════════════════════════════════════════════════════
# TURSO DATABASE
# ═══════════════════════════════════════════════════════
TURSO_DATABASE_URL=libsql://tu-database-url.turso.io
TURSO_AUTH_TOKEN=eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9...

# ═══════════════════════════════════════════════════════
# OPCIONAL: CRON TOKEN (para /status endpoint)
# ═══════════════════════════════════════════════════════
CRON_TOKEN=tu_token_secreto_aqui
```

### Obtener TELEGRAM_TOKEN

1. Abre Telegram
2. Busca **@BotFather**
3. Envía `/newbot`
4. Sigue las instrucciones (nombre del bot, etc.)
5. Copia el token que te devuelve

**Ejemplo token:**
```
123456789:ABCdefGHIjklmnoPQRstuvWXYZ_1a2b3c4d
```

### Obtener TELEGRAM_CHAT_ID

1. Abre Telegram
2. Busca **@userinfobot**
3. Inicia conversación
4. Te devuelve tu chat ID (ej: `987654321`)

### Obtener TURSO_DATABASE_URL + TURSO_AUTH_TOKEN

1. Visita [turso.tech](https://turso.tech)
2. Crea cuenta gratuita
3. Crea nueva base de datos
4. Copia:
   - **Database URL**: `libsql://xxxx-yyyy-zzzz.turso.io`
   - **Auth Token**: Token JWT (empieza con `eyJh...`)

---

## 5️⃣ Verificar Conexiones

### Probar Turso (Base de Datos)

```powershell
python db_manager.py
```

**Salida esperada:**
```
✅ Conexión a Turso establecida correctamente
✅ Test de conexión exitoso
📊 Señales activas: 0
```

### Probar Telegram

```powershell
python test_telegram.py
```

**Salida esperada:**
```
✅ Telegram: Mensaje enviado correctamente
```

---

## 6️⃣ Ejecutar el Sistema

### ✨ Opción 1: Sistema Completo (RECOMENDADO)

Ejecuta todos los detectores + monitor de señales + servidor Flask:

```powershell
python app.py
```

**Se inician automáticamente:**
- 🥇 detector_gold_1d, detector_gold_4h, detector_gold_15m, detector_gold_5m
- ₿ detector_bitcoin_1d, detector_bitcoin_4h
- 📈 detector_spx_1d, detector_spx_4h
- 💶 detector_eurusd_1d, detector_eurusd_4h
- 🥈 detector_silver_1d, detector_silver_4h
- 🛢️ detector_wti_1d, detector_wti_4h
- 📊 detector_nasdaq_1d, detector_nasdaq_4h
- 📱 signal_monitor (revisa TP/SL cada 5 min)
- 🌐 Flask server en `http://0.0.0.0:5000`

**Logs esperados:**
```
[INFO] 🔵 Iniciando DETECTOR GOLD 1D...
[INFO] 🔵 Iniciando DETECTOR GOLD 4H...
[INFO] 🔵 Iniciando DETECTOR SPX 1D...
[INFO] ✅ Detectores activos: 14 threads
[INFO] 📌 Presiona Ctrl+C para detener
[INFO] 🌐 Flask server listening on 0.0.0.0:5000
```

### 🔧 Opción 2: Un Detector Individual

```powershell
# Solo Oro 1D
python detectors/gold/detector_gold_1d.py

# Solo Bitcoin 4H
python detectors/bitcoin/detector_bitcoin_4h.py

# Solo SPX 1D
python detectors/spx/detector_spx_1d.py
```

### 📊 Opción 3: Monitor de Señales Solo

Revisa señales activas cada 5 minutos (TP/SL):

```powershell
python signal_monitor.py
```

---

## 🛑 Detener la Ejecución

Presiona **Ctrl + C** en la terminal. El sistema se detiene de forma segura:

```
^C
[INFO] 🛑 Deteniendo detectores...
[INFO] ✅ Todos los threads han terminado
```

---

## 🐛 Troubleshooting

### ❌ "ModuleNotFoundError: No module named 'flask'"

**Solución:** Instala dependencias:
```powershell
pip install -r requirements.txt
```

### ❌ "Connection refused to Turso"

**Solución:** Verifica `.env`:
```
TURSO_DATABASE_URL=libsql://...  ✅ URL correcta?
TURSO_AUTH_TOKEN=eyJh...         ✅ Token correcto?
```

### ❌ "Telegram: Unauthorized (401)"

**Solución:** Verifica token:
```powershell
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('TELEGRAM_TOKEN'))"
```

Debe mostrarte el token (ej: `123456789:ABCdef...`). Si está vacío, actualiza `.env`.

### ❌ "ModuleNotFoundError: No module named 'yfinance'"

**Solución:** Reinstala con pip:
```powershell
pip install --upgrade yfinance
```

---

## 🎯 Primeros Pasos Recomendados

1. ✅ Instalar (`pip install -r requirements.txt`)
2. ✅ Configurar `.env` con tus tokens
3. ✅ Probar conexiones (`python db_manager.py` + `python test_telegram.py`)
4. ✅ Ejecutar un detector individual (ej: `python detectors/gold/detector_gold_1d.py`)
5. ✅ Si todo funciona, ejecutar sistema completo (`python app.py`)

---

## 📚 Siguiente Paso

Una vez que todo funcione:

- **Entender la arquitectura** → Ver [ARQUITECTURA.md](ARQUITECTURA.md)
- **Crear un nuevo detector** → Ver [DETECTORS.md](DETECTORS.md)
- **Configurar para Render** → Ver [RENDER_CONFIG.md](RENDER_CONFIG.md)
- **Ver documentación completa** → Ver [INDEX.md](INDEX.md)

---

## 🔗 Enlaces Útiles

| Recurso | Enlace |
|---------|--------|
| Telegram BotFather | https://t.me/BotFather |
| Telegram UserInfoBot | https://t.me/userinfobot |
| Turso Cloud | https://turso.tech |
| Yahoo Finance API | https://finance.yahoo.com |

---

*Última actualización: 2026-04-18*
