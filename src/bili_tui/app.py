from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, ListItem, ListView, Static

from .bilibili import (
    BiliClient,
    BiliError,
    FavoriteFolder,
    LoginState,
    MediaEntry,
    format_duration,
)
from .player import PlayerError, open_video_url
from .storage import (
    clear_session,
    export_cookies_for_ytdlp,
    load_session,
    save_session,
)


# ---------------------------------------------------------------------------
# CRT theme palettes
# ---------------------------------------------------------------------------
# The UI is authored against the GREEN palette (original colour scheme). Other
# palettes just provide the same logical slots with different phosphor tints;
# we switch themes at startup by rewriting CSS / rich-markup strings, swapping
# every green hex for the corresponding entry in the chosen palette.

PALETTE_GREEN: dict[str, str] = {
    "deep": "#020902",
    "panel": "#071107",
    "list": "#061006",
    "alt": "#041106",
    "bright": "#9bff9b",
    "light": "#7cff7c",
    "accent": "#59ff59",
    "meta": "#4de64d",
    "dim": "#2fbf2f",
    "darker": "#1c5a25",
    "user": "#cdd6f4",
}

PALETTE_AMBER: dict[str, str] = {
    "deep": "#0a0600",
    "panel": "#140d00",
    "list": "#120b00",
    "alt": "#0e0700",
    "bright": "#ffc37a",
    "light": "#f0a655",
    "accent": "#e08a30",
    "meta": "#c47a2a",
    "dim": "#9a5e1f",
    "darker": "#5a3b12",
    "user": "#ffe8c7",
}

PALETTE_WHITE: dict[str, str] = {
    "deep": "#050505",
    "panel": "#0e0e0e",
    "list": "#0c0c0c",
    "alt": "#080808",
    "bright": "#eaeaea",
    "light": "#c8c8c8",
    "accent": "#a8a8a8",
    "meta": "#9a9a9a",
    "dim": "#707070",
    "darker": "#3a3a3a",
    "user": "#a9c8ff",
}

PALETTE_BLUE: dict[str, str] = {
    "deep": "#020410",
    "panel": "#050a1a",
    "list": "#040820",
    "alt": "#02061a",
    "bright": "#9bbfff",
    "light": "#7ca6ff",
    "accent": "#5988ff",
    "meta": "#4d75e6",
    "dim": "#2f57bf",
    "darker": "#1c2c5a",
    "user": "#ffd280",
}

THEMES: dict[str, dict[str, str]] = {
    "green": PALETTE_GREEN,
    "amber": PALETTE_AMBER,
    "white": PALETTE_WHITE,
    "blue": PALETTE_BLUE,
}


def _apply_palette(text: str, palette: dict[str, str]) -> str:
    """Rewrite a string authored in the GREEN palette to use another palette."""
    if palette is PALETTE_GREEN:
        return text
    for key, src_hex in PALETTE_GREEN.items():
        text = text.replace(src_hex, palette[key])
    return text


def _display_title(entry: MediaEntry) -> str:
    if entry.page_title and entry.page_title != entry.title:
        return f"{entry.title} - P{entry.page_index} {entry.page_title}"
    return entry.title


@dataclass(slots=True)
class BrowseSource:
    kind: str
    label: str
    title: str
    subtitle: str
    media_id: int | None = None


class PipTab(Static):
    DEFAULT_CSS = """
    PipTab {
        width: auto;
        height: 1;
        padding: 0 2;
        margin: 0 1 0 0;
        color: #2fbf2f;
        background: transparent;
        text-style: bold;
    }

    PipTab.-selected {
        color: #9bff9b;
        background: transparent;
    }

    PipTab.-active {
        color: #020902;
        background: #59ff59;
    }
    """

    def __init__(
        self,
        label: str,
        active: bool = False,
        selected: bool = False,
        classes: str | None = None,
    ) -> None:
        super().__init__(label, classes=classes)
        self.set_class(active, "-active")
        self.set_class(selected and not active, "-selected")


class VideoRow(ListItem):
    DEFAULT_CSS = """
    VideoRow {
        width: 100%;
        height: 4;
        margin: 0 0 1 0;
        padding: 0 1;
        background: transparent;
        border: none;
    }

    VideoRow > .video-title {
        color: #9bff9b;
        text-style: bold;
        height: 1;
    }

    VideoRow > .video-meta {
        color: #4de64d;
        height: 1;
    }

    VideoRow > .video-context {
        color: #2fbf2f;
        height: 1;
    }
    """

    def __init__(self, entry: MediaEntry) -> None:
        super().__init__(classes="video-row")
        self.entry = entry

    def compose(self) -> ComposeResult:
        yield Static(_display_title(self.entry), classes="video-title")
        duration = format_duration(self.entry.duration)
        yield Static(
            f"UPLINK  {self.entry.author}   RUN {duration}",
            classes="video-meta",
        )
        yield Static(
            self.entry.context.upper(),
            classes="video-context",
            markup=True,
        )


