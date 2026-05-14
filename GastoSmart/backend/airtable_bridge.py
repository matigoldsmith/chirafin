import os
import requests
from dotenv import load_dotenv

load_dotenv()

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = os.getenv("AIRTABLE_TABLE_NAME", "Gastos")

def sync_to_airtable(data):
    """Envia un registro confirmado a Airtable."""
    if not AIRTABLE_API_KEY or not AIRTABLE_BASE_ID:
        return False
        
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Formatear para Airtable
    record = {
        "fields": {
            "Comercio": data.get("comercio"),
            "Monto": data.get("monto"),
            "Moneda": data.get("moneda"),
            "Fecha": data.get("fecha"),
            "Categoria": data.get("categoria"),
            "Estado": "Confirmado",
            "Local_ID": str(data.get("id"))
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=record)
        return response.status_code == 200
    except Exception as e:
        print(f"Error sincronizando con Airtable: {e}")
        return False
