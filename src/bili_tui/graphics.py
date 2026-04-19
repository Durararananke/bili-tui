from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .bilibili import COMMON_HEADERS


class GraphicsError(RuntimeError):
    """Raised when terminal image preview fails."""


def show_cover_image(url: str) -> None:
    if not _supports_kitty_graphics():
        raise GraphicsError("Terminal image preview requires Ghostty, kitty, or another kitty-graphics terminal.")

    suffix = _guess_suffix(url)
    response = httpx.get(url, headers=COMMON_HEADERS, follow_redirects=True, timeout=20.0)
    response.raise_for_status()

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(response.content)
        temp_path = Path(handle.name)

    payload = base64.b64encode(str(temp_path).encode("utf-8")).decode("ascii")
    sequence = f"\x1b_Ga=T,t=t,q=2;{payload}\x1b\\\n"
    sys.stdout.write(sequence)
    sys.stdout.flush()


def _supports_kitty_graphics() -> bool:
    term = os.environ.get("TERM", "").lower()
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    return "ghostty" in term or "kitty" in term or term_program in {"ghostty", "kitty"}


def _guess_suffix(url: str) -> str:
    path = urlparse(url).path.lower()
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        if path.endswith(suffix):
            return suffix
    return ".img"
