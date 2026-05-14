#!/usr/bin/env python3
"""
Dashboard de Estado de GastoSmart
Muestra salud del sistema, últimos errores y fixes aplicados
"""

import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from error_tracker import ErrorTracker

BACKEND = Path(__file__).parent
ERROR_LOG = BACKEND / "ERROR_LOG.json"
RUN_LOG = BACKEND / "RUN_LOG.json"
FIX_HISTORY = BACKEND / "FIX_HISTORY.md"

class StatusDashboard:
    def __init__(self):
        self.tracker = ErrorTracker()
        self.backend = BACKEND

    def load_run_log(self):
        """Carga último log de ejecución"""
        if RUN_LOG.exists():
            try:
                with open(RUN_LOG, 'r') as f:
                    return json.load(f)
            except:
                return None
        return None

    def get_system_health(self):
        """Calcula salud del sistema (0-100)"""
        score = 100

        # Errores abiertos (-10 c/u, max -30)
        open_errors = sum(1 for e in self.tracker.errors if not e['fixed'])
        score -= min(open_errors * 10, 30)

        # Último run
        run_log = self.load_run_log()
        if run_log:
            failed = run_log.get('failed', 0)
            score -= min(failed * 5, 20)

            # Si último run fue hace >24h
            last_run = datetime.fromisoformat(run_log['timestamp'])
            if datetime.now() - last_run > timedelta(days=1):
                score -= 10

        return max(score, 0)

    def get_health_emoji(self, score):
        """Emoji según score"""
        if score >= 90:
            return "🟢"
        elif score >= 70:
            return "🟡"
        elif score >= 50:
            return "🟠"
        else:
            return "🔴"

    def print_header(self):
        """Banner inicial"""
        print("\n" + "=" * 70)
        print("🔧 GASTOSMART STATUS DASHBOARD".center(70))
        print("=" * 70 + "\n")

    def print_health(self):
        """Sección de salud general"""
        health = self.get_system_health()
        emoji = self.get_health_emoji(health)

        print(f"{emoji} SALUD DEL SISTEMA: {health}%")
        print(f"   {'█' * (health//5)}{'░' * (20 - health//5)}\n")

    def print_latest_run(self):
        """Información del último run"""
        run_log = self.load_run_log()
        if not run_log:
            print("⏸️  Sin ejecuciones registradas\n")
            return

        last_run = datetime.fromisoformat(run_log['timestamp'])
        time_ago = datetime.now() - last_run
        successful = run_log['successful']
        failed = run_log['failed']
        total = successful + failed

        print(f"▶️  ÚLTIMO RUN")
        print(f"   Hace: {self._format_timedelta(time_ago)}")
        print(f"   Resultado: {successful}/{total} pasos exitosos")
        if failed > 0:
            print(f"   ⚠️  {failed} pasos fallidos")
        if run_log.get('fixes_applied'):
            print(f"   🔧 Fixes aplicados: {len(run_log['fixes_applied'])}")
        print()

    def print_errors_summary(self):
        """Resumen de errores"""
        errors = self.tracker.errors
        open_errors = [e for e in errors if not e['fixed']]
        fixed_errors = [e for e in errors if e['fixed']]

        print(f"📋 ERRORES")
        print(f"   Total: {len(errors)}")
        print(f"   ✅ Solucionados: {len(fixed_errors)}")
        print(f"   ❌ Abiertos: {len(open_errors)}")

        if open_errors:
            print(f"\n   Últimos problemas:")
            for err in open_errors[-3:]:
                script = err['script'].split('/')[-1]
                time = err['timestamp'][-8:]
                print(f"   • [{time}] {script} - {err['error_type']}")
        print()

    def print_critical_info(self):
        """Información crítica para diagnóstico"""
        print(f"🔍 DIAGNÓSTICO")

        # BD
        db_path = self.backend / "gastosmart_v1.db"
        db_size_mb = db_path.stat().st_size / 1024 / 1024 if db_path.exists() else 0
        print(f"   DB: {'✓' if db_path.exists() else '✗'} ({db_size_mb:.1f} MB)")

        # .env
        env_ok = (self.backend / ".env").exists()
        print(f"   .env: {'✓' if env_ok else '✗'}")

        # Script críticos
        critical = ['gs_auto_processor.py', 'notion_bridge.py', 'supabase_bridge.py']
        for script in critical:
            exists = (self.backend / script).exists()
            print(f"   {script.split('/')[-1]}: {'✓' if exists else '✗'}")

        print()

    def print_recommendations(self):
        """Recomendaciones basadas en estado"""
        health = self.get_system_health()
        open_errors = sum(1 for e in self.tracker.errors if not e['fixed'])

        print(f"💡 RECOMENDACIONES")

        if health < 70:
            print(f"   → Ejecutar: python3 error_tracker.py diagnose")
            print(f"   → Reparar: python3 error_tracker.py fix")

        if open_errors > 0:
            print(f"   → Ver errores: python3 error_tracker.py errors")

        run_log = self.load_run_log()
        if not run_log or (datetime.now() - datetime.fromisoformat(run_log['timestamp'])) > timedelta(hours=6):
            print(f"   → Ejecutar pipeline: python3 run_with_autofix.py")

        print()

    def print_footer(self):
        """Pie de página con timestamp"""
        print("=" * 70)
        print(f"Actualizado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70 + "\n")

    def _format_timedelta(self, td):
        """Formatea timedelta en lenguaje natural"""
        total_seconds = int(td.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60

        if hours == 0:
            return f"{minutes} minutos"
        elif hours < 24:
            return f"{hours}h {minutes}m"
        else:
            days = hours // 24
            return f"{days} días"

    def show(self):
        """Muestra dashboard completo"""
        self.print_header()
        self.print_health()
        self.print_latest_run()
        self.print_errors_summary()
        self.print_critical_info()
        self.print_recommendations()
        self.print_footer()

if __name__ == "__main__":
    dashboard = StatusDashboard()
    dashboard.show()
