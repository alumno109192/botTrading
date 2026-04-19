"""
Stats Dashboard - Generador de estadísticas y reportes
Calcula métricas de performance, win rate, y genera reportes visuales
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from datetime import datetime, timezone, timedelta
from typing import Dict, List
from tabulate import tabulate
from adapters.database import DatabaseManager


class StatsDashboard:
    """Generador de estadísticas y reportes de señales"""
    
    def __init__(self):
        self.db = DatabaseManager()
    
    def generar_reporte_diario(self, fecha: datetime = None) -> str:
        """
        Genera un reporte completo del día
        
        Args:
            fecha: Fecha a analizar (default: hoy)
            
        Returns:
            String formateado con el reporte
        """
        if fecha is None:
            fecha = datetime.now(timezone.utc)
        
        stats = self.db.obtener_estadisticas_dia(fecha)
        
        if not stats or stats.get('total', 0) == 0:
            return f"📭 No hay señales registradas para {fecha.date()}"
        
        reporte = f"""
╔══════════════════════════════════════════════════════════╗
║          📊 REPORTE DIARIO - {fecha.date()}          ║
╚══════════════════════════════════════════════════════════╝

📈 RESUMEN GENERAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🎯 Total señales:         {stats['total']}
  ✅ Señales ganadoras:     {stats['wins']}
  ❌ Stop Loss:              {stats['sl']}
  ⏳ Señales activas:       {stats['total'] - stats['wins'] - stats['sl']}

📊 DISTRIBUCIÓN DE RESULTADOS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🎯 TP1 alcanzado:         {stats['tp1']}
  🎯🎯 TP2 alcanzado:       {stats['tp2']}
  🎯🎯🎯 TP3 alcanzado:     {stats['tp3']}

💰 RENDIMIENTO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📈 Win Rate:              {stats['win_rate']:.1f}%
  💵 Beneficio promedio:    {stats['avg_profit']:.2f}%
  ⏱️  Duración promedio:    {int(stats.get('avg_duration', 0))} min

🏆 MEJOR INSTRUMENTO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {stats['mejor_simbolo']}

╚══════════════════════════════════════════════════════════╝
        """
        
        return reporte
    
    def generar_reporte_semanal(self) -> str:
        """Genera reporte de los últimos 7 días"""
        fecha_fin = datetime.now(timezone.utc)
        fecha_inicio = fecha_fin - timedelta(days=7)
        
        stats = self.db.obtener_estadisticas_periodo(fecha_inicio, fecha_fin)
        
        if not stats or stats.get('total', 0) == 0:
            return "📭 No hay señales en los últimos 7 días"
        
        reporte = f"""
╔══════════════════════════════════════════════════════════╗
║              📊 REPORTE SEMANAL (7 días)              ║
╚══════════════════════════════════════════════════════════╝

📅 Período: {fecha_inicio.date()} → {fecha_fin.date()}

📈 RESUMEN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🎯 Total señales:         {stats['total']}
  ✅ Señales ganadoras:     {stats['wins']}
  📈 Win Rate:              {stats['win_rate']:.1f}%
  💵 Beneficio promedio:    {stats.get('avg_profit', 0):.2f}%

╚══════════════════════════════════════════════════════════╝
        """
        
        return reporte
    
    def calcular_win_rate(self, periodo: str = 'all') -> float:
        """
        Calcula el win rate general o de un período
        
        Args:
            periodo: 'all', 'day', 'week', 'month'
            
        Returns:
            Win rate como porcentaje
        """
        if periodo == 'day':
            stats = self.db.obtener_estadisticas_dia()
        elif periodo == 'week':
            fecha_fin = datetime.now(timezone.utc)
            fecha_inicio = fecha_fin - timedelta(days=7)
            stats = self.db.obtener_estadisticas_periodo(fecha_inicio, fecha_fin)
        elif periodo == 'month':
            fecha_fin = datetime.now(timezone.utc)
            fecha_inicio = fecha_fin - timedelta(days=30)
            stats = self.db.obtener_estadisticas_periodo(fecha_inicio, fecha_fin)
        else:  # all
            query = """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN estado IN ('TP1','TP2','TP3') THEN 1 ELSE 0 END) as wins
            FROM senales
            WHERE estado != 'ACTIVA'
            """
            result = self.db.ejecutar_query(query)
            if result.rows and len(result.rows) > 0:
                row = result.rows[0]
                # Convertir a int para evitar errores de comparación
                total = int(row.get('total', 0)) if row.get('total') else 0
                wins = int(row.get('wins', 0)) if row.get('wins') else 0
                if total > 0:
                    return (wins / total) * 100
            return 0.0
        
        return stats.get('win_rate', 0.0)
    
    def obtener_ranking_simbolos(self) -> str:
        """Genera ranking de símbolos por win rate"""
        ranking = self.db.obtener_win_rate_por_simbolo()
        
        if not ranking:
            return "📭 No hay datos suficientes para ranking"
        
        # Preparar datos para tabla
        tabla_datos = []
        for idx, item in enumerate(ranking, 1):
            emoji = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else "📊"
            tabla_datos.append([
                emoji,
                item['simbolo'],
                item['total'],
                item['wins'],
                f"{item['win_rate']:.1f}%"
            ])
        
        tabla = tabulate(
            tabla_datos,
            headers=['', 'Símbolo', 'Total', 'Wins', 'Win Rate'],
            tablefmt='fancy_grid'
        )
        
        reporte = f"""
