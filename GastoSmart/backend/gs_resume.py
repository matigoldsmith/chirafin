import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()

ICLOUD_PATH = os.getenv("ICLOUD_INPUT_PATH", "/Users/mgoldsmithd/Library/Mobile Documents/com~apple~CloudDocs/GastoSmart")
DB_PATH = "/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/backend/gastosmart_v1.db"

def resume():
    print("\n" + "="*50)
    print("🚀 GASTOSMART FINAL RESUME")
    print("="*50)
    
    # 1. iCloud
    if os.path.exists(ICLOUD_PATH):
        files = [f for f in os.listdir(ICLOUD_PATH) if not f.startswith('.')]
        print(f"📁 iCloud Pending: {len(files)} files")
    else:
        print("❌ iCloud Path not found")

    # 2. Database
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        
        total = conn.execute("SELECT count(*) FROM gastos").fetchone()[0]
        gastos_ok = conn.execute("SELECT count(*) FROM gastos WHERE es_recibo = 1").fetchone()[0]
        ignorados = conn.execute("SELECT count(*) FROM gastos WHERE es_recibo = 0 AND estado != 'Analizando...'").fetchone()[0]
        analizando = conn.execute("SELECT count(*) FROM gastos WHERE estado = 'Analizando...' OR estado = 'Procesando...'").fetchone()[0]
        sync_pending = conn.execute("SELECT count(*) FROM gastos WHERE sync_notion = 0 AND es_recibo = 1").fetchone()[0]
        
        print(f"📊 Local Database Items: {total}")
        print(f"   - ✅ Verified Receipts: {gastos_ok}")
        print(f"   - 💡 Ignored (Not receipts): {ignorados}")
        print(f"   - ⏳ Pending IA Analysis: {analizando}")
        print(f"   - 🛰️ Pending Notion Sync: {sync_pending}")
        
        if sync_pending > 0:
            print("\n🔍 Next sync will target these:")
            rows = conn.execute("SELECT comercio, hash FROM gastos WHERE sync_notion = 0 AND es_recibo = 1 LIMIT 5").fetchall()
            for r in rows:
                print(f"      - {r['comercio']} ({r['hash'][:8]})")
                
        conn.close()
    except Exception as e:
        print(f"❌ DB Error: {e}")

    print("="*50 + "\n")

if __name__ == "__main__":
    resume()
