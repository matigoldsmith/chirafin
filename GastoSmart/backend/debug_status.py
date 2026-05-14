import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()

ICLOUD_PATH = "/Users/mgoldsmithd/Library/Mobile Documents/com~apple~CloudDocs/GastoSmart"
DB_PATH = "/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/backend/gastosmart_v1.db"

def check_status():
    print("\n========= 📊 ESTADO DE GASTOSMART =========")
    
    # 1. Archivos en iCloud
    try:
        icloud_files = [f for f in os.listdir(ICLOUD_PATH) if not f.startswith('.')]
        print(f"📥 Pendientes en iCloud: {len(icloud_files)} fotos")
    except:
        print("❌ No se pudo acceder a la carpeta de iCloud.")

    # 2. Registros en Base de Datos Local
    try:
        conn = sqlite3.connect(DB_PATH)
        total = conn.execute("SELECT count(*) FROM gastos").fetchone()[0]
        pendientes = conn.execute("SELECT count(*) FROM gastos WHERE estado = 'Pendiente'").fetchone()[0]
        ignorados = conn.execute("SELECT count(*) FROM gastos WHERE estado = 'Ignorado'").fetchone()[0]
        
        print(f"🏠 Base de Datos Local: {total} registros totales")
        print(f"   - ✅ Procesados: {total - ignorados}")
        print(f"   - ⚠️ Errores/Ignorados: {ignorados}")
        conn.close()
    except:
        print("❌ Base de datos local no encontrada o vacía.")

    # 3. Verificación de Quota (Logs)
    print("\n🔍 Última actividad del sistema:")
    os.system("tail -n 5 '/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/backend/watcher.log'")
    print("==========================================\n")

if __name__ == "__main__":
    check_status()