╔══════════════════════════════════════════════════════════╗
║         🏆 RANKING DE SÍMBOLOS POR WIN RATE           ║
╚══════════════════════════════════════════════════════════╝

{tabla}

        """
        
        return reporte
    
    def obtener_mejores_indicadores(self) -> str:
        """Muestra qué combinaciones de indicadores funcionan mejor"""
        indicadores = self.db.obtener_mejores_indicadores()
        
        if not indicadores:
            return "📭 No hay datos de indicadores"
        
        reporte = """
╔══════════════════════════════════════════════════════════╗
║        🔬 MEJORES COMBINACIONES DE INDICADORES        ║
╚══════════════════════════════════════════════════════════╝

"""
        
        for idx, item in enumerate(indicadores[:5], 1):
            # Formatear JSON de indicadores
            try:
                import json
                ind_dict = json.loads(item['indicadores']) if item['indicadores'] else {}
                ind_str = ', '.join([f"{k}={v}" for k, v in ind_dict.items()])
            except:
                ind_str = item['indicadores']
            
            reporte += f"""
{idx}. Veces usado: {item['veces_usado']} | Wins: {item['wins']} | Score avg: {item['score_promedio']:.1f}
   {ind_str}
"""
        
        reporte += "\n╚══════════════════════════════════════════════════════════╝\n"
        
        return reporte
    
    def obtener_senales_por_hora(self) -> str:
        """Analiza a qué horas del día salen mejores señales"""
        query = """
        SELECT 
            substr(timestamp, 12, 2) as hora,
            COUNT(*) as total_senales,
            SUM(CASE WHEN estado IN ('TP1','TP2','TP3') THEN 1 ELSE 0 END) as wins,
            AVG(CASE WHEN beneficio_final_pct IS NOT NULL 
                THEN beneficio_final_pct ELSE 0 END) as avg_profit
        FROM senales
        WHERE estado != 'ACTIVA'
        GROUP BY hora
        HAVING total_senales >= 3
        ORDER BY avg_profit DESC
        LIMIT 10
        """
        
        result = self.db.ejecutar_query(query)
        
        if not result.rows:
            return "📭 No hay datos suficientes por hora"
        
        tabla_datos = []
        for row in result.rows:
            item = dict(row)
            win_rate = (item['wins'] / item['total_senales'] * 100) if item['total_senales'] > 0 else 0
            tabla_datos.append([
                f"{item['hora']}:00",
                item['total_senales'],
                item['wins'],
                f"{win_rate:.1f}%",
                f"{item['avg_profit']:.2f}%"
            ])
        
        tabla = tabulate(
            tabla_datos,
            headers=['Hora', 'Total', 'Wins', 'Win Rate', 'Profit Avg'],
            tablefmt='fancy_grid'
        )
        
        reporte = f"""
╔══════════════════════════════════════════════════════════╗
║           ⏰ MEJORES HORAS PARA SEÑALES               ║
╚══════════════════════════════════════════════════════════╝

