# 🚀 Próximos Pasos - Bot Trading

**Fecha:** 5 de Abril, 2026  
**Estado actual:** Indicadores de Alta Prioridad Implementados ✅

---

## 📋 HACER AHORA (Inmediato)

### 1. ✅ Verificar Instalación de Dependencias

Asegúrate de que todas las librerías están instaladas:

```bash
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Dependencias necesarias:**
- `yfinance` - Descarga datos de mercado
- `pandas` - Análisis de datos
- `numpy` - Cálculos numéricos
- `requests` - Comunicación con Telegram
- `python-dotenv` - Variables de entorno

---

### 2. ✅ Configurar Variables de Entorno

Si aún no lo has hecho, crea el archivo `.env`:

```bash
# Copiar plantilla
copy .env.example .env

# Editar con tus credenciales
notepad .env
```

**Contenido del `.env`:**
```env
TELEGRAM_TOKEN=tu_token_del_bot_aqui
TELEGRAM_CHAT_ID=tu_chat_id_aqui
```

**Cómo obtener las credenciales:**
1. **Token del Bot:**
   - Habla con [@BotFather](https://t.me/BotFather) en Telegram
   - Envía `/newbot` y sigue las instrucciones
   - Copia el token que te proporciona

2. **Chat ID:**
   - Envía un mensaje a tu bot
   - Visita: `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
   - Busca el campo `"chat":{"id":123456789}`
   - Copia ese número

---

### 3. 🧪 Prueba de Detección Única

Antes de dejar el bot corriendo 24/7, haz una prueba manual:

```bash
# Probar detector de ORO
.\venv\Scripts\python.exe detector_gold.py

# Probar detector de SPX500
.\venv\Scripts\python.exe detector_spx.py

# Probar detector de BITCOIN
.\venv\Scripts\python.exe detector_bitcoin.py
```

**Qué esperar:**
- ✅ Descarga de datos históricos (2 años)
- ✅ Cálculo de indicadores
- ✅ Análisis de última vela cerrada
- ✅ Envío de mensaje inicial a Telegram
- ✅ Loop cada 14 minutos

**Si hay errores:**
- Revisa las credenciales en `.env`
- Verifica conexión a internet
- Comprueba que el bot puede escribir al chat

---

### 4. 🚀 Ejecutar Todos los Detectores Simultáneamente

Una vez verificado que funcionan individualmente:

```bash
.\venv\Scripts\python.exe run_detectors.py
```

**Esto inicia:**
- 🥇 Detector XAUUSD (Oro)
- 📊 Detector SPX500
- ₿ Detector BTCUSD
- Todos en paralelo con threads

**Mensaje esperado en consola:**
```
🚀 Iniciando detectores multi-instrumento...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 Detectores activos:
  - XAUUSD (Gold)
  - SPX500 (S&P 500)
  - BTCUSD (Bitcoin)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 📊 VALIDACIÓN (1-2 Semanas)

### 5. 📝 Monitoreo de Señales

**Objetivo:** Validar que los nuevos indicadores funcionan correctamente.

**Acciones:**
1. **Crear hoja de seguimiento** (Excel/Google Sheets):
   ```
   | Fecha | Hora | Activo | Tipo | Score | Precio Entrada | SL | TP1 | Resultado |
   |-------|------|--------|------|-------|----------------|----|----|-----------|
   ```

2. **Registrar TODAS las señales** que envíe el bot:
   - Señales de Alerta (4+ pts)
   - Señales Medias (6+ pts)
   - Señales Fuertes (8+ pts)
   - Señales Máximas (10+ pts)

3. **Anotar observaciones:**
   - ¿La señal fue válida?
   - ¿Alcanzó TP1/TP2/TP3?
   - ¿Saltó el SL?
   - ¿Qué indicadores confluyeron?

**Métricas a calcular:**
- **Win Rate:** (Operaciones ganadoras / Total) × 100
- **Profit Factor:** Ganancias totales / Pérdidas totales
- **Señales por día:** Total señales / días monitorizados
- **Tasa de falsas alarmas:** Señales inválidas / Total señales

---

### 6. 🔍 Análisis de Indicadores

**Objetivo:** Identificar qué indicadores aportan más valor.

**Analizar:**
1. **ADX (Filtro lateral):**
   - ¿Cuántas señales penalizó?
   - ¿Evitó señales falsas efectivamente?
   - ¿Hay casos donde penalizó señales válidas?

2. **Bollinger Bands:**
   - ¿Las señales en bandas extremas fueron fiables?
   - ¿El squeeze predijo movimientos correctamente?

3. **MACD:**
   - ¿Los cruces fueron oportunos?
   - ¿Las divergencias se cumplieron?

4. **OBV:**
   - ¿Confirmó movimientos de precio?
   - ¿Las divergencias fueron fiables?

5. **Evening/Morning Star:**
   - ¿Se detectaron correctamente?
   - ¿Fueron reversiones reales?

---

### 7. 📈 Comparación con Sistema Anterior

**Si tienes datos históricos del sistema anterior:**

| Métrica | Sistema Anterior | Sistema Nuevo | Mejora |
|---------|------------------|---------------|--------|
| Win Rate | ___% | ___% | +___% |
| Señales/día | ___ | ___ | ___ |
| Falsas alarmas | ___% | ___% | -___% |
| Profit Factor | ___ | ___ | ___ |

**Objetivo:** Validar la mejora estimada de +15-20% en win rate.

---

## 🔧 AJUSTES (Según Resultados)

### 8. ⚙️ Ajustar Umbrales de Señal (Opcional)

**Si hay demasiadas señales de baja calidad:**

Editar en cada detector:

```python
# ACTUAL
senal_sell_maxima = score_sell >= 10
senal_sell_fuerte = score_sell >= 8
senal_sell_media  = score_sell >= 6
senal_sell_alerta = score_sell >= 4

