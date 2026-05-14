import os
import requests
from dotenv import load_dotenv

load_dotenv()
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def check():
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    res = requests.post(url, headers=HEADERS, json={"page_size": 1})
    if res.status_code == 200:
        pages = res.json().get("results", [])
        print(f"Páginas encontradas: {len(pages)}")
    else:
        print(f"Error: {res.text}")

if __name__ == "__main__":
    check()
