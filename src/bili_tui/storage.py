from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SESSION_DIR = Path("data")
SESSION_PATH = SESSION_DIR / "session.json"
PLAYER_COOKIES_PATH = SESSION_DIR / "player_cookies.txt"


def load_session() -> dict[str, Any]:
    if not SESSION_PATH.exists():
        return {}
    return json.loads(SESSION_PATH.read_text(encoding="utf-8"))


def save_session(session: dict[str, Any]) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(
        json.dumps(session, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_session() -> None:
    if SESSION_PATH.exists():
        SESSION_PATH.unlink()


def export_cookies_for_ytdlp(cookies: dict[str, str]) -> Path:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Netscape HTTP Cookie File",
        "",
    ]
    for name in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"):
        value = cookies.get(name)
        if not value:
            continue
        secure = "TRUE" if name == "SESSDATA" else "FALSE"
        lines.append(f".bilibili.com\tTRUE\t/\t{secure}\t0\t{name}\t{value}")

    PLAYER_COOKIES_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return PLAYER_COOKIES_PATH