class UrlInputScreen(ModalScreen[str | None]):
    CSS = """
    UrlInputScreen {
        align: center middle;
        background: #041106 80%;
    }

    #url-dialog {
        width: 84;
        height: auto;
        border: heavy #59ff59;
        background: #08160a;
        padding: 1 2;
    }

    #url-title {
        text-style: bold;
        color: #9bff9b;
        margin: 0 0 1 0;
    }

    #url-input {
        border: tall #1c5a25;
        background: #041106;
        color: #9bff9b;
    }

    #url-input:focus {
        border: tall #59ff59;
    }

    #url-help {
        color: #4de64d;
        margin: 1 0 0 0;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, palette: dict[str, str] | None = None) -> None:
        super().__init__()
        self._palette = palette or PALETTE_GREEN

    def compose(self) -> ComposeResult:
        with Vertical(id="url-dialog"):
            yield Static("DIRECT VIDEO LINK", id="url-title")
            yield Input(placeholder="https://www.bilibili.com/video/BV...", id="url-input")
            yield Static(
                _apply_palette("[#9bff9b]ENTER[/] PLAY   [#9bff9b]ESC[/] CANCEL", self._palette),
                id="url-help",
                markup=True,
            )

    def on_mount(self) -> None:
        self.query_one("#url-input", Input).focus()

    @on(Input.Submitted, "#url-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SearchInputScreen(ModalScreen[str | None]):
    CSS = """
    SearchInputScreen {
        align: center middle;
        background: #041106 80%;
    }

    #url-dialog {
        width: 84;
        height: auto;
        border: heavy #59ff59;
        background: #08160a;
        padding: 1 2;
    }

    #url-title {
        text-style: bold;
        color: #9bff9b;
        margin: 0 0 1 0;
    }

    #url-input {
        border: tall #1c5a25;
        background: #041106;
        color: #9bff9b;
    }

    #url-input:focus {
        border: tall #59ff59;
    }

    #url-help {
        color: #4de64d;
        margin: 1 0 0 0;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, initial: str = "", palette: dict[str, str] | None = None) -> None:
        super().__init__()
        self._initial = initial
        self._palette = palette or PALETTE_GREEN

    def compose(self) -> ComposeResult:
        with Vertical(id="url-dialog"):
            yield Static("SEARCH VIDEOS", id="url-title")
            yield Input(
                value=self._initial,
                placeholder="Enter keyword...",
                id="url-input",
            )
            yield Static(
                _apply_palette(
                    "[#9bff9b]ENTER[/] SEARCH   [#9bff9b]ESC[/] CANCEL",
                    self._palette,
                ),
                id="url-help",
                markup=True,
            )

    def on_mount(self) -> None:
        self.query_one("#url-input", Input).focus()

    @on(Input.Submitted, "#url-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class BiliTuiApp(App[None]):
    CSS = """
    Screen {
        background: #020902;
        color: #9bff9b;
    }

    .hidden {
        display: none;
    }

    #root {
        width: 100%;
        height: 100%;
    }

    #login-view {
        width: 100%;
        height: 100%;
        align: center middle;
    }

    #login-card {
        width: 82;
        height: auto;
        border: heavy #59ff59;
        background: #071107;
        padding: 1 2;
    }

    #login-title {
        text-style: bold;
        color: #9bff9b;
        margin: 0 0 1 0;
    }

    #login-qr {
        color: #7cff7c;
        margin: 0 0 1 0;
    }

    #login-url {
        color: #59ff59;
    }

    #login-status {
        color: #4de64d;
    }

    #browser-view {
        width: 100%;
        height: 100%;
    }

    #topbar {
        height: 3;
        border: heavy #59ff59;
        background: #071107;
        padding: 0 2;
        content-align: center middle;
    }

    #nav-panel {
        height: 4;
        padding: 0 1;
        background: #020902;
        border-bottom: tall #1c5a25;
    }

    #primary-tabs,
    #secondary-tabs {
        height: 1;
        width: 100%;
    }

    #primary-tabs {
        margin: 0 0 1 0;
    }

    #nav-panel.-active {
        border-bottom: heavy #59ff59;
    }

    #topbar-left {
        width: 22;
        color: #9bff9b;
        text-style: bold;
    }

    #topbar-center {
        width: 1fr;
        content-align: center middle;
        color: #7cff7c;
        text-style: bold;
    }

    #topbar-right {
        width: 32;
        content-align: right middle;
        color: #59ff59;
    }

    #body {
        width: 100%;
        height: 1fr;
        padding: 1;
    }

    #content {
        width: 100%;
        height: 100%;
    }

    #section-title {
        height: 1;
        color: #9bff9b;
        text-style: bold;
        padding: 0 1;
    }

    #section-subtitle {
        height: 1;
        color: #59ff59;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    #empty-state {
        height: auto;
        color: #2fbf2f;
        margin: 1 0;
        content-align: center middle;
    }

    #video-list {
        width: 100%;
        height: 1fr;
        border: heavy #1c5a25;
        background: #061006;
        padding: 1;
        scrollbar-background: #041106;
        scrollbar-background-hover: #041106;
        scrollbar-background-active: #041106;
        scrollbar-color: #2fbf2f;
        scrollbar-color-hover: #59ff59;
        scrollbar-color-active: #9bff9b;
        scrollbar-corner-color: #041106;
    }

    #video-list.-active {
        border: heavy #59ff59;
    }

    #video-list > ListItem {
        background: transparent;
        padding: 0;
    }

    #video-list > VideoRow.-highlight {
        background: #59ff59;
    }

    #video-list > VideoRow.-highlight .video-title,
    #video-list > VideoRow.-highlight .video-meta,
    #video-list > VideoRow.-highlight .video-context {
        color: #020902;
    }

    #video-list:focus > VideoRow.-highlight {
        background: #59ff59;
    }

    #statusbar {
        height: 1;
        background: #071107;
    }

    #status-line {
        width: 1fr;
        color: #7cff7c;
        padding: 0 1;
    }

    #page-indicator {
        width: 24;
        color: #59ff59;
        content-align: right middle;
        padding: 0 1;
    }

    #helpbar {
        height: 1;
        color: #2fbf2f;
        padding: 0 1;
        background: #020902;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("tab", "switch_pane", "Switch Pane"),
        Binding("up,k", "move_up", "Up"),
        Binding("down,j", "move_down", "Down"),
        Binding("left,h", "move_left", "Left"),
        Binding("right,l", "move_right", "Right"),
        Binding("enter", "activate", "Open"),
        Binding("p", "play_selected", "Play"),
        Binding("u", "open_url", "Open URL"),
        Binding("slash", "search", "Search"),
        Binding("r", "refresh", "Refresh"),
        Binding("L", "logout", "Logout"),
    ]

    def __init__(
        self,
        client: BiliClient | None = None,
        player_opener: Callable[[str, object], None] | None = None,
        palette: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.client = client or BiliClient(session=load_session())
        self.player_opener = player_opener or open_video_url
        self._palette = palette or PALETTE_GREEN
        self.sources: list[BrowseSource] = []
        self.favorite_sources: list[BrowseSource] = []
        self.cards: list[MediaEntry] = []
        self.selected_card_index = 0
        self.active_pane = "nav"
        # 0 = primary tabs row, 1 = favorite folders row (only used when
        # primary_tab == "favorites")
        self.nav_row = 0
        self.primary_tab = "home"
        self.favorite_tab_index = 0
        self.login_state: LoginState | None = None
        self.login_timer = None
        self.clock_timer = None
        self.browser_ready = False
        self.page_size = 20
        self.current_page = 1
        self.has_more = False
        self.total_items = 0
        self.loading_more = False
        # cursor for next history page request (max_oid, view_at)
        self.history_next_cursor: tuple[int, int] = (0, 0)
        # fresh_idx cursor for homepage rcmd feed — monotonically increasing
        self.home_fresh_idx = 1
        # search state
        self.search_keyword: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="root"):
            with Vertical(id="login-view"):
                with Vertical(id="login-card"):
                    yield Static("✦  Scan the QR code with the Bilibili mobile app", id="login-title")
                    yield Static("", id="login-qr")
                    yield Static("", id="login-url")
                    yield Static("", id="login-status")
            with Vertical(id="browser-view", classes="hidden"):
                with Horizontal(id="topbar"):
                    yield Static("PIP-OS", id="topbar-left")
                    yield Static("", id="topbar-center")
                    yield Static("", id="topbar-right")
                with Vertical(id="nav-panel", classes="-active"):
                    yield Horizontal(id="primary-tabs")
                    yield Horizontal(id="secondary-tabs")
                with Horizontal(id="body"):
                    with Vertical(id="content"):
                        yield Static("Loading", id="section-title")
                        yield Static("", id="section-subtitle")
                        yield Static("", id="empty-state")
                        yield ListView(id="video-list")
                with Horizontal(id="statusbar"):
                    yield Static("", id="status-line")
                    yield Static("", id="page-indicator")
                yield Static("", id="helpbar")

    def on_mount(self) -> None:
        self.title = "Bili TUI"
        self.sub_title = "Favorites and History"
        self._set_help()
        try:
            profile = self.client.get_current_user_profile()
        except BiliError:
            self._show_login_view()
            self._start_login_flow()
            return

        self._enter_browser(profile.uname, profile.level)

    def action_switch_pane(self) -> None:
        if len(self.screen_stack) > 1:
            return
        if not self.browser_ready:
            return
        self.active_pane = "videos" if self.active_pane == "nav" else "nav"
        self._refresh_pane_styles()
        self._set_help()

    def action_move_up(self) -> None:
        if len(self.screen_stack) > 1:
            return
        if not self.browser_ready:
            return
        if self.active_pane == "videos":
            # k in the video list: step up; if already at the top, cross
            # the boundary back into the nav pane, landing on the row
            # closest to the list (secondary row if favorites has folders).
            if self.selected_card_index > 0:
                self._move_card_cursor(-1)
                return
            self.active_pane = "nav"
            self.nav_row = (
                1 if (self.primary_tab == "favorites" and self.favorite_sources) else 0
            )
            self._refresh_pane_styles()
            self._refresh_nav()
            self._set_help()
            return
        # nav pane — step between rows only.
        if self.nav_row == 1:
            self.nav_row = 0
            self._refresh_nav()
        # already on row 0: top of app, do nothing

    def action_move_down(self) -> None:
        if len(self.screen_stack) > 1:
            return
        if not self.browser_ready:
            return
        if self.active_pane == "videos":
            self._move_card_cursor(1)
            return
        # nav pane — row 0 -> row 1 (if favorites with folders) -> videos.
        if (
            self.nav_row == 0
            and self.primary_tab == "favorites"
            and self.favorite_sources
        ):
            self.nav_row = 1
            self._refresh_nav()
            return
        if self.cards:
            self.active_pane = "videos"
            self._refresh_pane_styles()
            self._refresh_nav()
            self._set_help()

    def action_move_left(self) -> None:
        if len(self.screen_stack) > 1:
            return
        if not self.browser_ready:
            return
        if self.active_pane == "videos":
            self.active_pane = "nav"
            self._refresh_pane_styles()
            self._refresh_nav()
            self._set_help()
            return
        # nav: move horizontally within the current row.
        if self.nav_row == 1:
            self._move_favorite_tab(-1)
        else:
            self._move_primary_tab(-1)

    def action_move_right(self) -> None:
        if len(self.screen_stack) > 1:
            return
        if not self.browser_ready:
            return
        if self.active_pane == "nav":
            if self.nav_row == 1:
                self._move_favorite_tab(1)
            else:
                self._move_primary_tab(1)
            return
        if self.cards:
            self.active_pane = "videos"
            self._refresh_pane_styles()
            self._refresh_nav()
            self._set_help()

    @on(ListView.Selected, "#video-list")
    async def _on_list_selected(self, event: ListView.Selected) -> None:
        # ListView swallows Enter and emits Selected; route it through the
        # same activate logic as all other Enter presses.
        await self.action_activate()

    async def action_activate(self) -> None:
        # If a modal (URL / Search input) is on top of the stack, let it
        # handle Enter itself — don't steal the key via the priority binding.
        if len(self.screen_stack) > 1:
            return
        if self.login_state is not None and not self.browser_ready:
            return
        if not self.browser_ready:
            return
        if self.active_pane == "nav":
            if self.primary_tab == "search" and not self.search_keyword:
                self._prompt_search()
                return
            if self.cards:
                self.active_pane = "videos"
                self._refresh_pane_styles()
                self._set_help()
            return
        await self.action_play_selected()

    async def action_play_selected(self) -> None:
        if not self.cards:
            self._set_status("Nothing is selected.")
            return
        entry = self.cards[self.selected_card_index]
        await self._play_entry(entry)

    def action_open_url(self) -> None:
        if not self.browser_ready:
            return
        self.push_screen(UrlInputScreen(palette=self._palette), self._handle_url_input)

    def action_search(self) -> None:
        if not self.browser_ready:
            return
        self._prompt_search()

    def _prompt_search(self) -> None:
        self.push_screen(
            SearchInputScreen(initial=self.search_keyword, palette=self._palette),
            self._handle_search_input,
        )

    def _handle_search_input(self, keyword: str | None) -> None:
        if keyword is None:
            return
        keyword = keyword.strip()
        if not keyword:
            return
        self.search_keyword = keyword
        self.primary_tab = "search"
        self.selected_card_index = 0
        self.active_pane = "nav"
        self.run_worker(self._render_navigation(), exclusive=True, group="sources")
        self.run_worker(self._load_current_source(), exclusive=True, group="source-load")

    async def action_refresh(self) -> None:
        if not self.browser_ready:
            self._start_login_flow()
            return
        await self._load_current_source()

    def action_logout(self) -> None:
        if not self.browser_ready:
            return
        # cancel any in-flight data loaders
        for group in ("source-load", "sources", "more", "player"):
            try:
                self.workers.cancel_group(self, group)
            except Exception:
                pass
        # stop clock timer so it stops writing to the hidden topbar
        if self.clock_timer is not None:
            self.clock_timer.stop()
            self.clock_timer = None
        # purge stored credentials and rebuild a blank client
        try:
            self.client.close()
        except Exception:
            pass
        clear_session()
        self.client = BiliClient(session={})
        # reset browser state
        self.browser_ready = False
        self.sources = []
        self.favorite_sources = []
        self.cards = []
        self.selected_card_index = 0
        self.primary_tab = "home"
        self.favorite_tab_index = 0
        self.nav_row = 0
        self.search_keyword = ""
        self.home_fresh_idx = 1
        self.active_pane = "nav"
        # clear the login card (old QR may still be visible) and show login view
        for widget_id in ("login-qr", "login-url", "login-status"):
            try:
                self.query_one(f"#{widget_id}", Static).update("")
            except Exception:
                pass
        self._show_login_view()
        self._set_help()
        self._start_login_flow()

    def _show_login_view(self) -> None:
        self.query_one("#login-view").remove_class("hidden")
        self.query_one("#browser-view").add_class("hidden")
        self._set_status("")

    def _show_browser_view(self) -> None:
        self.query_one("#login-view").add_class("hidden")
        self.query_one("#browser-view").remove_class("hidden")
        self._refresh_pane_styles()
        self._set_help()

    def _start_login_flow(self) -> None:
        try:
            self.login_state = self.client.get_login_state()
        except BiliError as exc:
            self.query_one("#login-status", Static).update(f"Login setup failed: {exc}")
            return

        qr_text = self.client.render_qr_ascii(self.login_state.url)
        self.query_one("#login-qr", Static).update(qr_text)
        self.query_one("#login-url", Static).update(f"Login URL: {self.login_state.url}")
        self.query_one("#login-status", Static).update(
            "Waiting for confirmation. Press r to refresh the QR code."
        )
        if self.login_timer is not None:
            self.login_timer.stop()
        self.login_timer = self.set_interval(2, self._poll_login_state)

    def _poll_login_state(self) -> None:
        if self.login_state is None:
            return
        try:
            message, done = self.client.poll_login(self.login_state.qrcode_key)
        except BiliError as exc:
            self.query_one("#login-status", Static).update(str(exc))
            if self.login_timer is not None:
                self.login_timer.stop()
            return

        self.query_one("#login-status", Static).update(message)
        if not done:
            return

        save_session(self.client.export_session())
        if self.login_timer is not None:
            self.login_timer.stop()
        self.login_state = None
        profile = self.client.get_current_user_profile()
        self._enter_browser(profile.uname, profile.level)

    def _enter_browser(self, username: str, level: int | None) -> None:
        self.browser_ready = True
        self.login_state = None
        self.active_pane = "nav"
        self._account_label = f"[#cdd6f4]{username}[/]"
        self._tick_clock()
        if self.clock_timer is None:
            self.clock_timer = self.set_interval(1.0, self._tick_clock)
        self.query_one("#topbar-center", Static).update(
            self._c("[#9bff9b]MEDIA ARCHIVE[/] [#2fbf2f]::[/] [#59ff59]HOME / HISTORY / FAVORITES / SEARCH[/]")
        )
        self._show_browser_view()
        self._load_sources()

    def _tick_clock(self) -> None:
        clock = time.strftime("%H:%M:%S")
        try:
            self.query_one("#topbar-right", Static).update(
                self._c(f"{self._account_label}   [#9bff9b]{clock}[/]")
            )
        except Exception:
            pass

    def _load_sources(self) -> None:
        try:
            folders = self.client.list_favorite_folders()
        except BiliError as exc:
            self._set_status(f"Failed to load collections: {exc}")
            folders = []

        self.sources = [
            self._home_source(),
            self._history_source(),
            self._search_source(),
        ]
        self.favorite_sources = self._favorite_sources(folders)
        self.sources.extend(self.favorite_sources)
        if self.primary_tab == "favorites" and not self.favorite_sources:
            self.primary_tab = "home"
        if self.favorite_tab_index >= len(self.favorite_sources):
            self.favorite_tab_index = 0
        self.run_worker(self._render_navigation(), exclusive=True, group="sources")
        self.run_worker(self._load_current_source(), exclusive=True, group="source-load")

    async def _render_navigation(self) -> None:
        primary = self.query_one("#primary-tabs", Horizontal)
        secondary = self.query_one("#secondary-tabs", Horizontal)
        await primary.remove_children()
        await secondary.remove_children()
        await primary.mount_all(
            [
                PipTab("HOME", active=self.primary_tab == "home"),
                PipTab("HISTORY", active=self.primary_tab == "history"),
                PipTab("FAVORITES", active=self.primary_tab == "favorites"),
                PipTab("SEARCH", active=self.primary_tab == "search"),
            ]
        )

        if self.primary_tab == "favorites" and self.favorite_sources:
            await secondary.mount_all(
                PipTab(
                    source.label.upper(),
                    active=index == self.favorite_tab_index,
                )
                for index, source in enumerate(self.favorite_sources)
            )
        elif self.primary_tab == "home":
            await secondary.mount_all([PipTab("POPULAR FEED", active=True)])
        elif self.primary_tab == "search":
            label = self.search_keyword.upper() if self.search_keyword else "NO KEYWORD"
            await secondary.mount_all([PipTab(f"QUERY :: {label}", active=True)])
        else:
            await secondary.mount_all([PipTab("RECENT LOG", active=True)])

    def _home_source(self) -> BrowseSource:
        return BrowseSource(
            kind="popular",
            label="Home",
            title="Bilibili Home",
            subtitle="Popular videos on Bilibili",
        )

    def _history_source(self) -> BrowseSource:
        return BrowseSource(
            kind="history",
            label="Recent History",
            title="Recent History",
            subtitle="Latest watched videos",
        )

    def _search_source(self) -> BrowseSource:
        return BrowseSource(
            kind="search",
            label="Search",
            title="Search",
            subtitle="Press / or Enter to search",
        )

    def _favorite_sources(self, folders: list[FavoriteFolder]) -> list[BrowseSource]:
        return [
            BrowseSource(
                kind="favorite",
                label=folder.title,
                title=folder.title,
                subtitle=f"{folder.media_count} items",
                media_id=folder.media_id,
            )
            for folder in folders
        ]

    async def _load_current_source(self) -> None:
        source = self._current_source()
        if source is None:
            return

        # cancel any in-flight loader workers from the previous source
        try:
            self.workers.cancel_group(self, "more")
        except Exception:
            pass

        self.current_page = 1
        self.history_next_cursor = (0, 0)
        if source.kind == "popular":
            # Each fresh load of the homepage feed bumps fresh_idx so the
            # user sees different recommendations every time they hit `r`.
            self.home_fresh_idx += 1
        if source.kind == "search":
            source.subtitle = (
                f'Results for "{self.search_keyword}"'
                if self.search_keyword
                else "Press / or Enter to search"
            )
        self.loading_more = False

        self._set_status(f"[#59ff59]>[/] LOADING {source.title.upper()} ...")
        self.query_one("#section-title", Static).update(f"DATASET :: {source.title.upper()}")
        self.query_one("#section-subtitle", Static).update(source.subtitle)
        self._update_page_indicator()

        try:
            entries, title, has_more, total = await self._fetch_page(source, page=1)
        except BiliError as exc:
            self.cards = []
            await self._render_cards()
            self._set_status(f"[#59ff59]>[/] LOAD FAILED: {exc}")
            self._update_page_indicator()
            return

        self.cards = entries
        self.has_more = has_more
        self.total_items = total
        self.selected_card_index = 0
        source.title = title
        self.query_one("#section-title", Static).update(f"DATASET :: {title.upper()}")
        self.query_one("#section-subtitle", Static).update(self._format_subtitle())
        await self._render_cards()
        self._update_page_indicator()
        self._set_status(f"[#59ff59]>[/] {len(self.cards)} RECORDS LOADED.")

    async def _fetch_page(
        self, source: BrowseSource, page: int
    ) -> tuple[list[MediaEntry], str, bool, int]:
        """Fetch one page worth of entries from the active source.

        For history the pagination uses the cursor we stored in `history_next_cursor`.
        For favorites the page number is passed directly.
        """
        def call() -> tuple[list[MediaEntry], str, bool, int]:
            if source.kind == "popular":
                # For load-more (page > 1) advance the rcmd cursor so we get
                # a fresh batch instead of duplicates.
                if page > 1:
                    self.home_fresh_idx += 1
                entries, has_more = self.client.list_feed_rcmd(
                    fresh_idx=self.home_fresh_idx, ps=self.page_size
                )
                return entries, "Bilibili Home", has_more, 0
            if source.kind == "search":
                if not self.search_keyword:
                    return [], "Search", False, 0
                entries, has_more, total = self.client.search_videos(
                    keyword=self.search_keyword, page=page, ps=self.page_size
                )
                return entries, f"Search: {self.search_keyword}", has_more, total
            if source.kind == "history":
                max_oid, view_at = self.history_next_cursor if page > 1 else (0, 0)
                entries, next_max, next_view_at, has_more = self.client.list_history_items(
                    ps=self.page_size, max_oid=max_oid, view_at=view_at
                )
                self.history_next_cursor = (next_max, next_view_at)
                return entries, "Recent History", has_more, 0
            assert source.media_id is not None
            title, entries, has_more, total = self.client.list_favorite_items(
                source.media_id, pn=page, ps=self.page_size
            )
            return entries, title, has_more, total

        return await asyncio.to_thread(call)

    def _format_subtitle(self) -> str:
        if self.total_items:
            text = f"[#59ff59]{len(self.cards)}[/] OF [#59ff59]{self.total_items}[/] RECORDS"
        else:
            suffix = " [#2fbf2f](MORE AVAILABLE)[/]" if self.has_more else ""
            text = f"[#59ff59]{len(self.cards)}[/] RECORDS{suffix}"
        return self._c(text)

    def _update_page_indicator(self) -> None:
        if not self.is_mounted:
            return
        if self.loading_more:
            text = "[#59ff59]LOADING...[/]"
        elif self.has_more:
            text = f"[#59ff59]{len(self.cards)}[/] [#2fbf2f]/ MOVE DOWN FOR MORE[/]"
        else:
            text = f"[#59ff59]{len(self.cards)}[/] [#2fbf2f]/ END[/]"
        try:
            self.query_one("#page-indicator", Static).update(self._c(text))
        except Exception:
            pass

    async def _load_more(self) -> None:
        if self.loading_more or not self.has_more:
            return
        source = self._current_source()
        if source is None:
            return
        self.loading_more = True
        self._update_page_indicator()
        next_page = self.current_page + 1
        try:
            entries, _title, has_more, total = await self._fetch_page(source, page=next_page)
        except BiliError as exc:
            self._set_status(f"[#59ff59]>[/] LOAD MORE FAILED: {exc}")
            self.loading_more = False
            self._update_page_indicator()
            return

        new_cards = entries
        self.cards.extend(new_cards)
        self.current_page = next_page
        self.has_more = has_more
        if total:
            self.total_items = total
        await self._append_cards(new_cards)
        self.query_one("#section-subtitle", Static).update(self._format_subtitle())
        self.loading_more = False
        self._update_page_indicator()
        self._set_status(f"[#59ff59]>[/] {len(self.cards)} RECORDS LOADED.")

    async def _render_cards(self) -> None:
        video_list = self.query_one("#video-list", ListView)
        empty = self.query_one("#empty-state", Static)
        await video_list.remove_children()

        if not self.cards:
            empty.update("NO VIDEO RECORDS IN THIS DATASET.")
            empty.remove_class("hidden")
            return

        empty.update("")
        empty.add_class("hidden")
        await video_list.mount_all(VideoRow(data) for data in self.cards)
        video_list.index = self.selected_card_index

    async def _append_cards(self, new_cards: list[MediaEntry]) -> None:
        video_list = self.query_one("#video-list", ListView)
        await video_list.mount_all(VideoRow(data) for data in new_cards)

    async def _play_entry(self, entry: MediaEntry) -> None:
        try:
            cookies_path = export_cookies_for_ytdlp(self.client.export_session()["cookies"])
            self.player_opener(entry.page_url(), cookies_path)
        except (BiliError, PlayerError) as exc:
            self._set_status(f"Playback failed: {exc}")
            return
        self._set_status(f"[#59ff59]>[/] PLAYBACK OPENED: {_display_title(entry)}")

    async def _play_url(self, url: str) -> None:
        try:
            video = self.client.resolve_video(url)
            cookies_path = export_cookies_for_ytdlp(self.client.export_session()["cookies"])
            self.player_opener(video_page_url(video.bvid, video.page_index), cookies_path)
        except (BiliError, PlayerError) as exc:
            self._set_status(f"Playback failed: {exc}")
            return
        title = video.title if video.page_title == video.title else f"{video.title} - P{video.page_index} {video.page_title}"
        self._set_status(f"[#59ff59]>[/] PLAYBACK OPENED: {title}")

    def _handle_url_input(self, url: str | None) -> None:
        if not url:
            return
        self.run_worker(self._play_url(url), exclusive=True, group="player")

    def _move_primary_tab(self, delta: int) -> None:
        options = ["home", "history", "favorites", "search"]
        current = options.index(self.primary_tab)
        next_index = max(0, min(len(options) - 1, current + delta))
        next_tab = options[next_index]
        if next_tab == self.primary_tab:
            return
        if next_tab == "favorites" and not self.favorite_sources:
            return
        self.primary_tab = next_tab
        self.selected_card_index = 0
        self.run_worker(self._render_navigation(), exclusive=True, group="sources")
        self.run_worker(self._load_current_source(), exclusive=True, group="source-load")

    def _move_favorite_tab(self, delta: int) -> None:
        if self.primary_tab != "favorites" or not self.favorite_sources:
            return
        next_index = max(0, min(len(self.favorite_sources) - 1, self.favorite_tab_index + delta))
        if next_index == self.favorite_tab_index:
            return
        self.favorite_tab_index = next_index
        self.selected_card_index = 0
        self.run_worker(self._render_navigation(), exclusive=True, group="sources")
        self.run_worker(self._load_current_source(), exclusive=True, group="source-load")

    def _move_card_cursor(self, delta: int) -> None:
        if not self.cards:
            return
        next_index = max(0, min(len(self.cards) - 1, self.selected_card_index + delta))
        if next_index != self.selected_card_index:
            self.selected_card_index = next_index
            self._refresh_card_selection()
        # auto load more when approaching the end
        if self.has_more and not self.loading_more:
            threshold = 3
            if self.selected_card_index >= len(self.cards) - threshold:
                self.run_worker(self._load_more(), group="more", exclusive=True)

    def _refresh_card_selection(self) -> None:
        video_list = self.query_one("#video-list", ListView)
        video_list.index = self.selected_card_index

    def _current_source(self) -> BrowseSource | None:
        if self.primary_tab == "home":
            return self.sources[0] if self.sources else None
        if self.primary_tab == "history":
            return self.sources[1] if len(self.sources) > 1 else None
        if self.primary_tab == "search":
            return self.sources[2] if len(self.sources) > 2 else None
        if not self.favorite_sources:
            return None
        if 0 <= self.favorite_tab_index < len(self.favorite_sources):
            return self.favorite_sources[self.favorite_tab_index]
        return self.favorite_sources[0]

    def _refresh_nav(self) -> None:
        self.run_worker(self._render_navigation(), exclusive=True, group="sources")

    def _refresh_pane_styles(self) -> None:
        nav = self.query_one("#nav-panel")
        video_list = self.query_one("#video-list")
        nav.set_class(self.active_pane == "nav", "-active")
        video_list.set_class(self.active_pane == "videos", "-active")

    def _c(self, text: str) -> str:
        """Re-theme a rich-markup string authored in GREEN to the active palette."""
        return _apply_palette(text, self._palette)

    def _set_status(self, message: str) -> None:
        self.query_one("#status-line", Static).update(self._c(message))

    def _set_help(self) -> None:
        def key(label: str) -> str:
            return f"[#9bff9b b]{label}[/]"

        def sep() -> str:
            return "[#1c5a25] :: [/] "

        if self.browser_ready:
            if self.active_pane == "nav":
                parts = [
                    f"{key('h/l')} section",
                    f"{key('j/k')} flow through panes",
                    f"{key('enter')} focus / search",
                    f"{key('/')} search",
                    f"{key('u')} URL",
                    f"{key('r')} refresh",
                    f"{key('L')} logout",
                    f"{key('q')} quit",
                ]
            else:
                parts = [
                    f"{key('j/k')} select (k at top -> nav)",
                    f"{key('enter')} play",
                    f"{key('/')} search",
                    f"{key('u')} URL",
                    f"{key('r')} refresh",
                    f"{key('L')} logout",
                    f"{key('q')} quit",
                ]
            text = sep().join(parts)
        else:
            text = f"{key('r')} refresh QR code{sep()}{key('q')} quit"
        self.query_one("#helpbar", Static).update(self._c(text))


def video_page_url(bvid: str, page_index: int) -> str:
    return f"https://www.bilibili.com/video/{bvid}?p={page_index}"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="bili-tui", description="Bilibili TUI client")
    parser.add_argument(
        "--theme",
        choices=sorted(THEMES.keys()),
        default="green",
        help="CRT phosphor theme: green (default), amber, white, blue.",
    )
    return parser.parse_args(argv)


def _apply_theme_to_css(palette: dict[str, str]) -> None:
    """Rewrite every class-level CSS string in this module to the target palette."""
    BiliTuiApp.CSS = _apply_palette(BiliTuiApp.CSS, palette)
    PipTab.DEFAULT_CSS = _apply_palette(PipTab.DEFAULT_CSS, palette)
    VideoRow.DEFAULT_CSS = _apply_palette(VideoRow.DEFAULT_CSS, palette)
    UrlInputScreen.CSS = _apply_palette(UrlInputScreen.CSS, palette)
    SearchInputScreen.CSS = _apply_palette(SearchInputScreen.CSS, palette)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    palette = THEMES[args.theme]
    _apply_theme_to_css(palette)

    session = load_session()
    client = BiliClient(session=session)
    app = BiliTuiApp(client=client, palette=palette)
    try:
        app.run()
    finally:
        # The client may have been replaced (e.g. by logout), so persist the
        # one the app is actually using now.
        current = app.client
        save_session(current.export_session())
        current.close()
        if current is not client:
            try:
                client.close()
            except Exception:
                pass
