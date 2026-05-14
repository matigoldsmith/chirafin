#!/usr/bin/env python3
"""GastoSmart - VERSIÓN ESTABLE"""
import os, sys, time, sqlite3, hashlib
from datetime import datetime

ICLOUD_PATH = "/sessions/wonderful-nifty-knuth/mnt/com~apple~CloudDocs--GastoSmart"
DB_PATH = "/sessions/wonderful-nifty-knuth/mnt/Scripts Claude AI--GastoSmart/backend/gastosmart_v1.db"

sys.path.insert(0, os.path.dirname(__file__))
import processor_v2, supabase_bridge, notion_bridge

def log(tag, msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}]\t[{tag:10s}]\t{msg}", flush=True)

def get_hash(fp):
    import hashlib
    sha = hashlib.sha256()
    with open(fp, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''): sha.update(chunk)
    return sha.hexdigest()

def db_exec(sql, params=()):
    for attempt in range(3):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=15.0, isolation_level=None)
            cursor = conn.cursor()
            cursor.execute("PRAGMA synchronous=FULL")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(sql, params)
            conn.close()
            return True
        except:
            if attempt < 2: time.sleep(0.5)
    return False

def db_query(sql, params=(), one=False):
    for attempt in range(3):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=15.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql, params)
            result = cursor.fetchone() if one else cursor.fetchall()
            conn.close()
            return result
        except:
            if attempt < 2: time.sleep(0.5)
    return None

def process(filepath, idx, total):
    name = os.path.basename(filepath)
    try:
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            return False
        
        log(f"[{idx}/{total}]", f"✦ {name}")
        file_hash = get_hash(filepath)
        
        if db_query("SELECT id FROM gastos WHERE hash=?", (file_hash,), one=True):
            log(f"[{idx}/{total}]", "⊙ ya existe")
            return True
        
        # Supabase
        log(f"[{idx}/{total}]", "↑ supabase")
        foto_url = supabase_bridge.upload_image(filepath, file_hash)
        if not foto_url: return False
        
        # IA
        log(f"[{idx}/{total}]", "⚙ IA")
        result = processor.analyze_receipt(filepath)
        
        # Insert
        sql = "INSERT OR IGNORE INTO gastos (hash,fecha,comercio,monto,moneda,categoria,estado,foto_path,foto_url,es_recibo,sync_notion) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        params = (file_hash, result.get("fecha","2026-03-14") if result else "2026-03-14", result.get("comercio","?") if result else "?", result.get("monto",0) if result else 0, result.get("moneda","CLP") if result else "CLP", result.get("categoria_sugerida","Otros") if result else "Otros", "Analizando..." if result else "Error", filepath, foto_url, 1 if result and result.get("es_recibo") else 0, 0)
        db_exec(sql, params)
        log(f"[{idx}/{total}]", "✓ ok")
        return True
    except Exception as e:
        log(f"[{idx}/{total}]", f"✗ {str(e)[:30]}")
        return False

files = [f for f in os.listdir(ICLOUD_PATH) if not f.startswith('.') and f.lower().endswith(('.png', '.jpg', '.jpeg', '.heic'))]
log("INIT", f"🔄 {len(files)} archivos")

success = sum(1 for i, f in enumerate(files, 1) if process(os.path.join(ICLOUD_PATH, f), i, len(files)) and not time.sleep(2))
log("DONE", f"📊 {success}/{len(files)} ok")