# OPCIÓN CONSERVADORA
senal_sell_maxima = score_sell >= 14  # Solo las mejores
senal_sell_fuerte = score_sell >= 11
senal_sell_media  = score_sell >= 8
senal_sell_alerta = score_sell >= 5   # Ignorar alertas débiles
```

**Ventaja:** Menos señales pero más fiables.  
**Desventaja:** Puede perder oportunidades.

---

### 9. 🎛️ Calibrar Parámetros por Activo

**Si un activo genera señales menos fiables:**

Ajustar en `SIMBOLOS = {...}` de cada detector:

```python
# Ejemplo: Hacer ORO más conservador
'XAUUSD': {
    # ...parámetros existentes...
    'rsi_min_sell':       60.0,  # Era 55.0 → Más exigente
    'rsi_max_buy':        40.0,  # Era 45.0 → Más exigente
    'atr_sl_mult':        2.0,   # Era 1.5 → SL más amplio
}
```

---

### 10. 🚫 Filtros Adicionales (Si Muchas Falsas Señales)

**Agregar validaciones extras:**

```python
# Ejemplo: No operar cerca de eventos importantes
from datetime import datetime

# Evitar primer viernes del mes (NFP)
if datetime.now().weekday() == 4:  # Viernes
    if 1 <= datetime.now().day <= 7:
        print("🚫 Evitando trading por NFP")
        return

# Evitar horario asiático (menor liquidez)
hora_actual = datetime.now().hour
if 0 <= hora_actual <= 7:  # 00:00-07:00 GMT
    print("🚫 Evitando sesión asiática")
    return
```

---

## 🎯 FASE 2: Indicadores de Prioridad Media (Opcional)

### 11. 📚 Implementar Indicadores Adicionales

**Una vez validado que Fase 1 funciona bien:**

Según [ANALISIS_PATRONES_INDICADORES.md](ANALISIS_PATRONES_INDICADORES.md), los siguientes en prioridad son:

#### 6. Fibonacci Retracements ⭐⭐⭐⭐
```python
def calcular_fibonacci(swing_high, swing_low):
    diff = swing_high - swing_low
    niveles = {
        '23.6%': swing_low + (diff * 0.236),
        '38.2%': swing_low + (diff * 0.382),
        '50.0%': swing_low + (diff * 0.500),
        '61.8%': swing_low + (diff * 0.618),
        '78.6%': swing_low + (diff * 0.786),
    }
    return niveles
```

**Uso:** TP dinámicos en lugar de fijos.

---

#### 7. Stochastic Oscillator ⭐⭐⭐⭐
```python
def calcular_stochastic(df, k_period=14, d_period=3):
    low_min = df['Low'].rolling(window=k_period).min()
    high_max = df['High'].rolling(window=k_period).max()
    
    k = 100 * ((df['Close'] - low_min) / (high_max - low_min))
    d = k.rolling(window=d_period).mean()
    
    return k, d
