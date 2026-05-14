#!/usr/bin/env python3
"""
GastoSmart Auto Processor
=========================
Script central de procesamiento. Maneja todos los pasos excepto el análisis IA de imágenes
(que lo hace processor_v2.py con Gemini/Haiku). Normalmente lo ejecuta Claude automáticamente,
pero también puedes correrlo manualmente desde terminal.

REQUISITOS
----------
- Python 3.9+
- pip install supabase python-dotenv requests difflib --break-system-packages
- Archivo .env en la misma carpeta con:
    ANTHROPIC_API_KEY, GEMINI_API_KEY, NOTION_TOKEN,
    NOTION_DATABASE_ID, SUPABASE_URL, SUPABASE_KEY

FLUJO COMPLETO (orden correcto)
--------------------------------
  1. prepare   → escanea iCloud y genera /tmp/gs_prepare.json con fotos nuevas
  2. analyze   → analiza cada foto con Claude vision nativo (fallback: Gemini → Haiku), genera /tmp/gs_resultados.json
  3. upload    → sube fotos a Supabase, inserta en BD local, detecta duplicados
  4. sync      → sincroniza registros pendientes a Notion
  5. cleanup   → mueve fotos procesadas fuera de iCloud (evita reproceso)
  6. dedup     → (opcional) busca y archiva duplicados existentes en BD+Notion

COMANDOS INDIVIDUALES
---------------------
  python3 gs_auto_processor.py --step prepare
  python3 gs_auto_processor.py --step analyze
  python3 gs_auto_processor.py --step upload
  python3 gs_auto_processor.py --step sync
  python3 gs_auto_processor.py --step cleanup
  python3 gs_auto_processor.py --step dedup     → limpieza de duplicados (seguro correr en cualquier momento)
  python3 gs_auto_processor.py --step fix_fx    → recalcula FX para registros sin tipo_cambio
  python3 gs_auto_processor.py --step reglas    → muestra reglas de aprendizaje activas en BD

EJECUCIÓN COMPLETA MANUAL (equivalente al ciclo automático)
------------------------------------------------------------
  cd "/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/backend"
  python3 gs_auto_processor.py --step prepare
  python3 gs_auto_processor.py --step analyze
  python3 gs_auto_processor.py --step upload
  python3 gs_auto_processor.py --step sync
  python3 gs_auto_processor.py --step cleanup

ARCHIVOS TEMPORALES
-------------------
  /tmp/gs_prepare.json      → lista de fotos detectadas (prepare → analyze)
  /tmp/gs_resultados.json   → resultados del análisis IA (analyze → upload)
  /tmp/gastosmart_<uid>.db  → copia local de la BD para evitar conflictos de permisos

OTROS SCRIPTS RELACIONADOS
---------------------------
  processor_v2.py           → análisis de imágenes con Gemini/Haiku (usado como fallback por step_analyze)
  notion_bridge.py          → funciones de sync con Notion (llamado por step_sync y step_upload)
  fx_helper.py              → conversión de monedas a CLP via frankfurter.app
  run_24_7.sh               → ejecuta el ciclo completo cada hora en background
  reset_all.py              → limpia BD + Supabase + Notion (¡destructivo!)
  consistency_checker.py    → verifica sincronía entre BD local, Supabase y Notion
"""

import argparse
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time

# ── Paths dinámicos ────────────────────────────────────────────────────────────
BACKEND = os.path.dirname(os.path.abspath(__file__))
# TEMP_DIR: para archivos JSON temporales usa el backend; para BD SQLite usa fuera del mount
_session_candidates = [s for s in glob.glob('/sessions/*') if os.path.isdir(s) and os.access(s, os.W_OK) and not s.endswith('/mnt')]
_session_dir = _session_candidates[0] if _session_candidates else '/tmp'
TEMP_DIR = BACKEND  # JSONs temporales
ICLOUD_CANDIDATES = (
    glob.glob('/sessions/*/mnt/com~apple~CloudDocs--GastoSmart') +  # VM (Cowork) mount nombre legacy
    glob.glob('/sessions/*/mnt/GastoSmart') +                        # VM (Cowork) mount nombre simple
    glob.glob('/sessions/*/mnt/Scripts Claude AI/GastoSmart/uploads') +  # VM (Cowork) via mount Scripts Claude AI
    glob.glob(os.path.expanduser('~/Library/Mobile Documents/com~apple~CloudDocs/GastoSmart'))  # Mac directo
)
ICLOUD = ICLOUD_CANDIDATES[0] if ICLOUD_CANDIDATES else None
DB_ORIGINAL = os.path.join(BACKEND, 'gastosmart_v1.db')
# En el VM la BD montada no soporta escritura SQLite → copiar fuera del mount
DB_LOCAL    = DB_ORIGINAL if os.path.exists('/Users') else os.path.join(_session_dir, f'gastosmart_{os.getuid()}.db')
ENV         = os.path.join(BACKEND, '.env')
RESULTADOS  = os.path.join(TEMP_DIR, 'gs_resultados.json')

# ── Setup entorno ──────────────────────────────────────────────────────────────
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

from dotenv import dotenv_values
try:
    import email_integration as ei
except ImportError:
    ei = None  # email_integration es opcional
