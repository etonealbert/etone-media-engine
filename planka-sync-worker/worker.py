import os
import time
import json
from typing import Dict, List, Any, Optional, Set

import requests
from dotenv import load_dotenv


load_dotenv()


def env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing env var: {name}")
    return value


class TelegramClient:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base = f"https://api.telegram.org/bot{token}"

    def send(self, text: str) -> None:
        r = requests.post(
            f"{self.base}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
        r.raise_for_status()


class PlankaClient:
    """
    NOTE:
    Planka deployments differ by version/auth setup.
    This client uses common REST patterns and is intentionally easy to adapt.

    Expected endpoints (override if needed):
    - GET /api/projects
    - GET /api/projects/{id}/boards
    - GET /api/boards/{id}/cards

    If your local Planka uses a different route shape, adapt methods below.
    """

    def __init__(self, base_url: str, token: str, auth_header: str, auth_prefix: str):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()

        auth_value = token if not auth_prefix else f"{auth_prefix} {token}"
        self.session.headers.update({auth_header: auth_value, "Content-Type": "application/json"})

    def _get(self, path: str) -> Any:
        url = f"{self.base}{path}"
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        return r.json()

    def list_projects(self) -> List[Dict[str, Any]]:
        data = self._get("/api/projects")
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        if isinstance(data, list):
            return data
        return data.get("data", []) if isinstance(data, dict) else []

    def list_boards(self, project_id: str) -> List[Dict[str, Any]]:
        data = self._get(f"/api/projects/{project_id}/boards")
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        if isinstance(data, list):
            return data
        return data.get("data", []) if isinstance(data, dict) else []

    def list_cards(self, board_id: str) -> List[Dict[str, Any]]:
        data = self._get(f"/api/boards/{board_id}/cards")
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        if isinstance(data, list):
            return data
        return data.get("data", []) if isinstance(data, dict) else []


class SyncWorker:
    def __init__(self):
        self.poll_interval = int(env("POLL_INTERVAL_SECONDS", "20"))
        self.allowed_project_ids: Set[str] = {
            x.strip() for x in env("PLANKA_PROJECT_IDS", "").split(",") if x.strip()
        }
        self.verbose = env("VERBOSE_NOTIFICATIONS", "false").lower() == "true"

        self.telegram = TelegramClient(
            token=env("TELEGRAM_BOT_TOKEN"),
            chat_id=env("TELEGRAM_CHAT_ID"),
        )

        self.planka = PlankaClient(
            base_url=env("PLANKA_BASE_URL"),
            token=env("PLANKA_TOKEN"),
            auth_header=env("PLANKA_AUTH_HEADER", "Authorization"),
            auth_prefix=env("PLANKA_AUTH_PREFIX", "Bearer"),
        )

        # card_id -> last known list_id
        self.state: Dict[str, str] = {}

    def _notify(self, text: str) -> None:
        print(text)
        self.telegram.send(text)

    def _iter_cards(self):
        projects = self.planka.list_projects()
        for p in projects:
            pid = str(p.get("id"))
            pname = p.get("name", pid)
            if self.allowed_project_ids and pid not in self.allowed_project_ids:
                continue

            boards = self.planka.list_boards(pid)
            for b in boards:
                bid = str(b.get("id"))
                bname = b.get("name", bid)
                cards = self.planka.list_cards(bid)
                for c in cards:
                    yield {
                        "project_id": pid,
                        "project_name": pname,
                        "board_id": bid,
                        "board_name": bname,
                        "card": c,
                    }

    def run(self):
        self._notify("🟢 planka-sync-worker started")

        while True:
            try:
                for row in self._iter_cards():
                    card = row["card"]
                    card_id = str(card.get("id"))
                    card_name = card.get("name") or card.get("title") or card_id
                    list_id = str(card.get("listId") or card.get("list_id") or "")

                    if not list_id:
                        continue

                    prev = self.state.get(card_id)
                    if prev is None:
                        self.state[card_id] = list_id
                        continue

                    if prev != list_id:
                        self.state[card_id] = list_id
                        self._notify(
                            "\n".join(
                                [
                                    "🔁 Planka card moved",
                                    f"Project: {row['project_name']}",
                                    f"Board: {row['board_name']}",
                                    f"Card: {card_name}",
                                    f"From list id: {prev}",
                                    f"To list id: {list_id}",
                                ]
                            )
                        )

                if self.verbose:
                    self._notify("heartbeat: planka-sync-worker alive")

            except Exception as e:
                err = f"🔴 planka-sync-worker error: {type(e).__name__}: {e}"
                print(err)
                try:
                    self.telegram.send(err)
                except Exception:
                    pass

            time.sleep(self.poll_interval)


if __name__ == "__main__":
    worker = SyncWorker()
    worker.run()
