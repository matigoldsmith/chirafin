import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
from dotenv import load_dotenv

load_dotenv()

def setup_google_sheets():
    # 1. Configuración de autenticación
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    creds_path = os.getenv("GOOGLE_CREDS_JSON", "credentials.json")
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    
    if not os.path.exists(creds_path):
        print(f"❌ Error: No se encontró el archivo {creds_path}")
        return
    
    if not sheet_id:
        print("❌ Error: GOOGLE_SHEET_ID no definido en .env")
        return

    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(sheet_id)
        
        print(f"✅ Conectado a la planilla: {spreadsheet.title}")

        # 2. Crear pestañas y encabezados
        sheets_to_create = {
            "Gastos": ["Hash", "Fecha", "Comercio", "Monto", "Moneda", "Categoría", "Estado", "Link Foto", "Notas"],
            "Aprendizaje": ["Patron", "ComercioLimpio", "CategoriaFija"],
            "Configuracion": ["Categoria", "Moneda", "Default"]
        }

        for sheet_name, headers in sheets_to_create.items():
            try:
                # Intentar obtener la hoja, si no existe la crea
                try:
                    worksheet = spreadsheet.worksheet(sheet_name)
                    print(f"ℹ️ La pestaña '{sheet_name}' ya existe.")
                except gspread.exceptions.WorksheetNotFound:
                    worksheet = spreadsheet.add_worksheet(title=sheet_name, rows="1000", cols="20")
                    print(f"✨ Pestaña '{sheet_name}' creada.")
                
                # Escribir encabezados en la primera fila
                worksheet.update('A1', [headers])
                print(f"📝 Encabezados configurados en '{sheet_name}'.")

            except Exception as e:
                print(f"⚠️ Error procesando pestaña '{sheet_name}': {e}")

        print("\n🚀 ¡Configuración de Google Sheets terminada!")
        print("Recuerda que debes compartir la planilla con el email de la cuenta de servicio.")

    except Exception as e:
        print(f"❌ Error general: {e}")

if __name__ == "__main__":
    setup_google_sheets()