```

**Señales:**
- Sobrecompra: K > 80
- Sobreventa: K < 20
- Cruces: K cruza D

---

#### 8. Three Black Crows / Three White Soldiers ⭐⭐⭐
```python
def detectar_three_black_crows(df, idx):
    """Tres velas bajistas consecutivas decrecientes"""
    if idx < 2:
        return False
        
    v1 = df.iloc[idx - 2]
    v2 = df.iloc[idx - 1]
    v3 = df.iloc[idx]
    
    # Las 3 velas son bajistas
    bajistas = (v1['Close'] < v1['Open'] and 
                v2['Close'] < v2['Open'] and 
                v3['Close'] < v3['Open'])
    
    # Mínimos y máximos decrecientes
    decreciente = (v1['High'] > v2['High'] > v3['High'] and
                   v1['Low'] > v2['Low'] > v3['Low'])
    
    # Cuerpos grandes (> 60% del rango)
    cuerpos_grandes = all([
        abs(v['Close'] - v['Open']) > (v['High'] - v['Low']) * 0.6
        for v in [v1, v2, v3]
    ])
    
    return bajistas and decreciente and cuerpos_grandes
```

---

#### 9. Pivot Points ⭐⭐⭐
```python
def calcular_pivot_points(prev_high, prev_low, prev_close):
    """Pivot Points clásicos para niveles intradía"""
    pp = (prev_high + prev_low + prev_close) / 3
    
    r1 = (2 * pp) - prev_low
    r2 = pp + (prev_high - prev_low)
    r3 = prev_high + 2 * (pp - prev_low)
    
    s1 = (2 * pp) - prev_high
    s2 = pp - (prev_high - prev_low)
    s3 = prev_low - 2 * (prev_high - pp)
    
    return {
        'PP': pp,
        'R1': r1, 'R2': r2, 'R3': r3,
        'S1': s1, 'S2': s2, 'S3': s3,
    }
```

---

#### 10. Higher Timeframe Bias ⭐⭐⭐⭐⭐
```python
def analizar_htf_bias(ticker, params):
    """Analiza tendencia en timeframe superior"""
    # Descargar datos semanales
    df_weekly = yf.download(ticker, period='1y', interval='1wk')
    
    # Calcular EMAs en semanal
    ema_fast_w = calcular_ema(df_weekly['Close'], 9)
    ema_slow_w = calcular_ema(df_weekly['Close'], 21)
    
    # Determinar bias
    if ema_fast_w.iloc[-1] > ema_slow_w.iloc[-1]:
        return "ALCISTA"
    elif ema_fast_w.iloc[-1] < ema_slow_w.iloc[-1]:
        return "BAJISTA"
    else:
        return "NEUTRAL"
```

**Uso:** Solo operar a favor del bias semanal.

---

## 🔬 BACKTESTING (Recomendado)

### 12. 📊 Testear con Datos Históricos

**Crear script de backtesting:**

```python
# backtest.py
import pandas as pd
from detector_gold import analizar, SIMBOLOS

def backtest_historico(simbolo, params, fecha_inicio, fecha_fin):
    """
    Analiza todas las velas históricas y simula trading
    """
    resultados = []
    
    # Descargar datos históricos
    df = yf.download(params['ticker_yf'], 
                     start=fecha_inicio, 
                     end=fecha_fin, 
                     interval='1d')
    
    # Calcular todos los indicadores
    # ... (mismo código que en analizar())
    
    # Simular análisis vela por vela
    for i in range(210, len(df) - 1):  # Necesitamos 210 velas para indicadores
        # Simular que esta es la "última vela"
        vela_resultado = simular_trading(df, i, params)
        if vela_resultado:
            resultados.append(vela_resultado)
    
    # Calcular métricas
    win_rate = calcular_win_rate(resultados)
    profit_factor = calcular_profit_factor(resultados)
    
    return {
        'total_trades': len(resultados),
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'resultados': resultados
    }

# Ejecutar
if __name__ == '__main__':
    resultado = backtest_historico(
        'XAUUSD', 
        SIMBOLOS['XAUUSD'],
        fecha_inicio='2024-01-01',
        fecha_fin='2026-04-01'
    )
    
    print(f"Total trades: {resultado['total_trades']}")
    print(f"Win rate: {resultado['win_rate']:.2f}%")
    print(f"Profit factor: {resultado['profit_factor']:.2f}")
```

**Ejecutar:**
```bash
.\venv\Scripts\python.exe backtest.py
```

---

## 🎓 OPTIMIZACIÓN AVANZADA (Largo Plazo)

### 13. 🤖 Machine Learning (Futuro)

**Si quieres llevar el bot al siguiente nivel:**

1. **Recopilar datos de señales:**
   - Guardar todas las señales en CSV/base de datos
   - Incluir: scores de indicadores, resultado real, contexto de mercado

2. **Entrenar modelo predictivo:**
   ```python
   from sklearn.ensemble import RandomForestClassifier
   
   # Features: todos los indicadores
   X = df[['rsi', 'macd', 'adx', 'obv', 'bb_width', ...]]
   
   # Target: señal fue exitosa (1) o no (0)
   y = df['resultado_exitoso']
   
   # Entrenar
   model = RandomForestClassifier()
   model.fit(X, y)
   
   # Predecir probabilidad de éxito
   prob = model.predict_proba(X_nueva)
   ```

3. **Usar predicción para filtrar:**
   ```python
   # Solo enviar señal si probabilidad de éxito > 70%
   if prob[1] > 0.70:
       enviar_telegram(mensaje)
   ```

---

### 14. 📱 Dashboard de Monitoreo

**Crear interfaz web para visualizar:**

```bash
pip install streamlit plotly
```

```python
# dashboard.py
import streamlit as st
import plotly.graph_objects as go

