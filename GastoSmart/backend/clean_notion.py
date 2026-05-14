import os
import requests
from dotenv import load_dotenv
import time

load_dotenv()
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def clean_all():
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    has_more = True
    next_cursor = None
    
    print("🧹 Iniciando limpieza PROFUNDA de Notion...")
    
    while has_more:
        payload = {"page_size": 100}
        if next_cursor:
            payload["start_cursor"] = next_cursor
            
        res = requests.post(url, headers=HEADERS, json=payload)
        if res.status_code != 200:
            print(f"Error: {res.text}")
            break
            
        data = res.json()
        pages = data.get("results", [])
        
        if not pages:
            print("No hay páginas para borrar.")
            break
            
        print(f"🗑️ Archivando lote de {len(pages)} páginas...")
        
        for page in pages:
            page_id = page["id"]
            requests.patch(f"https://api.notion.com/v1/pages/{page_id}", 
                          headers=HEADERS, 
                          json={"archived": True})
            time.sleep(0.1) # Breve pausa para no saturar la API
        
        has_more = data.get("has_more")
        next_cursor = data.get("next_cursor")
    
    print("✅ Notion está ahora 100% limpio.")

if __name__ == "__main__":
    clean_all()
