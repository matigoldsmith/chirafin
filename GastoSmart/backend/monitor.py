import os
import time
import shutil
import collections
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

from rich.live import Live
from rich.text import Text

BASE_DIR = "/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/backend"
load_dotenv(os.path.join(BASE_DIR, ".env"))

DB_PATH = os.path.join(BASE_DIR, "gastosmart_v1.db")
LOG_PATH = os.path.join(BASE_DIR, "watcher.log")
ICLOUD_PATH = os.getenv("ICLOUD_INPUT_PATH", os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/GastoSmart"))

def get_stats():
    stats = {}
    try:
        if os.path.exists(ICLOUD_PATH):
            stats['icloud'] = len([f for f in os.listdir(ICLOUD_PATH) if not f.startswith('.') and f.lower().endswith(('.png', '.jpg', '.jpeg', '.pdf', '.heic'))])
        else:
            stats['icloud'] = 0

        conn = sqlite3.connect(DB_PATH)
        curr = conn.cursor()

        curr.execute("SELECT COUNT(*) FROM gastos")
        stats['db_total'] = curr.fetchone()[0]

        curr.execute("SELECT COUNT(*) FROM gastos WHERE estado IN ('Analizando...', 'Error AI', 'Pendiente')")
        stats['ia_pending'] = curr.fetchone()[0]

        curr.execute("SELECT COUNT(*) FROM gastos WHERE estado NOT IN ('Analizando...', 'Error AI', 'Pendiente')")
        stats['processed_total'] = curr.fetchone()[0]

        curr.execute("SELECT COUNT(*) FROM gastos WHERE sync_notion = 1")
        stats['synced_notion'] = curr.fetchone()[0]

        conn.close()

        stats['quota'] = "OK"
        stats['heartbeat'] = "???"
        stats['last_ok'] = None
        stats['quota_since'] = None

        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, 'r') as f:
                lines = list(collections.deque(f, 50))

            recent_text = "".join(lines[-10:]).lower()
            if "quota" in recent_text or "saturad" in recent_text:
                stats['quota'] = "SATURADA"
                for line in reversed(lines[-30:]):
                    if ('quota' in line.lower() or 'saturad' in line.lower()) and '[' in line:
                        stats['quota_since'] = line.split(']')[0].replace('[', '')
                        break

            for line in reversed(lines):
                if ('ok: notion ok' in line.lower() or '[ignorado]' in line.lower() or 'verificado' in line.lower()) and '[' in line:
                    stats['last_ok'] = line.split(']')[0].replace('[', '')
                    break

            for line in reversed(lines):
                if '[' in line and ']' in line:
                    stats['heartbeat'] = line.split(']')[0].replace('[', '')
                    break

    except Exception as e:
        stats['error'] = str(e)
    return stats


def render():
    s = get_stats()
    now = datetime.now().strftime('%H:%M:%S')

    if 'error' in s:
        return Text(f"ERROR: {s['error']}", style="red")

    ic = s.get('icloud', 0)
    ia = s.get('ia_pending', 0)
    proc = s.get('processed_total', 0)
    db_total = s.get('db_total', 0)
    sync = s.get('synced_notion', 0)
    sync_p = max(0, proc - sync)
    universe = ic + db_total
    pct = round(db_total / universe * 100, 1) if universe > 0 else 0
    safe = (ic == 0 and ia == 0 and sync_p == 0)

    out = Text()
    out.append(f"GASTOSMART  {now}\n", style="bold")
    out.append("─" * 44 + "\n")

    if safe:
        out.append("STATUS  OK — NOTION SEGURO\n", style="green")
    else:
        out.append("STATUS  TRABAJANDO\n", style="red")

    out.append(f"\nPROGRESO  {db_total}/{universe}  ({pct}%)\n")
    out.append(f"  iCloud   {ic} pendientes\n")
    out.append(f"  IA       {proc} listos  /  {ia} en cola\n")
    out.append(f"  Notion   {sync} / {proc} sync\n")

    out.append("\nSALUD\n")
    q_style = "green" if s.get('quota') == "OK" else "red"
    quota_str = s.get('quota', '?')
    if s.get('quota_since'):
        quota_str += f"  (desde {s['quota_since']})"
    out.append(f"  Gemini   {quota_str}\n", style=q_style)
    if s.get('last_ok'):
        out.append(f"  Ult.OK   {s['last_ok']}\n")
    out.append(f"  Log      {s.get('heartbeat')}\n")

    out.append("\nLOGS\n")
    if os.path.exists(LOG_PATH):
        try:
            term_h = shutil.get_terminal_size((80, 40)).lines
            log_lines = max(10, term_h - 18)
            with open(LOG_PATH, 'r') as f:
                for line in collections.deque(f, log_lines):
                    clean = line.strip()
                    style = "dim"
                    if any(x in clean.lower() for x in ["ok:", "✅", "aprendidas", "sincronizado"]):
                        style = "green"
                    if any(x in clean.lower() for x in ["error", "falló", "fatal"]):
                        style = "red"
                    if any(x in clean.lower() for x in ["quota", "saturad"]):
                        style = "yellow"
                    out.append(clean + "\n", style=style)
        except:
            pass

    return out


if __name__ == "__main__":
    with Live(render(), refresh_per_second=1, screen=True) as live:
        try:
            while True:
                live.update(render())
                time.sleep(2)
        except KeyboardInterrupt:
            pass
