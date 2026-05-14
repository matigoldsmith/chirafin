import os
import hashlib
import sqlite3
import json
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from processor import analyze_receipt
from airtable_bridge import sync_to_airtable
from PIL import Image
import io

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "gastosmart_v1.db")
UPLOADS_DIR = os.path.join(os.path.dirname(BASE_DIR), "database_files", "receipts")

os.makedirs(UPLOADS_DIR, exist_ok=True)

app = FastAPI(title="GastoSmart Catalog Pro")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gastos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hash TEXT UNIQUE NOT NULL,
            fecha TEXT,
            comercio TEXT,
            monto REAL,
            moneda TEXT DEFAULT 'CLP',
            categoria TEXT,
            estado TEXT DEFAULT 'Pendiente',
            es_recibo INTEGER DEFAULT 1,
            foto_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS aprendizaje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patron TEXT UNIQUE NOT NULL,
            comercio_limpio TEXT NOT NULL,
            categoria_fija TEXT
        )
    ''')
    
    # Migración: Agregar es_recibo si no existe
    try:
        cursor.execute("ALTER TABLE gastos ADD COLUMN es_recibo INTEGER DEFAULT 1")
    except:
        pass
        
    conn.commit()
    conn.close()

# Inicializar siempre al arrancar
init_db()

class GastoConfirm(BaseModel):
    fecha: str
    comercio: str
    monto: float
    moneda: str
    categoria: str
    estado: Optional[str] = "Confirmado"

@app.get("/")
def read_root():
    return {"status": "GastoSmart Local Running", "db": DB_PATH}

@app.get("/pending")
def list_pending():
    conn = get_db()
    # Sin límite estricto para pendientes ya que suelen ser pocos
    rows = conn.execute("SELECT * FROM gastos WHERE estado = 'Pendiente' ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/ignored")
def list_ignored():
    conn = get_db()
    rows = conn.execute("SELECT * FROM gastos WHERE estado = 'Ignorado' ORDER BY created_at DESC LIMIT 100").fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/history")
def list_history():
    conn = get_db()
    rows = conn.execute("SELECT * FROM gastos WHERE estado = 'Confirmado' ORDER BY fecha DESC LIMIT 100").fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/all")
def list_all():
    conn = get_db()
    rows = conn.execute("SELECT * FROM gastos ORDER BY created_at DESC LIMIT 200").fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/receipt/{filename}")
def get_receipt(filename: str):
    path = os.path.join(UPLOADS_DIR, filename)
    if not os.path.exists(path):
        conn = get_db()
        res = conn.execute("SELECT foto_path FROM gastos WHERE foto_path LIKE ?", (f"%{filename}",)).fetchone()
        conn.close()
        if res:
            path = res['foto_path']
        else:
            raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(path)

@app.post("/confirm/{record_id}")
def confirm_expense(record_id: int, data: GastoConfirm):
    conn = get_db()
    # 1. Actualización en la DB Local
    conn.execute('''
        UPDATE gastos 
        SET comercio = ?, fecha = ?, monto = ?, moneda = ?, categoria = ?, estado = ?
        WHERE id = ?
    ''', (data.comercio, data.fecha, data.monto, data.moneda, data.categoria, data.estado, record_id))
    
    # 2. Aprendizaje Local
    if data.comercio and data.estado == "Confirmado":
        conn.execute('''
            INSERT OR REPLACE INTO aprendizaje (patron, comercio_limpio, categoria_fija)
            VALUES (?, ?, ?)
        ''', (data.comercio.lower(), data.comercio, data.categoria))
        
    conn.commit()
    conn.close()
    
    # 3. Optional Cloud Sync
    gasto_data = {"id": record_id, **data.dict()}
    sync_to_airtable(gasto_data)
    
    return {"status": "confirmed"}

@app.get("/stats")
def get_stats():
    conn = get_db()
    stats = conn.execute("SELECT estado, COUNT(*) as total FROM gastos GROUP BY estado").fetchall()
    conn.close()
    return {row['estado']: row['total'] for row in stats}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
