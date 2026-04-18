# Bot Trading — Detectores de Señales Técnicas

Sistema automatizado de detección de señales de trading basado en análisis técnico multi-indicador. Analiza múltiples activos (XAUUSD, EURUSD, BTCUSD, SPX500, etc.) en timeframes 1D→5M, genera alertas a Telegram y hace seguimiento automático de TP/SL.

Desplegado en **Render** como servicio web Flask con detectores en background threads. Base de datos en Turso (SQLite Cloud).

🚀 **[DOCUMENTACIÓN COMPLETA →](docs/INDEX.md)** | 📖 [Guía de Inicio](docs/GUIA_INICIO.md) | 🏗️ [Arquitectura](docs/ARQUITECTURA.md) | 📊 [Indicadores](docs/INDICADORES.md)

---

## Instrumentos y Timeframes

| Instrumento | Timeframes | Detector |
|---|---|---|
| ₿ BTCUSD | 1D + 4H | `detectors/bitcoin/` |
| 🥇 XAUUSD | 1D + 4H + 15M (scalping) | `detectors/gold/` |
| 📈 SPX500 | 1D + 4H | `detectors/spx/` |

---

## Arquitectura del Sistema

```
app.py (Flask)
├── detector_bitcoin_1d  ─┐
├── detector_bitcoin_4h   │
├── detector_gold_1d      ├─ Threads en background (cada 4-10 min)
├── detector_gold_4h      │
├── detector_gold_15m     │
├── detector_spx_1d       │
├── detector_spx_4h      ─┘
└── signal_monitor ───── Revisa señales activas cada 5 min (TP/SL)
```

**Base de datos:** Turso (SQLite en la nube) — almacena señales activas, estado y beneficio.

---

## Sistema de Señales

### Scoring

Cada vela se puntúa con múltiples indicadores técnicos:

| Indicador | Peso | Notas |
|---|---|---|
| Zona soporte/resistencia | +2 | Zonas definidas por parámetros |
| Patrón de vela (rechazo/rebote) | +2 | Shooting star, hammer, engulfing... |
| Volumen alto en zona | +2 | `vol > vol_avg × vol_mult` |
| RSI sobrecompra/sobreventa | +1-2 | Umbrales configurables |
| Cruce EMA rápida/lenta | +1-2 | Cruce reciente suma extra |
| Bandas de Bollinger | +2 | Toca banda extrema |
| MACD cruce bajista/alcista | +2 | Confirmado con histograma |
| ADX tendencia | +2 | ADX > 25 con DI alineado |
| Evening/Morning Star | +2 | Patrón de reversión 3 velas |
| OBV divergencia | +1 | Confirmación por volumen acumulado |
| Divergencia RSI/precio | +1 | |
| **Penalización mercado lateral** | **-3** | ADX < 20 |

**Score máximo:** ~24 puntos (varía por detector)

### Niveles de alerta

| Nivel | Score | Descripción |
|---|---|---|
| 👀 ALERTA | 4-5 | Observar, posible oportunidad |
| ⚠️ MEDIA | 6-8 | Probabilidad moderada |
| 🔴🟢 FUERTE | 9-11 | Alta probabilidad |
| ⚡ MÁXIMA | 12+ | Confluencia múltiple fuerte |

### Filtros obligatorios (bloquean señal independientemente del score)

- **Liquidez BTC:** `vol < vol_avg × 0.5` → señal bloqueada (solo BTCUSD 1D y 4H)
- **Anti-duplicado:** no se emite si ya existe señal ACTIVA para ese símbolo+dirección en BD
- **Cancelación por precio:** precio demasiado lejos de la zona (configurable por `cancelar_dist`)

---

## Monitor de Señales

El `signal_monitor.py` revisa cada 5 minutos todas las señales ACTIVAS en la BD y notifica cuando el precio alcanza un nivel:

| Evento | Mensaje Telegram | Acción recomendada |
|---|---|---|
| TP1 alcanzado | 🎯 TP1 ALCANZADO | Cerrar 33% + mover SL a breakeven |
| TP2 alcanzado | 🎯🎯 TP2 ALCANZADO | Cerrar 33% + mover SL a TP1 |
| TP3 alcanzado | 🎯🎯🎯 TP3 ALCANZADO | Cerrar 100% restante |
| SL alcanzado | ❌ STOP LOSS | Cerrar 100% |

Las señales con más de 7 días activas se cierran automáticamente como CANCELADAS.

---

## Formato de Alertas Telegram

```
🔴 SELL FUERTE — BITCOIN 4H
━━━━━━━━━━━━━━━━━━━━
💰 Precio:     $95,500
📌 SELL LIMIT: $96,000
🛑 Stop Loss:  $98,000
🎯 TP1: $85,000  R:R 2.3:1
🎯 TP2: $75,000  R:R 6.5:1
🎯 TP3: $65,000  R:R 11.1:1
━━━━━━━━━━━━━━━━━━━━
📊 Score: 12/15  📉 RSI: 68.2
⏱️ TF: 4H  📅 2026-04-10
```

