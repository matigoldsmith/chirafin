import os
import sqlite3
import shutil
from dotenv import load_dotenv

load_dotenv()

# Rutas
DB_PATH = "/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/backend/gastosmart_v1.db"
RECEIPTS_DIR = "/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/database_files/receipts"
ERRORS_DIR = "/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/database_files/errors"

def reset_system():
    print("⚠️  AVISO: Esto borrará toda la base de datos local y los archivos procesados.")
    confirm = input("¿Estás seguro de que quieres continuar? (s/n): ")
    
    if confirm.lower() != 's':
        print("Operación cancelada.")
        return

    # 1. Limpiar Base de Datos
    try:
        if os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM gastos")
            conn.commit()
            conn.close()
            print("✅ Base de datos local vaciada.")
    except Exception as e:
        print(f"❌ Error al limpiar DB: {e}")

    # 2. Limpiar Carpeta de Recibos
    try:
        if os.path.exists(RECEIPTS_DIR):
            shutil.rmtree(RECEIPTS_DIR)
            os.makedirs(RECEIPTS_DIR)
            print("✅ Carpeta de recibos locales vaciada.")
        
        if os.path.exists(ERRORS_DIR):
            shutil.rmtree(ERRORS_DIR)
            os.makedirs(ERRORS_DIR)
            print("✅ Carpeta de errores vaciada.")
    except Exception as e:
        print(f"❌ Error al limpiar archivos: {e}")

    print("\n🚀 LISTO. Ahora puedes:")
    print("1. Limpiar manualmente tu base de datos de Notion (si quieres empezar de cero ahí también).")
    print("2. Poner los archivos originales en la carpeta de iCloud.")
    print("3. Iniciar el watcher: python3 watcher.py")

if __name__ == "__main__":
    reset_system()
