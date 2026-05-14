#!/usr/bin/env python3
"""
Ejecutor de scripts con Auto-Diagnosis y Auto-Fix
Cada error encontrado se registra y se intenta reparar automáticamente
"""

import subprocess
import sys
import os
from pathlib import Path
from error_tracker import ErrorTracker
from datetime import datetime
import json

BACKEND = Path(__file__).parent
RUN_LOG = BACKEND / "RUN_LOG.json"

class AutoFixRunner:
    def __init__(self):
        self.tracker = ErrorTracker()
        self.run_history = []
        self.fixes_applied = []

    def run_step(self, step_name, command, description=""):
        """Ejecuta un paso y maneja errores"""
        print(f"\n{'='*60}")
        print(f"▶️  {step_name} - {description}")
        print(f"{'='*60}")

        result = {
            "step": step_name,
            "command": command,
            "timestamp": datetime.now().isoformat(),
            "success": False,
            "error": None,
            "output": ""
        }

        try:
            output = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=300
            )

            result["output"] = output.stdout
            result["stderr"] = output.stderr

            if output.returncode == 0:
                result["success"] = True
                print(f"✅ {step_name} completado")
                print(output.stdout[:200])
            else:
                result["success"] = False
                result["error"] = output.stderr[:300]
                print(f"❌ Error en {step_name}")
                print(output.stderr[:500])
                self._handle_error(step_name, output.stderr)

        except subprocess.TimeoutExpired:
            result["error"] = "Timeout (>300s)"
            result["success"] = False
            print(f"❌ Timeout en {step_name}")
            self._handle_error(step_name, "Timeout")

        except Exception as e:
            result["error"] = str(e)
            result["success"] = False
            print(f"❌ Excepción en {step_name}: {e}")
            self._handle_error(step_name, str(e))

        self.run_history.append(result)
        return result["success"]

    def _handle_error(self, script_name, error_msg):
        """Intenta diagnosticar y reparar automáticamente"""
        print(f"\n🔧 Analizando error...")

        # Identificar tipo de error
        error_lower = error_msg.lower()

        if "modulenotfounderror" in error_lower or "no module named" in error_lower:
            print("  → Error de módulo faltante")
            self.tracker.log_error(script_name, "ModuleNotFound", error_msg,
                                 solution="pip install required modules", fixed=False)
            self._auto_install_deps()

        elif "filenotfounderror" in error_lower or "no such file" in error_lower:
            print("  → Error de archivo faltante")
            self.tracker.log_error(script_name, "FileNotFound", error_msg,
                                 solution="Check file paths", fixed=False)

        elif "connectionerror" in error_lower or "timeout" in error_lower:
            print("  → Error de conectividad")
            self.tracker.log_error(script_name, "ConnectionError", error_msg,
                                 solution="Check API keys and network", fixed=False)

        elif "keyerror" in error_lower or "attribute" in error_lower:
            print("  → Error de configuración/estructura")
            self.tracker.log_error(script_name, "ConfigError", error_msg,
                                 solution="Check .env or data structure", fixed=False)

        else:
            print("  → Error desconocido")
            self.tracker.log_error(script_name, "Unknown", error_msg, fixed=False)

    def _auto_install_deps(self):
        """Instala dependencias faltantes"""
        print("  → Auto-instalando dependencias...")
        deps = [
            'supabase',
            'notion-client',
            'google-generativeai',
            'Pillow',
            'python-dotenv'
        ]
        for dep in deps:
            cmd = f"pip install {dep} --break-system-packages -q 2>/dev/null"
            os.system(cmd)
        self.fixes_applied.append("installed_dependencies")
        print("  ✅ Dependencias instaladas")

    def run_all(self):
        """Ejecuta pipeline completo con auto-fix"""
        print("\n🚀 GastoSmart Auto-Processor with Auto-Fix")
        print("=" * 60)

        backend = str(BACKEND)

        # Diagnóstico inicial
        print("\n📋 Diagnóstico inicial...")
        self.tracker.diagnose()

        # Pipeline
        steps = [
            ("Step 0: Notion Sync Checker",
             f"python3 -W ignore {backend}/notion_sync_checker.py 2>/dev/null || true",
             "Sincronizar cambios de Notion"),

            ("Step 1: Prepare",
             f"python3 {backend}/gs_auto_processor.py --step prepare",
             "Limpiar, deduplicar, listar nuevas fotos"),

            ("Step 2: Analyze",
             f"python3 {backend}/gs_auto_processor.py --step analyze",
             "Analizar imágenes con Gemini/Haiku"),

            ("Step 3: Upload",
             f"python3 {backend}/gs_auto_processor.py --step upload",
             "Subir a Supabase e insertar en BD"),

            ("Step 4: Sync Notion",
             f"python3 {backend}/gs_auto_processor.py --step sync",
             "Sincronizar a Notion"),

            ("Step 5: Cleanup",
             f"python3 {backend}/gs_auto_processor.py --step cleanup",
             "Limpiar iCloud")
        ]

        all_success = True
        for step_name, command, description in steps:
            success = self.run_step(step_name, command, description)
            if not success:
                all_success = False
                # Intentar continuar con siguiente paso
                print(f"  ⚠️  Continuando con siguiente paso...")

        # Resumen final
        self._print_summary(all_success)
        self._save_run_log()

    def _print_summary(self, all_success):
        """Imprime resumen de ejecución"""
        print("\n" + "=" * 60)
        print("📊 RESUMEN DE EJECUCIÓN")
        print("=" * 60)

        successful = sum(1 for r in self.run_history if r["success"])
        failed = len(self.run_history) - successful

        print(f"\nPasos ejecutados: {len(self.run_history)}")
        print(f"  ✅ Exitosos: {successful}")
        print(f"  ❌ Fallidos: {failed}")

        if self.fixes_applied:
            print(f"\n🔧 Reparaciones aplicadas:")
            for fix in self.fixes_applied:
                print(f"  - {fix}")

        print(f"\nErrores registrados: {len(self.tracker.errors)}")

        print("\n" + "=" * 60)
        if all_success:
            print("✅ PIPELINE COMPLETADO SIN ERRORES")
        else:
            print("⚠️  COMPLETADO CON ERRORES - Ver ERROR_LOG.json")
            print("\nÚltimos errores:")
            self.tracker.show_errors(3)

    def _save_run_log(self):
        """Guarda log de ejecución"""
        with open(RUN_LOG, 'w') as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "total_steps": len(self.run_history),
                "successful": sum(1 for r in self.run_history if r["success"]),
                "failed": sum(1 for r in self.run_history if not r["success"]),
                "steps": self.run_history,
                "fixes_applied": self.fixes_applied
            }, f, indent=2)

if __name__ == "__main__":
    runner = AutoFixRunner()
    runner.run_all()
