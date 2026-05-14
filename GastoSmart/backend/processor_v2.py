import os
import json
import sqlite3
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
import google.generativeai as genai
from PIL import Image
from pillow_heif import register_heif_opener
from dotenv import load_dotenv
import time
import re
from anthropic import Anthropic

load_dotenv(override=True)
register_heif_opener()


def _load_aprendizaje_rules() -> str:
    """Carga reglas de categorización desde la BD de aprendizaje"""
    try:
        db_path = os.getenv("DB_PATH")
        if not db_path or not os.path.exists(db_path):
            return ""
        conn = sqlite3.connect(db_path, timeout=5)
        rows = conn.execute(
            "SELECT patron, comercio_limpio, categoria_fija FROM aprendizaje WHERE categoria_fija IS NOT NULL ORDER BY patron"
        ).fetchall()
        conn.close()
        if not rows:
            return ""
        lines = ["REGLAS FIJAS por comercio (aplica estas siempre):"]
        for patron, comercio, cat in rows:
            lines.append(f"  - Si el comercio contiene '{patron}' → Comercio='{comercio}', Categoría='{cat}'")
        return "\n".join(lines)
    except Exception:
        return ""

# ============================================================================
# CONFIGURACIÓN GEMINI — múltiples keys para rotación automática
# ============================================================================
_raw_keys = os.getenv("GEMINI_API_KEYS", os.getenv("GEMINI_API_KEY", ""))
GEMINI_API_KEYS = [k.strip() for k in _raw_keys.split(",") if k.strip()]
if not GEMINI_API_KEYS:
    raise ValueError("No hay GEMINI_API_KEYS en .env")

genai.configure(api_key=GEMINI_API_KEYS[0])

# Modelos Gemini en orden de preferencia (actualizados marzo 2026)
GEMINI_MODELS = [
    'gemini-2.5-flash-lite',   # default: más barato y rápido (Flash Lite 2.5)
    'gemini-2.5-flash',        # fallback: mejor precio/rendimiento
    'gemini-2.0-flash',        # fallback: backup hasta jun 2026
    'gemini-3-flash-preview',  # fallback: preview
    'gemini-2.5-pro',          # fallback: más capaz, último recurso
]

# ============================================================================
# CONFIGURACIÓN ANTHROPIC (Claude Haiku - Fallback)
# ============================================================================
def _get_anthropic_key():
    """Lee ANTHROPIC_API_KEY del .env directamente (más confiable que os.getenv)"""
    import pathlib
    from dotenv import dotenv_values
    env_file = pathlib.Path(__file__).parent / '.env'
    if env_file.exists():
        config = dotenv_values(str(env_file))
        key = config.get('ANTHROPIC_API_KEY')
        if key:
            return key
    return os.getenv("ANTHROPIC_API_KEY")

def _get_anthropic_client():
    key = _get_anthropic_key()
    return Anthropic(api_key=key) if key else None

