import os
import json
import google.generativeai as genai
from PIL import Image
from pillow_heif import register_heif_opener
from dotenv import load_dotenv
import time
import re

load_dotenv()
register_heif_opener()

# Configuración de Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

# Nombres oficiales de modelos: prioridad inteligente con fallback a Haiku
MODELS = [
    'gemini-2.0-flash',       # Preferido: rápido y preciso
    'gemini-1.5-flash',       # Alternativa confiable
    'gemini-1.5-flash-8b',    # Alternativa rápida
    'gemini-2.0-flash-lite-preview-02-05', # Alternativa ligera
    'gemini-1.5-pro',         # Fallback pro (cuota compartida)
    'gemini-1.5-pro-002',     # Fallback pro alternativo
    'gemini-2.0-pro-exp-02-05', # Experimental (si está disponible)
    'gemini-1.5-pro-vision',  # Pro con visión optimizada
]

# Modelos de fallback económico cuando todo lo anterior está agotado
FALLBACK_MODELS = [
    'gemini-2.0-flash-thinking-exp-1219',  # Experimental thinking (si disponible)
    'gemini-exp-1121',                      # Experimental
]

# Estado persistente para no reintentar modelos agotados en cada ticket
_healthy_model_idx = 0
_model_cooldowns = {} # model_name -> timestamp_until
COOLDOWN_SECONDS = 300 # 5 minutos de descanso por modelo agotado

def _get_next_model():
    return MODELS[_healthy_model_idx % len(MODELS)]

def _rotate_model():
    global _healthy_model_idx
    _healthy_model_idx = (_healthy_model_idx + 1) % len(MODELS)

PROMPT_RECEIPT = """
Analiza esta imagen como un experto contable y auditor de gastos. 
Determina si es un COMPROBANTE DE GASTO legítimo (recibo, boleta, factura, o confirmación de pago digital).

REGLAS DE CLASIFICACIÓN:
1. RESPONDE {"es_recibo": true} SI:
   - Es un TICKET FÍSICO o VOUCHER de tarjeta.
   - Es una CAPTURA de un pago finalizado (Uber, Rappi, etc). 
   - Debe mostrar el nombre del comercio y el MONTO TOTAL final.

2. RESPONDE {"es_recibo": false} SI:
   - Es una TRANSFERENCIA bancaria (TEF/Swift).
   - Es basura, fotos personales o documentos sin montos.

Si es recibo (es_recibo: true), extrae con máxima precisión:
- fecha: ISO format (YYYY-MM-DD). Usa 2026 si no tiene año.
- comercio: Nombre del local en formato Capitalizado (ej: "Starbucks", "Mc Donalds"). NUNCA todo en mayúsculas ni todo en minúsculas.
  *IMPORTANTE*: Si el comercio es "Starbucks", la categoría SIEMPRE debe ser "Representación".
- monto: El TOTAL FINAL pagado por el cliente. 
  *ATENCIÓN*: Si hay PROPINA (TIP), el monto debe ser la suma de (Consumo + Propina). No confundas el Subtotal o el IVA con el Total.
- moneda: Código ISO (CLP, USD, BRL, EUR).
- categoria_sugerida: Debe ser una de estas exactamente:
  * Viajes (Ej: estacionamientos, pasajes de hotel, bencina)
  * Representación (Ej: Restaurantes, Starbucks, cafés)
  * Supermercado / Insumos (Ej: Supermercados, compras de oficina)
  * Cuentas (Ej: luz, agua, gas, internet)
  * Servicios Profesionales (Ej: boletas de honorarios, profesionales)
  * Otros (Si no calza en ninguna anterior)

Responde ÚNICAMENTE en formato JSON plano, sin markdown.
"""

def analyze_receipt(image_path: str, retries=None):
    """Analiza una imagen con rotación de modelos y fallback robusto."""
    global _healthy_model_idx

    if retries is None:
        retries = len(MODELS) + len(FALLBACK_MODELS)

    # Validar que la imagen sea legible por PIL antes de intentar con la IA
    try:
        with Image.open(image_path) as test_img:
            test_img.load()
    except Exception as e:
        return {"es_recibo": False, "error": f"Archivo no listo o corrupto: {str(e)}"}

    # Combinar modelos: principales + fallback
    all_models = MODELS + FALLBACK_MODELS

    # Intentar desde el último modelo que funcionó
    for i in range(len(all_models)):
        current_idx = (_healthy_model_idx + i) % len(all_models)
        model_name = all_models[current_idx]

        # Verificar período de descanso
        if model_name in _model_cooldowns:
            if time.time() < _model_cooldowns[model_name]:
                continue
            else:
                _model_cooldowns.pop(model_name, None)

        try:
            if not os.path.exists(image_path):
                return {"es_recibo": False, "error": "Archivo no encontrado"}

            # Intentar con el modelo
            model = genai.GenerativeModel(model_name)
            with Image.open(image_path) as img:
                response = model.generate_content([PROMPT_RECEIPT, img])

            # Éxito: registrar como modelo sano
            _healthy_model_idx = current_idx
            raw_text = response.text.strip()

            # Extraer JSON robustamente
            json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            else:
                return json.loads(raw_text)

        except Exception as e:
            err_msg = str(e).lower()

            # Detectar saturación de cuota
            if any(x in err_msg for x in ["429", "quota", "resource_exhausted", "limit", "rate", "too many requests"]):
                _model_cooldowns[model_name] = time.time() + COOLDOWN_SECONDS
                dt = time.strftime("%H:%M:%S")
                is_fallback = model_name in FALLBACK_MODELS
                prefix = "[FALLBACK]" if is_fallback else "[CUOTA]"
                print(f"[{dt}]\t            \t{prefix}     \t{model_name} agotado. Entra en descanso 5 min.", flush=True)
                continue

            # Modelo no existe o no disponible
            if "404" in err_msg or "not found" in err_msg or "not available" in err_msg:
                _model_cooldowns[model_name] = time.time() + 3600
                continue

            # Error real (no cuota)
            dt = time.strftime("%H:%M:%S")
            print(f"[{dt}]\t            \t[ERROR]     \t{model_name}: {str(e)[:80]}", flush=True)
            return {"es_recibo": False, "error": str(e)}

    return {"es_recibo": False, "error": "Todos los modelos saturados. Sistema en espera."}
