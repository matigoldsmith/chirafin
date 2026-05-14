import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

# Retry helper with exponential backoff
def _retry_request(method, url, headers, json=None, max_attempts=3):
    """
    Retry HTTP request with exponential backoff on 429 rate limit.
    Attempts: 1 (immediate), 2 (wait 2s), 3 (wait 4s)
    """
    for attempt in range(1, max_attempts + 1):
        try:
            res = method(url, headers=headers, json=json, timeout=15)
            if res.status_code != 429:
                return res
            # Rate limited: wait before retry
            if attempt < max_attempts:
                wait_secs = 2 ** (attempt - 1)
                print(f"⏸️ Rate limit (429). Retry {attempt}/{max_attempts - 1} in {wait_secs}s...")
                time.sleep(wait_secs)
        except requests.RequestException as e:
            if attempt == max_attempts:
                raise
            print(f"⚠️ Request error (attempt {attempt}/{max_attempts}): {e}")
            time.sleep(2 ** (attempt - 1))
    return None

def archive_notion_page(page_id):
    """Archiva (oculta) una página de Notion."""
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    try:
        res = _retry_request(
            requests.patch,
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=headers,
            json={"archived": True}
        )
        return res and res.status_code == 200
    except requests.RequestException as e:
        print(f"❌ Error archivando página {page_id}: {e}")
        return False

