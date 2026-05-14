import os
import sqlite3
import requests
import datetime
import currency_utils
import supabase_bridge
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
DB_PATH = "/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/backend/gastosmart_v1.db"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def get_notion_updates():
    """Obtiene las últimas 50 páginas modificadas en Notion."""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    payload = {
        "page_size": 50,
        "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}]
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    if response.status_code == 200:
        return response.json().get("results", [])
    else:
        dt = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{dt}]\t            \t[LEARN]     \tError consultando Notion: {response.text}")
        return []

def learn_from_edits():
    """Compara Notion con DB local y genera reglas de aprendizaje."""
    updates = get_notion_updates()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    learned_count = 0
    
    for page in updates:
        props = page.get("properties", {})
        
        # Obtener Hash_ID para vincular con DB (busca en ambas columnas por compatibilidad)
        hash_id = ""
        for id_field in ["ID_Hash", "ID"]:
            rich_text = props.get(id_field, {}).get("rich_text", [])
            if rich_text:
                val = rich_text[0].get("text", {}).get("content", "")
                if val:
                    hash_id = val
                    break
            
        if not hash_id: continue
        
        # Obtener valores actuales de Notion
        notion_comercio = ""
        title_prop = props.get("Comercio") or props.get("Name")
        if title_prop and title_prop.get("title"):
            notion_comercio = title_prop["title"][0].get("text", {}).get("content", "").strip()
            # Forzar Title Case según regla del usuario
            if notion_comercio:
                notion_comercio = notion_comercio.title()
            
        cat_obj = props.get("Categoría", {}).get("select") or props.get("Categoría AI", {}).get("select")
        notion_categoria = cat_obj.get("name", "") if cat_obj else ""
        
        notion_monto = props.get("Monto Original", {}).get("number", 0) or 0

        fecha_obj = props.get("Fecha", {}).get("date")
        notion_fecha = fecha_obj.get("start", "") if fecha_obj else ""

        # Leer es_recibo desde la columna SELECT correcta (no checkbox)
        es_recibo_sel = props.get("Es recibo según Gemini", {}).get("select")
        if es_recibo_sel:
            notion_es_recibo = es_recibo_sel.get("name", "Sí") == "Sí"
        else:
            notion_es_recibo = None  # desconocido → no comparar

        # Priorizar "Estado final" como feedback del usuario
        estado_final_obj = props.get("Estado final", {}).get("select") or props.get("Estado final", {}).get("status")
        estado_obj = props.get("Estado inicial", {}).get("select")
        notion_estado = (estado_final_obj.get("name") if estado_final_obj else (estado_obj.get("name") if estado_obj else ""))

        # Lógica especial para feedback "No es gasto"
        if notion_estado and any(word in notion_estado.lower() for word in ["no es gasto", "ignorar", "incorrecto", "borrar"]):
            notion_es_recibo = False
        elif notion_estado and any(word in notion_estado.lower() for word in ["verificado", "confirmado", "ok", "listo"]):
            notion_estado = "Verificado"

        notion_moneda = "CLP"
        moneda_obj = props.get("Moneda", {}).get("select")
        if moneda_obj:
            notion_moneda = moneda_obj.get("name", "CLP")
        
        # Consultar DB local con las nuevas columnas
        local = cursor.execute("""
            SELECT comercio, comercio_ia, categoria, monto, monto_original, fecha, moneda_original, hash, es_recibo, estado 
            FROM gastos WHERE hash = ?
        """, (hash_id,)).fetchone()
        
        if local:
            # 1. Normalizar valores para comparación "fofa"
            l_com = (local["comercio"] or "").strip().lower()
            n_com = (notion_comercio or "").strip().lower()
            
            l_cat = (local["categoria"] or "").strip().lower()
            n_cat = (notion_categoria or "").strip().lower()
            
            l_fec = (local["fecha"] or "")[:10]
            n_fec = (notion_fecha or "")[:10]
            
            l_mon = float(local["monto_original"] or 0)
            n_mon = float(notion_monto or 0)

            l_est = (local["estado"] or "").strip().lower()
            n_est = (notion_estado or "").strip().lower()
            
            # Mapeo de estados equivalentes para evitar bucles (Si son parecidos, NO es un cambio manual)
            equivalentes = {
                "analizando...": "pendiente",
                "procesando...": "pendiente",
                "pendiente": "pendiente",
                "error ai": "re-intentar",
                "ignorado": "no es gasto",
                "verificado": "ok"
            }
            l_est_norm = equivalentes.get(l_est, l_est)
            n_est_norm = equivalentes.get(n_est, n_est)

            # Detectar cambios REALES
            changed_name = bool(n_com and n_com != l_com)
            changed_cat = bool(n_cat and n_cat != l_cat)
            changed_fecha = bool(n_fec and n_fec != l_fec)
            changed_monto = bool(n_mon > 0 and abs(n_mon - l_mon) > 0.1)
            
            local_es_recibo = bool(local["es_recibo"]) if "es_recibo" in local.keys() else True
            changed_recibo = (notion_es_recibo is not None) and (notion_es_recibo != local_es_recibo)
            
            # Solo consideramos cambio de estado si no son equivalentes
            changed_estado = bool(n_est and n_est_norm != l_est_norm)
            
            if changed_name or changed_cat or changed_fecha or changed_monto or changed_recibo or changed_estado:
                dt = datetime.datetime.now().strftime("%H:%M:%S")
                # Debug de qué cambió exactamente (útil para el log)
                razon = []
                if changed_name: razon.append(f"Nombre ({l_com} -> {n_com})")
                if changed_cat: razon.append("Cat")
                if changed_estado: razon.append(f"Estado ({l_est_norm} != {n_est_norm})")
                
                print(f"[{dt}]\t            \t[LEARN]     \t✨ Edición manual detectada en {hash_id[:8]}: {', '.join(razon)}")
                
                # --- RECALCULAR MONEDA SI CAMBIÓ FECHA O MONTO ---
                # Usamos los valores definitivos de Notion
                final_fecha = notion_fecha if notion_fecha else local["fecha"]
                final_monto = notion_monto if notion_monto > 0 else local["monto_original"]
                final_moneda = notion_moneda if notion_moneda else (local["moneda_original"] or "CLP")
                
                # Asegurar formato fecha YYYY-MM-DD
                if final_fecha and len(final_fecha) > 10:
                    final_fecha = final_fecha[:10]
                
                tipo_cambio = currency_utils.get_exchange_rate(final_moneda, final_fecha)
                monto_clp = float(final_monto) * tipo_cambio
                
                local_keys = local.keys()
                # Actualizar registro local
                cursor.execute("""
                    UPDATE gastos 
                    SET comercio = ?, categoria = ?, fecha = ?, monto_original = ?, monto = ?,
                        moneda_original = ?, moneda = ?, monto_clp = ?, tipo_cambio = ?,
                        es_recibo = ?, estado = ?, sync_notion = 0
                    WHERE hash = ?
                """, (
                    notion_comercio, notion_categoria, final_fecha, final_monto, final_monto,
                    final_moneda, final_moneda, monto_clp, tipo_cambio,
                    1 if notion_es_recibo else 0, notion_estado or (local["estado"]),
                    hash_id
                ))
                
                # APRENDIZAJE: Si hay cambios, guardamos el patrón de la IA como "causa raíz"
                if local["comercio_ia"]:
                    raw_ia = local["comercio_ia"].lower().strip()
                    if raw_ia:
                        # Guardamos la 'verdad' del usuario para este patrón
                        cursor.execute("""
                            INSERT OR REPLACE INTO aprendizaje (
                                patron, comercio_limpio, categoria_fija, fecha_fija, monto_fijo, es_recibo_fijo
                            )
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            raw_ia, 
                            notion_comercio, 
                            notion_categoria, 
                            notion_fecha if changed_fecha else None, 
                            notion_monto if changed_monto else None,
                            1 if notion_es_recibo else 0
                        ))
                        dt = datetime.datetime.now().strftime("%H:%M:%S")
                        print(f"[{dt}]\t            \t[LEARN]     \t🎓 Memoria Reforzada: {raw_ia} -> {'(Ignorado)' if not notion_es_recibo else notion_comercio}")
                
                # Lógica de eliminación física si el estado es "Imagen rechazada"
                if notion_estado == "Imagen rechazada":
                    dt = datetime.datetime.now().strftime("%H:%M:%S")
                    print(f"[{dt}]\t            \t[LEARN]     \t🗑️ Limpiando rastros locales de imagen rechazada: {hash_id[:8]}...")
                    
                    # 1. NO borramos de Supabase (mantenemos histórico allá)
                    # if local.get("foto_url"):
                    #     ...
                            
                    # 2. Borrar de Disco Local (Backup) para ahorrar espacio
                    local_path = local["foto_path"] if "foto_path" in local_keys else None
                    if local_path and os.path.exists(local_path):
                        try:
                            os.remove(local_path)
                            print(f"   ✅ Archivo local eliminado.")
                        except Exception as e:
                            print(f"   ⚠️ Error borrando de disco: {e}")

                learned_count += 1
                
    conn.commit()
    conn.close()
    return learned_count

if __name__ == "__main__":
    print("🧠 Sincronizando cambios manuales desde Notion...")
    count = learn_from_edits()
    if count > 0:
        print(f"✅ ¡Éxito! Se sincronizaron {count} ediciones manuales.")
    else:
        print("ℹ️ No se detectaron cambios nuevos en Notion.")