config = dotenv_values(ENV)
for k, v in config.items():
    os.environ[k] = v

def db_open():
    """Open DB with corruption handling and WAL mode. Falls back to backup if main DB is corrupted."""
    try:
        if DB_LOCAL != DB_ORIGINAL:
            shutil.copy2(DB_ORIGINAL, DB_LOCAL)
        conn = sqlite3.connect(DB_LOCAL, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")   # previene corrupción
        conn.execute("PRAGMA synchronous=NORMAL") # balance rendimiento/seguridad
        # Test connection
        conn.execute("SELECT 1")
        return conn
    except (sqlite3.DatabaseError, sqlite3.OperationalError) as e:
        print(f"⚠️  DB corruption detected: {e}")
        # Try to find and restore from backup
        import glob as _glob
        backups = sorted(_glob.glob(f"{DB_ORIGINAL}.backup_*"), reverse=True)
        if backups:
            print(f"  Attempting restore from {backups[0]}")
            try:
                shutil.copy2(backups[0], DB_ORIGINAL)
                if DB_LOCAL != DB_ORIGINAL:
                    shutil.copy2(DB_ORIGINAL, DB_LOCAL)
                conn = sqlite3.connect(DB_LOCAL, timeout=15)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                return conn
            except Exception as e2:
                print(f"  Restore failed: {e2}")
                raise
        else:
            print("  No backup found")
            raise

def db_save(conn):
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # flush WAL antes de copiar
    conn.commit()
    conn.close()
    if DB_LOCAL != DB_ORIGINAL:
        shutil.copy2(DB_LOCAL, DB_ORIGINAL)
        # Eliminar WAL viejo en DB_ORIGINAL para evitar corrupción al abrir sin WAL de DB_LOCAL
        wal_orig = DB_ORIGINAL + '-wal'
        shm_orig = DB_ORIGINAL + '-shm'
        for f in [wal_orig, shm_orig]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except (PermissionError, OSError):
                    # FUSE mount no permite borrar — truncar a 0 bytes es equivalente
                    try:
                        open(f, 'wb').close()
                    except Exception:
                        pass

def sha256(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()

def _normalizar(s):
    """Quita acentos y pasa a minúsculas para comparación robusta."""
    import unicodedata
    return unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower().strip()

def _similitud(a, b):
    """Similitud robusta: ratio estándar + prefix check, ambos con acentos normalizados."""
    from difflib import SequenceMatcher
    an, bn = _normalizar(a), _normalizar(b)
    if not an or not bn:
        return 0.0
    # Prefix check: "Boka" vs "Boka Spa" → similar
    if an.startswith(bn) or bn.startswith(an):
        return 0.95
    return SequenceMatcher(None, an, bn).ratio()

def es_duplicado(conn, comercio, fecha, monto, moneda, categoria='', **kwargs):
    """
    Retorna (confianza, id_keeper):
      'seguro'   → skip automático
      'probable' → flagear en Notion para revisión del usuario
      None       → no es duplicado

    Reglas (en orden de prioridad):
      R0a: mismo email_id → SEGURO (mismo documento, distintas fotos del adjunto)
      R0b: mismo numero_boleta existente → SEGURO
      R0c: numero_boleta nuevo y distinto a todos → NO es duplicado (documentos distintos)
      R1:  misma fecha + monto + moneda → SEGURO
      R2:  misma fecha + monto + moneda + comercio similar ≥80% → SEGURO
      R3:  misma categoría + monto + moneda + fecha ±3 días → PROBABLE

    Invariante: si fecha es None, no se aplican R1/R2/R3 (evita falsos positivos).
    """
    moneda = (moneda or 'CLP').upper()

    # R0a: mismo email_id → el mismo adjunto de email procesado antes (sea recibo o no)
    email_id = kwargs.get('email_id') if kwargs else None
    if email_id:
        row = conn.execute("""
            SELECT id FROM gastos WHERE email_id=? LIMIT 1
        """, (email_id,)).fetchone()
        if row:
            return 'seguro', row['id']

    # R0b/R0c: número de boleta
    numero_boleta = kwargs.get('numero_boleta') if kwargs else None
    if numero_boleta:
        mismo_numero = conn.execute("""
            SELECT id FROM gastos WHERE numero_boleta=? AND es_recibo=1 LIMIT 1
        """, (numero_boleta,)).fetchone()
        if mismo_numero:
            return 'seguro', mismo_numero['id']   # mismo folio = mismo documento
        else:
            return None, None                      # folio distinto = documentos distintos

    # A partir de aquí: sin numero_boleta. Requiere fecha no-nula para evitar falsos positivos.
    if not fecha:
        return None, None

    # R1: misma fecha + monto + moneda
    row = conn.execute("""
        SELECT id FROM gastos
        WHERE monto=? AND moneda=? AND fecha=? AND es_recibo=1
          AND comercio IS NOT NULL
        LIMIT 1
    """, (monto, moneda, fecha)).fetchone()
    if row:
        return 'seguro', row['id']

    # R2: misma fecha + monto + moneda + comercio similar ≥80%
    if comercio and comercio != 'Desconocido' and monto:
        candidatos = conn.execute("""
            SELECT id, comercio FROM gastos
            WHERE monto=? AND moneda=? AND fecha=? AND es_recibo=1
              AND comercio IS NOT NULL AND comercio != '' AND comercio != 'Desconocido'
        """, (monto, moneda, fecha)).fetchall()
        for c in candidatos:
            if _similitud(comercio, c['comercio']) >= 0.80:
                return 'seguro', c['id']

    # R3: misma categoría + monto + moneda + fecha ±3 días → PROBABLE (requiere revisión)
    if categoria and monto:
        row = conn.execute("""
            SELECT id FROM gastos
            WHERE monto=? AND moneda=? AND categoria=? AND es_recibo=1
              AND fecha IS NOT NULL
              AND ABS(julianday(fecha) - julianday(?)) <= 3
            LIMIT 1
        """, (monto, moneda, categoria, fecha)).fetchone()
        if row:
            return 'probable', row['id']

    return None, None

# ── STEP: analyze ─────────────────────────────────────────────────────────────
def step_analyze():
    """
    Analiza las fotos nuevas con Gemini (+ fallback Haiku).
    Lee gs_prepare.json, escribe gs_resultados.json.
    Claude no ve ninguna imagen — todo ocurre en este script.
    """
    prepare_file = os.path.join(TEMP_DIR, 'gs_prepare.json')
    if not os.path.exists(prepare_file):
        print("ERROR: corre --step prepare primero")
        sys.exit(1)

    try:
        with open(prepare_file) as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"ERROR: Invalid JSON in {prepare_file}: {e}")
        sys.exit(1)

    fotos = data.get('fotos', [])
    if not fotos:
        print("Sin fotos nuevas.")
        with open(RESULTADOS, 'w') as f:
            json.dump([], f)
        return

    # Importar funciones de análisis del procesador existente
    from processor_v2 import analyze_with_gemini, analyze_with_claude

    def _analyze_with_claude_vision(image_path):
        """Usa Claude vision nativo (este mismo proceso) para analizar la imagen. Sin API externa."""
        import base64, json as _json, re as _re
        try:
            import anthropic as _anthropic
            # Leer imagen como base64
            with open(image_path, 'rb') as f:
                img_data = base64.standard_b64encode(f.read()).decode('utf-8')
            # Detectar mime type
            mime = 'image/png' if image_path.lower().endswith('.png') else 'image/jpeg'

            # Usar la API key del .env pero con el modelo que YA ESTAMOS usando (no Haiku)
            import os as _os
            from dotenv import load_dotenv as _lde
            _lde(_os.path.join(BACKEND, '.env'))
            api_key = _os.getenv('ANTHROPIC_API_KEY', '')
            if not api_key:
                return None, None

            client = _anthropic.Anthropic(api_key=api_key)
            prompt = """Analiza esta imagen. Si es un recibo, ticket, factura o comprobante de pago, extrae:
- es_recibo: true
- fecha: YYYY-MM-DD (si no hay año usa el año actual)
- comercio: nombre del negocio/empresa
- monto: número exacto como aparece en el documento (CLP/BRL: entero; USD/EUR/otras: decimal permitido)
- moneda: CLP, USD, EUR, BRL, etc.
- categoria_sugerida: elige exactamente una de estas:
  Viajes / Representación / Supermercado / Insumos / Cuentas / Servicios Profesionales / Gastos Comunes / Inversiones / Software / Otros
- numero_boleta: número o folio del documento (N°, Folio, Boleta N°, DTE). null si no hay.

Si NO es un recibo/comprobante de pago: {"es_recibo": false}

Responde SOLO con JSON válido, sin texto adicional."""

            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": img_data}},
                        {"type": "text", "text": prompt}
                    ]
                }]
            )
            raw = msg.content[0].text.strip()
            match = _re.search(r'\{.*\}', raw, _re.DOTALL)
            result = _json.loads(match.group() if match else raw)
            result.setdefault('modelo', 'claude-vision')
            print(f"  [claude-vision] OK")
            return result, 'claude-vision'
        except Exception as e:
            print(f"  [claude-vision] Error: {str(e)[:80]}")
            return None, None

    def _prepare_path(fpath):
        """Convierte PDF a imagen temporal (3x zoom) si es necesario. Retorna (path_a_usar, es_temporal)."""
        if fpath.lower().endswith('.pdf'):
            try:
                import fitz, hashlib, os as _os
                # Usar /tmp/gs_pdf_tmp para evitar problemas de permisos
                import os as _os2
                session_user = _os2.environ.get('USER', 'session')
                pdf_tmp_dir = f'/tmp/gs_pdf_tmp_{session_user}'
                _os.makedirs(pdf_tmp_dir, exist_ok=True)
                doc = fitz.open(fpath)
                page = doc[0]
                mat = fitz.Matrix(3.0, 3.0)  # 3x zoom for high-quality OCR/analysis
                pix = page.get_pixmap(matrix=mat)
                fname_base = hashlib.md5(fpath.encode()).hexdigest()
                tmp_path = f'{pdf_tmp_dir}/{fname_base}_p0.png'
                pix.save(tmp_path)
                doc.close()
                return tmp_path, True
            except Exception as e:
                print(f"  ⚠️  PDF→imagen falló: {e}")
                return fpath, False
        return fpath, False

    resultados = []
    for foto in fotos:
        fpath = foto['path']
        fname = foto['fname']
        h     = foto['hash']

        analyze_path, is_tmp = _prepare_path(fpath)
        # Claude vision nativo primero (sin API externa, sin créditos)
        try:
            result, modelo = _analyze_with_claude_vision(analyze_path)
        except Exception:
            result, modelo = None, None
        if not result:
            # Fallback: Gemini
            try:
                result, modelo = analyze_with_gemini(analyze_path)
            except Exception:
                result, modelo = None, None
        if not result:
            # Fallback final: Claude Haiku API
            try:
                result, modelo = analyze_with_claude(analyze_path)
            except Exception:
                result, modelo = None, None
        if not result:
            print(f"  ❌ {fname}: todos los métodos fallaron, se reintentará")
            if is_tmp and os.path.exists(analyze_path):
                os.remove(analyze_path)
            continue
        if is_tmp and os.path.exists(analyze_path):
            os.remove(analyze_path)

        resultado = {
            "path":              fpath,
            "fname":             fname,
            "hash":              h,
            "es_recibo":         result.get("es_recibo", False),
            "fecha":             result.get("fecha"),
            "comercio":          result.get("comercio", ""),
            "monto":             result.get("monto", 0),
            "moneda":            result.get("moneda", "CLP"),
            "categoria_sugerida": result.get("categoria_sugerida", "Otros"),
            "_modelo":           modelo,
            # Metadatos de email (si aplica)
            "source_type":       foto.get("source_type", "photo"),
            "email_id":          foto.get("email_id"),
            "email_from":        foto.get("email_from"),
            "email_subject":     foto.get("email_subject"),
        }
        estado = "✅" if resultado["es_recibo"] else "⏭️ "
        print(f"  {estado} [{modelo}] {fname}: {resultado['comercio']} ${resultado['monto']} {resultado['moneda']}")
        resultados.append(resultado)

    with open(RESULTADOS, 'w') as f:
        json.dump(resultados, f, ensure_ascii=False)

    recibos = sum(1 for r in resultados if r["es_recibo"])
    no_recibos = len(resultados) - recibos
    print(f"\n{'='*60}")
    print(f"STEP ANALYZE SUMMARY")
    print(f"{'='*60}")
    print(f"  Processed: {len(resultados)}")
    print(f"  Receipts (es_recibo=true): {recibos}")
    print(f"  Non-receipts: {no_recibos}")
    print(f"  Results saved: {RESULTADOS}")
    print(f"{'='*60}\n")

