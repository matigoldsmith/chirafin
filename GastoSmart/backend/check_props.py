import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("NOTION_TOKEN")
db_id = os.getenv("NOTION_DATABASE_ID")

headers = {
    "Authorization": f"Bearer {token}",
    "Notion-Version": "2022-06-28"
}

res = requests.get(f"https://api.notion.com/v1/databases/{db_id}", headers=headers)
if res.status_code == 200:
    props = res.json().get("properties", {})
    # Filter to show property names and types
    simple_props = {name: props[name]["type"] for name in props}
    print(json.dumps(simple_props, indent=2))
else:
    print(f"Error {res.status_code}: {res.text}")
