import os
from supabase import create_client, Client
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BUCKET_NAME = "receipts"

# Validate Supabase configuration at import time
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError(
        "❌ Missing Supabase credentials. Set SUPABASE_URL and SUPABASE_KEY in .env"
    )

# Cache client to avoid recreating on every call
_supabase_client = None

def get_supabase_client() -> Client:
    """Get or create cached Supabase client."""
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_client

import re
import mimetypes

def sanitize_filename(name: str) -> str:
    """Sanitize filename for Supabase Storage.

    Removes/replaces special characters and accents with underscores.
    Accents are removed to ensure compatibility with storage systems
    that may have stricter character restrictions.
    """
    # Reemplazar espacios y caracteres problemáticos por guiones bajos
    clean_name = re.sub(r'[^a-zA-Z0-9._-]', '_', name)
    # Evitar múltiples guiones bajos seguidos
    clean_name = re.sub(r'_{2,}', '_', clean_name)
    return clean_name

def get_mime_type(file_path: str) -> str:
    """Detect MIME type from file extension.

    Args:
        file_path: Path to the file

    Returns:
        MIME type string (e.g., 'image/png', 'application/pdf')
        Defaults to 'application/octet-stream' if unknown.
    """
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or 'application/octet-stream'

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True
)
def _attempt_upload(file_path: str, destination_name: str):
    """Upload file to Supabase with automatic retries."""
    # Validate file exists before attempting upload
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    supabase = get_supabase_client()
    # Sanitizar el nombre para evitar InvalidKey en Supabase
    clean_dest = sanitize_filename(destination_name)

    # Detect MIME type from file extension
    content_type = get_mime_type(file_path)
    print(f"🔄 Uploading {file_path} → {clean_dest} (MIME: {content_type})")

    with open(file_path, 'rb') as f:
        supabase.storage.from_(BUCKET_NAME).upload(
            path=clean_dest,
            file=f,
            file_options={"content-type": content_type, "upsert": "true"}
        )
    return supabase.storage.from_(BUCKET_NAME).get_public_url(clean_dest)

def upload_image(file_path: str, destination_name: str):
    """Upload image to Supabase Storage with automatic retries.

    Args:
        file_path: Local path to the file to upload
        destination_name: Destination path in Supabase Storage

    Returns:
        Public URL if successful, None if all retries failed
    """
    try:
        return _attempt_upload(file_path, destination_name)
    except Exception as e:
        print(f"❌ Error crítico subiendo a Supabase tras 5 intentos: {e}")
        return None

def delete_image(filename: str):
    """Elimina una imagen de Supabase Storage."""
    try:
        supabase = get_supabase_client()
        supabase.storage.from_(BUCKET_NAME).remove([filename])
        return True
    except Exception as e:
        print(f"❌ Error eliminando de Supabase: {e}")
        return False
