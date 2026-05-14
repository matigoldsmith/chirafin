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

def setup_database():
    print(f"🛠️ Configurando base de datos Notion: {db_id}")
    url = f"https://api.notion.com/v1/databases/{db_id}"
    
    # Definir la estructura de columnas (Properties)
    # Nota: 'Name' es la principal y ya existe por defecto.
    payload = {
        "properties": {
            "Comercio": { "title": {} },
            "Monto": { "number": { "format": "number" } },
            "Fecha": { "date": {} },
            "Categoría": {
                "select": {
                    "options": [
                        {"name": "Supermercado", "color": "green"},
                        {"name": "Restaurante", "color": "orange"},
                        {"name": "Transporte", "color": "blue"},
                        {"name": "Servicios", "color": "red"},
                        {"name": "Hogar", "color": "brown"},
                        {"name": "Otros", "color": "gray"}
                    ]
                }
            },
            "Estado": {
                "select": {
                    "options": [
                        {"name": "Pendiente", "color": "yellow"},
                        {"name": "Confirmado", "color": "blue"},
                        {"name": "Ignorado", "color": "gray"}
                    ]
                }
            },
            "Moneda": {
                "select": {
                    "options": [
                        {"name": "CLP", "color": "green"},
                        {"name": "USD", "color": "blue"}
                    ]
                }
            }
        }
    }
    
    try:
        response = requests.patch(url, headers=headers, json=payload)
        if response.status_code == 200:
            print("✅ Base de Datos configurada con éxito. ¡Ya puedes ver las columnas en Notion!")
        else:
            print(f"❌ Error configurando DB: {response.text}")
    except Exception as e:
        print(f"❌ Error conexión: {e}")

if __name__ == "__main__":
    setup_database()
