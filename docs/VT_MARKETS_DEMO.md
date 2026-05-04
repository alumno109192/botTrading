# Integración con VT Markets — Cuenta Demo MT5

> **Estado:** Planificación / Fase de pruebas  
> **Fecha:** Mayo 2026  
> **Objetivo:** Ejecutar automáticamente las señales del bot en una cuenta demo de VT Markets vía MetaTrader 5 Python API.

---

## 1. Arquitectura de la integración

```
┌─────────────────────────────────────────────────────────────────┐
│                       BOT TRADING (actual)                      │
│                                                                 │
│  Detectors  →  base_detector._guardar_senal()  →  Telegram     │
│   (5M/15M/                                                      │
│   1H/4H/1D)                                                     │
└──────────────────────────┬──────────────────────────────────────┘
                           │  NUEVO HOOK (si MT5_AUTO_TRADE=true)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              adapters/mt5_broker.py  (nuevo)                    │
│                                                                 │
│  abrir_operacion(simbolo, direccion, entry, sl, tp1, lotes)     │
│  gestionar_tp(ticket, tp2, tp3)                                 │
│  cerrar_operacion(ticket)                                       │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
             MetaTrader5 Python API (local)
                           │
                           ▼
          VT Markets MT5 Demo → Servidor de trading
```

**Restricción importante:** La librería `MetaTrader5` de Python **solo funciona en Windows** con MT5 instalado localmente. Si el bot corre en Render (Linux), se necesita un servidor puente en Windows local.

---

## 2. Requisitos previos

### 2.1 Cuenta Demo VT Markets

1. Ir a **vtmarkets.com** → *Abrir cuenta demo*
2. Seleccionar plataforma: **MetaTrader 5**
3. Anotar:
   - Número de cuenta (login)
   - Contraseña
   - Servidor (ej. `VTMarkets-Demo`)
4. Descargar e instalar **MetaTrader 5** desde vtmarkets.com

### 2.2 Verificar símbolo XAU/USD en MT5

En MT5 → Ver → Símbolos → buscar `XAUUSD`. VT Markets puede listarlo como:
- `XAUUSD` (más común)
- `GOLD`
- `XAUUSDm`

Añadirlo al Market Watch antes de ejecutar el bot.

### 2.3 Instalar dependencia Python

```bash
pip install MetaTrader5
```

Agregar a `requirements.txt`:
```
MetaTrader5>=5.0.45
```

> **Nota:** Esta dependencia es opcional (solo para Windows con MT5). En Render/Linux se ignora.

---

## 3. Variables de entorno nuevas

Añadir al archivo `.env` local (nunca en Render en esta fase):

```env
# ─── VT Markets / MetaTrader 5 ───────────────────────────────────
MT5_AUTO_TRADE=false          # Poner true para activar ejecución real
MT5_LOGIN=12345678            # Número de cuenta demo
MT5_PASSWORD=tu_password
MT5_SERVER=VTMarkets-Demo     # Nombre exacto del servidor MT5
MT5_SYMBOL=XAUUSD             # Símbolo en MT5 (verificar en Market Watch)

# ─── Gestión de riesgo ───────────────────────────────────────────
MT5_RISK_PCT=1.0              # % del balance a arriesgar por operación (1%)
MT5_MAX_LOTES=0.10            # Máximo de lotes por operación (seguridad)
MT5_TIMEFRAMES_ACTIVOS=5m,15m # Solo ejecutar en estos TFs (separados por coma)
MT5_MIN_SCORE=4               # Score mínimo para ejecutar (filtro adicional)
```

---

## 4. Nuevo adaptador: `adapters/mt5_broker.py`

Crear el archivo siguiente:

```python
"""
adapters/mt5_broker.py — Ejecución automática en MetaTrader 5 (VT Markets Demo).

Solo activo si MT5_AUTO_TRADE=true y la librería MetaTrader5 está disponible.
Diseñado para Windows con MT5 instalado localmente.
"""

import os
import math
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Configuración ──────────────────────────────────────────────────────────────
MT5_AUTO_TRADE      = os.getenv('MT5_AUTO_TRADE', 'false').lower() == 'true'
MT5_LOGIN           = int(os.getenv('MT5_LOGIN', '0'))
MT5_PASSWORD        = os.getenv('MT5_PASSWORD', '')
MT5_SERVER          = os.getenv('MT5_SERVER', 'VTMarkets-Demo')
MT5_SYMBOL          = os.getenv('MT5_SYMBOL', 'XAUUSD')
MT5_RISK_PCT        = float(os.getenv('MT5_RISK_PCT', '1.0'))
MT5_MAX_LOTES       = float(os.getenv('MT5_MAX_LOTES', '0.10'))
MT5_MIN_SCORE       = int(os.getenv('MT5_MIN_SCORE', '4'))
_TFS_ACTIVOS        = {tf.strip() for tf in os.getenv('MT5_TIMEFRAMES_ACTIVOS', '5m,15m').split(',')}

# ── Importación opcional ───────────────────────────────────────────────────────
_mt5 = None
if MT5_AUTO_TRADE:
    try:
        import MetaTrader5 as _mt5
        logger.info("✅ MetaTrader5 importado correctamente")
    except ImportError:
        logger.warning("⚠️ MetaTrader5 no disponible — ejecución automática desactivada")
        MT5_AUTO_TRADE = False


# ── Conexión ───────────────────────────────────────────────────────────────────

def conectar() -> bool:
    """Inicializa MT5 y abre sesión con las credenciales demo."""
    if not _mt5:
        return False
    if not _mt5.initialize():
        logger.error(f"MT5 initialize() falló: {_mt5.last_error()}")
        return False
    ok = _mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    if not ok:
        logger.error(f"MT5 login falló: {_mt5.last_error()}")
        _mt5.shutdown()
        return False
    info = _mt5.account_info()
    logger.info(f"✅ MT5 conectado | Cuenta: {info.login} | Balance: {info.balance} {info.currency}")
    return True


def desconectar():
    """Cierra la conexión con MT5."""
    if _mt5:
        _mt5.shutdown()


# ── Cálculo de lotes ───────────────────────────────────────────────────────────

def calcular_lotes(entry: float, sl: float) -> float:
    """
    Calcula el tamaño de lote según riesgo fijo (MT5_RISK_PCT % del balance).

    Para XAU/USD en MT5:
      - 1 lote estándar = 100 oz → valor pip ≈ 10 USD (con precio ~2000)
      - El cálculo exacto usa symbol_info().trade_tick_value

    Retorna lotes redondeados al step mínimo del broker, con tope MT5_MAX_LOTES.
    """
    if not _mt5:
        return 0.0

    info_cuenta = _mt5.account_info()
    if not info_cuenta:
        return 0.0

    balance      = info_cuenta.balance
    riesgo_usd   = balance * (MT5_RISK_PCT / 100.0)
    distancia_sl = abs(entry - sl)

    if distancia_sl == 0:
        logger.warning("calcular_lotes: distancia SL = 0, abortando")
        return 0.0

    sym_info = _mt5.symbol_info(MT5_SYMBOL)
    if not sym_info:
        logger.error(f"No se encontró símbolo {MT5_SYMBOL} en MT5")
        return 0.0

    # Valor del tick: USD por 1 lote por 1 punto de movimiento
    tick_value = sym_info.trade_tick_value
    tick_size  = sym_info.trade_tick_size
    puntos_sl  = distancia_sl / tick_size

    lotes_raw  = riesgo_usd / (puntos_sl * tick_value)

    # Redondear al step del broker
    step  = sym_info.volume_step
    lotes = math.floor(lotes_raw / step) * step
    lotes = round(min(lotes, MT5_MAX_LOTES), 2)

    logger.info(
        f"Sizing | Balance {balance:.0f} | Riesgo {riesgo_usd:.2f} USD | "
        f"SL dist {distancia_sl:.2f} | Lotes calculados: {lotes}"
    )
    return lotes


# ── Apertura de operación ──────────────────────────────────────────────────────

def abrir_operacion(senal_data: dict) -> int | None:
    """
    Abre una operación en MT5 a partir de los datos de señal del bot.

    senal_data esperado:
      direccion  : 'BUY' | 'SELL'
      entry      : float (precio de entrada)
      sl         : float (stop loss)
      tp1        : float (primer objetivo)
      score      : int
      timeframe  : str  (ej. '5m')
      simbolo    : str  (ej. 'GC=F')

    Retorna ticket de la orden o None si falla.
    """
    if not MT5_AUTO_TRADE or not _mt5:
        return None

    tf       = senal_data.get('timeframe', '')
    score    = senal_data.get('score', 0) or 0
    direccion = senal_data.get('direccion', '').upper()

    # ── Filtros de seguridad ──
    if tf not in _TFS_ACTIVOS:
        logger.debug(f"TF {tf} no está en MT5_TIMEFRAMES_ACTIVOS → omitido")
        return None
    if score < MT5_MIN_SCORE:
        logger.info(f"Score {score} < {MT5_MIN_SCORE} → operación no ejecutada")
        return None
    if direccion not in ('BUY', 'SELL'):
        logger.warning(f"Dirección inválida: {direccion!r}")
        return None

    entry = float(senal_data['entry'])
    sl    = float(senal_data['sl'])
    tp1   = float(senal_data['tp1'])
    lotes = calcular_lotes(entry, sl)

    if lotes <= 0:
        logger.error("Lotes calculados = 0, operación cancelada")
        return None

    order_type = _mt5.ORDER_TYPE_BUY if direccion == 'BUY' else _mt5.ORDER_TYPE_SELL

    request = {
        "action":       _mt5.TRADE_ACTION_DEAL,
        "symbol":       MT5_SYMBOL,
        "volume":       lotes,
        "type":         order_type,
        "price":        entry,
        "sl":           sl,
        "tp":           tp1,
        "deviation":    20,           # slippage máximo en puntos
        "magic":        20260504,     # identificador del bot
        "comment":      f"BotGold {tf} sc{score}",
        "type_time":    _mt5.ORDER_TIME_GTC,
        "type_filling": _mt5.ORDER_FILLING_IOC,
    }

    resultado = _mt5.order_send(request)

    if resultado is None or resultado.retcode != _mt5.TRADE_RETCODE_DONE:
        codigo = resultado.retcode if resultado else "None"
        logger.error(f"❌ MT5 order_send falló | retcode={codigo} | {resultado}")
        return None

    ticket = resultado.order
    logger.info(
        f"✅ Operación abierta | Ticket: {ticket} | {direccion} {lotes} lotes "
        f"@ {entry} | SL {sl} | TP1 {tp1} | TF {tf} | Score {score}"
    )
    return ticket


# ── Cierre manual ──────────────────────────────────────────────────────────────

def cerrar_operacion(ticket: int) -> bool:
    """Cierra una posición abierta por su ticket."""
    if not _mt5:
        return False
    posicion = _mt5.positions_get(ticket=ticket)
    if not posicion:
        logger.warning(f"No se encontró posición con ticket {ticket}")
        return False

    pos      = posicion[0]
    tipo_cierre = _mt5.ORDER_TYPE_SELL if pos.type == 0 else _mt5.ORDER_TYPE_BUY
    precio  = _mt5.symbol_info_tick(MT5_SYMBOL).bid if pos.type == 0 else _mt5.symbol_info_tick(MT5_SYMBOL).ask

    request = {
        "action":       _mt5.TRADE_ACTION_DEAL,
        "symbol":       MT5_SYMBOL,
        "volume":       pos.volume,
        "type":         tipo_cierre,
        "position":     ticket,
        "price":        precio,
        "deviation":    20,
        "magic":        20260504,
        "comment":      "BotGold cierre",
        "type_time":    _mt5.ORDER_TIME_GTC,
        "type_filling": _mt5.ORDER_FILLING_IOC,
    }
    resultado = _mt5.order_send(request)
    ok = resultado and resultado.retcode == _mt5.TRADE_RETCODE_DONE
    if ok:
        logger.info(f"✅ Posición {ticket} cerrada")
    else:
        logger.error(f"❌ No se pudo cerrar {ticket}: {resultado}")
    return ok


# ── Estado ─────────────────────────────────────────────────────────────────────

def estado_cuenta() -> dict:
    """Retorna balance, equity y posiciones abiertas del bot."""
    if not _mt5 or not conectar():
        return {}
    info      = _mt5.account_info()
    posiciones = _mt5.positions_get(magic=20260504) or []
    return {
        "balance":    info.balance,
        "equity":     info.equity,
        "profit":     info.profit,
        "currency":   info.currency,
        "posiciones": len(posiciones),
        "tickets":    [p.ticket for p in posiciones],
    }
```