st.title("🤖 Bot Trading Dashboard")

# Mostrar señales recientes
st.header("📊 Señales Recientes")
df_senales = pd.read_csv("senales.csv")
st.dataframe(df_senales.tail(20))

# Gráfico de win rate
st.header("📈 Win Rate por Activo")
fig = go.Figure(data=[
    go.Bar(x=['XAUUSD', 'SPX500', 'BTCUSD'], 
           y=[75, 68, 82])
])
st.plotly_chart(fig)
```

**Ejecutar:**
```bash
streamlit run dashboard.py
```

---

### 15. 🔔 Alertas Multi-Canal

**Expandir más allá de Telegram:**

1. **Email:**
   ```python
   import smtplib
   from email.mime.text import MIMEText
   
   def enviar_email(mensaje):
       msg = MIMEText(mensaje)
       msg['Subject'] = '🚨 Nueva Señal Trading'
       msg['From'] = 'bot@trading.com'
       msg['To'] = 'tu@email.com'
       
       server = smtplib.SMTP('smtp.gmail.com', 587)
       server.starttls()
       server.login('bot@trading.com', 'password')
       server.send_message(msg)
       server.quit()
   ```

2. **Discord:**
   ```python
   import discord_webhook
   
   def enviar_discord(mensaje):
       webhook = discord_webhook.DiscordWebhook(
           url='tu_webhook_url',
           content=mensaje
       )
       webhook.execute()
   ```

3. **Notificaciones Push (Pushover):**
   ```python
   import requests
   
   def enviar_push(mensaje):
       requests.post("https://api.pushover.net/1/messages.json", data={
           "token": "APP_TOKEN",
           "user": "USER_KEY",
           "message": mensaje
       })
   ```

---

## ✅ CHECKLIST COMPLETO

### Inmediato (Hoy)
- [ ] Verificar instalación de dependencias
- [ ] Configurar variables de entorno (.env)
- [ ] Prueba individual de cada detector
- [ ] Ejecutar run_detectors.py
- [ ] Verificar recepción en Telegram

### Corto Plazo (1-2 Semanas)
- [ ] Crear hoja de seguimiento de señales
- [ ] Monitorizar señales diariamente
- [ ] Registrar resultados (TP/SL alcanzados)
- [ ] Calcular win rate preliminar
- [ ] Analizar efectividad de indicadores

### Medio Plazo (1 Mes)
- [ ] Comparar métricas con sistema anterior
- [ ] Ajustar umbrales si es necesario
- [ ] Calibrar parámetros por activo
- [ ] Decidir si implementar Fase 2

### Largo Plazo (2+ Meses)
- [ ] Implementar backtesting completo
- [ ] Añadir indicadores Fase 2 (opcional)
- [ ] Considerar ML para optimización
- [ ] Crear dashboard de monitoreo
- [ ] Expandir a más activos/mercados

---

## 📞 Soporte y Recursos

**Documentación del proyecto:**
- [README.md](README.md) - Guía general
- [INDICADORES_IMPLEMENTADOS.md](INDICADORES_IMPLEMENTADOS.md) - Detalles técnicos
- [ANALISIS_PATRONES_INDICADORES.md](ANALISIS_PATRONES_INDICADORES.md) - Análisis completo

**Referencias externas:**
- [yfinance Docs](https://pypi.org/project/yfinance/)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Pandas Documentation](https://pandas.pydata.org/docs/)

**Comunidad:**
- Telegram: Grupo de traders (crear si no existe)
- GitHub: Issues para reportar problemas

---

## 🎯 Objetivo Final

**Tener un sistema de trading automatizado:**
- ✅ Confiable (win rate > 65%)
- ✅ Rentable (profit factor > 1.5)
- ✅ Automatizado (corre 24/7 sin intervención)
- ✅ Escalable (fácil añadir más activos)
- ✅ Monitoreado (métricas claras de rendimiento)

**Próxima revisión:** 19 de Abril, 2026 (en 2 semanas)

---

**¡Éxito en tu trading! 🚀📈**
