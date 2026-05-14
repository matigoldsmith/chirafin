# Generar rutas correctas automáticamente
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

ICLOUD_INPUT_PATH = "/sessions/wonderful-nifty-knuth/mnt/com~apple~CloudDocs--GastoSmart"
LOCAL_ARCHIVE = os.path.join(PROJECT_ROOT, "database_files", "receipts")
ERROR_ARCHIVE = os.path.join(PROJECT_ROOT, "database_files", "errors")
DB_PATH = os.path.join(SCRIPT_DIR, "gastosmart_v1.db")

print(f"ICLOUD_INPUT_PATH: {ICLOUD_INPUT_PATH}")
print(f"LOCAL_ARCHIVE: {LOCAL_ARCHIVE}")
print(f"ERROR_ARCHIVE: {ERROR_ARCHIVE}")
print(f"DB_PATH: {DB_PATH}")