# ============================================================================
# PROMPT UNIVERSAL (funciona para Gemini y Claude)
# ============================================================================
PROMPT_RECEIPT = """
Eres experto en análisis de recibos chilenos y del mundo.

════════════════════════════════════════════
PASO 1 — ¿ES UN RECIBO/COMPROBANTE DE PAGO?
════════════════════════════════════════════

SÍ ES RECIBO (es_recibo: true) — basta con VER CUALQUIERA de estos indicios:
✅ Papel impreso con números y texto de negocio (boleta, ticket, factura)
✅ Palabras clave: BOLETA, FACTURA, TICKET, TOTAL, IVA, NETO, RUT, SII, Timbre
✅ Lista de productos/servicios con precios
✅ Comprobante digital: app de banco, Mercado Pago, PayPal, Transbank, WebPay
✅ Recibo de delivery: Uber Eats, Rappi, PedidosYa, DiDi Food
✅ Comprobante de transporte: Uber, taxi, peaje, estacionamiento, bencina
✅ Recibo borroso, torcido, en mano, arrugado, con poca luz → IGUAL es recibo
✅ Screenshot parcial donde se ve al menos el comercio O el monto

NO ES RECIBO (es_recibo: false) — SOLO si claramente es una de estas:
❌ Foto de paisaje, edificio, persona, animal, comida sin comprobante
❌ Documento de identidad, pasaporte, licencia de conducir
❌ Cotización / presupuesto / lista de precios (sin pago realizado)
❌ Captura de redes sociales, chat, correo sin comprobante adjunto
❌ Contrato, carta, documento legal sin transacción monetaria
❌ Pantalla en blanco, foto sin contenido relevante

REGLA DE ORO: Si el documento muestra que SE REALIZÓ UN PAGO → es_recibo: true.
Si el documento muestra precios pero NO confirma un pago → es_recibo: false.

════════════════════════════════════════════
PASO 2 — EXTRACCIÓN (solo cuando es_recibo: true)
════════════════════════════════════════════

- fecha: ISO YYYY-MM-DD. Busca cualquier fecha visible. Sin año → usa 2026.
  Si NO hay ninguna fecha → usa null.

- comercio: Nombre del negocio en Title Case ("Better Food", "Farmacia Cruz Verde").
  Si el nombre no se puede leer claramente → usa "Desconocido".

- monto: TOTAL FINAL pagado, número entero.
  Formato CLP (punto = miles): "$ 7.975" → 7975 | "$ 3.590" → 3590
  Si no puedes leer el monto → usa 0.
  NUNCA pongas decimales en CLP.

- moneda: "CLP" por defecto. Usa "USD"/"EUR"/etc solo si lo ves explícito.

- categoria_sugerida (elige exactamente una de estas):
  * Viajes — transporte, uber, taxi, bencina, estacionamiento, peaje, pasajes, hotel
  * Representación — restaurante, café, bar, comida, delivery, entretención
  * Supermercado / Insumos — super, almacén, farmacia, papelería, ferretería
  * Cuentas — luz, agua, internet, teléfono, streaming, seguros, servicios básicos
  * Servicios Profesionales — médico, dentista, clínica, abogado, contador
  * Gastos Comunes — ACODEF, gastos comunes edificio, condominio
  * Inversiones — Fraccional, corretaje, comisiones de inversión
  * Software — suscripciones de software, licencias, apps, Claude, Notion, etc.
  * Otros — todo lo que no encaja arriba

- numero_boleta: número o folio del documento (ej: "12345", "N°4521", "DTE-33-00012345").
  Busca: "N°", "Folio", "Boleta N°", "N° Boleta", número después de "Boleta electrónica".
  Si no hay número visible → usa null.

IMPORTANTE: Si no puedes leer bien el comercio o el monto, IGUAL marca es_recibo: true
y usa "Desconocido" / 0. El usuario podrá corregir. NO marques false por imagen borrosa.

RESPONDE SOLO JSON válido (sin markdown, sin texto extra):
{"es_recibo": true, "fecha": "YYYY-MM-DD", "comercio": "Nombre", "monto": 1234, "moneda": "CLP", "categoria_sugerida": "Categoría", "numero_boleta": "12345"}
o si NO es recibo:
{"es_recibo": false, "fecha": null, "comercio": "", "monto": 0, "moneda": "", "categoria_sugerida": "", "numero_boleta": null}
"""

def _build_prompt():
    """Construye el prompt con reglas de aprendizaje dinámicas"""
    rules = _load_aprendizaje_rules()
    if rules:
        return PROMPT_RECEIPT + f"\n{rules}\n"
    return PROMPT_RECEIPT

# Estado persistente — NOTA DE THREAD-SAFETY
# _healthy_gemini_idx, _model_cooldowns, _key_cooldowns son variables globales mutables.
# En un ambiente multi-threading, deben protegerse con locks si se espera concurrencia.
# Por ahora, se asume ejecución secuencial (watcher procesa imágenes una a una).
_healthy_gemini_idx = 0
_model_cooldowns = {}   # model_name → cooldown_until (404s)
_key_cooldowns = {}     # key_index → cooldown_until (quota por key)
COOLDOWN_SECONDS = 300

# Archivo de estado para el dashboard
import pathlib
_STATUS_FILE = pathlib.Path(__file__).parent / ".model_status.json"

