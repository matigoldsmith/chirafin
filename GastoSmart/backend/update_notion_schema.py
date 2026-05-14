import os
import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

def update_notion_schema():
    if not NOTION_TOKEN or not DATABASE_ID:
        print("❌ Error: NOTION_TOKEN o NOTION_DATABASE_ID no encontrados en .env")
        return

    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    # Definir las nuevas columnas que queremos asegurar en Notion
    payload = {
        "properties": {
            "ID": { "rich_text": {} }
        }
    }

    print(f"🚀 Intentando añadir columna 'ID' a Notion en la DB: {DATABASE_ID}...")
    try:
        response = requests.patch(url, headers=headers, json=payload)
        if response.status_code == 200:
            print("✅ Columna 'ID' añadida con éxito.")
        else:
            print(f"❌ Error al actualizar Notion ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"❌ Error de conexión: {e}")

if __name__ == "__main__":
    update_notion_schema()
