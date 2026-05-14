#!/usr/bin/env python3
"""
Reset completo y robusto: BD Local + Supabase + Notion
Limpia todo incluyendo aprendizajes.
"""

import sqlite3
import requests
import sys
import time
from datetime import datetime

sys.path.insert(0, '.')

DB_PATH = "gastosmart_v1.db"
NOTION_TOKEN = "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"
NOTION_DATABASE_ID = "321d94979b86800a9fb0dcf1cc30231a"

def log(level, msg):
    dt = datetime.now().strftime("%H:%M:%S")
    print(f"[{dt}]\t[{level:8s}]\t{msg}", flush=True)

def reset_db():
    """Limpia tabla gastos, preserva aprendizaje"""
    log("INFO", "🗄️  Limpiando BD local...")
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)

        # Backup
        import shutil
        backup_name = f"{DB_PATH}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy(DB_PATH, backup_name)
        log("OK", f"✓ Backup: {backup_name}")

        # Limpiar gastos y aprendizajes
        conn.execute("DELETE FROM gastos")
        conn.execute("DELETE FROM aprendizaje")
        conn.commit()

        # Verificar
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM gastos")
        gastos = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM aprendizaje")
        aprend = cursor.fetchone()[0]

        conn.close()

        log("OK", f"✓ Gastos: {gastos} (vacío)")
        log("OK", f"✓ Aprendizaje: {aprend} (vacío)")
        return True
    except Exception as e:
        log("ERROR", f"BD: {str(e)[:80]}")
        return False

def reset_supabase():
    """Limpia Supabase storage"""
    log("INFO", "☁️  Limpiando Supabase...")
    try:
        import supabase_bridge
        client = supabase_bridge.get_supabase_client()

        # Obtener todos los archivos
        response = client.storage.from_("receipts").list()
        filenames = [item['name'] for item in response]

        if len(filenames) == 0:
            log("OK", "✓ Supabase ya estaba vacío")
            return True

        # Borrar en lotes
        batch_size = 50
        total_deleted = 0

        for i in range(0, len(filenames), batch_size):
            batch = filenames[i:i+batch_size]
            try:
                result = client.storage.from_("receipts").remove(batch)
                total_deleted += len(batch)
                log("OK", f"✓ Batch {i//batch_size + 1}: {len(batch)} archivos")
            except Exception as e:
                log("WARN", f"Batch {i//batch_size + 1}: {str(e)[:60]}")

        # Verificar (con reintento)
        time.sleep(1)
        response = client.storage.from_("receipts").list()
        remaining = len(response)

        if remaining == 0:
            log("OK", f"✓ {total_deleted} archivos borrados")
        else:
            log("WARN", f"⚠️  {remaining} archivos residuales (API issue)")

        return True
    except Exception as e:
        log("ERROR", f"Supabase: {str(e)[:80]}")
        return False

def reset_notion():
    """Limpia Notion (archiva todos los registros, con paginación)"""
    log("INFO", "📝 Limpiando Notion...")
    try:
        headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        query_url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"

        # Obtener TODOS los registros con paginación
        all_pages = []
        body = {"page_size": 100}
        while True:
            response = requests.post(query_url, headers=headers, json=body)
            if response.status_code != 200:
                log("ERROR", f"Query: {response.status_code}")
                return False
            data = response.json()
            all_pages.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            body["start_cursor"] = data["next_cursor"]
            log("INFO", f"  Cargando... {len(all_pages)} registros")

        if len(all_pages) == 0:
            log("OK", "✓ Notion ya estaba vacío")
            return True

        log("INFO", f"  Total a archivar: {len(all_pages)}")

        # Archivar cada página
        archived = 0
        for page in all_pages:
            page_id = page["id"]
            try:
                url = f"https://api.notion.com/v1/pages/{page_id}"
                r = requests.patch(url, headers=headers, json={"archived": True})
                if r.status_code == 200:
                    archived += 1
                    if archived % 10 == 0:
                        log("OK", f"✓ Archivados: {archived}/{len(all_pages)}")
            except Exception as e:
                log("WARN", f"Página {page_id[:8]}: {str(e)[:40]}")

        # Verificar
        r2 = requests.post(query_url, headers=headers, json={"page_size": 100})
        remaining = len(r2.json().get("results", []))

        if remaining == 0:
            log("OK", f"✓ {archived} registros archivados")
        else:
            log("WARN", f"⚠️  {remaining} registros residuales (puede tardar unos segundos en reflejarse)")

        return True
    except Exception as e:
        log("ERROR", f"Notion: {str(e)[:80]}")
        return False

def main():
    log("START", "=== RESET COMPLETO: BD + Supabase + Notion ===")

    results = {
        "BD Local": reset_db(),
        "Supabase": reset_supabase(),
        "Notion": reset_notion()
    }

    print("\n" + "="*60)
    log("SUMMARY", "Resultados:")
    for system, success in results.items():
        status = "✓" if success else "✗"
        log("SUMMARY", f"  {status} {system}")
    print("="*60 + "\n")

    all_ok = all(results.values())
    if all_ok:
        log("OK", "✅ Reset completado exitosamente")
        return 0
    else:
        log("WARN", "⚠️  Reset con advertencias")
        return 1

if __name__ == "__main__":
    sys.exit(main())
