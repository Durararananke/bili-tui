from __future__ import annotations

import hashlib
import http.cookiejar
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import httpx
import qrcode


# Bilibili WBI mixin-key permutation. Concatenate img_key + sub_key (64 hex
# chars), pick characters at these indices, take the first 32 → mixin_key.
_WBI_MIXIN_TABLE: tuple[int, ...] = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)
_WBI_FORBIDDEN = "!'()*"  # stripped from values before signing


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


@dataclass(slots=True)
class VideoSelection:
    bvid: str
    title: str
    page_title: str
    page_index: int


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


@dataclass(slots=True)
class MediaEntry:
    title: str
    bvid: str
    page_index: int
    page_title: str
    author: str
    duration: int
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
        self._wbi_keys: tuple[str, str] | None = None
        self._wbi_fetched_at: float = 0.0

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
        )

    def render_qr_ascii(self, url: str) -> str:
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()

        # Pack two module rows into one terminal row using half-block glyphs.
        # QR convention: True means a dark module. Terminals usually render
        # dark-on-light, so we invert (dark module -> space) and use the
        # opposite glyphs for the light background.
        lines: list[str] = []
        for i in range(0, len(matrix), 2):
            top = matrix[i]
            bottom = matrix[i + 1] if i + 1 < len(matrix) else [False] * len(top)
            row_chars: list[str] = []
            for t, b in zip(top, bottom):
                if not t and not b:
                    row_chars.append("█")
                elif not t and b:
                    row_chars.append("▀")
                elif t and not b:
                    row_chars.append("▄")
                else:
                    row_chars.append(" ")
            lines.append("".join(row_chars))
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
                    context=f"progress {format_duration(progress)} / {format_duration(duration)}",
                )
            )
        cursor = payload.get("cursor") or {}
        next_max = int(cursor.get("max") or 0)
        next_view_at = int(cursor.get("view_at") or 0)
        has_more = next_max > 0
        return results, next_max, next_view_at, has_more

    def _get_wbi_keys(self) -> tuple[str, str]:
        # Cache for an hour — keys rotate daily on Bilibili's side.
        now = time.time()
        if self._wbi_keys is not None and now - self._wbi_fetched_at < 3600:
            return self._wbi_keys
        resp = self._client.get("https://api.bilibili.com/x/web-interface/nav")
        # /nav returns code=-101 for unlogged users but data.wbi_img is still
        # populated, so we can't use _unwrap here.
        payload = resp.json()
        data = payload.get("data") or {}
        wbi_img = data.get("wbi_img") or {}
        img_url = str(wbi_img.get("img_url") or "")
        sub_url = str(wbi_img.get("sub_url") or "")
        if not img_url or not sub_url:
            raise BiliError("Failed to fetch WBI keys from Bilibili.")
        img_key = img_url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        sub_key = sub_url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        self._wbi_keys = (img_key, sub_key)
        self._wbi_fetched_at = now
        return self._wbi_keys

    def _sign_wbi(self, params: dict[str, Any]) -> dict[str, Any]:
        img_key, sub_key = self._get_wbi_keys()
        orig = img_key + sub_key
        mixin_key = "".join(orig[i] for i in _WBI_MIXIN_TABLE)[:32]

        signed: dict[str, Any] = dict(params)
        signed["wts"] = int(time.time())
        # strip forbidden chars from values, sort by key, build query string
        clean: list[tuple[str, str]] = []
        for key in sorted(signed.keys()):
            value = str(signed[key])
            value = "".join(ch for ch in value if ch not in _WBI_FORBIDDEN)
            clean.append((key, value))
        query = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in clean)
        w_rid = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
        signed["w_rid"] = w_rid
        return signed

    def list_feed_rcmd(
        self, fresh_idx: int = 1, ps: int = 20
    ) -> tuple[list[MediaEntry], bool]:
        params = self._sign_wbi(
            {
                "fresh_type": 4,
                "ps": ps,
                "fresh_idx": fresh_idx,
                "fresh_idx_1h": fresh_idx,
            }
        )
        resp = self._client.get(
            "https://api.bilibili.com/x/web-interface/wbi/index/top/feed/rcmd",
            params=params,
        )
        payload = _unwrap(resp.json())
        items = payload.get("item") or []
        if not isinstance(items, list):
            raise BiliError("Bilibili returned an unexpected rcmd response.")

        results: list[MediaEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            # Skip ads / live cards etc — keep only archive videos.
            if str(item.get("goto") or "") != "av":
                continue
            bvid = str(item.get("bvid") or "").strip()
            if not bvid:
                continue
            owner = item.get("owner") or {}
            stat = item.get("stat") or {}
            views = int(stat.get("view") or 0)
            results.append(
                MediaEntry(
                    title=str(item.get("title") or "Untitled"),
                    bvid=bvid,
                    page_index=1,
                    page_title="",
                    author=str(owner.get("name") or "Unknown"),
                    duration=int(item.get("duration") or 0),
                    context=f"recommended / {_format_count(views)} views",
                )
            )
        has_more = len(results) > 0
        return results, has_more

    def search_videos(
        self, keyword: str, page: int = 1, ps: int = 20
    ) -> tuple[list[MediaEntry], bool, int]:
        keyword = keyword.strip()
        if not keyword:
            return [], False, 0
        params = self._sign_wbi(
            {
                "search_type": "video",
                "keyword": keyword,
                "page": page,
                "page_size": ps,
                "platform": "pc",
            }
        )
        resp = self._client.get(
            "https://api.bilibili.com/x/web-interface/wbi/search/type",
            params=params,
        )
        payload = _unwrap(resp.json())
        results_raw = payload.get("result") or []
        if not isinstance(results_raw, list):
            raise BiliError("Bilibili returned an unexpected search response.")
        total = int(payload.get("numResults") or 0)

        results: list[MediaEntry] = []
        for item in results_raw:
            if not isinstance(item, dict):
                continue
            bvid = str(item.get("bvid") or "").strip()
            if not bvid:
                continue
            title = _strip_html_tags(str(item.get("title") or "Untitled"))
            duration_raw = item.get("duration")
            if isinstance(duration_raw, int):
                duration = duration_raw
            else:
                duration = _parse_duration_str(str(duration_raw or ""))
            views = int(item.get("play") or 0)
            results.append(
                MediaEntry(
                    title=title,
                    bvid=bvid,
                    page_index=1,
                    page_title="",
                    author=str(item.get("author") or "Unknown"),
                    duration=duration,
                    context=f"search / {_format_count(views)} views",
                )
            )
        has_more = page * ps < total if total else len(results) >= ps
        return results, has_more, total

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
            title=payload["title"],
            page_title=page["part"],
            page_index=page_index,
        )


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


def _strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _parse_duration_str(raw: str) -> int:
    raw = raw.strip()
    if not raw:
        return 0
    try:
        parts = [int(p) for p in raw.split(":")]
    except ValueError:
        return 0
    if len(parts) == 3:
        h, m, s = parts
        return h * 3600 + m * 60 + s
    if len(parts) == 2:
        m, s = parts
        return m * 60 + s
    if len(parts) == 1:
        return parts[0]
    return 0


def _format_count(value: int) -> str:
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f}亿"
    if value >= 10_000:
        return f"{value / 10_000:.1f}万"
    return str(value)


def format_duration(total_seconds: int) -> str:
    seconds = max(int(total_seconds), 0)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def _cookie_snapshot(jar: http.cookiejar.CookieJar) -> dict[str, str]:
    selected: dict[str, http.cookiejar.Cookie] = {}

    for cookie in jar:
        current = selected.get(cookie.name)
        if current is None or _cookie_score(cookie) >= _cookie_score(current):
            selected[cookie.name] = cookie

    return {name: cookie.value for name, cookie in selected.items()}


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
