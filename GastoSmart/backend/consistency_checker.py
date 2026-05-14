#!/usr/bin/env python3
"""
Verificador de Consistencia: BD Local ↔ Supabase ↔ Notion
Asegura que los datos estén sincronizados entre los 3 sistemas
"""

import sqlite3
import sys
import os
import shutil
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

import supabase_bridge
import notion_bridge

# DB path dinámico (mismo patrón que gs_auto_processor.py)
DB_ORIGINAL = os.path.join(BACKEND, 'gastosmart_v1.db')
if not os.path.exists('/Users'):
    # VM: copiar BD a /tmp para escritura
    import glob
    _session_candidates = [s for s in glob.glob('/sessions/*') if os.path.isdir(s) and os.access(s, os.W_OK) and not s.endswith('/mnt')]
    _session_dir = _session_candidates[0] if _session_candidates else '/tmp'
    DB_PATH = os.path.join(_session_dir, f'gastosmart_{os.getuid()}.db')
    if not os.path.exists(DB_PATH):
        shutil.copy2(DB_ORIGINAL, DB_PATH)
else:
    DB_PATH = DB_ORIGINAL

def log(level, msg):
    dt = datetime.now().strftime("%H:%M:%S")
    print(f"[{dt}]\t[{level:8s}]\t{msg}")

