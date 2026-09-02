# Bili TUI

A light Python TUI for Bilibili that can:

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

## Notes

- `yt-dlp` must be installed and available in `PATH`.
- IINA is expected at `/Applications/IINA.app`.
- `b23.tv` short links are still not expanded automatically.

## References

- [MareDevi/bilibili-tui](https://github.com/MareDevi/bilibili-tui)
- [Textual](https://github.com/Textualize/textual)