---

## 5. Integración en el pipeline de señales

El punto de inyección es `base_detector.py`, método `enviar()`. Después de confirmar que se ha guardado la señal y enviado a Telegram, se ejecuta la orden en MT5:

```python
# En core/base_detector.py — dentro de enviar() tras el envío a Telegram:

from adapters import mt5_broker   # añadir import al inicio del archivo

# ... (código existente de enviar_telegram) ...

# Hook MT5 — solo si AUTO_TRADE activo
if mt5_broker.MT5_AUTO_TRADE and senal_data:
    mt5_broker.abrir_operacion(senal_data)
```

> **Alternativa sin modificar base_detector:** crear un `services/mt5_executor.py` que lea las señales nuevas de la BD cada 30 segundos y ejecute las que tengan `mt5_ticket IS NULL`. Esto desacopla totalmente el ejecutor del detector.

---

## 6. Gestión del riesgo — Parámetros recomendados para demo

| Parámetro             | Demo (inicio) | Demo (tras 2 semanas) |
|-----------------------|---------------|-----------------------|
| `MT5_RISK_PCT`        | 0.5 %         | 1.0 %                 |
| `MT5_MAX_LOTES`       | 0.05          | 0.10                  |
| `MT5_MIN_SCORE`       | 5             | 4                     |
| `MT5_TIMEFRAMES_ACTIVOS` | `15m`      | `5m,15m`              |

