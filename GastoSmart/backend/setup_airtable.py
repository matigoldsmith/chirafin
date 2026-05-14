import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

def setup_airtable():
    token = os.getenv("AIRTABLE_API_KEY")
    if not token:
        print("❌ Error: AIRTABLE_API_KEY no encontrado en el archivo .env")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    workspace_id = os.getenv("AIRTABLE_WORKSPACE_ID")
    if not workspace_id:
        print("⚠️ AIRTABLE_WORKSPACE_ID no configurado en .env")
        print("Por favor, entra a Airtable, ve a tu área de trabajo (Workspace) y copia el ID de la URL.")
        return

    # Definición de la nueva Base "GastoSmart"
    new_base_data = {
        "name": "GastoSmart",
        "workspaceId": workspace_id,
        "tables": [
            {
                "name": "Gastos",
                "description": "Registro principal de boletas y facturas",
                "fields": [
                    {"name": "Hash", "type": "singleLineText"},
                    {"name": "Fecha", "type": "date", "options": {"format": "YYYY-MM-DD"}},
                    {"name": "Comercio", "type": "singleLineText"},
                    {"name": "Monto", "type": "number", "options": {"precision": 0}},
                    {"name": "Moneda", "type": "singleLineText"},
                    {"name": "Categoría", "type": "singleLineText"},
                    {"name": "Estado", "type": "singleSelect", "options": {
                        "choices": [
                            {"name": "Pendiente", "color": "orangeLight1"},
                            {"name": "Confirmado", "color": "greenLight1"}
                        ]
                    }},
                    {"name": "Foto", "type": "multipleAttachments"}
                ]
            },
            {
                "name": "Aprendizaje",
                "description": "Reglas para autocompletar comercios",
                "fields": [
                    {"name": "Patron", "type": "singleLineText"},
                    {"name": "ComercioLimpio", "type": "singleLineText"},
                    {"name": "CategoriaFija", "type": "singleLineText"}
                ]
            }
        ]
    }

    print(f"🚀 Creando Base GastoSmart en Workspace: {workspace_id}...")
    create_response = requests.post("https://api.airtable.com/v0/meta/bases", headers=headers, json=new_base_data)
    
    if create_response.status_code == 200:
        res_json = create_response.json()
        print(f"✅ Base creada exitosamente!")
        print(f"🆔 Base ID: {res_json['id']}")
        print("Añade este Base ID a tu archivo .env")
    else:
        print(f"❌ Error al crear la base: {create_response.text}")

if __name__ == "__main__":
    setup_airtable()
