"""
Database Manager - Gestión de señales de trading en Turso (SQLite en la nube)
Maneja todas las operaciones CRUD para señales, historial de precios y estadísticas
"""

import os
import json
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

class DatabaseManager:
    """Gestiona la conexión y operaciones con la base de datos Turso"""
    
    def __init__(self):
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
    
    def _convert_param(self, param):
        """Convierte un parámetro al formato que espera Turso"""
        if param is None:
            return {"type": "null"}
        elif isinstance(param, bool):
            return {"type": "integer", "value": str(int(param))}
        elif isinstance(param, int):
            return {"type": "integer", "value": str(param)}
        elif isinstance(param, float):
            return {"type": "float", "value": param}
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
            
            # Crear objeto de resultado compatible
            class Result:
                def __init__(self, rows_data, columns):
                    self.rows = rows_data
                    self.columns = columns
            
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
                        cell = row[i] if i < len(row) else None
                        if isinstance(cell, dict) and 'value' in cell:
                            row_dict[col] = cell['value']
                        else:
                            row_dict[col] = cell
                    rows_dicts.append(row_dict)
                
                return Result(rows_dicts, columns)
            
            return Result([], [])
            
        except Exception as e:
            print(f"❌ Error ejecutando query: {e}")
            print(f"Query: {query}")
            print(f"Params: {params}")
            raise
    
    def ejecutar_insert(self, query: str, params: tuple = ()) -> int:
        """Ejecuta un INSERT y retorna el ID insertado"""
        try:
            # Ejecutar el INSERT
            self.ejecutar_query(query, params)
            
            # Obtener el último ID insertado
            result = self.ejecutar_query("SELECT last_insert_rowid() as id")
            
            if result.rows and len(result.rows) > 0:
                return result.rows[0]['id']
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
        query = """
        INSERT INTO senales (
            timestamp, simbolo, direccion, precio_entrada,
            tp1, tp2, tp3, sl, score,
            indicadores, patron_velas, version_detector,
            estado
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVA')
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
        Verifica si existe una señal activa reciente para evitar duplicados
        
        Args:
            simbolo: BTCUSD, XAUUSD, SPX500
            direccion: COMPRA o VENTA
            horas: Ventana de tiempo para considerar duplicado
            
        Returns:
            True si existe una señal similar reciente
        """
        fecha_limite = datetime.now(timezone.utc) - timedelta(hours=horas)
        
        query = """
        SELECT COUNT(*) as count
        FROM senales
        WHERE simbolo = ?
        AND direccion = ?
        AND estado = 'ACTIVA'
        AND timestamp >= ?
        """
        
        result = self.ejecutar_query(query, (simbolo, direccion, fecha_limite.isoformat()))
        
        if result.rows and result.rows[0]['count'] > 0:
            print(f"⚠️ Señal duplicada detectada: {simbolo} {direccion}")
            return True
        return False
    
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
        
        # Actualizar flags correspondientes
        if nuevo_estado == 'TP1':
            query = """
            UPDATE senales 
            SET tp1_alcanzado = TRUE, fecha_tp1 = ?, estado = ?
            WHERE id = ?
            """
            self.ejecutar_query(query, (now, nuevo_estado, senal_id))
            
        elif nuevo_estado == 'TP2':
            query = """
            UPDATE senales 
            SET tp2_alcanzado = TRUE, fecha_tp2 = ?, estado = ?
            WHERE id = ?
            """
            self.ejecutar_query(query, (now, nuevo_estado, senal_id))
            
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
    
    def registrar_precio(self, senal_id: int, precio_actual: float):
        """
        Registra un snapshot de precio para análisis histórico
        
        Args:
            senal_id: ID de la señal
            precio_actual: Precio actual del instrumento
        """
        # Obtener datos de la señal para calcular distancias
        query = "SELECT tp1, tp2, tp3, sl, precio_entrada, direccion FROM senales WHERE id = ?"
        result = self.ejecutar_query(query, (senal_id,))
        
        if not result.rows:
            return
        
        senal = dict(result.rows[0])
        
        # Calcular distancias relativas
        if senal['direccion'] == 'COMPRA':
            dist_tp1 = ((precio_actual - senal['tp1']) / senal['precio_entrada']) * 100
            dist_tp2 = ((precio_actual - senal['tp2']) / senal['precio_entrada']) * 100
            dist_tp3 = ((precio_actual - senal['tp3']) / senal['precio_entrada']) * 100
            dist_sl = ((precio_actual - senal['sl']) / senal['precio_entrada']) * 100
        else:  # VENTA
            dist_tp1 = ((senal['tp1'] - precio_actual) / senal['precio_entrada']) * 100
            dist_tp2 = ((senal['tp2'] - precio_actual) / senal['precio_entrada']) * 100
            dist_tp3 = ((senal['tp3'] - precio_actual) / senal['precio_entrada']) * 100
            dist_sl = ((senal['sl'] - precio_actual) / senal['precio_entrada']) * 100
        
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