**Regla de parada automática sugerida:**  
Si el drawdown del día supera el 3% del balance, el bot deja de operar hasta el día siguiente. Implementar en `mt5_broker.abrir_operacion()` consultando `account_info().equity`.

---

## 7. Workflow de pruebas paso a paso

### Fase 0 — Preparación (local Windows)

- [ ] Crear cuenta demo en vtmarkets.com
- [ ] Instalar MT5 y hacer login manual una vez para verificar credenciales
- [ ] Verificar que `XAUUSD` aparece en Market Watch y tiene precios en tiempo real
- [ ] `pip install MetaTrader5` en el venv del proyecto
- [ ] Copiar variables de entorno MT5 al `.env` local con `MT5_AUTO_TRADE=false`

### Fase 1 — Validación del adaptador (sin órdenes reales)

```bash
# Script de diagnóstico rápido
python -c "
import MetaTrader5 as mt5
mt5.initialize()
ok = mt5.login(TU_LOGIN, password='TU_PASS', server='VTMarkets-Demo')
print('Login:', ok)
info = mt5.account_info()
print('Balance:', info.balance, info.currency)
sym = mt5.symbol_info('XAUUSD')
print('Tick value:', sym.trade_tick_value, '| Step:', sym.volume_step)
mt5.shutdown()
"
```

- [ ] Login OK, balance visible
- [ ] `XAUUSD` encontrado con `trade_tick_value` y `volume_step` correctos
- [ ] `calcular_lotes()` devuelve valores razonables (0.01 – 0.10)

### Fase 2 — Primera orden manual desde Python

```python
from adapters.mt5_broker import conectar, abrir_operacion, estado_cuenta

conectar()

# Señal sintética de prueba
senal_test = {
    'direccion': 'BUY',
    'entry':     2330.50,
    'sl':        2320.00,
    'tp1':       2346.00,
    'score':     5,
    'timeframe': '15m',
    'simbolo':   'GC=F',
}

ticket = abrir_operacion(senal_test)
print("Ticket:", ticket)
print("Estado:", estado_cuenta())
```

