import os
import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

def debug_notion():
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}"
    res = requests.get(url, headers=headers)
    if res.status_code == 200:
        props = res.json().get("properties", {})
        print("COLUMNAS ENCONTRADAS EN NOTION:")
        for name, details in props.items():
            print(f" - {name} ({details['type']})")
    else:
        print(f"ERROR {res.status_code}: {res.text}")

if __name__ == "__main__":
    debug_notion()
