from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

IINA_CLI = "/Applications/IINA.app/Contents/MacOS/iina-cli"


class PlayerError(RuntimeError):
    """Raised when no supported player strategy is available."""


def open_video_url(url: str, cookies_path: Path) -> None:
    if not Path(IINA_CLI).exists():
        raise PlayerError("IINA was not found at /Applications/IINA.app.")
    if shutil.which("yt-dlp") is None:
        raise PlayerError("yt-dlp is required for direct streaming but is not installed.")

    command = [
        IINA_CLI,
        "--no-stdin",
        url,
        "--",
        "--force-window=immediate",
        "--ytdl=yes",
        f"--ytdl-raw-options=cookies={cookies_path}",
        "--ytdl-format=bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise PlayerError("Failed to open the video in IINA.") from exc