def get_local_hashes():
    """Obtiene todos los hashes y URLs de Supabase de la BD local"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT id, hash, foto_url, estado, sync_notion FROM gastos WHERE foto_url IS NOT NULL ORDER BY hash")
        results = cursor.fetchall()
        conn.close()
        # Extrae el filename de la URL usando urllib.parse
        data = {}
        for row_id, hash_val, url, estado, sync_notion in results:
            if url:
                # Parsear URL: https://...supabase.co/storage/v1/object/public/receipts/FILENAME
                try:
                    parsed = urlparse(url)
                    path_parts = parsed.path.split('/')
                    if 'receipts' in path_parts:
                        idx = path_parts.index('receipts')
                        if idx + 1 < len(path_parts):
                            filename = path_parts[idx + 1]
                            data[filename] = {
                                "id": row_id,
                                "hash": hash_val,
                                "estado": estado,
                                "sync_notion": sync_notion,
                                "url": url
                            }
                except (ValueError, IndexError):
                    log("WARN", f"URL parse falló: {url[:50]}")
        return data
    except Exception as e:
        log("ERROR", f"BD local: {str(e)[:50]}")
        return {}

def get_supabase_files():
    """Obtiene lista de archivos en Supabase"""
    try:
        client = supabase_bridge.get_supabase_client()
        response = client.storage.from_("receipts").list()
        # response es una lista de dicts con clave 'name'
        return {item['name'] for item in response if isinstance(item, dict) and 'name' in item}
    except Exception as e:
        log("ERROR", f"Supabase: {str(e)[:50]}")
        return set()

def get_notion_hashes():
    """Obtiene hashes de Notion usando notion_bridge"""
    try:
        # Obtener registros de Notion
        notion_records = notion_bridge.get_all_expenses()
        if not notion_records:
            return {}

        data = {}
        for record in notion_records:
            # Nota: Notion guarda el hash en el campo 'hash' de propiedades
            hash_val = record.get('hash') or record.get('Hash')
            notion_id = record.get('id')
            if hash_val:
                data[hash_val] = {"notion_id": notion_id, "notion_record": record}

        return data
    except Exception as e:
        log("WARN", f"Notion API: {str(e)[:50]} - usando sin integración")
        return {}

def mark_for_reupload(row_ids):
    """Marca registros para re-upload si tienen foto_url pero falta en Supabase"""
    if not row_ids:
        return
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        placeholders = ','.join('?' * len(row_ids))
        conn.execute(
            f"UPDATE gastos SET foto_url=NULL WHERE id IN ({placeholders})",
            row_ids
        )
        conn.commit()
        conn.close()
        log("INFO", f"📤 Marcados {len(row_ids)} registros para re-upload")
    except Exception as e:
        log("ERROR", f"No se pudo marcar para re-upload: {str(e)[:50]}")

def check_consistency(auto_repair=False):
    """
    Verifica consistencia entre las 3 bases.
    Si auto_repair=True, marca para re-upload los que falten en Supabase.
    """
    log("INFO", "🔍 Iniciando verificación de consistencia...")

    local = get_local_hashes()  # Filenames en Supabase extraídos de BD local (key=filename)
    supabase = get_supabase_files()  # Filenames actuales en Supabase storage (set)
    notion = get_notion_hashes()  # Hashes en Notion

    log("INFO", f"📊 BD Local (con URL): {len(local)} registros")
    log("INFO", f"📊 Supabase Storage: {len(supabase)} archivos")
    log("INFO", f"📊 Notion: {len(notion)} registros")

    print("\n" + "="*80)
    print(f"{'Categoría':<25} {'Count':>10} {'%':>8}")
    print("-"*80)

    # Verificar: En BD local pero NO en Supabase (fotos perdidas)
    missing_supabase = set(local.keys()) - supabase
    missing_supabase_ids = [local[fn]['id'] for fn in missing_supabase if fn in local]

    if missing_supabase:
        pct = (len(missing_supabase) / max(len(local), 1)) * 100
        print(f"❌ Fotos perdidas (BD→Supabase) {len(missing_supabase):>10} {pct:>7.1f}%")
        for fn in list(missing_supabase)[:3]:
            print(f"   - {fn[:50]}...")
        if len(missing_supabase) > 3:
            print(f"   ... y {len(missing_supabase) - 3} más")
        if auto_repair:
            mark_for_reupload(missing_supabase_ids)
    else:
        print(f"✅ Fotos en Supabase                {len(local):>10} {'100.0':>7}%")

    # Verificar: En Supabase pero NO en BD local (archivos huérfanos)
    missing_local = supabase - set(local.keys())
    if missing_local:
        pct = (len(missing_local) / max(len(supabase), 1)) * 100
        print(f"📦 Archivos huérfanos en Supabase {len(missing_local):>10} {pct:>7.1f}%")
        for fn in list(missing_local)[:3]:
            print(f"   - {fn[:50]}...")
        if len(missing_local) > 3:
            print(f"   ... y {len(missing_local) - 3} más")
    else:
        print(f"✅ Sin archivos huérfanos         {0:>10} {'0.0':>7}%")

    # Verificar: No sincronizados a Notion (sync_notion=0)
    not_synced_notion = [f for f, d in local.items() if d.get("sync_notion") == 0]
    if not_synced_notion:
        pct = (len(not_synced_notion) / max(len(local), 1)) * 100
        print(f"⏳ Pendientes sincronizar Notion  {len(not_synced_notion):>10} {pct:>7.1f}%")
    else:
        print(f"✅ Todos sincronizados a Notion  {len(local):>10} {'100.0':>7}%")

    # Verificar: Con error de IA
    with_error = [f for f, d in local.items() if "Error" in str(d.get("estado", ""))]
    if with_error:
        pct = (len(with_error) / max(len(local), 1)) * 100
        print(f"❌ Con Error IA                   {len(with_error):>10} {pct:>7.1f}%")
    else:
        print(f"✅ Sin errores de análisis IA    {0:>10} {'0.0':>7}%")

    # Verificar: BD ↔ Notion mismatch (en BD pero no en Notion)
    local_hashes = set(local.values()) if isinstance(local, dict) else set()
    local_in_notion = len([h for h in local_hashes if h in notion])
    missing_notion = len(local) - local_in_notion

    if missing_notion > 0:
        pct = (missing_notion / max(len(local), 1)) * 100
        print(f"⚠️  Falta en Notion                {missing_notion:>10} {pct:>7.1f}%")
    else:
        print(f"✅ BD ↔ Notion sincronizados     {len(local):>10} {'100.0':>7}%")

    print("="*80)

    # Resumen final
    all_synced = (len(missing_supabase) == 0 and len(missing_local) == 0 and
                  len(with_error) == 0 and missing_notion == 0)

    print()
    if all_synced:
        log("OK", "✅ TODOS LOS SISTEMAS SINCRONIZADOS ✓")
    else:
        issues = []
        if missing_supabase:
            issues.append(f"{len(missing_supabase)} fotos faltantes en Supabase")
        if missing_local:
            issues.append(f"{len(missing_local)} archivos huérfanos")
        if with_error:
            issues.append(f"{len(with_error)} con errores IA")
        if missing_notion:
            issues.append(f"{missing_notion} faltantes en Notion")
        log("WARN", f"⚠️  Problemas encontrados: {', '.join(issues)}")

    return all_synced

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Verificar consistencia entre BD, Supabase y Notion")
    parser.add_argument("--auto-repair", action="store_true", help="Marcar para re-upload los que falten en Supabase")
    args = parser.parse_args()

    check_consistency(auto_repair=args.auto_repair)