- [ ] Orden aparece en MT5 → pestaña "Trade"
- [ ] SL y TP1 asignados correctamente
- [ ] Comentario visible: `BotGold 15m sc5`
- [ ] Magic number: `20260504`

### Fase 3 — Activar con señales reales del bot

- [ ] Poner `MT5_AUTO_TRADE=true` en `.env`
- [ ] Iniciar el bot: `.\venv\Scripts\python.exe app.py`
- [ ] Esperar primera señal del detector 15M o 5M
- [ ] Verificar en Telegram y en MT5 que la orden coincide
- [ ] Registrar slippage: diferencia entre `entry` en BD y precio de ejecución real

### Fase 4 — Seguimiento y evaluación (2 semanas)

Métricas a registrar diariamente:

| Métrica               | Objetivo demo |
|-----------------------|---------------|
| Señales enviadas      | —             |
| Órdenes ejecutadas    | ≥ 80 %        |
| Slippage promedio     | < 5 pips      |
| Win rate              | > 45 %        |
| R:R promedio real     | > 1.5         |
| Drawdown máximo diario| < 3 %         |
| Errores de conexión   | < 2/día       |

---

## 8. Posibles problemas y soluciones

| Problema | Causa probable | Solución |
|----------|----------------|----------|
| `MT5 initialize() falló` | MT5 no está abierto | Abrir MT5 manualmente antes de iniciar el bot |
| `Login falló (error 10013)` | Credenciales incorrectas o servidor equivocado | Verificar nombre exacto del servidor en MT5 |
| `XAUUSD no encontrado` | Símbolo no en Market Watch | Añadirlo en Ver → Símbolos |
| `retcode=10006` (No connection) | Desconexión de red | `mt5_broker.py` reintenta la conexión en el siguiente ciclo |
| `retcode=10014` (Invalid volume) | Lotes fuera del rango broker | Revisar `volume_min` y `volume_step` con `symbol_info()` |
| `retcode=10016` (Invalid stops) | SL/TP muy cerca del precio | VT Markets requiere distancia mínima de stops — ajustar `atr_sl_mult` |
| Bot en Render (Linux) | `MetaTrader5` no disponible | Ejecutar solo en Windows local en fase demo; migrar a VPS Windows para producción |

---

## 9. Consideraciones para producción (post-demo)

1. **VPS Windows** — Si el bot debe correr 24/7 automáticamente, necesita un servidor con Windows y MT5 siempre abierto. Alternativas: AWS EC2 Windows, Azure VM, o un VPS Windows económico.

2. **Servidor puente HTTP** — Para mantener el bot en Render (Linux), crear un microservicio en el VPS Windows que exponga un endpoint `/ejecutar` que reciba la señal y abra la orden en MT5 local. El bot en Render hace un POST HTTP a ese endpoint.

3. **Reconexión automática** — MT5 cierra la sesión tras varias horas de inactividad. Implementar `conectar()` antes de cada `abrir_operacion()` con verificación de `mt5.account_info()`.

4. **Tabla `mt5_operaciones` en BD** — Registrar cada orden con `ticket`, `senal_id`, `lotes`, `precio_apertura`, `precio_cierre`, `resultado_pips`, `resultado_usd` para estadísticas reales.

---

## 10. Archivos a crear/modificar

| Archivo | Acción | Descripción |
|---------|--------|-------------|
| `adapters/mt5_broker.py` | **Crear** | Adaptador MT5 completo (sección 4) |
| `core/base_detector.py` | **Modificar** (mínimo) | Añadir hook MT5 en `enviar()` |
| `requirements.txt` | **Modificar** | Añadir `MetaTrader5>=5.0.45` (opcional/Windows) |
| `.env` | **Modificar** | Añadir variables `MT5_*` (sección 3) |
| `docs/VT_MARKETS_DEMO.md` | Este archivo | — |

---

*Documento creado: 4 de mayo de 2026*