# ── STEP: reglas ──────────────────────────────────────────────────────────────
def step_reglas():
    conn = db_open()
    rows = conn.execute(
        "SELECT patron, comercio_limpio, categoria_fija, es_recibo_fijo FROM aprendizaje ORDER BY patron"
    ).fetchall()
    conn.close()
    if not rows:
        print("Sin reglas de aprendizaje.")
        return
    for patron, comercio, cat, es_recibo in rows:
        if es_recibo == 0:
            print(f"  - '{patron}' → NO es recibo (ignorar)")
        else:
            partes = []
            if comercio: partes.append(f"comercio='{comercio}'")
            if cat:      partes.append(f"categoría='{cat}'")
            print(f"  - '{patron}' → " + (", ".join(partes) if partes else "es recibo"))

# ── STEP: prepare ─────────────────────────────────────────────────────────────
def step_prepare():
    if not ICLOUD:
        print(json.dumps({"error": "iCloud path no encontrado", "fotos": []}))
        return

    # 1. Borrar UUID/no-IMG primero
    borrados_uuid = 0
    for ext in ['*.jpg','*.jpeg','*.png','*.heic','*.JPG','*.JPEG','*.PNG','*.HEIC']:
        for f in glob.glob(f"{ICLOUD}/{ext}"):
            if not os.path.basename(f).startswith('IMG_'):
                try:
                    os.remove(f)
                    borrados_uuid += 1
                except (OSError, PermissionError) as e:
                    print(f"  ⚠️  Could not delete {os.path.basename(f)}: {e}")
                    pass

    # 2. Listar IMG_* y deduplicar
    img_files = []
    for ext in ['*.jpg','*.jpeg','*.png','*.heic','*.JPG','*.JPEG','*.PNG','*.HEIC']:
        img_files += [f for f in glob.glob(f"{ICLOUD}/{ext}")
                      if os.path.basename(f).startswith('IMG_')]

    conn = db_open()

    fotos_nuevas = []
    saltadas_dup = 0
    saltadas_proceso = 0

    # Warn if more than 200 files exist (high-volume processing)
    MAX_PROCESS = 200
    if len(img_files) > MAX_PROCESS:
        print(f"⚠️  WARNING: {len(img_files)} image files found, processing {MAX_PROCESS}. Rest will be picked up on next run.")

    for fpath in img_files[:MAX_PROCESS]:
        fname = os.path.basename(fpath)
        try:
            h = sha256(fpath)
        except:
            continue

        row = conn.execute(
            "SELECT id, foto_url, sync_notion, es_recibo FROM gastos WHERE hash=?", (h,)
        ).fetchone()

        if row:
            es_procesado = row['sync_notion'] == 1 and (row['foto_url'] or not row['es_recibo'])
            if es_procesado:
                # Completada: borrar de iCloud
                try:
                    os.remove(fpath)
                except (OSError, PermissionError) as e:
                    print(f"    ⚠️  Could not delete {fname}: {e}")
                conn.execute("UPDATE gastos SET eliminada=1 WHERE hash=?", (h,))
                saltadas_dup += 1
            elif row['es_recibo'] and not row['foto_url']:
                # recibo con foto_url vacío: upload falló → agregar a fotos_nuevas para reintentar
                # Actualizar foto_path al path actual (puede ser de sesión VM distinta)
                conn.execute("UPDATE gastos SET foto_path=? WHERE hash=?", (fpath, h))
                conn.commit()
                fotos_nuevas.append({"path": fpath, "fname": fname, "hash": h})
                print(f"    🔁 {fname}: en BD sin foto_url — reintentando upload")
            else:
                saltadas_proceso += 1  # sync_notion pendiente, esperar
        else:
            fotos_nuevas.append({"path": fpath, "fname": fname, "hash": h})

    conn.commit()
    db_save(conn)

    # ── Agregar emails ──
    todos_archivos = fotos_nuevas.copy()
    emails_count = 0
    if ei:
        try:
            email_files = ei.get_email_files()
            # Validar que no sean duplicados por hash
            hashes_fotos = {f['hash'] for f in fotos_nuevas}
            conn2 = db_open()
            for email_file in email_files:
                if email_file['hash'] not in hashes_fotos:
                    # Solo saltar si ya está completamente procesado
                    # Recibo: necesita foto_url + sync_notion=1
                    # No-recibo: solo sync_notion=1 (nunca tiene foto_url)
                    row = conn2.execute(
                        "SELECT foto_url, sync_notion, es_recibo FROM gastos WHERE hash=?",
                        (email_file['hash'],)
                    ).fetchone()
                    if row and row['sync_notion'] == 1 and (row['foto_url'] or not row['es_recibo']):
                        pass  # ya procesado, no agregar
                    else:
                        todos_archivos.append(email_file)
                        emails_count += 1
            db_save(conn2)
        except Exception as e:
            print(f"⚠️  Email integration: {e}")

    resultado = {
        "fotos": todos_archivos,
        "borrados_uuid": borrados_uuid,
        "saltadas_dup": saltadas_dup,
        "saltadas_proceso": saltadas_proceso,
        "total_img": len(img_files),
        "emails_nuevos": emails_count
    }
    # Guardar para que --step analyze lo lea
    prepare_file = os.path.join(TEMP_DIR, 'gs_prepare.json')
    with open(prepare_file, 'w') as f:
        json.dump(resultado, f, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"STEP PREPARE SUMMARY")
    print(f"{'='*60}")
    print(f"  Total files in iCloud: {len(img_files)}")
    print(f"  Deleted (UUID/non-IMG): {borrados_uuid}")
    print(f"  Already processed (dup): {saltadas_dup}")
    print(f"  Pending processing: {saltadas_proceso}")
    print(f"  New to process: {len(fotos_nuevas)}")
    print(f"  Email attachments added: {emails_count}")
    print(f"  Total to analyze: {len(todos_archivos)}")
    print(f"  Manifest saved: {prepare_file}")
    print(f"{'='*60}\n")

