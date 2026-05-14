#!/usr/bin/env python3
"""
GastoSmart Watcher V4 - Limpio y Robusto
Sin bugs de concurrencia de SQLite
"""
import os, sys, time, sqlite3, hashlib, datetime
from pathlib import Path

# Config
ICLOUD_INPUT_PATH = "/sessions/wonderful-nifty-knuth/mnt/com~apple~CloudDocs--GastoSmart"
DB_PATH = "/sessions/wonderful-nifty-knuth/mnt/Scripts Claude AI--GastoSmart/backend/gastosmart_v1.db"
DELAY_BETWEEN = 15

def log_info(tag, msg):
    dt = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{dt}]\t[{tag:12}]\t{msg}", flush=True)

def get_db_connection():
    """Obtener conexión con reintentos automáticos"""
    for attempt in range(3):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.OperationalError as e:
            if attempt < 2:
                time.sleep(2)
            else:
                raise

def get_file_hash(file_path):
    """Hash del archivo"""
    with open(file_path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()

def process_file(file_path, idx, total):
    """Procesar UN archivo - sin BD concurrent issues"""
    filename = os.path.basename(file_path)
    
    try:
        # Validar archivo
        if not os.path.exists(file_path):
            return
        
        img_hash = get_file_hash(file_path)
        log_info("SCAN", f"[{idx}/{total}] {filename} hash={img_hash[:8]}")
        
        # Abrir BD una sola vez por archivo
        conn = get_db_connection()
        
        try:
            # Verificar si existe
            c = conn.cursor()
            c.execute("SELECT id FROM gastos WHERE hash = ?", (img_hash,))
            exists = c.fetchone()
            
            if exists:
                log_info("SCAN", f"  Ya procesado. ID={exists[0]}")
                conn.close()
                return
            
            # Insertar como "Analizando"
            c.execute("""
                INSERT INTO gastos (hash, foto_path, estado, created_at, es_recibo)
                VALUES (?, ?, ?, datetime('now'), 0)
            """, (img_hash, file_path, "Analizando..."))
            conn.commit()
            log_info("SCAN", f"  ✓ Insertado en BD")
            
        finally:
            conn.close()
        
    except Exception as e:
        log_info("ERROR", f"{filename}: {str(e)[:60]}")

def main():
    log_info("SYSTEM", "GastoSmart V4 iniciando...")
    log_info("SYSTEM", f"Escaneando: {ICLOUD_INPUT_PATH}")
    
    # Listar archivos
    if not os.path.exists(ICLOUD_INPUT_PATH):
        log_info("ERROR", f"Carpeta iCloud no encontrada: {ICLOUD_INPUT_PATH}")
        return
    
    files = sorted([
        f for f in os.listdir(ICLOUD_INPUT_PATH)
        if f.lower().endswith(('.jpg', '.jpeg', '.png', '.heic'))
        and not f.startswith('.')
    ])
    
    if not files:
        log_info("SYSTEM", "No hay archivos pendientes")
        return
    
    log_info("SYSTEM", f"Encontrados {len(files)} archivos")
    
    # Procesar cada uno
    for idx, filename in enumerate(files, 1):
        file_path = os.path.join(ICLOUD_INPUT_PATH, filename)
        process_file(file_path, idx, len(files))
        
        if idx < len(files):
            time.sleep(DELAY_BETWEEN)
    
    log_info("SYSTEM", f"✓ Ciclo completado. {len(files)} archivos procesados")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log_info("SYSTEM", "Detenido por usuario")
    except Exception as e:
        log_info("ERROR", f"Fatal: {e}")
        sys.exit(1)
