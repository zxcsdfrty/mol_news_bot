"""讀取環境變數與 YAML 設定。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _load_dotenv() -> None:
    """簡易 .env 載入（不另外依賴 python-dotenv）。已存在的環境變數優先。"""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    supabase_url: str = os.getenv("SUPABASE_URL", "").rstrip("/")
    # 寫入用 service_role key，只放在排程端（GitHub Secrets / 伺服器 .env），絕不可放到網頁
    supabase_service_key: str = os.getenv("SUPABASE_SERVICE_KEY", "")
    # 單則推播最多幾則新聞；超過會自動分多則訊息
    max_items_per_push: int = int(os.getenv("MAX_ITEMS_PER_PUSH", "30"))
    # 只處理多久以內發布的新聞（小時），避免 RSS 舊文重推
    max_age_hours: int = int(os.getenv("MAX_AGE_HOURS", "36"))
    # 兩次推播最短間隔（分鐘）；排程重複觸發時避免 30 分鐘內推兩次
    min_push_interval: int = int(os.getenv("MIN_PUSH_INTERVAL_MIN", "25"))
    # 夜間靜音（台灣時間），例如 "23-7" 代表 23:00~06:59 不推播，累積到早上一起推
    quiet_hours: str = os.getenv("QUIET_HOURS", "")
    http_timeout: int = int(os.getenv("HTTP_TIMEOUT", "15"))
    web_url: str = os.getenv("WEB_URL", "")


def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


settings = Settings()
