import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BUCKET_NAME = "receipts"

def clean_storage():
    print(f"🧹 Iniciando limpieza de Supabase (Bucket: {BUCKET_NAME})...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    # 1. Listar archivos
    try:
        res = supabase.storage.from_(BUCKET_NAME).list()
    except Exception as e:
        print(f"Error accediendo al bucket: {e}")
        return
    
    if not res:
        print("No se encontraron archivos o el bucket está vacío.")
        return

    files_to_delete = [f['name'] for f in res if f['name'] != '.emptyKeep']
    
    if not files_to_delete:
        print("Bucket ya está limpio.")
        return

    print(f"🗑️ Eliminando {len(files_to_delete)} archivos...")
    
    # Supabase permite borrar en lotes
    for i in range(0, len(files_to_delete), 100):
        batch = files_to_delete[i:i+100]
        try:
            supabase.storage.from_(BUCKET_NAME).remove(batch)
            print(f"✅ Borrado lote {i//100 + 1}")
        except Exception as e:
            print(f"⚠️ Error en lote {i//100 + 1}: {e}")

    print("✨ Supabase Storage está limpio.")

if __name__ == "__main__":
    clean_storage()