def _save_model_status(last_used=None):
    """Escribe el estado actual de cada modelo/key al archivo de status."""
    try:
        now = time.time()
        status = {}
        # Gemini: un modelo está OK si al menos 1 key no está en cooldown
        for m in GEMINI_MODELS:
            # Cooldown 404
            if _model_cooldowns.get(m, 0) > now:
                status[m] = {"ok": False, "until": _model_cooldowns[m]}
                continue
            # Verificar si hay alguna key disponible
            keys_ok = sum(1 for ki in range(len(GEMINI_API_KEYS)) if _key_cooldowns.get(ki, 0) <= now)
            status[m] = {"ok": keys_ok > 0, "until": 0,
                         "keys_ok": keys_ok, "keys_total": len(GEMINI_API_KEYS)}
        # OpenAI
        until = _model_cooldowns.get("gpt-4o-mini", 0)
        status["gpt-4o-mini"] = {"ok": until <= now, "until": until if until > now else 0}
        if last_used:
            status["_last_used"] = last_used
        status["_updated"] = now
        with open(_STATUS_FILE, "w") as f:
            json.dump(status, f)
    except Exception:
        pass

def _log_attempt(model_name, status, msg=""):
    dt = time.strftime("%H:%M:%S")
    print(f"[{dt}]\t            \t[{model_name[:10]}]\t{status} {msg}", flush=True)

# ============================================================================
# ANÁLISIS CON GEMINI
# ============================================================================
def analyze_with_gemini(image_path: str):
    """Intenta análisis con Gemini — rota modelos Y keys automáticamente."""
    global _healthy_gemini_idx

    for mi in range(len(GEMINI_MODELS)):
        model_name = GEMINI_MODELS[(_healthy_gemini_idx + mi) % len(GEMINI_MODELS)]

        # Modelo en cooldown por 404
        if _model_cooldowns.get(model_name, 0) > time.time():
            continue

        # Intentar con cada key
        for ki, api_key in enumerate(GEMINI_API_KEYS):
            if _key_cooldowns.get(ki, 0) > time.time():
                continue  # esta key está saturada

            try:
                genai.configure(api_key=api_key)
                model = genai.GenerativeModel(model_name)
                with Image.open(image_path) as img:
                    # Verificar integridad de imagen antes de enviar
                    try:
                        img.verify()
                    except Exception:
                        # verify() puede lanzar exception en algunos formatos, es OK
                        pass

                # Timeout de 30 segundos en llamada a Gemini
                try:
                    response = model.generate_content([_build_prompt(), img], timeout=30)
                except TypeError:
                    # Fallback si timeout no es soportado
                    response = model.generate_content([_build_prompt(), img])

                _healthy_gemini_idx = (_healthy_gemini_idx + mi) % len(GEMINI_MODELS)
                raw_text = response.text.strip()

                # Mejorar extracción JSON: buscar PRIMER objeto JSON completo
                json_match = re.search(r'\{(?:[^{}]|(?:\{[^{}]*\}))*\}', raw_text, re.DOTALL)
                result = json.loads(json_match.group() if json_match else raw_text)
                result["_model_id"] = model_name
                return result, "gemini"

            except Exception as e:
                err_msg = str(e).lower()

                if any(x in err_msg for x in ["429", "quota", "resource_exhausted", "rate"]):
                    _key_cooldowns[ki] = time.time() + COOLDOWN_SECONDS
                    _log_attempt(model_name, "QUOTA", f"key#{ki+1}/{len(GEMINI_API_KEYS)} saturada")
                    _save_model_status()
                    continue  # probar siguiente key

                # Verificar 404 de HTTP con word boundary (no en nombre de archivo)
                is_http_404 = (
                    re.search(r'\b404\b', str(e)) is not None or
                    "not found" in err_msg
                ) and not isinstance(e, (IOError, OSError))
                if is_http_404:
                    _model_cooldowns[model_name] = time.time() + 3600
                    break  # modelo no existe, pasar al siguiente

                _log_attempt(model_name, "ERROR", str(e)[:50])
                return None, None

    return None, None

