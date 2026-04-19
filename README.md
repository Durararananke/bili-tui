# Bili TUI

A small Python TUI for Bilibili that can:

- sign in with a QR code
- browse your favorite folders and recent watch history in a Pip-Boy-inspired list UI
- open any selected video in IINA through `yt-dlp`
- open a pasted Bilibili URL without downloading the video file locally

## Current Stack

- UI: [Textual](https://github.com/Textualize/textual)
- Login and account data: Bilibili web APIs
- Playback: IINA + `yt-dlp`
- Environment manager: `uv`

The playback flow follows the same broad idea as `MareDevi/bilibili-tui`: export Bilibili cookies, hand the page URL to the player, and let the player stream through `yt-dlp`.

## Install

```bash
uv sync
```

Approximate disk usage:

- virtual environment: about 20-30 MB
- Python packages and dependencies: about 20-30 MB
- total: usually about 40-60 MB

## Run

```bash
uv run bili-tui
```

## Controls

- `j` / `k`: move in the current list
- `h` / `l`: switch between the source list and the video list
- `tab`: switch between the source list and the video list
- `enter`: open a folder or play the selected video
- `p`: play the selected video
- `u`: paste a Bilibili URL and play it directly
- `r`: refresh the current view or refresh the login QR code
- `q`: quit

## Notes

- Video files are streamed through IINA and `yt-dlp`; they are not downloaded and cached by this app.
- `yt-dlp` must be installed and available in `PATH`.
- IINA is expected at `/Applications/IINA.app`.
- `b23.tv` short links are still not expanded automatically.

## References

- [MareDevi/bilibili-tui](https://github.com/MareDevi/bilibili-tui)
- [Textual](https://github.com/Textualize/textual)
