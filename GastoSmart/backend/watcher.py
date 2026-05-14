#!/usr/bin/env python3
# watcher.py — solo cleanup de iCloud
# El análisis de imágenes lo hace el scheduled task de Cowork con Claude vision.
import os, sys, time, sqlite3, hashlib
from datetime import datetime

from dotenv import load_dotenv
load_dotenv(override=True)

ICLOUD = os.getenv("ICLOUD_INPUT_PATH", "/Users/mgoldsmithd/Library/Mobile Documents/com~apple~CloudDocs/GastoSmart")
DB = os.getenv("DB_PATH", "/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/backend/gastosmart_v1.db")

def log(t, m): print(f"[{datetime.now().strftime('%H:%M:%S')}]\t{t}\t{m}", flush=True)

def hash_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while True:
            chunk = f.read(8192)
            if not chunk: break
            h.update(chunk)
    return h.hexdigest()

def db_exec(sql, params):
    for _ in range(3):
        try:
            c = sqlite3.connect(DB, timeout=10)
            c.execute("PRAGMA synchronous=FULL")
            c.execute(sql, params)
            c.commit()
            c.close()
            return True
        except: time.sleep(0.3)
    return False

files = sorted([f for f in os.listdir(ICLOUD) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.heic'))])
log("START", f"Revisando {len(files)} archivos en iCloud (solo cleanup)")

borrados = 0
for i, f in enumerate(files, 1):
    path = os.path.join(ICLOUD, f)
    try:
        h = hash_file(path)
    except FileNotFoundError:
        continue

    try:
        c = sqlite3.connect(DB, timeout=5)
        row = c.execute("SELECT foto_url, sync_notion FROM gastos WHERE hash=?", (h,)).fetchone()
        c.close()
    except:
        continue

    if row:
        foto_url, sync_notion = row
        if foto_url and sync_notion == 1:
            try:
                os.remove(path)
                db_exec("UPDATE gastos SET eliminada=1 WHERE hash=?", (h,))
                log(f"[{i}/{len(files)}]", f"🗑️  Borrado (ya procesado): {f}")
                borrados += 1
            except Exception as e:
                log(f"[{i}/{len(files)}]", f"⚠️  No se pudo borrar {f}: {e}")
        else:
            log(f"[{i}/{len(files)}]", f"⏳ Pendiente de procesar: {f}")
    else:
        log(f"[{i}/{len(files)}]", f"🆕 Nueva foto (pendiente análisis Cowork): {f}")

log("DONE", f"Cleanup completado — {borrados} borrados de {len(files)} archivos")