{tabla}

        """
        
        return reporte
    
    def calcular_expectancy(self) -> float:
        """
        Calcula la expectancy matemática del sistema
        Expectancy = (Win Rate × Avg Win) - (Loss Rate × Avg Loss)
        """
        query = """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN estado IN ('TP1','TP2','TP3') THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN estado = 'SL' THEN 1 ELSE 0 END) as losses,
            AVG(CASE WHEN beneficio_final_pct > 0 
                THEN beneficio_final_pct ELSE 0 END) as avg_win,
            AVG(CASE WHEN beneficio_final_pct < 0 
                THEN ABS(beneficio_final_pct) ELSE 0 END) as avg_loss
        FROM senales
        WHERE estado IN ('TP1', 'TP2', 'TP3', 'SL')
        """
        
        result = self.db.ejecutar_query(query)
        
        if not result.rows or not result.rows[0]['total']:
            return 0.0
        
        stats = dict(result.rows[0])
        
        if stats['total'] == 0:
            return 0.0
        
        win_rate = stats['wins'] / stats['total']
        loss_rate = stats['losses'] / stats['total'] if stats['losses'] else 0
        
        expectancy = (win_rate * stats['avg_win']) - (loss_rate * stats['avg_loss'])
        
        return expectancy
    
    def generar_reporte_completo(self) -> str:
        """Genera un reporte completo con todas las métricas"""
        reporte = f"""
╔══════════════════════════════════════════════════════════╗
║                 📊 ANÁLISIS COMPLETO                   ║
║              Sistema de Trading - BotTrading           ║
╚══════════════════════════════════════════════════════════╝

📅 Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

"""
        
        # Reporte diario
        reporte += self.generar_reporte_diario()
        reporte += "\n\n"
        
        # Ranking de símbolos
        reporte += self.obtener_ranking_simbolos()
        reporte += "\n"
        
        # Mejores horas
        reporte += self.obtener_senales_por_hora()
        reporte += "\n"
        
        # Expectancy
        expectancy = self.calcular_expectancy()
        reporte += f"""
╔══════════════════════════════════════════════════════════╗
║              💰 EXPECTANCY MATEMÁTICA                  ║
╚══════════════════════════════════════════════════════════╝

  Expectancy: {expectancy:+.3f}%
  
  {'✅ Sistema rentable (expectancy positiva)' if expectancy > 0 else '⚠️ Sistema necesita ajustes (expectancy negativa)'}

╚══════════════════════════════════════════════════════════╝
        """
        
        return reporte
    
    def exportar_csv(self, filename: str = None, periodo_dias: int = 30):
        """
        Exporta señales a archivo CSV
        
        Args:
            filename: Nombre del archivo (default: signals_YYYYMMDD.csv)
            periodo_dias: Días atrás a exportar
        """
        if filename is None:
            filename = f"signals_{datetime.now().strftime('%Y%m%d')}.csv"
        
        fecha_limite = datetime.now(timezone.utc) - timedelta(days=periodo_dias)
        
        query = """
        SELECT 
            timestamp, simbolo, direccion, precio_entrada,
            tp1, tp2, tp3, sl, score, estado,
            beneficio_final_pct, patron_velas
        FROM senales
        WHERE timestamp >= ?
        ORDER BY timestamp DESC
        """
        
        result = self.db.ejecutar_query(query, (fecha_limite.isoformat(),))
        
        if not result.rows:
            print("📭 No hay datos para exportar")
            return
        
        # Escribir CSV
        import csv
        
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            if result.rows:
                headers = list(dict(result.rows[0]).keys())
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                
                for row in result.rows:
                    writer.writerow(dict(row))
        
        print(f"✅ Datos exportados a: {filename}")
        print(f"📊 Total registros: {len(result.rows)}")


def main():
    """Función principal para testing"""
    dashboard = StatsDashboard()
    
    print("\n" + "="*60)
    print("GENERANDO REPORTES...")
    print("="*60 + "\n")
    
    # Reporte completo
    reporte = dashboard.generar_reporte_completo()
    print(reporte)
    
    # Win rates por período
    print("\n📈 WIN RATES POR PERÍODO:")
    print(f"  Hoy:     {dashboard.calcular_win_rate('day'):.1f}%")
    print(f"  Semana:  {dashboard.calcular_win_rate('week'):.1f}%")
    print(f"  Mes:     {dashboard.calcular_win_rate('month'):.1f}%")
    print(f"  Total:   {dashboard.calcular_win_rate('all'):.1f}%")


if __name__ == '__main__':
    main()