---

## Instalación

**Requisitos:** Python 3.8+, bot de Telegram, cuenta Turso (BD)

```bash
git clone https://github.com/alumno109192/botTrading.git
cd botTrading
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copiar `.env.example` a `.env` y rellenar:

```env
TELEGRAM_TOKEN=tu_token_del_bot
TELEGRAM_CHAT_ID=tu_chat_id
TURSO_DATABASE_URL=libsql://tu-db.turso.io
TURSO_AUTH_TOKEN=tu_token_turso
```

---

## Ejecución

```bash
# Todos los detectores + monitor (recomendado)
.\venv\Scripts\python.exe app.py

# Un detector individual
.\venv\Scripts\python.exe detectors/bitcoin/detector_bitcoin_4h.py
```

El servidor Flask escucha en `http://0.0.0.0:5000` con endpoint `/health` para keep-alive.

---

## Utilidades

```bash
# Limpiar señales duplicadas en la BD (ejecutar si hay duplicados visibles)
.\venv\Scripts\python.exe limpiar_duplicados.py
```

---

## Variables de entorno

| Variable | Descripción |
|---|---|
| `TELEGRAM_TOKEN` | Token del bot (desde @BotFather) |
| `TELEGRAM_CHAT_ID` | ID del chat donde enviar alertas |
| `TURSO_DATABASE_URL` | URL de la BD Turso (`libsql://...`) |
| `TURSO_AUTH_TOKEN` | Token de autenticación Turso |

### Obtener TELEGRAM_TOKEN
1. Abrir Telegram → buscar **@BotFather**
2. Enviar `/newbot` y seguir instrucciones
3. Copiar el token proporcionado

### Obtener TELEGRAM_CHAT_ID
1. Buscar **@userinfobot** en Telegram
2. Iniciar conversación → te devuelve tu chat ID

---

## Seguridad

- `.env` en `.gitignore` — credenciales nunca se suben a GitHub
- Tokens cargados exclusivamente desde variables de entorno
- Queries a BD parametrizadas (sin concatenación de strings)



🤖 Guía de Agentes IA (Python Architecture Team)
Este repositorio utiliza agentes personalizados de GitHub Copilot para automatizar la auditoría, corrección y documentación del código.
🛠️ Instrucciones de Instalación
Para que los agentes funcionen, asegúrate de que los archivos .agent.md estén en la carpeta .github/agents/ de este repositorio.
👥 El Equipo de Agentes
1. 🏛️ @arquitecto (El Cerebro)
Cuándo usarlo: Al inicio de una tarea o para revisar la salud global del proyecto.

    Comando de una línea:

        @arquitecto #codebase Realiza una auditoría técnica exhaustiva. Genera el REPORTE DENSO: errores críticos, deuda técnica y rendimiento. Formato ultra-esquemático.

2. 🛡️ @seguridad (El Guardián)
Cuándo usarlo: Antes de fusionar cualquier cambio (PR) o al tocar APIs y Base de Datos.

    Comando de una línea:

        @seguridad #codebase Escaneo de seguridad: busca inyecciones, secretos expuestos y dependencias vulnerables. Resumen crítico.

3. 🛠️ @ejecutor (El Mecánico)
Cuándo usarlo: Para aplicar las soluciones del arquitecto o la seguridad.

    Comando de una línea:

        @ejecutor #codebase Aplica estas correcciones del reporte: [Pega aquí el texto del reporte o selecciona el texto del chat]

4. 🧪 @tester (El Verificador)
Cuándo usarlo: Inmediatamente después de que el ejecutor termine su trabajo.

    Comando de una línea:

        @tester #codebase Genera tests con Pytest para los últimos cambios. Usa mocks para DB/APIs y cubre edge cases.

5. 📝 @doc (El Escriba)
Cuándo usarlo: Como paso final antes de hacer el commit.

    Comando de una línea:

        @doc Añade docstrings (Google Style) y Type Hints a las funciones modificadas. Actualiza el README si es necesario.

⚡ Flujo de Trabajo Recomendado (Ahorro de Tokens)
Para trabajar de la forma más barata y eficiente posible, sigue este orden en el chat de Copilot:

    Auditar: @arquitecto #codebase auditoría rápida
    Corregir: @ejecutor arregla el punto 1 y 2 del reporte anterior
    Probar: @tester crea los tests para el fix anterior
    Documentar: @doc añade docstrings al fix

    [!IMPORTANT]
    Usa Claude 3 Opus para el @arquitecto y el @seguridad si el problema es muy complejo. Para el resto de tareas de escritura de código, Claude 3.5 Sonnet es más eficiente.

¿Cómo lo pongo en marcha?
Simplemente abre el panel de chat de Copilot (Ctrl + Shift + i) y empieza escribiendo cualquiera de los comandos anteriores. No olvides incluir siempre #codebase cuando necesites que el agente vea archivos que no tienes abiertos.