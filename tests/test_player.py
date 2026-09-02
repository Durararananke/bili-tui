from pathlib import Path
from subprocess import CalledProcessError
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from bili_tui.player import IINA_BUNDLE_ID, MACOS_OPEN, PlayerError, open_video_url


class OpenVideoUrlTests(TestCase):
    @patch("bili_tui.player.subprocess.run")
    @patch("bili_tui.player.shutil.which", return_value="/opt/homebrew/bin/yt-dlp")
    @patch("bili_tui.player.Path.exists", return_value=True)
    def test_opens_video_through_iina_url_scheme(self, _exists, _which, run):
        url = "https://www.bilibili.com/video/BV1test?p=2"
        cookies_path = Path("data/player cookies.txt")

        open_video_url(url, cookies_path)

        command = run.call_args.args[0]
        self.assertEqual(command[:3], [MACOS_OPEN, "-b", IINA_BUNDLE_ID])
        parsed = urlparse(command[3])
        self.assertEqual((parsed.scheme, parsed.netloc), ("iina", "open"))
        self.assertEqual(
            parse_qs(parsed.query),
            {
                "url": [url],
                "new_window": ["1"],
                "mpv_force-window": ["immediate"],
                "mpv_ytdl": ["yes"],
                "mpv_script-opts": [
                    "ytdl_hook-ytdl_path=/opt/homebrew/bin/yt-dlp"
                ],
                "mpv_ytdl-raw-options": [f"cookies={cookies_path.resolve()}"],
                "mpv_ytdl-format": [
                    "bestvideo[height<=1080]+bestaudio/best[height<=1080]"
                ],
            },
        )
        run.assert_called_once_with(command, check=True)

    @patch("bili_tui.player.subprocess.run")
    @patch("bili_tui.player.shutil.which", return_value="/opt/homebrew/bin/yt-dlp")
    @patch("bili_tui.player.Path.exists", return_value=True)
    def test_reports_open_failure(self, _exists, _which, run):
        run.side_effect = CalledProcessError(1, [MACOS_OPEN])

        with self.assertRaisesRegex(PlayerError, "Failed to open the video in IINA"):
            open_video_url("https://example.com/video", Path("/tmp/cookies.txt"))