# ── Upload Retry Queue ────────────────────────────────────────────────────────
def _add_to_upload_retry_queue(hash_val, fpath, fname, upload_path, dest):
    """Save failed upload to retry queue for later recovery."""
    retry_queue_file = '/tmp/gs_upload_retry.json'
    retry_items = []

    if os.path.exists(retry_queue_file):
        try:
            with open(retry_queue_file) as f:
                retry_items = json.load(f)
        except (json.JSONDecodeError, ValueError):
            retry_items = []

    # Add new item (avoid duplicates by hash)
    existing_hashes = {item.get('hash') for item in retry_items}
    if hash_val not in existing_hashes:
        retry_items.append({
            'hash': hash_val,
            'fpath': fpath,
            'fname': fname,
            'upload_path': upload_path,
            'dest': dest,
            'timestamp': time.time()
        })

        with open(retry_queue_file, 'w') as f:
            json.dump(retry_items, f, ensure_ascii=False, indent=2)
        print(f"    → Added to retry queue: {retry_queue_file}")

# ── STEP: upload ──────────────────────────────────────────────────────────────
def step_upload():
    if not os.path.exists(RESULTADOS):
        print(f"ERROR: No existe {RESULTADOS}")
        sys.exit(1)

    try:
        with open(RESULTADOS) as f:
            resultados = json.load(f)  # lista de dicts con análisis IA
    except (json.JSONDecodeError, ValueError) as e:
        print(f"ERROR: Invalid JSON in {RESULTADOS}: {e}")
        sys.exit(1)

    # ── Validar con gs_validate antes de procesar ──────────────────────────
    try:
        import gs_validate as _gsv
        errores_v, warnings_v, _ = _gsv.validar(resultados, fix=False)
        if warnings_v:
            for w in warnings_v:
                print(f"  ⚠️  validate: {w}")
        if errores_v:
            print(f"\n❌ VALIDACIÓN FALLÓ — {len(errores_v)} error(es) críticos:")
            for e in errores_v:
                print(f"   · {e}")
            print("   Corrige gs_resultados.json y vuelve a correr --step upload")
            sys.exit(1)
    except ImportError:
        pass  # gs_validate opcional

    import supabase_bridge as sb
    from fx_helper import convert_to_clp
    conn = db_open()
    subidas = 0
    errores = 0

    for item in resultados:
        h          = item['hash']
        fpath      = item['path']
        fname      = item['fname']
        es_recibo      = item.get('es_recibo', False)
        fecha          = item.get('fecha')
        comercio       = item.get('comercio', '')
        monto          = item.get('monto', 0)        # float si no es CLP
        moneda         = (item.get('moneda') or 'CLP').upper()
        categoria      = item.get('categoria_sugerida', '')
        numero_boleta  = item.get('numero_boleta')

        # Verificar que no exista ya completamente procesado
        existe = conn.execute("SELECT id, foto_url, sync_notion, es_recibo FROM gastos WHERE hash=?", (h,)).fetchone()
        if existe and existe['sync_notion'] == 1 and (existe['foto_url'] or not existe['es_recibo']):
            continue  # ya completo, saltar

        # Verificar duplicado lógico SIEMPRE (pasa numero_boleta para evitar falsos duplicados)
        estado_gasto = "Pendiente"
        if es_recibo:
            confianza, keeper_id = es_duplicado(conn, comercio, fecha, monto, moneda, categoria, numero_boleta=numero_boleta)
            if confianza == 'seguro':
                print(f"  ⏭️  {fname}: duplicado seguro de ID {keeper_id} — skip")
                continue
            elif confianza == 'probable':
                estado_gasto = "⚠️ Revisar duplicado"
                print(f"  ⚠️  {fname}: posible duplicado de ID {keeper_id} — entra con flag")

        # Calcular FX y monto_clp
        # CLP y BRL no tienen decimales → forzar entero. USD/EUR/otros → respetar decimales.
        MONEDAS_ENTERAS = {'CLP', 'BRL'}
        if moneda in MONEDAS_ENTERAS:
            monto = int(round(float(monto))) if monto else 0
        if moneda == 'CLP':
            monto_clp   = monto
            tipo_cambio = 1.0
        else:
            monto_clp, tipo_cambio = convert_to_clp(float(monto or 0), moneda, fecha)
            print(f"  💱 {moneda} {monto} → CLP {monto_clp} (fx={tipo_cambio})")

        # Subir foto a Supabase
        # Si es PDF → SIEMPRE subir PNG convertida (para que Notion muestre preview)
        foto_url = ''
        if es_recibo and os.path.exists(fpath):
            upload_path = fpath
            upload_fname = fname
            if fpath.lower().endswith('.pdf'):
                # Usar PNG de /tmp/gs_pdf_tmp/ (ya generada por _prepare_path con 3x zoom)
                import hashlib as _hashlib, os as _os3
                _session_user = _os3.environ.get('USER', 'session')
                _pdf_tmp_dir = f'/tmp/gs_pdf_tmp_{_session_user}'
                hash_md5 = _hashlib.md5(fpath.encode()).hexdigest()
                png_path = f'{_pdf_tmp_dir}/{hash_md5}_p0.png'
                if os.path.exists(png_path):
                    upload_path = png_path
                    upload_fname = fname.replace('.pdf', '.png')
                else:
                    # Convertir ahora si no existe
                    try:
                        import fitz
                        os.makedirs(_pdf_tmp_dir, exist_ok=True)
                        doc = fitz.open(fpath)
                        pix = doc[0].get_pixmap(matrix=fitz.Matrix(3.0, 3.0))
                        pix.save(png_path)
                        doc.close()
                        upload_path = png_path
                        upload_fname = fname.replace('.pdf', '.png')
                    except Exception as e:
                        print(f"  ⚠️  PDF→PNG error: {e} (subiendo PDF original)")
            dest = f"{h[:8]}_{int(time.time())}_{upload_fname}"
            try:
                foto_url = sb.upload_image(upload_path, dest) or ''
            except Exception as e:
                print(f"  ⚠️  Supabase error {fname}: {e}")
                # Add to retry queue instead of losing photo
                _add_to_upload_retry_queue(h, fpath, fname, upload_path, dest)

        # Solo insertar en BD si tenemos foto_url (para es_recibo=True)
        # o si es_recibo=False (para marcar y no procesar más)
        if es_recibo and not foto_url:
            print(f"  ❌ {fname}: sin foto_url — se agregó a retry queue")
            errores += 1
            continue

        # Obtener source_type y metadata de email si aplica
        source_type = item.get('source_type', 'photo')
        email_id = item.get('email_id')
        email_from = item.get('email_from')
        email_subject = item.get('email_subject')

        conn.execute("""INSERT OR IGNORE INTO gastos
            (hash, fecha, comercio, monto, moneda, categoria, estado, foto_path, foto_url,
             es_recibo, sync_notion, monto_original, moneda_original, monto_clp, tipo_cambio,
             source_type, email_id, email_from, email_subject, numero_boleta)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (h, fecha, comercio, monto, moneda, categoria,
             estado_gasto, fpath, foto_url,
             1 if es_recibo else 0,
             0,   # sync_notion=0 (pendiente)
             monto, moneda, monto_clp, tipo_cambio if moneda != 'CLP' else None,
             source_type, email_id, email_from, email_subject, numero_boleta))
        conn.commit()
        subidas += 1
        estado = "✅ recibo" if es_recibo else "⏭️  no-recibo"
        print(f"  {estado}: {fname} | {comercio} {moneda} {monto} → CLP {monto_clp}")

    db_save(conn)

    print(f"\n{'='*60}")
    print(f"STEP UPLOAD SUMMARY")
    print(f"{'='*60}")
    print(f"  Successfully uploaded: {subidas}")
    print(f"  Failed/retry queued: {errores}")
    print(f"  Retry queue: /tmp/gs_upload_retry.json")
    print(f"{'='*60}\n")

    # Reintentar uploads fallidos de sesiones anteriores
    try:
        import subprocess
        subprocess.run([sys.executable, os.path.join(BACKEND, 'retry_failed_uploads.py')],
                      check=False, timeout=120)
    except Exception as e:
        print(f"  ⚠️  retry_failed_uploads: {e}")

# ── STEP: sync ────────────────────────────────────────────────────────────────
def step_sync():
    from notion_bridge import sync_to_notion

    conn = db_open()
    # eliminada=1 solo significa que el archivo fuente fue limpiado de iCloud — el registro es válido.
    # Solo sync si: recibo con foto_url, o no-recibo
    rows = list(conn.execute("""
        SELECT * FROM gastos WHERE sync_notion=0
        AND (es_recibo=0 OR (es_recibo=1 AND foto_url IS NOT NULL AND foto_url!=''))
        ORDER BY id
    """).fetchall())

    synced = 0
    for row in rows:
        data = dict(row)
        # Mapear source_type → origen para Notion
        if not data.get("origen"):
            st = data.get("source_type", "photo")
            data["origen"] = "Gmail OWA" if st == "email" else "Foto iPhone"
        try:
            if sync_to_notion(data):
                conn.execute("UPDATE gastos SET sync_notion=1 WHERE id=?", (data['id'],))
                synced += 1
        except Exception as e:
            print(f"  ❌ ID {data['id']}: {e}")
    conn.commit()
    db_save(conn)

    print(f"\n{'='*60}")
    print(f"STEP SYNC SUMMARY")
    print(f"{'='*60}")
    print(f"  Synced to Notion: {synced}")
    print(f"  Total pending: {len(rows)}")
    print(f"{'='*60}\n")

# ── STEP: cleanup ─────────────────────────────────────────────────────────────
def step_cleanup():
    if not ICLOUD:
        print("iCloud path no encontrado")
        return

    conn = db_open()
    rows = conn.execute("""
        SELECT hash, foto_path FROM gastos
        WHERE sync_notion=1 AND (eliminada=0 OR eliminada IS NULL)
    """).fetchall()

    borradas = 0
    for hash_val, foto_path in rows:
        fname = os.path.basename(foto_path or '')
        if not fname: continue
        f = f"{ICLOUD}/{fname}"
        if os.path.exists(f):
            try:
                os.remove(f)
                borradas += 1
            except:
                pass
        conn.execute("UPDATE gastos SET eliminada=1 WHERE hash=?", (hash_val,))

    conn.commit()
    db_save(conn)

    print(f"\n{'='*60}")
    print(f"STEP CLEANUP SUMMARY")
    print(f"{'='*60}")
    print(f"  Photos deleted from iCloud: {borradas}")
    print(f"{'='*60}\n")

# ── STEP: dedup ──────────────────────────────────────────────────────────────
def step_dedup():
    """
    Detecta duplicados y los marca en BD con estado='Duplicado'.
    Notion se actualiza vía --step sync (no llamadas individuales).
    Reglas:
      - Si dos registros tienen numero_boleta distintos → NO son duplicados
      - Regla 1: mismo monto+moneda+fecha → duplicado (keeper = menor id)
      - Regla 2: similar comercio (>=80%) + mismo monto+moneda → duplicado
    """
    conn = db_open()

    # eliminada=1 solo significa archivo fuente limpiado, el registro sigue siendo válido
    rows = list(conn.execute("""
        SELECT id, fecha, comercio, monto, moneda, numero_boleta
        FROM gastos WHERE es_recibo=1
        ORDER BY id ASC
    """).fetchall())

    vistos = {}   # (fecha, monto, moneda) → {id, numero_boleta, comercio}
    duplicados = []  # (dup_id, keeper_id)

    for row in rows:
        moneda   = (row['moneda'] or 'CLP').upper()
        monto    = row['monto']
        fecha    = row['fecha']
        comercio = row['comercio'] or ''
        boleta   = row['numero_boleta']
        key      = (fecha, monto, moneda)

        # Regla 0: si ambos tienen numero_boleta distintos → NO es duplicado
        if key in vistos and boleta and vistos[key]['boleta'] and boleta != vistos[key]['boleta']:
            vistos[key] = {'id': row['id'], 'boleta': boleta, 'comercio': comercio}
            continue

        # Regla 1: mismo monto+moneda+fecha (solo si fecha no es None)
        if fecha and key in vistos:
            duplicados.append((row['id'], vistos[key]['id']))
            continue

        # Regla 2: similitud nombre (>=80%) + mismo monto+moneda+fecha
        # REQUIERE fecha: sin ella, dos visitas distintas al mismo comercio con mismo monto
        # en días diferentes serían colapsadas incorrectamente.
        encontrado = None
        if comercio and comercio != 'Desconocido' and fecha:
            for (f2, m2, mon2), v in vistos.items():
                if m2 == monto and mon2 == moneda and f2 == fecha:
                    if _similitud(comercio, v['comercio']) >= 0.80:
                        encontrado = v['id']
                        break
        if encontrado:
            duplicados.append((row['id'], encontrado))
            continue

        vistos[key] = {'id': row['id'], 'boleta': boleta, 'comercio': comercio}

    print(f"Duplicados encontrados: {len(duplicados)}")

    # Marcar en BD: estado='Duplicado', sync_notion=0 → --step sync actualiza Notion
    for dup_id, keeper_id in duplicados:
        row_info = conn.execute("SELECT comercio, fecha, monto FROM gastos WHERE id=?", (dup_id,)).fetchone()
        conn.execute(
            "UPDATE gastos SET estado='Duplicado', sync_notion=0 WHERE id=? AND estado != 'Duplicado'",
            (dup_id,)
        )
        if row_info:
            print(f"  🗑️  ID {dup_id} ({row_info['comercio']} {row_info['fecha']} ${row_info['monto']}) → keeper {keeper_id}")

    conn.commit()
    db_save(conn)

    print(f"\n{'='*60}")
    print(f"STEP DEDUP SUMMARY")
    print(f"{'='*60}")
    print(f"  Duplicados marcados en BD: {len(duplicados)}")
    print(f"  → Correr --step sync para actualizar Notion")
    print(f"{'='*60}\n")

# ── STEP: fix_fx ─────────────────────────────────────────────────────────────
def step_fix_fx():
    """Recalcula FX y monto_clp en registros con moneda extranjera que no lo tienen."""
    from fx_helper import convert_to_clp
    from notion_bridge import sync_to_notion

    conn = db_open()
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT id, fecha, comercio, monto_original, moneda_original, monto_clp, tipo_cambio
        FROM gastos
        WHERE moneda_original NOT IN ('CLP', '', 'clp')
          AND moneda_original IS NOT NULL
          AND (tipo_cambio IS NULL OR monto_clp IS NULL OR monto_clp = 0)
        ORDER BY id
    """).fetchall()

    print(f"Registros sin FX: {len(rows)}")
    actualizados = 0

    for row in rows:
        monto_orig  = float(row['monto_original'] or 0)
        moneda      = row['moneda_original'].upper()
        fecha       = row['fecha']

        if monto_orig == 0:
            print(f"  ⏭️  ID {row['id']} ({row['comercio']}): monto 0, skip")
            continue

        monto_clp, fx = convert_to_clp(monto_orig, moneda, fecha)
        conn.execute("""
            UPDATE gastos SET monto_clp=?, tipo_cambio=?, sync_notion=0
            WHERE id=?
        """, (monto_clp, fx, row['id']))
        print(f"  ✓ ID {row['id']} ({row['comercio']}): {moneda} {monto_orig} → CLP {monto_clp} (fx={fx})")
        actualizados += 1

    conn.commit()

    # Re-sync a Notion los actualizados (con foto_url)
    synced = 0
    if actualizados > 0:
        rows_sync = list(conn.execute("""
            SELECT * FROM gastos WHERE sync_notion=0
            AND (es_recibo=0 OR (es_recibo=1 AND foto_url IS NOT NULL AND foto_url!=''))
        """).fetchall())
        for row in rows_sync:
            data = dict(row)
            if sync_to_notion(data):
                conn.execute("UPDATE gastos SET sync_notion=1 WHERE id=?", (data['id'],))
                synced += 1
        conn.commit()

    db_save(conn)

    print(f"\n{'='*60}")
    print(f"STEP FIX_FX SUMMARY")
    print(f"{'='*60}")
    print(f"  FX rates calculated: {actualizados}")
    print(f"  Re-synced to Notion: {synced}")
    print(f"{'='*60}\n")

# ── Main ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--step', default='all',
                        choices=['all', 'prepare', 'analyze', 'upload', 'sync', 'cleanup', 'reglas', 'fix_fx', 'dedup'])
    args = parser.parse_args()

    if args.step == 'all':
        step_prepare()
        step_analyze()
        step_upload()
        step_sync()
        step_cleanup()
    elif args.step == 'prepare':   step_prepare()
    elif args.step == 'analyze':   step_analyze()
    elif args.step == 'upload':    step_upload()
    elif args.step == 'sync':      step_sync()
    elif args.step == 'cleanup':   step_cleanup()
    elif args.step == 'reglas':    step_reglas()
    elif args.step == 'fix_fx':    step_fix_fx()
    elif args.step == 'dedup':     step_dedup()
