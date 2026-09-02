# Bili TUI

A small Python TUI for Bilibili that can:

- sign in with a QR code
- browse your favorite folders and recent watch history in a Pip-Boy-inspired list UI
- open any selected video in IINA through `yt-dlp`

## Current Stack

- UI: [Textual](https://github.com/Textualize/textual)
- Login and account data: Bilibili web APIs
- Playback: IINA + `yt-dlp`
- Environment manager: `uv`

## Install

```bash
uv sync
```

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

- `yt-dlp` must be installed and available in `PATH`.
- IINA is expected at `/Applications/IINA.app`.
- `b23.tv` short links are still not expanded automatically.

## References

- [MareDevi/bilibili-tui](https://github.com/MareDevi/bilibili-tui)
- [Textual](https://github.com/Textualize/textual)
