#!/usr/bin/env python3
"""
Script para resetear la base de datos preservando la tabla de aprendizaje.

USO:
    python3 reset_db.py

IMPORTANTE:
- Borra la tabla 'gastos' completamente
- Preserva la tabla 'aprendizaje' (machine learning patterns)
- NO se puede deshacer. Haz un backup antes si lo necesitas.
"""

import sqlite3
import shutil
import os
from datetime import datetime

DB_PATH = "/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/backend/gastosmart_v1.db"

def reset_database():
    """Resetea gastos pero preserva aprendizaje."""

    # Verificar que la base de datos existe
    if not os.path.exists(DB_PATH):
        print(f"❌ Base de datos no encontrada: {DB_PATH}")
        return False

    # Crear backup automático
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{DB_PATH}.backup_{timestamp}"
    shutil.copy2(DB_PATH, backup_path)
    print(f"✅ Backup creado: {backup_path}")

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Contar registros antes del reset
        cursor.execute("SELECT COUNT(*) FROM gastos")
        count_gastos = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM aprendizaje")
        count_aprendizaje = cursor.fetchone()[0]

        print(f"\n📊 Estado antes del reset:")
        print(f"   - gastos: {count_gastos} registros")
        print(f"   - aprendizaje: {count_aprendizaje} registros")

        # Confirmación
        resp = input("\n⚠️  ¿Seguro que quieres borrar la tabla 'gastos'? (escribe 'SÍ' para confirmar): ")
        if resp.strip().upper() != "SÍ":
            print("❌ Operación cancelada.")
            conn.close()
            return False

        # Dropear tabla gastos y recrearla vacía
        print("\n🔄 Reseteando tabla gastos...")
        cursor.execute("DROP TABLE IF EXISTS gastos")

        # Recrear tabla con estructura original
        cursor.execute("""
        CREATE TABLE gastos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hash TEXT UNIQUE,
            fecha TEXT,
            comercio TEXT,
            monto REAL,
            moneda TEXT,
            categoria TEXT,
            estado TEXT,
            foto_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            es_recibo INTEGER,
            sync_notion INTEGER DEFAULT 0,
            last_sync_error TEXT,
            foto_url TEXT,
            comercio_ia TEXT,
            monto_original REAL,
            moneda_original TEXT,
            monto_clp REAL,
            tipo_cambio REAL,
            eliminada INTEGER DEFAULT 0
        )
        """)

        conn.commit()

        # Verificar resultado
        cursor.execute("SELECT COUNT(*) FROM gastos")
        count_new = cursor.fetchone()[0]

        print(f"✅ Tabla gastos reseteada. Registros ahora: {count_new}")
        print(f"✅ Tabla aprendizaje preservada: {count_aprendizaje} patrones")
        print(f"\n✨ Reset completado exitosamente.")

        conn.close()
        return True

    except Exception as e:
        print(f"❌ Error durante reset: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("🔧 Reset de Base de Datos GastoSmart")
    print("=" * 60)
    success = reset_database()
    exit(0 if success else 1)
