#!/usr/bin/env python3
"""
Sistema de updates pendientes para GastoSmart
Cuando el VM no puede escribir directamente en la BD (conflict con watcher Mac),
guarda los cambios en un archivo JSON que el watcher aplica en el próximo ciclo.

Uso:
    # Agregar update pendiente
    from pending_updates import queue_update
    queue_update("UPDATE gastos SET foto_url=? WHERE id=?", (url, id))

    # Aplicar updates pendientes (lo hace el watcher/Mac)
    python3 pending_updates.py apply
"""

import json, os, sys, sqlite3, time
from pathlib import Path
from datetime import datetime

BACKEND = Path(__file__).parent
PENDING_FILE = BACKEND / "pending_db_updates.json"

def load_pending():
    if PENDING_FILE.exists():
        with open(PENDING_FILE) as f:
            return json.load(f)
    return []

def save_pending(updates):
    with open(PENDING_FILE, 'w') as f:
        json.dump(updates, f, indent=2)

def queue_update(sql, params, description=""):
    """Encola un UPDATE para ser aplicado por el Mac"""
    updates = load_pending()
    updates.append({
        "timestamp": datetime.now().isoformat(),
        "sql": sql,
        "params": list(params),
        "description": description,
        "applied": False
    })
    save_pending(updates)

def apply_pending():
    """Aplica updates pendientes (llamar desde Mac/watcher)"""
    import shutil

    DB_ORIGINAL = BACKEND / 'gastosmart_v1.db'
    # Copiar a /tmp para escritura segura
    if not os.path.exists('/Users'):
        DB_LOCAL = Path(f'/tmp/gastosmart_pending_{os.getuid()}.db')
        shutil.copy2(DB_ORIGINAL, DB_LOCAL)
    else:
        DB_LOCAL = DB_ORIGINAL

    updates = load_pending()
    pending = [u for u in updates if not u['applied']]

    if not pending:
        return 0

    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    applied = 0

    for update in pending:
        try:
            conn.execute(update['sql'], update['params'])
            conn.commit()
            update['applied'] = True
            update['applied_at'] = datetime.now().isoformat()
            applied += 1
            print(f"  ✅ Aplicado: {update.get('description', update['sql'][:50])}")
        except Exception as e:
            print(f"  ❌ Error: {e} | {update['sql'][:50]}")

    conn.close()

    # Copiar de vuelta si usamos /tmp
    if DB_LOCAL != DB_ORIGINAL:
        shutil.copy2(DB_LOCAL, DB_ORIGINAL)

    save_pending(updates)
    print(f"\n✅ {applied}/{len(pending)} updates aplicados")
    return applied

def check_pending():
    """Retorna cuántos updates están pendientes"""
    updates = load_pending()
    return len([u for u in updates if not u['applied']])

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "apply":
        print("📥 Aplicando updates pendientes...")
        n = apply_pending()
        if n == 0:
            print("  Sin updates pendientes")
    elif len(sys.argv) > 1 and sys.argv[1] == "status":
        n = check_pending()
        print(f"Updates pendientes: {n}")
    else:
        print(f"Uso: python3 pending_updates.py [apply|status]")
