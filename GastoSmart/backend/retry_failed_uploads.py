#!/usr/bin/env python3
"""
Retry de uploads fallidos a Supabase
Busca registros en BD con foto_url vacío y reintenta subir desde iCloud
"""

import os, sys, sqlite3, glob, time, logging
from pathlib import Path

BACKEND = Path(__file__).parent
sys.path.insert(0, str(BACKEND))

from dotenv import dotenv_values
config = dotenv_values(BACKEND / '.env')
for k, v in config.items():
    os.environ.setdefault(k, v)

# Setup logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Path iCloud (detectar automáticamente)
ICLOUD_CANDIDATES = [
    glob.glob('/sessions/*/mnt/com~apple~CloudDocs--GastoSmart'),
    glob.glob('/Users/*/Library/Mobile Documents/com~apple~CloudDocs/GastoSmart'),
]
ICLOUD = None
for candidates in ICLOUD_CANDIDATES:
    if candidates:
        ICLOUD = candidates[0]
        break

DB_ORIGINAL = BACKEND / 'gastosmart_v1.db'
# En el VM no existe /Users → copiar BD a /tmp para poder escribir
import shutil
if not os.path.exists('/Users'):
    DB = Path(f'/tmp/gastosmart_retry_{os.getuid()}.db')
    shutil.copy2(DB_ORIGINAL, DB)
else:
    DB = DB_ORIGINAL

def save_db():
    if DB != DB_ORIGINAL:
        shutil.copy2(DB, DB_ORIGINAL)

def find_photo_in_icloud(fname):
    """Busca foto en iCloud por nombre de archivo"""
    if not ICLOUD:
        return None
    path = os.path.join(ICLOUD, fname)
    return path if os.path.exists(path) else None

def is_file_readable(path):
    """Verifica que archivo existe y es readable"""
    return os.path.exists(path) and os.access(path, os.R_OK)

def upload_with_retry(sb, icloud_path, dest, max_retries=3, wait_sec=2):
    """
    Intenta upload a Supabase con reintentos en caso de fallos transitorios.
    Retorna (foto_url, error_msg) o (None, error_msg) si falló.
    """
    for attempt in range(1, max_retries + 1):
        try:
            foto_url = sb.upload_image(icloud_path, dest)
            if foto_url:
                return foto_url, None
            else:
                return None, "Upload devolvió vacío"
        except Exception as e:
            error_str = str(e)
            is_transient = any(x in error_str.lower() for x in ['timeout', 'connection', 'temporary', '503', '429'])

            if is_transient and attempt < max_retries:
                logger.info(f"  Intento {attempt}/{max_retries} falló (transient): {error_str[:50]}, reintentando en {wait_sec}s...")
                time.sleep(wait_sec)
                continue
            else:
                return None, f"Error upload (intento {attempt}): {error_str}"

    return None, "Agotados reintentos"

def retry_uploads():
    """Reintenta uploads fallidos"""
    import supabase_bridge as sb

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    try:
        # Registros con foto_url vacío que son recibos
        rows = conn.execute("""
            SELECT id, hash, foto_path, comercio, monto, moneda, es_recibo
            FROM gastos
            WHERE (foto_url IS NULL OR foto_url = '')
              AND es_recibo = 1
            ORDER BY id DESC
        """).fetchall()

        print(f"Encontrados {len(rows)} registros sin foto_url")

        if not rows:
            print("✅ Nada que reintentar")
            return

        if not ICLOUD:
            print("❌ iCloud path no encontrado")
            return

        exitos = 0
        no_encontradas = 0
        errores_upload = 0

        for row in rows:
            row = dict(row)
            fname = os.path.basename(row['foto_path']) if row['foto_path'] else None

            if not fname:
                logger.warning(f"  id={row['id']}: sin foto_path")
                no_encontradas += 1
                continue

            icloud_path = find_photo_in_icloud(fname)

            if not icloud_path:
                logger.info(f"  {fname}: no está en iCloud (ya borrada o movida)")
                no_encontradas += 1
                continue

            # Verificar readability antes de intentar upload
            if not is_file_readable(icloud_path):
                logger.warning(f"  {fname}: archivo no readable o no existe")
                no_encontradas += 1
                continue

            # Intentar upload a Supabase con reintentos
            dest = f"{row['hash'][:8]}_{int(time.time())}_{fname}"
            foto_url, error_msg = upload_with_retry(sb, icloud_path, dest)

            if foto_url:
                conn.execute(
                    "UPDATE gastos SET foto_url=?, foto_path=? WHERE id=?",
                    (foto_url, icloud_path, row['id'])
                )
                conn.commit()
                # Encolar también como pending update (por si el Mac sobreescribe)
                try:
                    from pending_updates import queue_update
                    queue_update(
                        "UPDATE gastos SET foto_url=?, foto_path=? WHERE id=?",
                        (foto_url, icloud_path, row['id']),
                        description=f"retry upload {fname}"
                    )
                except (ImportError, Exception) as e:
                    logger.debug(f"  Warning: no se pudo encolar update: {e}")
                print(f"  ✅ {fname}: subida OK → {foto_url[:60]}...")
                exitos += 1
            else:
                logger.error(f"  {fname}: {error_msg}")
                errores_upload += 1

    finally:
        conn.close()
        save_db()

    print(f"\nResumen: ✅ {exitos} subidas | ⏭️  {no_encontradas} no encontradas | ❌ {errores_upload} errores")

if __name__ == "__main__":
    print("🔄 Reintentando uploads fallidos a Supabase...")
    retry_uploads()
