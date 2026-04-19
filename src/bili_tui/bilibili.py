from __future__ import annotations

import http.cookiejar
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import qrcode


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)
COMMON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://www.bilibili.com",
}


class BiliError(RuntimeError):
    """Raised when Bilibili API replies with an error."""


@dataclass(slots=True)
class LoginState:
    qrcode_key: str
    url: str
    expires_at: float


@dataclass(slots=True)
class VideoSelection:
    bvid: str
    aid: int
    cid: int
    title: str
    page_title: str
    page_index: int


@dataclass(slots=True)
class PlaybackBundle:
    video_url: str
    audio_url: str | None
    quality: int
    cookies: dict[str, str]
    bvid: str
    cid: int


@dataclass(slots=True)
class UserProfile:
    mid: int
    uname: str
    level: int | None


@dataclass(slots=True)
class FavoriteFolder:
    media_id: int
    title: str
    media_count: int
    attr: int


@dataclass(slots=True)
class MediaEntry:
    title: str
    bvid: str
    page_index: int
    page_title: str
    author: str
    duration: int
    cover_url: str | None
    context: str

    def page_url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}?p={self.page_index}"


class BiliClient:
    def __init__(self, session: dict[str, Any] | None = None) -> None:
        cookies = (session or {}).get("cookies", {})
        self._client = httpx.Client(
            headers=COMMON_HEADERS,
            cookies=cookies,
            follow_redirects=True,
            timeout=15.0,
        )

    def export_session(self) -> dict[str, Any]:
        return {
            "cookies": _cookie_snapshot(self._client.cookies.jar),
        }

    def close(self) -> None:
        self._client.close()

    def get_login_state(self) -> LoginState:
        resp = self._client.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
        )
        payload = _unwrap(resp.json())
        return LoginState(
            qrcode_key=payload["qrcode_key"],
            url=payload["url"],
            expires_at=time.time() + 180,
        )

    def render_qr_ascii(self, url: str) -> str:
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()

        lines: list[str] = []
        for row in matrix:
            pieces = ["  " if cell else "██" for cell in row]
            lines.append("".join(pieces))
        return "\n".join(lines)

    def poll_login(self, qrcode_key: str) -> tuple[str, bool]:
        resp = self._client.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
            params={"qrcode_key": qrcode_key},
        )
        payload = _unwrap(resp.json())
        code = payload["code"]
        message = payload["message"]

        if code == 0:
            return "Login succeeded.", True
        if code == 86101:
            return "Waiting for QR scan.", False
        if code == 86090:
            return "Scanned. Please confirm on your phone.", False
        if code == 86038:
            raise BiliError("The QR code expired. Please run login again.")
        raise BiliError(f"Login failed: {_api_message(code, message)}")

    def get_current_user_label(self) -> str:
        profile = self.get_current_user_profile()
        return f"{profile.uname} Lv{profile.level}" if profile.level else profile.uname

    def get_current_user_profile(self) -> UserProfile:
        resp = self._client.get("https://api.bilibili.com/x/web-interface/nav")
        payload = _unwrap(resp.json())
        if not payload.get("isLogin"):
            raise BiliError("No active login session.")
        return UserProfile(
            mid=int(payload.get("mid") or 0),
            uname=payload.get("uname") or "Unknown user",
            level=payload.get("level_info", {}).get("current_level"),
        )

    def list_favorite_folders(self) -> list[FavoriteFolder]:
        profile = self.get_current_user_profile()
        resp = self._client.get(
            "https://api.bilibili.com/x/v3/fav/folder/created/list-all",
            params={"up_mid": profile.mid},
        )
        payload = _unwrap(resp.json())
        folders = payload.get("list") or []
        if not isinstance(folders, list):
            raise BiliError("Bilibili returned an unexpected favorites response.")

        results: list[FavoriteFolder] = []
        for item in folders:
            if not isinstance(item, dict):
                continue
            results.append(
                FavoriteFolder(
                    media_id=int(item.get("id") or 0),
                    title=str(item.get("title") or "Untitled"),
                    media_count=int(item.get("media_count") or 0),
                    attr=int(item.get("attr") or 0),
                )
            )
        return results

    def list_favorite_items(
        self, media_id: int, pn: int = 1, ps: int = 20
    ) -> tuple[str, list[MediaEntry], bool, int]:
        resp = self._client.get(
            "https://api.bilibili.com/x/v3/fav/resource/list",
            params={
                "media_id": media_id,
                "platform": "web",
                "pn": pn,
                "ps": ps,
            },
        )
        payload = _unwrap(resp.json())
        info = payload.get("info") or {}
        folder_title = str(info.get("title") or f"Favorites {media_id}")
        total = int(info.get("media_count") or 0)
        medias = payload.get("medias") or []
        if not isinstance(medias, list):
            raise BiliError("Bilibili returned an unexpected favorites-items response.")

        results: list[MediaEntry] = []
        for item in medias:
            if not isinstance(item, dict):
                continue
            bvid = str(item.get("bvid") or item.get("bv_id") or "").strip()
            if not bvid:
                continue
            upper = item.get("upper") or {}
            results.append(
                MediaEntry(
                    title=str(item.get("title") or "Untitled"),
                    bvid=bvid,
                    page_index=1,
                    page_title="",
                    author=str(upper.get("name") or "Unknown"),
                    duration=int(item.get("duration") or 0),
                    cover_url=_clean_cover_url(item.get("cover")),
                    context=f"in {folder_title}",
                )
            )
        has_more = bool(payload.get("has_more")) or (pn * ps < total)
        return folder_title, results, has_more, total

    def list_history_items(
        self, ps: int = 20, max_oid: int = 0, view_at: int = 0
    ) -> tuple[list[MediaEntry], int, int, bool]:
        params: dict[str, Any] = {"ps": ps, "type": "archive"}
        if max_oid:
            params["max"] = max_oid
        if view_at:
            params["view_at"] = view_at
        resp = self._client.get(
            "https://api.bilibili.com/x/web-interface/history/cursor",
            params=params,
        )
        payload = _unwrap(resp.json())
        items = payload.get("list") or []
        if not isinstance(items, list):
            raise BiliError("Bilibili returned an unexpected history response.")

        results: list[MediaEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            history = item.get("history") or {}
            bvid = str(history.get("bvid") or "").strip()
            if not bvid:
                continue
            progress = int(item.get("progress") or 0)
            duration = int(item.get("duration") or 0)
            page_index = int(history.get("page") or 1)
            page_title = str(history.get("part") or "")
            results.append(
                MediaEntry(
                    title=str(item.get("title") or "Untitled"),
                    bvid=bvid,
                    page_index=page_index,
                    page_title=page_title,
                    author=str(item.get("author_name") or "Unknown"),
                    duration=duration,
                    cover_url=_clean_cover_url(item.get("cover")),
                    context=f"progress {format_duration(progress)} / {format_duration(duration)}",
                )
            )
        cursor = payload.get("cursor") or {}
        next_max = int(cursor.get("max") or 0)
        next_view_at = int(cursor.get("view_at") or 0)
        has_more = len(results) >= ps and next_max > 0
        return results, next_max, next_view_at, has_more

    def resolve_video(self, raw_url: str) -> VideoSelection:
        bvid = extract_bvid(raw_url)
        resp = self._client.get(
            "https://api.bilibili.com/x/web-interface/view",
            params={"bvid": bvid},
        )
        payload = _unwrap(resp.json())

        pages = payload["pages"]
        page_index = extract_page_index(raw_url)
        if page_index < 1 or page_index > len(pages):
            raise BiliError(f"Page index is out of range. This video has {len(pages)} page(s).")
        page = pages[page_index - 1]

        return VideoSelection(
            bvid=payload["bvid"],
            aid=payload["aid"],
            cid=page["cid"],
            title=payload["title"],
            page_title=page["part"],
            page_index=page_index,
        )

    def get_playback_bundle(self, video: VideoSelection) -> PlaybackBundle:
        params = {
            "bvid": video.bvid,
            "cid": video.cid,
            "qn": 80,
            "fnval": 16,
            "fnver": 0,
            "fourk": 0,
        }
        resp = self._client.get(
            "https://api.bilibili.com/x/player/playurl",
            params=params,
        )
        payload = _unwrap(resp.json())
        dash = payload.get("dash")
        if isinstance(dash, dict):
            video_track = _pick_video_track(dash.get("video") or [])
            audio_track = _pick_audio_track(dash.get("audio") or [])
            return PlaybackBundle(
                video_url=_track_url(video_track),
                audio_url=_track_url(audio_track) if audio_track else None,
                quality=int(video_track.get("id", 0)),
                cookies=_cookie_snapshot(self._client.cookies.jar),
                bvid=video.bvid,
                cid=video.cid,
            )

        durl = payload.get("durl") or []
        if durl:
            return PlaybackBundle(
                video_url=durl[0]["url"],
                audio_url=None,
                quality=int(payload.get("quality") or 0),
                cookies=_cookie_snapshot(self._client.cookies.jar),
                bvid=video.bvid,
                cid=video.cid,
            )

        raise BiliError("No playable stream was returned by Bilibili.")


def extract_bvid(raw_url: str) -> str:
    match = re.search(r"(BV[0-9A-Za-z]{10})", raw_url)
    if match:
        return match.group(1)

    parsed = urlparse(raw_url)
    if parsed.netloc in {"b23.tv", "www.b23.tv"}:
        raise BiliError("Short b23.tv links are not expanded yet. Please paste the final full video URL.")
    raise BiliError("Could not find a BV id in the URL.")


def extract_page_index(raw_url: str) -> int:
    parsed = urlparse(raw_url)
    query = parse_qs(parsed.query)
    try:
        return int(query.get("p", ["1"])[0])
    except ValueError as exc:
        raise BiliError("The page index in the URL is invalid.") from exc


def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
    code = payload.get("code", -1)
    if code != 0:
        raise BiliError(_api_message(code, payload.get("message")))
    data = payload.get("data")
    if not isinstance(data, dict):
        raise BiliError("Bilibili returned an unexpected payload.")
    return data


def _api_message(code: int, message: Any) -> str:
    raw = str(message or "").strip()
    if raw and raw.isascii() and raw != "0":
        return f"{raw} ({code})"
    return f"Bilibili API error {code}."


def format_duration(total_seconds: int) -> str:
    seconds = max(int(total_seconds), 0)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def _pick_video_track(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    if not tracks:
        raise BiliError("No DASH video tracks were returned.")

    eligible = [track for track in tracks if int(track.get("id", 0)) <= 80]
    if not eligible:
        raise BiliError("No non-premium 1080P-or-below video track was returned.")

    target_quality = max(int(track.get("id", 0)) for track in eligible)
    same_quality = [track for track in eligible if int(track.get("id", 0)) == target_quality]

    avc_tracks = [track for track in same_quality if int(track.get("codecid", 0)) == 7]
    candidates = avc_tracks or same_quality
    return max(candidates, key=lambda track: int(track.get("bandwidth", 0)))


def _pick_audio_track(tracks: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not tracks:
        return None
    return max(tracks, key=lambda track: int(track.get("bandwidth", 0)))


def _track_url(track: dict[str, Any]) -> str:
    url = track.get("baseUrl") or track.get("base_url")
    if not isinstance(url, str) or not url:
        raise BiliError("Bilibili returned a stream track without a valid URL.")
    return url


def _cookie_snapshot(jar: http.cookiejar.CookieJar) -> dict[str, str]:
    selected: dict[str, http.cookiejar.Cookie] = {}

    for cookie in jar:
        current = selected.get(cookie.name)
        if current is None or _cookie_score(cookie) >= _cookie_score(current):
            selected[cookie.name] = cookie

    return {name: cookie.value for name, cookie in selected.items()}


def _clean_cover_url(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    if raw.startswith("//"):
        return f"https:{raw}"
    if raw.startswith("http://"):
        return "https://" + raw.removeprefix("http://")
    return raw


def _cookie_score(cookie: http.cookiejar.Cookie) -> tuple[int, int, int, int]:
    domain = cookie.domain or ""
    normalized = domain.lstrip(".")
    is_bilibili = normalized.endswith("bilibili.com")
    domain_depth = len(normalized.split(".")) if normalized else 0
    path_length = len(cookie.path or "")
    secure = 1 if cookie.secure else 0
    return (
        1 if is_bilibili else 0,
        domain_depth,
        path_length,
        secure,
    )
