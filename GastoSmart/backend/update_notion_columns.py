import os
import requests
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("NOTION_TOKEN")
db_id = os.getenv("NOTION_DATABASE_ID")

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

# 1. Agregar el campo Hash y Recibo (tipo URL/File)
payload = {
    "properties": {
        "ID_Hash": { "rich_text": {} },
        "recibo": { "url": {} }
    }
}

try:
    r = requests.patch(f"https://api.notion.com/v1/databases/{db_id}", headers=headers, json=payload)
    if r.status_code == 200:
        print("✅ Campos técnicos (ID_Hash y Recibo) agregados con éxito.")
    else:
        print(f"❌ Error actualizando columnas: {r.text}")
except Exception as e:
    print(f"Error: {e}")
