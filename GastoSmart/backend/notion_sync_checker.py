#!/usr/bin/env python3
"""
notion_sync_checker.py
Detecta cambios manuales en Notion y los aplica a la BD local + aprendizaje.

Aprende de:
  - Correcciones de comercio   → actualiza aprendizaje (nombre limpio)
  - Correcciones de categoría  → actualiza aprendizaje (categoría fija)
  - Cambios de fecha/moneda    → recalcula FX en BD
  - Estado = Rechazado         → aprende que ese comercio NO es gasto
  - Estado = Aprobado          → confirma que ese comercio ES gasto
"""
import os, sys, sqlite3, requests, tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import dotenv_values
config = dotenv_values(os.path.join(os.path.dirname(__file__), '.env'))
for k, v in config.items(): os.environ.setdefault(k, v)

try: from fx_helper import convert_to_clp
except: convert_to_clp = lambda m, c, d=None: (m, 1.0)

def _reanalyze_from_supabase(foto_url: str, record_hash: str):
    """Descarga imagen desde Supabase y re-analiza con el prompt actualizado."""
    try:
        from processor_v2 import analyze_receipt
        import tempfile, os

        # Descargar imagen
        r = requests.get(foto_url, timeout=30)
        if r.status_code != 200:
            log(f"   ⚠️  No se pudo descargar imagen ({r.status_code}): {foto_url[:60]}")
            return None

        # Determinar extensión
        ext = foto_url.split('?')[0].split('.')[-1].lower()
        if ext not in ['jpg', 'jpeg', 'png', 'heic']:
            ext = 'jpg'

        with tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False) as tmp:
            tmp.write(r.content)
            tmp_path = tmp.name

        try:
            result = analyze_receipt(tmp_path)
            log(f"   🔄 Re-análisis [{result.get('modelo','?')}]: es_recibo={result.get('es_recibo')} | {result.get('comercio','?')} | {result.get('monto',0)}")
            return result
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        log(f"   ❌ Error re-análisis: {e}")
        return None

TOKEN   = os.getenv("NOTION_TOKEN")
DB_ID   = os.getenv("NOTION_DATABASE_ID")
DB_PATH = os.getenv("DB_PATH")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Notion-Version": "2022-06-28",
           "Content-Type": "application/json"}

def log(msg): print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def get_notion_pages():
    pages, cursor = [], None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        res = requests.post(f"https://api.notion.com/v1/databases/{DB_ID}/query",
                            headers=HEADERS, json=payload)
        data = res.json()
        pages.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return pages

def get_prop(page, name, ptype):
    prop = page.get("properties", {}).get(name, {})
    if ptype == "rich_text":
        items = prop.get("rich_text", [])
        return items[0]["text"]["content"].strip() if items else None
    elif ptype == "title":
        items = prop.get("title", [])
        return items[0]["text"]["content"].strip() if items else None
    elif ptype == "number":
        return prop.get("number")
    elif ptype == "select":
        sel = prop.get("select")
        return sel["name"] if sel else None
    elif ptype == "date":
        d = prop.get("date")
        return d["start"] if d else None
    return None

def upsert_aprendizaje(conn, patron, comercio_limpio, categoria=None, es_recibo=None):
    """Guarda o actualiza un patrón aprendido."""
    patron = patron.lower().strip()
    existing = conn.execute(
        "SELECT id FROM aprendizaje WHERE patron=?", (patron,)
    ).fetchone()

    if existing:
        updates, vals = [], []
        if comercio_limpio:
            updates.append("comercio_limpio=?"); vals.append(comercio_limpio)
        if categoria:
            updates.append("categoria_fija=?"); vals.append(categoria)
        if es_recibo is not None:
            updates.append("es_recibo_fijo=?"); vals.append(int(es_recibo))
        if updates:
            vals.append(patron)
            conn.execute(f"UPDATE aprendizaje SET {', '.join(updates)} WHERE patron=?", vals)
            log(f"   📚 Aprendizaje actualizado: '{patron}' → {comercio_limpio} | {categoria} | recibo={es_recibo}")
    else:
        conn.execute(
            "INSERT INTO aprendizaje (patron, comercio_limpio, categoria_fija, es_recibo_fijo) VALUES (?,?,?,?)",
            (patron, comercio_limpio or patron, categoria, int(es_recibo) if es_recibo is not None else None)
        )
        log(f"   📚 Aprendizaje nuevo: '{patron}' → {comercio_limpio} | {categoria} | recibo={es_recibo}")

