from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote, urlencode

IINA_APP = "/Applications/IINA.app"
IINA_BUNDLE_ID = "com.colliderli.iina"
MACOS_OPEN = "/usr/bin/open"


class PlayerError(RuntimeError):
    """Raised when no supported player strategy is available."""


def open_video_url(url: str, cookies_path: Path) -> None:
    if not Path(IINA_APP).exists():
        raise PlayerError("IINA was not found at /Applications/IINA.app.")
    yt_dlp_path = shutil.which("yt-dlp")
    if yt_dlp_path is None:
        raise PlayerError("yt-dlp is required for direct streaming but is not installed.")
    cookies_path = cookies_path.resolve()

    query = urlencode(
        {
            "url": url,
            "new_window": "1",
            "mpv_force-window": "immediate",
            "mpv_ytdl": "yes",
            "mpv_script-opts": f"ytdl_hook-ytdl_path={yt_dlp_path}",
            "mpv_ytdl-raw-options": f"cookies={cookies_path}",
            "mpv_ytdl-format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        },
        quote_via=quote,
    )
    command = [
        MACOS_OPEN,
        "-b",
        IINA_BUNDLE_ID,
        f"iina://open?{query}",
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise PlayerError("Failed to open the video in IINA.") from exc