# ============================================================================
# ANÁLISIS CON CLAUDE HAIKU (Fallback)
# ============================================================================
def analyze_with_claude(image_path: str):
    """Fallback: Claude Haiku cuando Gemini está saturado"""

    anthropic_client = _get_anthropic_client()
    if not anthropic_client:
        return None, None

    try:
        import base64, io
        ext = image_path.lower().split('.')[-1]

        MAX_BYTES = 4 * 1024 * 1024  # 4MB límite Claude

        # HEIC o imágenes grandes → convertir/comprimir a JPEG
        if ext == 'heic' or os.path.getsize(image_path) > MAX_BYTES:
            with Image.open(image_path) as img:
                # Verificar integridad de imagen
                try:
                    img.verify()
                except Exception:
                    pass
                img = img.convert('RGB')
                # Reducir si es muy grande
                if max(img.size) > 2048:
                    img.thumbnail((2048, 2048), Image.LANCZOS)
                buf = io.BytesIO()
                quality = 85
                img.save(buf, format='JPEG', quality=quality)
                # Si sigue grande, bajar calidad
                while buf.tell() > MAX_BYTES and quality > 40:
                    quality -= 15
                    buf = io.BytesIO()
                    img.save(buf, format='JPEG', quality=quality)
                image_data = base64.standard_b64encode(buf.getvalue()).decode('utf-8')
            media_type = 'image/jpeg'
        else:
            with Image.open(image_path) as img:
                try:
                    img.verify()
                except Exception:
                    pass
            with open(image_path, 'rb') as f:
                image_data = base64.standard_b64encode(f.read()).decode('utf-8')
            media_type_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png'}
            media_type = media_type_map.get(ext, 'image/jpeg')

        # Llamar a Claude Haiku con timeout de 30 segundos
        try:
            response = anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                timeout=30,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_data,
                                },
                            },
                            {
                                "type": "text",
                                "text": _build_prompt()
                            }
                        ],
                    }
                ],
            )
        except TypeError:
            # Fallback si timeout no es soportado en esta versión
            response = anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_data,
                                },
                            },
                            {
                                "type": "text",
                                "text": _build_prompt()
                            }
                        ],
                    }
                ],
            )

        raw_text = response.content[0].text.strip()
        # Mejorar extracción JSON: buscar PRIMER objeto JSON completo
        json_match = re.search(r'\{(?:[^{}]|(?:\{[^{}]*\}))*\}', raw_text, re.DOTALL)

        if json_match:
            result = json.loads(json_match.group())
        else:
            result = json.loads(raw_text)

        dt = time.strftime("%H:%M:%S")
        print(f"[{dt}]\t            \t[CLAUDE]\t✓ Análisis con Claude Haiku", flush=True)

        return result, "claude"

    except Exception as e:
        dt = time.strftime("%H:%M:%S")
        print(f"[{dt}]\t            \t[CLAUDE]\t✗ {str(e)[:200]}", flush=True)
        return None, None

# ============================================================================
# ANÁLISIS CON OPENAI GPT-4o-mini (Fallback 2)
# ============================================================================
def _get_openai_key():
    import pathlib
    from dotenv import dotenv_values
    env_file = pathlib.Path(__file__).parent / '.env'
    if env_file.exists():
        config = dotenv_values(str(env_file))
        key = config.get('OPENAI_API_KEY')
        if key:
            return key
    return os.getenv("OPENAI_API_KEY")

def analyze_with_openai(image_path: str):
    """Fallback 2: GPT-4o-mini cuando Gemini y Claude fallan."""
    key = _get_openai_key()
    if not key:
        return None, None

    try:
        from openai import OpenAI
        import base64, io

        client = OpenAI(api_key=key, timeout=30)
        ext = image_path.lower().split('.')[-1]
        MAX_BYTES = 4 * 1024 * 1024

        if ext == 'heic' or os.path.getsize(image_path) > MAX_BYTES:
            with Image.open(image_path) as img:
                # Verificar integridad de imagen
                try:
                    img.verify()
                except Exception:
                    pass
                img = img.convert('RGB')
                if max(img.size) > 2048:
                    img.thumbnail((2048, 2048), Image.LANCZOS)
                buf = io.BytesIO()
                quality = 85
                img.save(buf, format='JPEG', quality=quality)
                while buf.tell() > MAX_BYTES and quality > 40:
                    quality -= 15
                    buf = io.BytesIO()
                    img.save(buf, format='JPEG', quality=quality)
                image_b64 = base64.standard_b64encode(buf.getvalue()).decode('utf-8')
        else:
            with Image.open(image_path) as img:
                try:
                    img.verify()
                except Exception:
                    pass
            with open(image_path, 'rb') as f:
                image_b64 = base64.standard_b64encode(f.read()).decode('utf-8')

        media_type_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'heic': 'image/jpeg'}
        media_type = media_type_map.get(ext, 'image/jpeg')

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=500,
            timeout=30,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{image_b64}"}
                        },
                        {
                            "type": "text",
                            "text": _build_prompt()
                        }
                    ]
                }
            ]
        )

        raw_text = response.choices[0].message.content.strip()
        # Mejorar extracción JSON: buscar PRIMER objeto JSON completo
        json_match = re.search(r'\{(?:[^{}]|(?:\{[^{}]*\}))*\}', raw_text, re.DOTALL)
        result = json.loads(json_match.group() if json_match else raw_text)

        dt = time.strftime("%H:%M:%S")
        print(f"[{dt}]\t            \t[OPENAI]\t✓ Análisis con GPT-4o-mini", flush=True)
        return result, "openai"

    except Exception as e:
        dt = time.strftime("%H:%M:%S")
        print(f"[{dt}]\t            \t[OPENAI]\t✗ {str(e)[:200]}", flush=True)
        return None, None

