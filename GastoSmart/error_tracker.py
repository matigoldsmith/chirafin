#!/usr/bin/env python3
"""
Error Tracking & Auto-Fix System para GastoSmart
Registra, diagnostica y auto-repara errores encontrados
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
import subprocess
import traceback

BACKEND = Path(__file__).parent
ERROR_LOG = BACKEND / "ERROR_LOG.json"
FIX_HISTORY = BACKEND / "FIX_HISTORY.md"

class ErrorTracker:
    def __init__(self):
        self.errors = self.load_errors()
        self.backend_path = BACKEND

    def load_errors(self):
        """Carga log de errores existentes"""
        if ERROR_LOG.exists():
            try:
                with open(ERROR_LOG, 'r') as f:
                    return json.load(f)
            except:
                return []
        return []

    def save_errors(self):
        """Guarda log de errores"""
        with open(ERROR_LOG, 'w') as f:
            json.dump(self.errors, f, indent=2)

    def log_error(self, script_name, error_type, error_msg, solution=None, fixed=False):
        """Registra un error con timestamp"""
        error_entry = {
            "timestamp": datetime.now().isoformat(),
            "script": script_name,
            "error_type": error_type,
            "error_msg": str(error_msg)[:200],  # Truncar
            "solution": solution,
            "fixed": fixed,
            "fix_timestamp": datetime.now().isoformat() if fixed else None
        }
        self.errors.append(error_entry)
        self.save_errors()
        return error_entry

    def check_dependencies(self):
        """Verifica módulos Python faltantes"""
        required = {
            'supabase': 'pip install supabase',
            'notion_client': 'pip install notion-client',
            'google.generativeai': 'pip install google-generativeai',
            'PIL': 'pip install Pillow',
            'dotenv': 'pip install python-dotenv'
        }

        missing = []
        for module, install_cmd in required.items():
            try:
                __import__(module)
            except ImportError:
                missing.append((module, install_cmd))

        if missing:
            print(f"⚠️  Módulos faltantes: {len(missing)}")
            for module, cmd in missing:
                print(f"   - {module}")
                # Auto-install
                print(f"   Instalando: {cmd}")
                os.system(f"{cmd} --break-system-packages -q 2>/dev/null")
            return False
        return True

    def check_env_vars(self):
        """Verifica variables de entorno críticas"""
        env_file = BACKEND / ".env"
        if not env_file.exists():
            return False, "Archivo .env no encontrado"

        required_vars = [
            'ANTHROPIC_API_KEY',
            'GEMINI_API_KEY',
            'NOTION_API_KEY',
            'NOTION_DB_ID',
            'SUPABASE_URL',
            'SUPABASE_KEY'
        ]

        missing = []
        for var in required_vars:
            if not os.getenv(var):
                missing.append(var)

        if missing:
            return False, f"Faltan: {', '.join(missing)}"
        return True, "OK"

    def check_db(self):
        """Verifica base de datos local"""
        db_path = BACKEND / "gastosmart_v1.db"
        if not db_path.exists():
            return False, "BD no encontrada"
        if db_path.stat().st_size == 0:
            return False, "BD corrupta (tamaño=0)"
        return True, "OK"

    def diagnose(self):
        """Diagnóstico completo del sistema"""
        print("\n🔍 Diagnóstico de GastoSmart")
        print("=" * 50)

        # Dependencias
        print("\n✓ Verificando dependencias Python...")
        deps_ok = self.check_dependencies()
        print(f"  {'✓' if deps_ok else '❌'} Dependencias")

        # Variables entorno
        print("\n✓ Verificando .env...")
        env_ok, env_msg = self.check_env_vars()
        print(f"  {'✓' if env_ok else '❌'} {env_msg}")

        # BD
        print("\n✓ Verificando base de datos...")
        db_ok, db_msg = self.check_db()
        print(f"  {'✓' if db_ok else '❌'} {db_msg}")

        # Scripts principales
        print("\n✓ Verificando scripts críticos...")
        critical_scripts = ['gs_auto_processor.py', 'notion_bridge.py', 'supabase_bridge.py']
        for script in critical_scripts:
            script_path = BACKEND / script
            exists = script_path.exists()
            print(f"  {'✓' if exists else '❌'} {script}")

        print("\n" + "=" * 50)
        if deps_ok and env_ok and db_ok:
            print("✅ Sistema operacional")
        else:
            print("⚠️  Se encontraron problemas - ejecutar fix_issues()")

        return {
            "dependencies": deps_ok,
            "env": env_ok,
            "db": db_ok
        }

    def fix_issues(self):
        """Intenta auto-reparar problemas detectados"""
        print("\n🔧 Auto-reparando problemas...")

        # 1. Instalar dependencias
        print("  → Instalando dependencias faltantes...")
        os.system("pip install supabase notion-client google-generativeai Pillow python-dotenv --break-system-packages -q 2>/dev/null")

        # 2. Crear .env si falta
        env_file = BACKEND / ".env"
        if not env_file.exists():
            print("  → Creando .env template...")
            with open(BACKEND / ".env.example", 'r') as f:
                content = f.read()
            with open(env_file, 'w') as f:
                f.write(content)
            print(f"    ⚠️  Completar variables en {env_file}")

        # 3. Verificar BD
        db_path = BACKEND / "gastosmart_v1.db"
        if not db_path.exists() or db_path.stat().st_size == 0:
            print("  → Reinicializando BD...")
            os.system(f"python3 {BACKEND}/reset_db.py 2>/dev/null")

        print("\n✅ Reparación completada")

    def write_fix_history(self, action, details):
        """Documenta los fixes realizados"""
        entry = f"\n## {datetime.now().isoformat()}\n"
        entry += f"- **Acción:** {action}\n"
        entry += f"- **Detalles:** {details}\n"

        with open(FIX_HISTORY, 'a') as f:
            f.write(entry)

    def show_errors(self, limit=10):
        """Muestra últimos errores"""
        if not self.errors:
            print("✓ Sin errores registrados")
            return

        print(f"\n📋 Últimos {min(limit, len(self.errors))} errores:")
        print("=" * 60)
        for err in self.errors[-limit:]:
            status = "✅ FIXED" if err['fixed'] else "❌ OPEN"
            print(f"\n{status} | {err['timestamp'][:10]}")
            print(f"  Script: {err['script']}")
            print(f"  Tipo: {err['error_type']}")
            print(f"  Msg: {err['error_msg'][:80]}")

def main():
    tracker = ErrorTracker()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "diagnose":
            tracker.diagnose()
        elif cmd == "fix":
            tracker.fix_issues()
        elif cmd == "errors":
            tracker.show_errors()
        elif cmd == "clear":
            ERROR_LOG.unlink(missing_ok=True)
            print("✓ Error log borrado")
    else:
        tracker.diagnose()

if __name__ == "__main__":
    main()