def sync_to_notion(data):
    """Envia o actualiza un registro en Notion (UPSERT). Evita duplicados."""
    if not NOTION_TOKEN or not NOTION_DATABASE_ID:
        return False

    pages_created = 0
    pages_updated = 0

    # Si NO es recibo: archivar en Notion si ya existe, luego marcar como synced
    is_recibo = bool(data.get("es_recibo", False))
    if not is_recibo:
        record_id = str(data.get("hash") or data.get("id", ""))
        headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        # Buscar si existe y archivar
        try:
            search_res = _retry_request(
                requests.post,
                f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
                headers=headers,
                json={"filter": {"property": "ID", "rich_text": {"equals": record_id}}}
            )
            if search_res and search_res.status_code == 200:
                results = search_res.json().get("results", [])
                for r in results:
                    archive_notion_page(r["id"])
        except requests.RequestException as e:
            print(f"⚠️ Error archivando no-recibos: {e}")
        return True  # marcar como synced para no reintentar

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    
    # Usar hash como ID único (más estable que el integer autoincrement)
    record_id = str(data.get("hash") or data.get("id", ""))
    if not record_id:
        return False

    # 1. BUSCAR SI YA EXISTE (Deduplicación)
    search_url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    search_payload = {
        "filter": {
            "property": "ID",
            "rich_text": { "equals": record_id }
        }
    }

    notion_page_id = None
    try:
        search_res = _retry_request(requests.post, search_url, headers=headers, json=search_payload)
        if search_res and search_res.status_code == 200:
            results = search_res.json().get("results", [])
            if results:
                notion_page_id = results[0].get("id")
    except (requests.RequestException, KeyError) as e:
        print(f"⚠️ Error buscando duplicados: {e}")

    # 🔍 MAPEO INTELIGENTE DE COLUMNAS
    mapping = {
        "title": {"name": "Comercio", "type": "title"},
        "id": {"name": "ID", "type": "rich_text"},
        "monto_orig": {"name": "Monto Original", "type": "number"},
        "monto_clp": {"name": "Monto CLP", "type": "number"},
        "fx": {"name": "FX", "type": "number"},
        "moneda": {"name": "Moneda", "type": "select"},
        "fecha": {"name": "Fecha", "type": "date"},
        "categoria": {"name": "Categoría", "type": "select"},
        "estado": {"name": "Estado final", "type": "select"},
        "es_recibo": {"name": "Es recibo según Gemini", "type": "select"},
        "recibo_file": {"name": "Recibo", "type": "files"},
        "origen": {"name": "Origen", "type": "select"}
    }

    # Track if ID column exists (graceful fallback if missing)
    id_column_exists = True

    try:
        db_url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}"
        db_res = _retry_request(requests.get, db_url, headers=headers)
        if db_res and db_res.status_code == 200:
            props = db_res.json().get("properties", {})
            id_column_exists = any(p.lower() == "id" for p in props.keys())
            for name, details in props.items():
                low_name = name.lower()
                p_type = details.get("type", "")
                if p_type == "title": mapping["title"] = {"name": name, "type": "title"}
                elif low_name == "id": mapping["id"] = {"name": name, "type": "rich_text"}
                elif low_name in ["monto original", "monto_orig"]: mapping["monto_orig"] = {"name": name, "type": "number"}
                elif low_name in ["monto clp", "total clp"]: mapping["monto_clp"] = {"name": name, "type": "number"}
                elif low_name in ["fx", "tipo cambio"]: mapping["fx"] = {"name": name, "type": "number"}
                elif low_name in ["moneda", "currency"]: mapping["moneda"] = {"name": name, "type": "select"}
                elif low_name in ["fecha", "date"]: mapping["fecha"] = {"name": name, "type": "date"}
                elif low_name in ["categoría", "categoria", "category"]: mapping["categoria"] = {"name": name, "type": "select"}
                elif low_name in ["estado final", "estado", "estado inicial"]: mapping["estado"] = {"name": name, "type": "select"}
                elif low_name in ["es recibo según gemini", "es_recibo", "recibo?", "es un recibo?"]: mapping["es_recibo"] = {"name": name, "type": p_type}
                elif low_name in ["recibo", "foto"]: mapping["recibo_file"] = {"name": name, "type": p_type}  # detectar tipo real (url, files, rich_text)
    except (requests.RequestException, KeyError) as e:
        print(f"⚠️ Error validando esquema Notion: {e}")

    # Graceful fallback: if ID column doesn't exist, proceed without it
    if not id_column_exists:
        print("⚠️ ID column not found in Notion database. Proceeding without ID deduplication.")

    # PAYLOAD
    props_payload = {}
    
    def add_prop(key, content):
        conf = mapping.get(key)
        if conf and conf.get("name"):
            props_payload[conf["name"]] = content

    is_recibo = bool(data.get("es_recibo", False))

    # Título: comercio si es gasto, vacío si no
    comercio = data.get("comercio") or ""
    add_prop("title", {"title": [{"text": {"content": comercio}}]})
    # Only add ID if column exists in DB
    if id_column_exists:
        add_prop("id", {"rich_text": [{"text": {"content": record_id}}]})

    # Campos numéricos y de texto: solo si ES recibo
    if is_recibo:
        monto_val = data.get("monto_original") or data.get("monto")
        if monto_val:
            add_prop("monto_orig", {"number": float(monto_val)})
        monto_clp_val = data.get("monto_clp") or data.get("monto")
        if monto_clp_val:
            add_prop("monto_clp", {"number": float(monto_clp_val)})
        if data.get("tipo_cambio"):
            add_prop("fx", {"number": float(data["tipo_cambio"])})
        moneda_val = data.get("moneda_original") or data.get("moneda")
        if moneda_val:
            add_prop("moneda", {"select": {"name": moneda_val}})
        if data.get("categoria"):
            add_prop("categoria", {"select": {"name": data["categoria"]}})
        # Fecha SOLO si es recibo
        if data.get("fecha"):
            add_prop("fecha", {"date": {"start": data["fecha"]}})

    # Estado: "Pendiente" al crear. Si el registro es "Duplicado" → siempre pushear (crear o actualizar).
    bd_estado = data.get("estado", "Pendiente")
    if not notion_page_id:
        add_prop("estado", {"select": {"name": "Pendiente"}})
    if bd_estado == "Duplicado":
        add_prop("estado", {"select": {"name": "Duplicado"}})

    conf_rec = mapping.get("es_recibo")
    if conf_rec and conf_rec.get("name"):
        if conf_rec["type"] == "checkbox":
            props_payload[conf_rec["name"]] = {"checkbox": is_recibo}
        else:
            props_payload[conf_rec["name"]] = {"select": {"name": "Sí" if is_recibo else "No"}}

    if data.get("origen"):
        add_prop("origen", {"select": {"name": data["origen"]}})

    if data.get("foto_url"):
        foto = data["foto_url"]
        rec_conf = mapping.get("recibo_file", {})
        rec_type = rec_conf.get("type", "files")
        if rec_type == "url":
            add_prop("recibo_file", {"url": foto})
        elif rec_type == "rich_text":
            add_prop("recibo_file", {"rich_text": [{"text": {"content": foto}}]})
        else:  # files (default)
            fname = foto.split("/")[-1].split("?")[0] or "foto.png"
            fname = fname[:100]  # Notion API limit: file name ≤ 100 chars
            add_prop("recibo_file", {
                "files": [{"name": fname, "type": "external", "external": {"url": foto}}]
            })

    # EJECUTAR
    try:
        is_update = notion_page_id and notion_page_id.strip()
        url = f"https://api.notion.com/v1/pages/{notion_page_id}" if is_update else "https://api.notion.com/v1/pages"
        method = requests.patch if is_update else requests.post
        payload = {"properties": props_payload}
        if not is_update:
            payload["parent"] = {"database_id": NOTION_DATABASE_ID}
        # Cover = foto del recibo (para galería)
        if data.get("foto_url"):
            payload["cover"] = {"type": "external", "external": {"url": data["foto_url"]}}

        res = _retry_request(method, url, headers=headers, json=payload)
        if res and res.status_code in [200, 201]:
            action = "Actualizado" if is_update else "Creado"
            print(f"✅ {action}: {data.get('comercio')}")
            if is_update:
                pages_updated += 1
            else:
                pages_created += 1
            return True
        else:
            error_msg = res.text if res else "Sin respuesta"
            status = res.status_code if res else "N/A"
            print(f"❌ Error Notion ({status}): {error_msg}")
            return False
    except (requests.RequestException, KeyError) as e:
        print(f"❌ Error: {e}")
        return False
    finally:
        # Summary logging
        if pages_created > 0 or pages_updated > 0:
            print(f"📊 Resumen: {pages_created} creadas, {pages_updated} actualizadas")