# ============================================================================
# NORMALIZACIÓN DE NOMBRES DE COMERCIO
# ============================================================================
_MINUSCULAS = {"de", "del", "la", "las", "los", "el", "y", "a", "en", "por"}

def _clean_comercio(nombre: str) -> str:
    """Convierte STARBUCKS → Starbucks, JUMBO LAS CONDES → Jumbo Las Condes"""
    if not nombre:
        return nombre
    palabras = nombre.strip().split()
    resultado = []
    for i, p in enumerate(palabras):
        if i > 0 and p.lower() in _MINUSCULAS:
            resultado.append(p.lower())
        else:
            resultado.append(p.capitalize())
    return " ".join(resultado)

# ============================================================================
# FUNCIÓN PRINCIPAL: ANALYZE_RECEIPT
# ============================================================================
def _pdf_to_image(pdf_path: str) -> str:
    """Convierte primera página de PDF a imagen temporal PNG. Retorna path.

    Nota: IMPORTANTE desde CLAUDE.md — usar zoom 3.0x (no 2.0x) para mejor calidad
    y precisión en OCR de recibos pequeños.
    """
    try:
        import fitz  # pymupdf
        doc = fitz.open(pdf_path)
        page = doc[0]
        mat = fitz.Matrix(3.0, 3.0)  # 3x zoom para mejor calidad (CLAUDE.md rule)
        pix = page.get_pixmap(matrix=mat)
        tmp_path = pdf_path.replace('.pdf', '_page0.jpg')
        pix.save(tmp_path)
        doc.close()
        return tmp_path
    except Exception as e:
        raise RuntimeError(f"No se pudo convertir PDF: {e}")

def analyze_receipt(image_path: str):
    """Analiza con Gemini, fallback a Claude Haiku. Devuelve resultado + modelo usado."""

    # Si es PDF, convertir a imagen primero
    actual_path = image_path
    if image_path.lower().endswith('.pdf'):
        try:
            actual_path = _pdf_to_image(image_path)
        except Exception as e:
            return {"es_recibo": False, "error": f"PDF no procesable: {str(e)}", "modelo": "N/A"}, "N/A"

    # Validar imagen
    try:
        with Image.open(actual_path) as test_img:
            test_img.load()
    except Exception as e:
        return {"es_recibo": False, "error": f"Archivo no listo: {str(e)}", "modelo": "N/A"}, "N/A"

    if not os.path.exists(actual_path):
        return {"es_recibo": False, "error": "Archivo no encontrado", "modelo": "N/A"}, "N/A"

    # Mapa model_id → label exacto del dashboard
    _MODEL_LABELS = {
        "gemini-3-flash-preview":  "Gemini 3 Flash",
        "gemini-2.5-flash":        "Gemini 2.5 Flash",
        "gemini-2.5-flash-lite":   "Gemini 2.5 Flash-Lite",
        "gemini-2.0-flash":        "Gemini 2.0 Flash",
        "gemini-2.5-pro":          "Gemini 2.5 Pro",
    }

    # Intenta 1: Claude Haiku (primario)
    result, source = analyze_with_claude(actual_path)
    if result:
        result["modelo"] = "Claude Haiku"
        result["comercio"] = _clean_comercio(result.get("comercio", ""))
        _save_model_status(last_used="Claude Haiku")
        return result, "Claude Haiku"

    # Intenta 2: Gemini (fallback)
    result, source = analyze_with_gemini(actual_path)
    if result:
        model_id = result.pop("_model_id", "gemini")
        model_label = _MODEL_LABELS.get(model_id, model_id)
        result["modelo"] = model_label
        result["comercio"] = _clean_comercio(result.get("comercio", ""))
        _save_model_status(last_used=model_label)
        return result, model_label

    # Falló todo — se reintentará en el próximo ciclo
    return {"es_recibo": False, "error": "Claude y Gemini sin quota disponible", "modelo": "N/A"}, "N/A"
