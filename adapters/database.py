"""
Database Manager - Gestión de señales de trading en Turso (SQLite en la nube)
Maneja todas las operaciones CRUD para señales, historial de precios y estadísticas
"""

import os
import json
import threading
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()


class _Result:
    """Resultado compatible de queries Turso."""
    __slots__ = ('rows', 'columns')

    def __init__(self, rows_data, columns):
        self.rows = rows_data
        self.columns = columns


class DatabaseManager:
    """Gestiona la conexión y operaciones con la base de datos Turso"""
    _instance = None
    _singleton_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Inicializa la conexión a Turso usando URL y TOKEN del .env"""
        if self._initialized:
            return
        self._initialized = True
        self.db_url = os.environ.get('TURSO_DATABASE_URL')
        self.db_token = os.environ.get('TURSO_AUTH_TOKEN')
        
        if not self.db_url or not self.db_token:
            raise ValueError(
                "❌ TURSO_DATABASE_URL y TURSO_AUTH_TOKEN deben estar definidos en .env"
            )
        
        # Convertir libsql:// a https:// para API HTTP
        if self.db_url.startswith('libsql://'):
            self.api_url = self.db_url.replace('libsql://', 'https://')
        else:
            self.api_url = self.db_url
        
        # Asegurar que termine sin barra
        self.api_url = self.api_url.rstrip('/')
        
        # Headers para autenticación
        self.headers = {
            'Authorization': f'Bearer {self.db_token}',
            'Content-Type': 'application/json'
        }
        
        print("✅ Conexión a Turso establecida correctamente")
        # Lock para atomicidad INSERT + last_insert_rowid
        self._insert_lock = threading.Lock()
    
    def _convert_param(self, param):
        """Convierte un parámetro al formato que espera Turso"""
        if param is None:
            return {"type": "null"}
        elif isinstance(param, bool):
            return {"type": "integer", "value": str(int(param))}
        elif isinstance(param, int):
            return {"type": "integer", "value": str(param)}
        elif isinstance(param, float):
            return {"type": "float", "value": float(param)}
        else:
            return {"type": "text", "value": str(param)}
    
    # Códigos HTTP transitorios que se reintentan
    _RETRY_CODES = {502, 503, 429}
    _MAX_RETRIES = 2

    def ejecutar_query(self, query: str, params: tuple = ()) -> Any:
        """Ejecuta una query usando la API HTTP de Turso (con retry en errores transitorios)"""
        try:
            # Convertir parámetros al formato Turso
            converted_params = [self._convert_param(p) for p in params] if params else []
            
            # Preparar el payload según especificación Turso
            payload = {
                'requests': [
                    {
                        'type': 'execute',
                        'stmt': {
                            'sql': query,
                            'args': converted_params
                        }
                    }
                ]
            }
            
            # Hacer request a la API (con retry en errores transitorios)
            last_exc = None
            for intento in range(self._MAX_RETRIES + 1):
                if intento > 0:
                    time.sleep(intento * 1.5)
                try:
                    response = requests.post(
                        f'{self.api_url}/v2/pipeline',
                        headers=self.headers,
                        json=payload,
                        timeout=30
                    )
                except requests.exceptions.RequestException as req_e:
                    last_exc = req_e
                    continue

                if response.status_code in self._RETRY_CODES:
                    last_exc = Exception(f"HTTP {response.status_code}: {response.text[:120]}")
                    continue

                if response.status_code != 200:
                    raise Exception(f"HTTP {response.status_code}: {response.text}")

                last_exc = None
                break

            if last_exc is not None:
                raise last_exc
            
            # Parsear respuesta
            data = response.json()
            
            # Extraer resultados
            if data and 'results' in data and len(data['results']) > 0:
                result_data = data['results'][0].get('response', {}).get('result', {})
                columns = [col['name'] for col in result_data.get('cols', [])]
                rows_array = result_data.get('rows', [])
                
                # Convertir rows a diccionarios
                rows_dicts = []
                for row in rows_array:
                    row_dict = {}
                    for i, col in enumerate(columns):
                        # Manejar valores que vienen en formato {"type": "integer", "value": "123"}
                        # CRÍTICO: Turso devuelve booleanos e integers como strings — convertir
                        # al tipo nativo correcto, si no `not senal['tp1_alcanzado']` donde
                        # tp1_alcanzado = "0" siempre es False (string no vacío = truthy).
                        cell = row[i] if i < len(row) else None
                        if isinstance(cell, dict) and 'value' in cell:
                            cell_type = cell.get('type', 'text')
                            cell_val  = cell['value']
                            if cell_type == 'integer':
                                row_dict[col] = int(cell_val) if cell_val is not None else 0
                            elif cell_type in ('float', 'real'):
                                row_dict[col] = float(cell_val) if cell_val is not None else 0.0
                            else:
                                row_dict[col] = cell_val
                        else:
                            row_dict[col] = cell
                    rows_dicts.append(row_dict)
                
                return _Result(rows_dicts, columns)
            
            return _Result([], [])
            
        except Exception as e:
            print(f"❌ Error ejecutando query: {e}")
            print(f"Query: {query}")
            print(f"Params: {params}")
            raise
    
    def ejecutar_insert(self, query: str, params: tuple = ()) -> int:
        """Ejecuta un INSERT y retorna el ID insertado.

        INSERT + last_insert_rowid() se envían en un único pipeline (una sola
        petición HTTP) por lo que son atómicos a nivel de base de datos.
        No se necesita lock Python-side ya que cada sesión Turso gestiona su
        propia secuencia de rowid de forma aislada.
        """
        try:
            # INSERT + last_insert_rowid() en el mismo pipeline para atomicidad
            converted_params = [self._convert_param(p) for p in params] if params else []
            payload = {
                'requests': [
                    {
                        'type': 'execute',
                        'stmt': {'sql': query, 'args': converted_params}
                    },
                    {
                        'type': 'execute',
                        'stmt': {'sql': 'SELECT last_insert_rowid() as id', 'args': []}
                    }
                ]
            }
            last_exc = None
            for intento in range(self._MAX_RETRIES + 1):
                if intento > 0:
                    time.sleep(intento * 1.5)
                try:
                    response = requests.post(
                        f'{self.api_url}/v2/pipeline',
                        headers=self.headers,
                        json=payload,
                        timeout=30
                    )
                except requests.exceptions.RequestException as req_e:
                    last_exc = req_e
                    continue

                if response.status_code in self._RETRY_CODES:
                    last_exc = Exception(f"HTTP {response.status_code}: {response.text[:120]}")
                    continue

                if response.status_code != 200:
                    raise Exception(f"HTTP {response.status_code}: {response.text}")

                last_exc = None
                break

            if last_exc is not None:
                raise last_exc
            data = response.json()
            if data and 'results' in data and len(data['results']) >= 2:
                result_data = data['results'][1].get('response', {}).get('result', {})
                rows = result_data.get('rows', [])
                if rows:
                    cell = rows[0][0]
                    if isinstance(cell, dict):
                        return int(cell.get('value', 0))
                    return int(cell) if cell is not None else None
            return None
        except Exception as e:
            print(f"❌ Error en INSERT: {e}")
            raise
    
    # ═══════════════════════════════════════════════════════════
    # OPERACIONES DE SEÑALES
    # ═══════════════════════════════════════════════════════════
    
    def guardar_senal(self, senal_data: Dict) -> int:
        """
        Guarda una nueva señal en la base de datos
        
        Args:
            senal_data: Dictionary con los datos de la señal
            
        Returns:
            ID de la señal insertada
        """
        estado = senal_data.get('estado', 'ACTIVA')
        query = f"""
        INSERT INTO senales (
            timestamp, simbolo, direccion, precio_entrada,
            tp1, tp2, tp3, sl, score,
            indicadores, patron_velas, version_detector,
            estado
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{estado}')
        """
        
        params = (
            senal_data.get('timestamp', datetime.now(timezone.utc)).isoformat(),
            senal_data['simbolo'],
            senal_data['direccion'],
            senal_data['precio_entrada'],
            senal_data['tp1'],
            senal_data['tp2'],
            senal_data['tp3'],
            senal_data['sl'],
            senal_data['score'],
            json.dumps(senal_data.get('indicadores', {})),
            senal_data.get('patron_velas', ''),
            senal_data.get('version_detector', '2.0')
        )
        
        senal_id = self.ejecutar_insert(query, params)
        print(f"✅ Señal guardada con ID: {senal_id}")
        # Log automático al guardar señal
        try:
            simbolo = senal_data['simbolo']
            direccion = senal_data['direccion']
            score = senal_data['score']
            entry = senal_data['precio_entrada']
            tp1 = senal_data['tp1']
            sl = senal_data['sl']
            self.guardar_log(
                f"Señal #{senal_id} {direccion} | entry={entry:.2f} TP1={tp1:.2f} SL={sl:.2f} score={score} estado={estado}",
                nivel='INFO', modulo=senal_data.get('version_detector', ''), simbolo=simbolo
            )
        except Exception:
            pass
        return senal_id
    
    def existe_senal_reciente(self, simbolo: str, direccion: str, horas: int = 2) -> bool:
        """
        Verifica si ya existe una señal ACTIVA/PENDIENTE para el símbolo+dirección
        creada dentro de las últimas N horas.  Señales más antiguas se ignoran
        (probablemente ya se cerraron en producción o son de un ciclo anterior).

        Args:
            simbolo: BTCUSD_4H, XAUUSD_15M, etc.
            direccion: COMPRA o VENTA
            horas: Ventana de deduplicación (por defecto 2h)

        Returns:
            True si ya existe una señal activa RECIENTE para ese símbolo+dirección
        """
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat()

        query = """
        SELECT COUNT(*) as count
        FROM senales
        WHERE simbolo = ?
        AND direccion = ?
        AND estado IN ('ACTIVA', 'PENDIENTE_CONFIRM')
        AND timestamp > ?
        """

        result = self.ejecutar_query(query, (simbolo, direccion, cutoff))

        if result.rows and int(result.rows[0]['count']) > 0:
            print(f"⚠️ Ya existe señal ACTIVA/PENDIENTE ({horas}h): {simbolo} {direccion} — no se duplica")
            return True
        return False
    
    def existe_senal_activa_tf(self, simbolo: str) -> bool:
        """
        Bloquea nueva señal si ya existe UNA ACTIVA para este símbolo,
        independientemente de la dirección (COMPRA o VENTA).
        Usa el mismo formato de simbolo que guardar_senal: XAUUSD_1D, XAUUSD_4H, etc.
        """
        query = """
        SELECT COUNT(*) as count
        FROM senales
        WHERE simbolo = ?
        AND estado IN ('ACTIVA', 'PENDIENTE_CONFIRM')
        """
        result = self.ejecutar_query(query, (simbolo,))
        if result.rows and int(result.rows[0]['count']) > 0:
            print(f"⚠️ Ya existe señal ACTIVA/PENDIENTE en {simbolo} (cualquier dirección) — bloqueado")
            return True
        return False

    def contar_perdidas_consecutivas(self, simbolo: str) -> int:
        """
        Cuenta cuántas señales consecutivas más recientes terminaron en SL
        para el símbolo dado. Se detiene al encontrar la primera señal con TP.
        
        Returns:
            Número de pérdidas consecutivas (0 si no hay historial o la última fue TP)
        """
        query = """
        SELECT estado FROM senales
        WHERE simbolo = ?
        AND estado IN ('SL', 'TP1', 'TP2', 'TP3')
        ORDER BY timestamp DESC
        LIMIT 10
        """
        result = self.ejecutar_query(query, (simbolo,))
        if not result.rows:
            return 0
        count = 0
        for row in result.rows:
            if row['estado'] == 'SL':
                count += 1
            else:
                break
        return count

    def obtener_senales_activas(self) -> List[Dict]:
        """
        Obtiene todas las señales que aún están activas (no cerradas)
        
        Returns:
            Lista de diccionarios con datos de señales activas
        """
        query = """
        SELECT *
        FROM senales
        WHERE estado = 'ACTIVA'
        ORDER BY timestamp DESC
        """
        
        result = self.ejecutar_query(query)
        return [dict(row) for row in result.rows] if result.rows else []

    def obtener_senales_pendientes_confirm(self) -> List[Dict]:
        """Obtiene señales 1H en espera de confirmación por 5M/15M."""
        query = """
        SELECT *
        FROM senales
        WHERE estado = 'PENDIENTE_CONFIRM'
        ORDER BY timestamp ASC
        """
        result = self.ejecutar_query(query)
        return [dict(row) for row in result.rows] if result.rows else []

    def confirmar_senal_pendiente(self, senal_id: int) -> None:
        """Activa una señal que estaba esperando confirmación de TF inferior."""
        query = "UPDATE senales SET estado = 'ACTIVA' WHERE id = ?"
        self.ejecutar_query(query, (senal_id,))
        print(f"✅ Señal {senal_id} confirmada — estado → ACTIVA")

    def caducar_senal_pendiente(self, senal_id: int) -> None:
        """Caduca una señal PENDIENTE_CONFIRM que no recibió confirmación a tiempo."""
        now = datetime.now(timezone.utc).isoformat()
        query = "UPDATE senales SET estado = 'CADUCADA', fecha_cierre = ? WHERE id = ?"
        self.ejecutar_query(query, (now, senal_id))
        print(f"⏰ Señal {senal_id} caducada — sin confirmación 5M/15M en tiempo")
    
    def actualizar_precio_actual(self, senal_id: int, precio: float):
        """Actualiza el precio actual de una señal"""
        query = "UPDATE senales SET precio_actual = ? WHERE id = ?"
        self.ejecutar_query(query, (precio, senal_id))
    
    def actualizar_estado_senal(self, senal_id: int, nuevo_estado: str, 
                                beneficio_pct: float = None):
        """
        Actualiza el estado de una señal cuando alcanza TP o SL
        
        Args:
            senal_id: ID de la señal
            nuevo_estado: TP1, TP2, TP3, SL, CANCELADA
            beneficio_pct: Porcentaje de beneficio/pérdida
        """
        now = datetime.now(timezone.utc).isoformat()
        
        # Actualiza flags correspondientes sin cerrar la señal — solo TP3/SL/BREAKEVEN la cierran
        if nuevo_estado == 'TP1':
            # Al alcanzar TP1 se mueve el SL automáticamente a breakeven (precio_entrada)
            query = """
            UPDATE senales 
            SET tp1_alcanzado = TRUE, fecha_tp1 = ?,
                sl = precio_entrada
            WHERE id = ?
            """
            self.ejecutar_query(query, (now, senal_id))

        elif nuevo_estado == 'BREAKEVEN':
            # Precio volvió al nivel de entrada tras haber tocado TP1 — cierre en 0
            query = """
            UPDATE senales
            SET sl_alcanzado = TRUE, fecha_sl = ?,
                estado = 'BREAKEVEN', fecha_cierre = ?, beneficio_final_pct = 0.0
            WHERE id = ?
            """
            self.ejecutar_query(query, (now, now, senal_id))

        elif nuevo_estado == 'TP2':
            query = """
            UPDATE senales 
            SET tp2_alcanzado = TRUE, fecha_tp2 = ?
            WHERE id = ?
            """
            self.ejecutar_query(query, (now, senal_id))
            
        elif nuevo_estado == 'TP3':
            query = """
            UPDATE senales 
            SET tp3_alcanzado = TRUE, fecha_tp3 = ?, 
                estado = ?, fecha_cierre = ?, beneficio_final_pct = ?
            WHERE id = ?
            """
            self.ejecutar_query(query, (now, nuevo_estado, now, beneficio_pct, senal_id))
            
        elif nuevo_estado == 'SL':
            query = """
            UPDATE senales 
            SET sl_alcanzado = TRUE, fecha_sl = ?, 
                estado = ?, fecha_cierre = ?, beneficio_final_pct = ?
            WHERE id = ?
            """
            self.ejecutar_query(query, (now, nuevo_estado, now, beneficio_pct, senal_id))
        
        print(f"🔄 Señal {senal_id} actualizada a estado: {nuevo_estado}")
    
    def cerrar_senal(self, senal_id: int, estado_final: str, beneficio_pct: float = None):
        """Cierra una señal manualmente con un estado final"""
        now = datetime.now(timezone.utc).isoformat()
        
        query = """
        UPDATE senales
        SET estado = ?, fecha_cierre = ?, beneficio_final_pct = ?
        WHERE id = ?
        """
        
        self.ejecutar_query(query, (estado_final, now, beneficio_pct, senal_id))
        print(f"🔒 Señal {senal_id} cerrada con estado: {estado_final}")
    
    # ═══════════════════════════════════════════════════════════
    # HISTORIAL DE PRECIOS
    # ═══════════════════════════════════════════════════════════

    def init_historial_precios_table(self):
        """Crea la tabla historial_precios si no existe."""
        self.ejecutar_query("""
        CREATE TABLE IF NOT EXISTS historial_precios (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            senal_id       INTEGER NOT NULL,
            timestamp      TEXT    NOT NULL,
            precio         REAL    NOT NULL,
            distancia_tp1  REAL,
            distancia_tp2  REAL,
            distancia_tp3  REAL,
            distancia_sl   REAL
        )
        """)

    def registrar_precio(self, senal_id: int, precio_actual: float, senal_data: dict = None):
        """
        Registra un snapshot de precio para análisis histórico.
        Si se pasa senal_data (con tp1,tp2,tp3,sl,precio_entrada,direccion) se
        evita la SELECT individual, reduciendo 1 query HTTP por llamada.
        """
        if senal_data is None:
            query = "SELECT tp1, tp2, tp3, sl, precio_entrada, direccion FROM senales WHERE id = ?"
            result = self.ejecutar_query(query, (senal_id,))
            if not result.rows:
                return
            senal_data = dict(result.rows[0])
        
        # Convertir valores numéricos a float (CRÍTICO: Turso retorna strings)
        precio_entrada = float(senal_data['precio_entrada'])
        tp1 = float(senal_data['tp1'])
        tp2 = float(senal_data['tp2'])
        tp3 = float(senal_data['tp3'])
        sl = float(senal_data['sl'])
        
        # Calcular distancias relativas
        if senal_data['direccion'] == 'COMPRA':
            dist_tp1 = ((precio_actual - tp1) / precio_entrada) * 100
            dist_tp2 = ((precio_actual - tp2) / precio_entrada) * 100
            dist_tp3 = ((precio_actual - tp3) / precio_entrada) * 100
            dist_sl = ((precio_actual - sl) / precio_entrada) * 100
        else:  # VENTA
            dist_tp1 = ((tp1 - precio_actual) / precio_entrada) * 100
            dist_tp2 = ((tp2 - precio_actual) / precio_entrada) * 100
            dist_tp3 = ((tp3 - precio_actual) / precio_entrada) * 100
            dist_sl = ((sl - precio_actual) / precio_entrada) * 100
        
        # CRÍTICO: actualizar precio_actual en la señal PRIMERO
        # Si el historial falla, al menos el campo principal queda actualizado
        self.actualizar_precio_actual(senal_id, precio_actual)

        # Insertar en historial (secundario — fallo no bloquea el UPDATE anterior)
        insert_query = """
        INSERT INTO historial_precios (
            senal_id, timestamp, precio,
            distancia_tp1, distancia_tp2, distancia_tp3, distancia_sl
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        try:
            self.ejecutar_query(insert_query, (
                senal_id,
                datetime.now(timezone.utc).isoformat(),
                precio_actual,
                dist_tp1,
                dist_tp2,
                dist_tp3,
                dist_sl
            ))
        except Exception as e:
            print(f"⚠️ registrar_precio: fallo INSERT historial (no crítico): {e}")
    
    # ═══════════════════════════════════════════════════════════
    # ESTADÍSTICAS Y ANÁLISIS
    # ═══════════════════════════════════════════════════════════
    
    def obtener_estadisticas_dia(self, fecha: datetime = None) -> Dict:
        """
        Calcula estadísticas de un día específico
        
        Args:
            fecha: Fecha a analizar (default: hoy)
            
        Returns:
            Dictionary con estadísticas del día
        """
        if fecha is None:
            fecha = datetime.now(timezone.utc)
        
        fecha_str = fecha.date().isoformat()
        
        query = """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN estado = 'TP1' THEN 1 ELSE 0 END) as tp1,
            SUM(CASE WHEN estado = 'TP2' THEN 1 ELSE 0 END) as tp2,
            SUM(CASE WHEN estado = 'TP3' THEN 1 ELSE 0 END) as tp3,
            SUM(CASE WHEN estado = 'SL' THEN 1 ELSE 0 END) as sl,
            SUM(CASE WHEN estado IN ('TP1','TP2','TP3') THEN 1 ELSE 0 END) as wins,
            AVG(CASE WHEN beneficio_final_pct IS NOT NULL 
                THEN beneficio_final_pct ELSE 0 END) as avg_profit,
            AVG(CASE WHEN duracion_minutos IS NOT NULL 
                THEN duracion_minutos ELSE 0 END) as avg_duration
        FROM senales
        WHERE DATE(timestamp) = ?
        """
        
        result = self.ejecutar_query(query, (fecha_str,))
        
        if not result.rows:
            return {}
        
        stats = dict(result.rows[0])
        
        # Calcular win rate
        if stats['total'] > 0:
            stats['win_rate'] = (stats['wins'] / stats['total']) * 100
        else:
            stats['win_rate'] = 0.0
        
        # Obtener mejor símbolo del día
        mejor_query = """
        SELECT simbolo, COUNT(*) as count
        FROM senales
        WHERE DATE(timestamp) = ?
        AND estado IN ('TP1', 'TP2', 'TP3')
        GROUP BY simbolo
        ORDER BY count DESC
        LIMIT 1
        """
        
        mejor_result = self.ejecutar_query(mejor_query, (fecha_str,))
        stats['mejor_simbolo'] = mejor_result.rows[0]['simbolo'] if mejor_result.rows else 'N/A'
        
        return stats
    
    def obtener_estadisticas_periodo(self, fecha_inicio: datetime, 
                                     fecha_fin: datetime) -> Dict:
        """Estadísticas de un período de tiempo"""
        query = """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN estado IN ('TP1','TP2','TP3') THEN 1 ELSE 0 END) as wins,
            AVG(beneficio_final_pct) as avg_profit
        FROM senales
        WHERE timestamp BETWEEN ? AND ?
        AND estado != 'ACTIVA'
        """
        
        result = self.ejecutar_query(query, (
            fecha_inicio.isoformat(),
            fecha_fin.isoformat()
        ))
        
        if not result.rows:
            return {}
        
        stats = dict(result.rows[0])
        stats['win_rate'] = (stats['wins'] / stats['total'] * 100) if stats['total'] > 0 else 0
        
        return stats
    
    def obtener_win_rate_por_simbolo(self) -> List[Dict]:
        """Win rate separado por cada símbolo"""
        query = """
        SELECT 
            simbolo,
            COUNT(*) as total,
            SUM(CASE WHEN estado IN ('TP1','TP2','TP3') THEN 1 ELSE 0 END) as wins,
            ROUND(100.0 * SUM(CASE WHEN estado IN ('TP1','TP2','TP3') 
                THEN 1 ELSE 0 END) / COUNT(*), 2) as win_rate
        FROM senales
        WHERE estado != 'ACTIVA'
        GROUP BY simbolo
        ORDER BY win_rate DESC
        """
        
        result = self.ejecutar_query(query)
        return [dict(row) for row in result.rows] if result.rows else []
    
    def obtener_mejores_indicadores(self) -> List[Dict]:
        """Indicadores con mejor performance"""
        query = """
        SELECT 
            indicadores,
            COUNT(*) as veces_usado,
            AVG(score) as score_promedio,
            SUM(CASE WHEN estado IN ('TP1','TP2','TP3') THEN 1 ELSE 0 END) as wins
        FROM senales
        WHERE estado != 'ACTIVA'
        GROUP BY indicadores
        ORDER BY wins DESC
        LIMIT 10
        """
        
        result = self.ejecutar_query(query)
        return [dict(row) for row in result.rows] if result.rows else []
    
    def cerrar_senal_mas_antigua(self):
        """Cierra la señal activa más antigua (para límite de señales)"""
        query = """
        SELECT id FROM senales
        WHERE estado = 'ACTIVA'
        ORDER BY timestamp ASC
        LIMIT 1
        """
        
        result = self.ejecutar_query(query)
        
        if result.rows:
            senal_id = result.rows[0]['id']
            self.cerrar_senal(senal_id, 'CANCELADA')
            print(f"⚠️ Señal {senal_id} cerrada automáticamente (límite alcanzado)")

    # ═══════════════════════════════════════════════════════════
    # USO DE API KEYS (Twelve Data quota tracking)
    # ═══════════════════════════════════════════════════════════

    def init_api_key_usage_table(self):
        """Crea la tabla api_key_usage si no existe."""
        self.ejecutar_query("""
        CREATE TABLE IF NOT EXISTS api_key_usage (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha       TEXT    NOT NULL,          -- ISO date YYYY-MM-DD (UTC)
            key_alias   TEXT    NOT NULL,          -- 'key1', 'key2', 'key3'
            peticiones  INTEGER NOT NULL DEFAULT 0,
            actualizado TEXT    NOT NULL,          -- ISO datetime última actualización
            UNIQUE(fecha, key_alias)
        )
        """)

    def incrementar_uso_key(self, key_alias: str) -> int:
        """
        Incrementa en 1 el contador de peticiones de la key para hoy.
        Usa INSERT OR REPLACE para ser idempotente si no existe la fila.
        Retorna el total acumulado del día para esa key.
        """
        hoy = datetime.now(timezone.utc).date().isoformat()
        ahora = datetime.now(timezone.utc).isoformat()

        # Upsert: si ya existe la fila, suma 1; si no, la crea con peticiones=1
        self.ejecutar_query("""
        INSERT INTO api_key_usage (fecha, key_alias, peticiones, actualizado)
        VALUES (?, ?, 1, ?)
        ON CONFLICT(fecha, key_alias)
        DO UPDATE SET
            peticiones  = peticiones + 1,
            actualizado = excluded.actualizado
        """, (hoy, key_alias, ahora))

        result = self.ejecutar_query("""
        SELECT peticiones FROM api_key_usage
        WHERE fecha = ? AND key_alias = ?
        """, (hoy, key_alias))

        return int(result.rows[0]['peticiones']) if result.rows else 1

    def obtener_uso_keys_hoy(self) -> Dict[str, int]:
        """
        Retorna el uso de todas las keys para hoy.
        Ejemplo: {'key1': 312, 'key2': 298, 'key3': 305}
        """
        hoy = datetime.now(timezone.utc).date().isoformat()
        result = self.ejecutar_query("""
        SELECT key_alias, peticiones FROM api_key_usage
        WHERE fecha = ?
        """, (hoy,))
        return {row['key_alias']: int(row['peticiones']) for row in result.rows}

    def obtener_uso_keys_periodo(self, dias: int = 7) -> List[Dict]:
        """Histórico de uso de keys de los últimos N días."""
        result = self.ejecutar_query("""
        SELECT fecha, key_alias, peticiones
        FROM api_key_usage
        WHERE fecha >= DATE('now', ?)
        ORDER BY fecha DESC, key_alias ASC
        """, (f'-{dias} days',))
        return [dict(row) for row in result.rows]

    # ═══════════════════════════════════════════════════════════
    # OHLCV — Cache persistente de velas (tabla ohlcv)
    # ═══════════════════════════════════════════════════════════

    def _ejecutar_pipeline(self, stmts: list):
        """Ejecuta múltiples statements SQL en un único HTTP call (sin retorno de filas).
        stmts: lista de dicts {'sql': str, 'args': tuple | list}
        """
        payload = {
            'requests': [
                {
                    'type': 'execute',
                    'stmt': {
                        'sql': s['sql'],
                        'args': [self._convert_param(v) for v in s.get('args', ())]
                    }
                }
                for s in stmts
            ]
        }
        response = requests.post(
            f'{self.api_url}/v2/pipeline',
            headers=self.headers,
            json=payload,
            timeout=60
        )
        if response.status_code != 200:
            raise Exception(f"Pipeline HTTP {response.status_code}: {response.text[:300]}")

    def init_ohlcv_table(self):
        """Crea la tabla ohlcv y su índice si no existen."""
        self.ejecutar_query("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            symbol   TEXT NOT NULL,
            interval TEXT NOT NULL,
            ts       TEXT NOT NULL,
            open     REAL NOT NULL,
            high     REAL NOT NULL,
            low      REAL NOT NULL,
            close    REAL NOT NULL,
            volume   REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (symbol, interval, ts)
        )
        """)
        self.ejecutar_query(
            "CREATE INDEX IF NOT EXISTS idx_ohlcv_lookup ON ohlcv(symbol, interval, ts)"
        )

    def guardar_velas(self, symbol: str, interval: str, rows: list):
        """
        Inserta o reemplaza velas OHLCV en la BD.
        rows: lista de (ts_str, open, high, low, close, volume)
        Usa lotes de 80 statements por HTTP call para no superar límites de payload.
        """
        if not rows:
            return
        sql = (
            "INSERT OR REPLACE INTO ohlcv "
            "(symbol, interval, ts, open, high, low, close, volume) "
            "VALUES (?,?,?,?,?,?,?,?)"
        )
        CHUNK = 80
        for i in range(0, len(rows), CHUNK):
            chunk = rows[i:i + CHUNK]
            stmts = [
                {'sql': sql, 'args': (symbol, interval, r[0], r[1], r[2], r[3], r[4], r[5])}
                for r in chunk
            ]
            self._ejecutar_pipeline(stmts)

    def obtener_velas(self, symbol: str, interval: str, period: str) -> list:
        """
        Lee velas de la BD para el símbolo e intervalo indicados.
        Retorna lista de dicts: {ts, open, high, low, close, volume}.
        """
        dias_map = {
            '1d': 1, '2d': 2, '5d': 5, '7d': 7, '14d': 14,
            '1mo': 30, '2mo': 60, '3mo': 90, '6mo': 180,
            '1y': 365, '2y': 730, '60d': 60,
        }
        dias = dias_map.get(period, 7)
        desde = (datetime.now(timezone.utc) - timedelta(days=dias + 1)).isoformat()

        result = self.ejecutar_query("""
        SELECT ts, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol = ? AND interval = ? AND ts >= ?
        ORDER BY ts ASC
        """, (symbol, interval, desde))

        return result.rows  # lista de dicts

    def obtener_ultima_ts_vela(self, symbol: str, interval: str):
        """Retorna el ts (str ISO8601) de la vela más reciente en BD, o None."""
        result = self.ejecutar_query("""
        SELECT ts FROM ohlcv
        WHERE symbol = ? AND interval = ?
        ORDER BY ts DESC LIMIT 1
        """, (symbol, interval))
        return result.rows[0]['ts'] if result.rows else None

    def purgar_velas_antiguas(self, symbol: str, interval: str, dias_max: int):
        """Elimina velas más antiguas que dias_max para controlar el tamaño de la tabla."""
        desde = (datetime.now(timezone.utc) - timedelta(days=dias_max)).isoformat()
        self.ejecutar_query("""
        DELETE FROM ohlcv WHERE symbol = ? AND interval = ? AND ts < ?
        """, (symbol, interval, desde))

    def obtener_precio_reciente_bd(self, symbol: str, interval: str = '5m',
                                   max_minutos: int = 10) -> tuple | None:
        """
        Lee las últimas velas de la tabla ohlcv (sin usar yfinance ni locks).
        Retorna (close_actual, high_max_5velas, low_min_5velas) o None si la
        vela más reciente tiene más de max_minutos de antigüedad.
        """
        desde = (datetime.now(timezone.utc) - timedelta(minutes=max_minutos)).isoformat()
        result = self.ejecutar_query("""
        SELECT ts, close, high, low
        FROM ohlcv
        WHERE symbol = ? AND interval = ? AND ts >= ?
        ORDER BY ts DESC LIMIT 5
        """, (symbol, interval, desde))

        if not result.rows:
            return None

        rows = result.rows
        precio_actual = float(rows[0]['close'])
        precio_max    = max(float(r['high'])  for r in rows)
        precio_min    = min(float(r['low'])   for r in rows)
        return (precio_actual, precio_max, precio_min)

    # ═══════════════════════════════════════════════════════════
    # ESTADO DE CANAL ROTO (persistencia entre deploys/restarts)
    # ═══════════════════════════════════════════════════════════
    # MACRO EVENTS LOG
    # ═══════════════════════════════════════════════════════════

    def init_macro_events_log_table(self):
        """Crea la tabla macro_events_log si no existe."""
        self.ejecutar_query("""
        CREATE TABLE IF NOT EXISTS macro_events_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT    NOT NULL,
            tf              TEXT    NOT NULL,
            simbolo         TEXT    NOT NULL DEFAULT '',
            evento          TEXT    NOT NULL,
            ventana_minutos INTEGER NOT NULL
        )
        """)

    def guardar_macro_event_log(self, tf: str, simbolo: str,
                                evento: str, ventana_minutos: int) -> None:
        """Inserta un registro en macro_events_log cuando un detector detecta un evento próximo."""
        ts = datetime.now(timezone.utc).isoformat()
        self.ejecutar_insert(
            """
            INSERT INTO macro_events_log (timestamp, tf, simbolo, evento, ventana_minutos)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ts, tf, simbolo, evento, ventana_minutos),
        )

    # ═══════════════════════════════════════════════════════════

    def init_bot_logs_table(self):
        """Crea la tabla bot_logs si no existe."""
        self.ejecutar_query("""
        CREATE TABLE IF NOT EXISTS bot_logs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT    NOT NULL,
            nivel     TEXT    NOT NULL DEFAULT 'INFO',
            modulo    TEXT    NOT NULL DEFAULT '',
            simbolo   TEXT    NOT NULL DEFAULT '',
            mensaje   TEXT    NOT NULL
        )
        """)

    def guardar_log(self, mensaje: str, nivel: str = 'INFO',
                    modulo: str = '', simbolo: str = '') -> None:
        """Inserta un registro en bot_logs."""
        ts = datetime.now(timezone.utc).isoformat()
        self.ejecutar_insert(
            "INSERT INTO bot_logs (timestamp, nivel, modulo, simbolo, mensaje) VALUES (?, ?, ?, ?, ?)",
            (ts, nivel.upper(), modulo, simbolo, mensaje),
        )

    # ═══════════════════════════════════════════════════════════

    def init_canal_roto_table(self):
        """Crea la tabla canal_roto_state si no existe."""
        self.ejecutar_query("""
        CREATE TABLE IF NOT EXISTS canal_roto_state (
            simbolo        TEXT    NOT NULL,
            tf             TEXT    NOT NULL,
            alcista_roto   INTEGER NOT NULL DEFAULT 0,
            bajista_roto   INTEGER NOT NULL DEFAULT 0,
            linea_soporte  REAL    NOT NULL DEFAULT 0,
            linea_resist   REAL    NOT NULL DEFAULT 0,
            ts             TEXT    NOT NULL,
            PRIMARY KEY (simbolo, tf)
        )
        """)

    def guardar_canal_roto(self, simbolo: str, tf: str,
                           alcista_roto: bool, bajista_roto: bool,
                           linea_soporte: float, linea_resist: float) -> None:
        """Upsert del estado de canal roto para (simbolo, tf)."""
        ts = datetime.now(timezone.utc).isoformat()
        self.ejecutar_query("""
        INSERT INTO canal_roto_state
            (simbolo, tf, alcista_roto, bajista_roto, linea_soporte, linea_resist, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(simbolo, tf) DO UPDATE SET
            alcista_roto  = excluded.alcista_roto,
            bajista_roto  = excluded.bajista_roto,
            linea_soporte = excluded.linea_soporte,
            linea_resist  = excluded.linea_resist,
            ts            = excluded.ts
        """, (simbolo, tf,
              int(alcista_roto), int(bajista_roto),
              linea_soporte, linea_resist, ts))

    def obtener_canal_roto(self, simbolo: str, tf: str,
                           ttl_horas: float = 4.0) -> dict:
        """
        Devuelve el estado persistido de canal roto o None si no existe / expiró.
        ttl_horas: ventana máxima de validez (default 4h para 1H, 2h para 4H).
        """
        desde = (datetime.now(timezone.utc) - timedelta(hours=ttl_horas)).isoformat()
        result = self.ejecutar_query("""
        SELECT alcista_roto, bajista_roto, linea_soporte, linea_resist, ts
        FROM canal_roto_state
        WHERE simbolo = ? AND tf = ? AND ts >= ?
        """, (simbolo, tf, desde))
        if not result.rows:
            return None
        row = result.rows[0]
        return {
            'alcista_roto':  bool(row['alcista_roto']),
            'bajista_roto':  bool(row['bajista_roto']),
            'linea_soporte': float(row['linea_soporte']),
            'linea_resist':  float(row['linea_resist']),
            'ts':            row['ts'],
        }


    # ═══════════════════════════════════════════════════════════
    # RENDIMIENTO — Tablas de toques de nivel (TP1, TP2, TP3, BreakEven)
    # ═══════════════════════════════════════════════════════════

    def init_nivel_touches_tables(self):
        """Crea las 4 tablas de registro de toques de nivel si no existen."""
        for tabla in ('tp1_hits', 'tp2_hits', 'tp3_hits', 'breakeven_hits'):
            self.ejecutar_query(f"""
            CREATE TABLE IF NOT EXISTS {tabla} (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                senal_id     INTEGER NOT NULL,
                simbolo      TEXT    NOT NULL,
                direccion    TEXT    NOT NULL,
                precio_nivel REAL    NOT NULL,
                precio_actual REAL   NOT NULL,
                beneficio_pct REAL,
                timestamp    TEXT    NOT NULL
            )
            """)
            self.ejecutar_query(
                f"CREATE INDEX IF NOT EXISTS idx_{tabla}_senal ON {tabla}(senal_id)"
            )

    def _registrar_nivel_hit(self, tabla: str, senal_id: int, simbolo: str,
                              direccion: str, precio_nivel: float,
                              precio_actual: float, beneficio_pct: float) -> None:
        """Inserta un registro en la tabla de toques de nivel indicada."""
        ts = datetime.now(timezone.utc).isoformat()
        self.ejecutar_insert(
            f"""
            INSERT INTO {tabla}
                (senal_id, simbolo, direccion, precio_nivel, precio_actual, beneficio_pct, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (senal_id, simbolo, direccion, precio_nivel, precio_actual, beneficio_pct, ts),
        )

    def registrar_tp1_hit(self, senal_id: int, simbolo: str, direccion: str,
                          precio_tp1: float, precio_actual: float, beneficio_pct: float) -> None:
        self._registrar_nivel_hit('tp1_hits', senal_id, simbolo, direccion,
                                  precio_tp1, precio_actual, beneficio_pct)

    def registrar_tp2_hit(self, senal_id: int, simbolo: str, direccion: str,
                          precio_tp2: float, precio_actual: float, beneficio_pct: float) -> None:
        self._registrar_nivel_hit('tp2_hits', senal_id, simbolo, direccion,
                                  precio_tp2, precio_actual, beneficio_pct)

    def registrar_tp3_hit(self, senal_id: int, simbolo: str, direccion: str,
                          precio_tp3: float, precio_actual: float, beneficio_pct: float) -> None:
        self._registrar_nivel_hit('tp3_hits', senal_id, simbolo, direccion,
                                  precio_tp3, precio_actual, beneficio_pct)

    def registrar_breakeven_hit(self, senal_id: int, simbolo: str, direccion: str,
                                precio_be: float, precio_actual: float) -> None:
        self._registrar_nivel_hit('breakeven_hits', senal_id, simbolo, direccion,
                                  precio_be, precio_actual, 0.0)

    def obtener_hits_senal(self, senal_id: int) -> dict:
        """Retorna todos los toques de nivel registrados para una señal."""
        resultado = {}
        for nivel, tabla in (('tp1', 'tp1_hits'), ('tp2', 'tp2_hits'),
                              ('tp3', 'tp3_hits'), ('breakeven', 'breakeven_hits')):
            res = self.ejecutar_query(
                f"SELECT * FROM {tabla} WHERE senal_id = ? ORDER BY timestamp ASC",
                (senal_id,)
            )
            resultado[nivel] = [dict(r) for r in res.rows] if res.rows else []
        return resultado

    # ── Tabla senal_analisis (histórico de análisis de obstáculos) ────────────

    def init_senal_analisis_table(self) -> None:
        """Crea la tabla senal_analisis para histórico de análisis de obstáculos."""
        self.ejecutar_query("""
        CREATE TABLE IF NOT EXISTS senal_analisis (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            senal_id         INTEGER NOT NULL,
            timestamp        TEXT    NOT NULL,
            tiene_obstaculos INTEGER NOT NULL,
            impacto_tp1      TEXT,
            impacto_tp2      TEXT,
            impacto_tp3      TEXT,
            recomendacion    TEXT    NOT NULL,
            obstaculos_json  TEXT,
            todas_a_favor    INTEGER DEFAULT 0
        )""")
        self.ejecutar_query(
            "CREATE INDEX IF NOT EXISTS idx_senal_analisis_senal "
            "ON senal_analisis(senal_id)"
        )

    def guardar_analisis_senal(self, senal_id: int, resultado: dict) -> None:
        """
        Persiste el resultado de analizar_obstaculos() en senal_analisis.

        Args:
            senal_id : ID de la señal analizada.
            resultado: Dict devuelto por core.signal_analyzer.analizar_obstaculos().
        """
        ts = datetime.now(timezone.utc).isoformat()
        self.ejecutar_query(
            """INSERT INTO senal_analisis
               (senal_id, timestamp, tiene_obstaculos, impacto_tp1, impacto_tp2,
                impacto_tp3, recomendacion, obstaculos_json, todas_a_favor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                senal_id,
                ts,
                int(bool(resultado.get('tiene_obstaculos', False))),
                resultado.get('impacto_tp1'),
                resultado.get('impacto_tp2'),
                resultado.get('impacto_tp3'),
                resultado.get('recomendacion', 'OPERAR_NORMAL'),
                json.dumps(resultado.get('obstaculos', []), ensure_ascii=False),
                int(bool(resultado.get('todas_a_favor', False))),
            )
        )

    def obtener_ultimo_analisis(self, senal_id: int) -> dict | None:
        """
        Devuelve el análisis más reciente de una señal, o None si no hay.

        Returns:
            Dict con id, senal_id, timestamp, tiene_obstaculos, impacto_tp1/2/3,
            recomendacion, obstaculos, todas_a_favor.
        """
        res = self.ejecutar_query(
            "SELECT * FROM senal_analisis WHERE senal_id = ? ORDER BY id DESC LIMIT 1",
            (senal_id,)
        )
        if not res.rows:
            return None
        r = dict(res.rows[0])
        return {
            'id':               r['id'],
            'senal_id':         r['senal_id'],
            'timestamp':        r['timestamp'],
            'tiene_obstaculos': bool(r['tiene_obstaculos']),
            'impacto_tp1':      r['impacto_tp1'],
            'impacto_tp2':      r['impacto_tp2'],
            'impacto_tp3':      r['impacto_tp3'],
            'recomendacion':    r['recomendacion'],
            'obstaculos':       json.loads(r.get('obstaculos_json') or '[]'),
            'todas_a_favor':    bool(r['todas_a_favor']),
        }


_db_warning_printed = False


def get_db() -> Optional[DatabaseManager]:
    """Retorna el singleton DatabaseManager, o None si Turso no está configurado.

    Centraliza la lógica de inicialización opcional de la BD para que los
    detectores no repitan el mismo bloque try/except en cada módulo.
    Imprime un aviso una sola vez cuando las variables de entorno no están
    presentes, equivalente al comportamiento anterior por detector.
    """
    global _db_warning_printed
    turso_url = os.environ.get('TURSO_DATABASE_URL')
    turso_token = os.environ.get('TURSO_AUTH_TOKEN')
    if not turso_url or not turso_token:
        if not _db_warning_printed:
            print("⚠️  Variables Turso no configuradas - Sistema funcionará sin tracking de BD")
            _db_warning_printed = True
        return None
    return DatabaseManager()


if __name__ == '__main__':
    # Test de conexión
    try:
        db = DatabaseManager()
        print("✅ Test de conexión exitoso")
        
        # Probar obtener señales activas
        activas = db.obtener_senales_activas()
        print(f"📊 Señales activas: {len(activas)}")
        
    except Exception as e:
        print(f"❌ Error en test: {e}")
