from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

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
from .storage import export_cookies_for_ytdlp, load_session, save_session


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


@dataclass(slots=True)
class VideoCardData:
    entry: MediaEntry
    cover: object | None = None


class SourceItem(ListItem):
    def __init__(self, source: BrowseSource) -> None:
        self.source = source
        super().__init__(
            Vertical(
                Static(source.label.upper(), classes="source-label"),
                Static(source.subtitle, classes="source-subtitle"),
            ),
            classes="source-item",
        )


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

    PipTab.-active {
        color: #020902;
        background: #59ff59;
    }
    """

    def __init__(self, label: str, active: bool = False, classes: str | None = None) -> None:
        super().__init__(label, classes=classes)
        self.set_class(active, "-active")


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

    def __init__(self, data: VideoCardData) -> None:
        super().__init__(classes="video-row")
        self.data = data

    def compose(self) -> ComposeResult:
        yield Static(_display_title(self.data.entry), classes="video-title")
        duration = format_duration(self.data.entry.duration)
        yield Static(
            f"UPLINK  {self.data.entry.author}   RUN {duration}",
            classes="video-meta",
        )
        yield Static(
            self.data.entry.context.upper(),
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

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="url-dialog"):
            yield Static("DIRECT VIDEO LINK", id="url-title")
            yield Input(placeholder="https://www.bilibili.com/video/BV...", id="url-input")
            yield Static("[#9bff9b]ENTER[/] PLAY   [#9bff9b]ESC[/] CANCEL", id="url-help", markup=True)

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

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("tab", "switch_pane", "Switch Pane", priority=True),
        Binding("up,k", "move_up", "Up"),
        Binding("down,j", "move_down", "Down"),
        Binding("left,h", "move_left", "Left"),
        Binding("right,l", "move_right", "Right"),
        Binding("enter", "activate", "Open", priority=True),
        Binding("p", "play_selected", "Play"),
        Binding("u", "open_url", "Open URL"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(
        self,
        client: BiliClient | None = None,
        player_opener: Callable[[str, object], None] | None = None,
    ) -> None:
        super().__init__()
        self.client = client or BiliClient(session=load_session())
        self.player_opener = player_opener or open_video_url
        self.sources: list[BrowseSource] = []
        self.favorite_sources: list[BrowseSource] = []
        self.cards: list[VideoCardData] = []
        self.selected_card_index = 0
        self.active_pane = "nav"
        self.primary_tab = "history"
        self.favorite_tab_index = 0
        self.login_state: LoginState | None = None
        self.login_timer = None
        self.browser_ready = False
        self.page_size = 20
        self.current_page = 1
        self.has_more = False
        self.total_items = 0
        self.loading_more = False
        # cursor for next history page request (max_oid, view_at)
        self.history_next_cursor: tuple[int, int] = (0, 0)
        self._active_source_key: tuple[str, int | None] | None = None

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
        if not self.browser_ready:
            return
        self.active_pane = "videos" if self.active_pane == "nav" else "nav"
        self._refresh_pane_styles()
        self._set_help()

    def action_move_up(self) -> None:
        if not self.browser_ready:
            return
        if self.active_pane == "videos":
            self._move_card_cursor(-1)
        elif self.primary_tab == "favorites":
            self._move_favorite_tab(-1)

    def action_move_down(self) -> None:
        if not self.browser_ready:
            return
        if self.active_pane == "videos":
            self._move_card_cursor(1)
        elif self.primary_tab == "favorites":
            self._move_favorite_tab(1)

    def action_move_left(self) -> None:
        if not self.browser_ready:
            return
        if self.active_pane == "videos":
            self.active_pane = "nav"
            self._refresh_pane_styles()
            self._set_help()
            return
        self._move_primary_tab(-1)

    def action_move_right(self) -> None:
        if not self.browser_ready:
            return
        if self.active_pane == "nav":
            self._move_primary_tab(1)
            return
        if self.cards:
            self.active_pane = "videos"
            self._refresh_pane_styles()
            self._set_help()

    async def action_activate(self) -> None:
        if self.login_state is not None and not self.browser_ready:
            return
        if not self.browser_ready:
            return
        if self.active_pane == "nav":
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
        entry = self.cards[self.selected_card_index].entry
        await self._play_entry(entry)

    def action_open_url(self) -> None:
        if not self.browser_ready:
            return
        self.push_screen(UrlInputScreen(), self._handle_url_input)

    async def action_refresh(self) -> None:
        if not self.browser_ready:
            self._start_login_flow()
            return
        await self._load_current_source()

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
        account = f"[#cdd6f4]{username}[/]"
        self.query_one("#topbar-right", Static).update(account)
        self.query_one("#topbar-center", Static).update(
            "[#9bff9b]MEDIA ARCHIVE[/] [#2fbf2f]::[/] [#59ff59]FAVORITES / HISTORY[/]"
        )
        self._show_browser_view()
        self._load_sources()

    def _load_sources(self) -> None:
        try:
            folders = self.client.list_favorite_folders()
        except BiliError as exc:
            self._set_status(f"Failed to load collections: {exc}")
            folders = []

        self.sources = [self._history_source()]
        self.favorite_sources = self._favorite_sources(folders)
        self.sources.extend(self.favorite_sources)
        if self.primary_tab == "favorites" and not self.favorite_sources:
            self.primary_tab = "history"
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
                PipTab("HISTORY", active=self.primary_tab == "history"),
                PipTab("FAVORITES", active=self.primary_tab == "favorites"),
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
        else:
            await secondary.mount_all([PipTab("RECENT LOG", active=True)])

    def _history_source(self) -> BrowseSource:
        return BrowseSource(
            kind="history",
            label="Recent History",
            title="Recent History",
            subtitle="Latest watched videos",
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

        source_key = (source.kind, source.media_id)
        self._active_source_key = source_key
        self.current_page = 1
        self.history_next_cursor = (0, 0)
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

        self.cards = [VideoCardData(entry=e, cover=None) for e in entries]
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
            return f"[#59ff59]{len(self.cards)}[/] OF [#59ff59]{self.total_items}[/] RECORDS"
        suffix = " [#2fbf2f](MORE AVAILABLE)[/]" if self.has_more else ""
        return f"[#59ff59]{len(self.cards)}[/] RECORDS{suffix}"

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
            self.query_one("#page-indicator", Static).update(text)
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

        new_cards = [VideoCardData(entry=e, cover=None) for e in entries]
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

    async def _append_cards(self, new_cards: list[VideoCardData]) -> None:
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
        options = ["history", "favorites"]
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
        if self.primary_tab == "history":
            return self.sources[0] if self.sources else None
        if not self.favorite_sources:
            return None
        if 0 <= self.favorite_tab_index < len(self.favorite_sources):
            return self.favorite_sources[self.favorite_tab_index]
        return self.favorite_sources[0]

    def _refresh_pane_styles(self) -> None:
        nav = self.query_one("#nav-panel")
        video_list = self.query_one("#video-list")
        nav.set_class(self.active_pane == "nav", "-active")
        video_list.set_class(self.active_pane == "videos", "-active")

    def _set_status(self, message: str) -> None:
        self.query_one("#status-line", Static).update(message)

    def _set_help(self) -> None:
        def key(label: str) -> str:
            return f"[#9bff9b b]{label}[/]"

        def sep() -> str:
            return "[#1c5a25] :: [/] "

        if self.browser_ready:
            if self.active_pane == "nav":
                parts = [
                    f"{key('tab')} list",
                    f"{key('h/l')} section",
                    f"{key('j/k')} favorite folder",
                    f"{key('enter')} focus list",
                    f"{key('u')} URL",
                    f"{key('r')} refresh",
                    f"{key('q')} quit",
                ]
            else:
                parts = [
                    f"{key('tab')} nav",
                    f"{key('j/k')} select",
                    f"{key('h')} nav",
                    f"{key('enter')} play",
                    f"{key('u')} URL",
                    f"{key('r')} refresh",
                    f"{key('q')} quit",
                ]
            text = sep().join(parts)
        else:
            text = f"{key('r')} refresh QR code{sep()}{key('q')} quit"
        self.query_one("#helpbar", Static).update(text)


def video_page_url(bvid: str, page_index: int) -> str:
    return f"https://www.bilibili.com/video/{bvid}?p={page_index}"


def main() -> None:
    session = load_session()
    client = BiliClient(session=session)
    app = BiliTuiApp(client=client)
    try:
        app.run()
    finally:
        save_session(client.export_session())
        client.close()