def check_and_sync():
    log("🔍 Verificando cambios en Notion...")

    pages = get_notion_pages()
    log(f"   {len(pages)} páginas en Notion")

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    updated = 0

    for page in pages:
        # Buscar registro por hash (campo ID en Notion = hash del archivo)
        record_hash = get_prop(page, "ID", "rich_text")
        if not record_hash:
            continue

        row = conn.execute("SELECT * FROM gastos WHERE hash=?", (record_hash,)).fetchone()
        if not row:
            continue

        row = dict(row)
        changes = {}
        aprender = {}  # qué guardar en aprendizaje

        # ── TRIGGER: Re-análisis manual ──────────────────────────────
        # Si el usuario cambió Estado="Pendiente" + Es_Recibo="Sí" pero en BD es_recibo=0
        # → descargar desde Supabase y re-analizar con el prompt actualizado
        notion_estado_check = get_prop(page, "Estado final", "select") or get_prop(page, "Estado", "select")
        notion_es_recibo_raw = (
            get_prop(page, "Es recibo según Gemini", "select") or
            get_prop(page, "Es recibo según Gemini", "rich_text") or
            get_prop(page, "Es_Recibo", "select") or ""
        )
        notion_es_recibo_check = str(notion_es_recibo_raw).strip().lower() in ["sí", "si", "yes", "true", "1"]
        if not notion_es_recibo_check:
            # también chequear checkbox
            prop_rec = page.get("properties", {}).get("Es recibo según Gemini", {})
            if prop_rec.get("type") == "checkbox":
                notion_es_recibo_check = prop_rec.get("checkbox", False)

        es_recibo_notion_si  = notion_es_recibo_check  # True si Notion dice "Sí"
        es_recibo_notion_no  = str(notion_es_recibo_raw).strip().lower() in ["no", "false", "0"]
        if not es_recibo_notion_no:
            prop_rec2 = page.get("properties", {}).get("Es recibo según Gemini", {})
            if prop_rec2.get("type") == "checkbox":
                es_recibo_notion_no = not prop_rec2.get("checkbox", True)

        estado_pendiente = notion_estado_check and notion_estado_check.lower() == "pendiente"

        # ── CASO A: Pendiente + Es recibo=Sí, pero BD dice es_recibo=0 → re-analizar ──
        if estado_pendiente and es_recibo_notion_si and row.get("es_recibo") == 0:
            foto_url = row.get("foto_url") or ""
            if foto_url:
                log(f"   🔁 Usuario marcó como recibo: {record_hash[:12]} → re-analizando...")
                new_result = _reanalyze_from_supabase(foto_url, record_hash)
                if new_result and new_result.get("es_recibo"):
                    changes["es_recibo"]       = 1
                    changes["comercio"]        = new_result.get("comercio") or ""
                    changes["monto"]           = new_result.get("monto") or 0
                    changes["monto_original"]  = new_result.get("monto") or 0
                    changes["moneda"]          = new_result.get("moneda") or "CLP"
                    changes["moneda_original"] = new_result.get("moneda") or "CLP"
                    changes["categoria"]       = new_result.get("categoria_sugerida") or ""
                    changes["fecha"]           = new_result.get("fecha")
                    monto_clp, fx = convert_to_clp(float(changes["monto"]), changes["moneda"], changes["fecha"])
                    changes["monto_clp"]   = monto_clp
                    changes["tipo_cambio"] = fx
                    changes["sync_notion"] = 0
                    log(f"   ✅ Re-análisis exitoso: {changes['comercio']} | {changes['monto']} {changes['moneda']}")
                    # Aprender: este tipo de imagen SÍ es recibo
                    comercio_nuevo = changes.get("comercio", "").lower().strip()
                    if comercio_nuevo:
                        upsert_aprendizaje(conn, comercio_nuevo, changes["comercio"],
                                           categoria=changes.get("categoria"), es_recibo=True)
                elif new_result and not new_result.get("es_recibo"):
                    log(f"   ℹ️  Re-análisis confirma: NO es recibo (imagen poco clara)")

        # ── CASO B: Pendiente + Es recibo=No, pero BD dice es_recibo=1 → aprender y limpiar ──
        elif estado_pendiente and es_recibo_notion_no and row.get("es_recibo") == 1:
            db_comercio_actual = row.get("comercio") or ""
            log(f"   🚫 Usuario marcó como NO-recibo: {record_hash[:12]} ({db_comercio_actual}) → aprendiendo y limpiando...")
            # Limpiar todos los campos en BD
            changes["es_recibo"]       = 0
            changes["comercio"]        = ""
            changes["monto"]           = 0
            changes["monto_original"]  = 0
            changes["moneda"]          = ""
            changes["moneda_original"] = ""
            changes["categoria"]       = ""
            changes["fecha"]           = None
            changes["monto_clp"]       = 0
            changes["tipo_cambio"]     = None
            changes["sync_notion"]     = 0
            # Aprender: este comercio NO es recibo
            if db_comercio_actual:
                upsert_aprendizaje(conn, db_comercio_actual.lower().strip(),
                                   db_comercio_actual, es_recibo=False)
                log(f"   📚 Aprendido: '{db_comercio_actual}' → NO es recibo")

        # ── Comercio ────────────────────────────────────────────────
        notion_comercio = get_prop(page, "Recibo", "title")  # columna título
        if not notion_comercio:
            notion_comercio = get_prop(page, "Comercio", "title")
        db_comercio = row.get("comercio") or ""
        if notion_comercio and notion_comercio != db_comercio:
            changes["comercio"] = notion_comercio
            if db_comercio:  # solo aprende si había comercio original de la IA
                aprender["comercio_corregido"] = (db_comercio, notion_comercio)

        # ── Categoría ───────────────────────────────────────────────
        notion_cat = get_prop(page, "Categoría", "select") or get_prop(page, "Categoria", "select")
        db_cat = row.get("categoria") or ""
        if notion_cat and notion_cat != db_cat:
            changes["categoria"] = notion_cat
            patron = (changes.get("comercio") or db_comercio or "").lower().strip()
            if patron:
                aprender["categoria"] = (patron, notion_cat)

        # ── Fecha / Moneda → recalcular FX ──────────────────────────
        notion_fecha  = get_prop(page, "Fecha", "date")
        notion_moneda = get_prop(page, "Moneda", "select")
        db_fecha  = row.get("fecha")
        db_moneda = row.get("moneda_original") or row.get("moneda")
        db_monto  = row.get("monto_original") or row.get("monto") or 0

        fecha_changed  = notion_fecha  and notion_fecha  != db_fecha
        moneda_changed = notion_moneda and notion_moneda != db_moneda

        if fecha_changed or moneda_changed:
            new_fecha  = notion_fecha  or db_fecha
            new_moneda = notion_moneda or db_moneda
            new_monto_clp, new_fx = convert_to_clp(float(db_monto), new_moneda, new_fecha)
            changes.update({"fecha": new_fecha, "moneda": new_moneda,
                            "moneda_original": new_moneda,
                            "monto_clp": new_monto_clp, "tipo_cambio": new_fx})
            log(f"   ⚡ FX recalculado: {db_monto} {new_moneda} → {new_monto_clp} CLP")

        # ── Estado → aprender ───────────────────────────────────────
        # Estados posibles:
        #   "Pendiente"        → default, sin acción
        #   "No es gasto"      → la imagen NO es un gasto en absoluto (IA se equivocó) → limpiar + aprender es_recibo=False
        #   "Gasto rechazado"  → IA identificó bien, pero el usuario no puede imputarlo → NO tocar es_recibo, solo marcar
        #   "Gasto confirmado" → usuario validó/corrigió → reforzar aprendizaje comercio+categoría
        notion_estado = get_prop(page, "Estado", "select")
        if notion_estado:
            comercio_key = (changes.get("comercio") or db_comercio or "").lower().strip()
            estado_lower = notion_estado.lower().strip()

            if estado_lower == "no es gasto":
                # La IA se equivocó: no es recibo en absoluto → limpiar campos y aprender
                if row.get("es_recibo") != 0:
                    changes["es_recibo"] = 0
                    changes["comercio"] = ""
                    changes["monto"] = 0
                    changes["moneda"] = ""
                    changes["categoria"] = ""
                    changes["fecha"] = None
                    changes["monto_clp"] = 0
                    changes["tipo_cambio"] = None
                    changes["monto_original"] = 0
                    changes["moneda_original"] = ""
                    log(f"   🚫 No es gasto: {record_hash[:12]} → es_recibo=0, campos limpiados")
                if comercio_key:
                    upsert_aprendizaje(conn, comercio_key,
                                       db_comercio,
                                       es_recibo=False)

            elif estado_lower in ("imagen rechazada", "eliminar", "eliminado", "🗑️ eliminar"):
                # Eliminación profunda: la foto no era ni siquiera un gasto
                # → marcar eliminada=1 en BD + aprender que no es recibo + archivar en Notion
                changes["eliminada"] = 1
                changes["es_recibo"] = 0
                changes["sync_notion"] = 1  # ya archivado en Notion, no re-sincronizar
                if comercio_key:
                    upsert_aprendizaje(conn, comercio_key, db_comercio, es_recibo=False)
                # Archivar la página en Notion
                try:
                    import requests as _req
                    _req.patch(
                        f"https://api.notion.com/v1/pages/{page['id']}",
                        headers=headers,
                        json={"archived": True}
                    )
                except Exception:
                    pass
                log(f"   🗑️  Eliminado definitivo: {db_comercio or record_hash[:12]} → eliminada=1, aprendido como no-recibo")

            elif estado_lower == "gasto rechazado":
                # IA identificó bien, pero no es imputable a la empresa
                # NO cambiamos es_recibo (la IA estuvo correcta), solo registramos el estado
                # No aprendemos "no es recibo" — el gasto existió, solo no es imputable
                log(f"   ❌ Gasto rechazado (no imputable): {db_comercio or record_hash[:12]} — sin cambio en es_recibo")

            elif estado_lower == "gasto confirmado":
                # Usuario validó → reforzar aprendizaje con comercio y categoría correctos
                if comercio_key:
                    cat = changes.get("categoria") or notion_cat or db_cat
                    upsert_aprendizaje(conn, comercio_key,
                                       changes.get("comercio") or db_comercio,
                                       categoria=cat, es_recibo=True)
                    log(f"   ✅ Gasto confirmado: aprendizaje reforzado para '{comercio_key}'")

        # ── Aplicar aprendizajes de comercio/categoría ──────────────
        if "comercio_corregido" in aprender:
            original, corregido = aprender["comercio_corregido"]
            cat = changes.get("categoria") or notion_cat or db_cat
            upsert_aprendizaje(conn, original.lower(), corregido, categoria=cat)

        elif "categoria" in aprender and "comercio_corregido" not in aprender:
            patron, cat = aprender["categoria"]
            upsert_aprendizaje(conn, patron, changes.get("comercio") or db_comercio, categoria=cat)

        # ── Aplicar cambios a BD local ──────────────────────────────
        if changes:
            changes["sync_notion"] = 0  # marcar para re-sync con valores corregidos
            set_clause = ", ".join(f"{k}=?" for k in changes)
            vals = list(changes.values()) + [record_hash]
            conn.execute(f"UPDATE gastos SET {set_clause} WHERE hash=?", vals)
            log(f"   ✓ Actualizado: {db_comercio or record_hash[:8]} → {changes}")
            updated += 1

    conn.commit()
    conn.close()
    log(f"✅ Checker completado | {updated} registros actualizados")
    return updated

if __name__ == "__main__":
    check_and_sync()
