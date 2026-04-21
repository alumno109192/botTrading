"""
Database Manager - Gestión de señales de trading en Turso (SQLite en la nube)
Maneja todas las operaciones CRUD para señales, historial de precios y estadísticas
"""

import os
import json
import threading
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
        if self._initialized:
            return
        self._initialized = True
        """Inicializa la conexión a Turso usando URL y TOKEN del .env"""
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
    
    def ejecutar_query(self, query: str, params: tuple = ()) -> Any:
        """Ejecuta una query usando la API HTTP de Turso"""
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
            
            # Hacer request a la API
            response = requests.post(
                f'{self.api_url}/v2/pipeline',
                headers=self.headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code != 200:
                raise Exception(f"HTTP {response.status_code}: {response.text}")
            
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
            response = requests.post(
                f'{self.api_url}/v2/pipeline',
                headers=self.headers,
                json=payload,
                timeout=30
            )
            if response.status_code != 200:
                raise Exception(f"HTTP {response.status_code}: {response.text}")
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
        return senal_id
    
    def existe_senal_reciente(self, simbolo: str, direccion: str, horas: int = 2) -> bool:
        """
        Verifica si ya existe una señal ACTIVA para el símbolo+dirección dados.
        Si la señal sigue activa (no ha tocado TP ni SL), no se crea otra.
        El parámetro horas se mantiene por compatibilidad pero no se usa.
        
        Args:
            simbolo: BTCUSD_4H, XAUUSD_15M, etc.
            direccion: COMPRA o VENTA
            horas: Ignorado (mantenido por compatibilidad)
            
        Returns:
            True si ya existe una señal activa para ese símbolo+dirección
        """
        query = """
        SELECT COUNT(*) as count
        FROM senales
        WHERE simbolo = ?
        AND direccion = ?
        AND estado IN ('ACTIVA', 'PENDIENTE_CONFIRM')
        """
        
        result = self.ejecutar_query(query, (simbolo, direccion))
        
        if result.rows and int(result.rows[0]['count']) > 0:
            print(f"⚠️ Ya existe señal ACTIVA/PENDIENTE: {simbolo} {direccion} — no se duplica")
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
        
        # Actualiza flags correspondientes sin cerrar la señal — solo TP3/SL la cierran
        if nuevo_estado == 'TP1':
            query = """
            UPDATE senales 
            SET tp1_alcanzado = TRUE, fecha_tp1 = ?
            WHERE id = ?
            """
            self.ejecutar_query(query, (now, senal_id))
            
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
        
        # Insertar en historial
        insert_query = """
        INSERT INTO historial_precios (
            senal_id, timestamp, precio,
            distancia_tp1, distancia_tp2, distancia_tp3, distancia_sl
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        
        self.ejecutar_query(insert_query, (
            senal_id,
            datetime.now(timezone.utc).isoformat(),
            precio_actual,
            dist_tp1,
            dist_tp2,
            dist_tp3,
            dist_sl
        ))
        
        # También actualizar precio_actual en la señal
        self.actualizar_precio_actual(senal_id, precio_actual)
    
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
