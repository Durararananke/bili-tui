from unittest import TestCase
from unittest.mock import Mock

from bili_tui.bilibili import (
    BiliClient,
    BiliError,
    MediaEntry,
    VideoSelection,
    extract_bvid,
    extract_page_index,
    format_duration,
)


def client_returning(data: dict) -> BiliClient:
    client = BiliClient()
    client._client.close()

    response = Mock()
    response.json.return_value = {"code": 0, "data": data}
    client._client = Mock()
    client._client.get.return_value = response
    return client


class UrlParsingTests(TestCase):
    def test_extracts_video_and_page(self):
        url = "https://www.bilibili.com/video/BV1xx411c7mD?p=3"

        self.assertEqual(extract_bvid(url), "BV1xx411c7mD")
        self.assertEqual(extract_page_index(url), 3)

    def test_rejects_invalid_page(self):
        with self.assertRaisesRegex(BiliError, "page index"):
            extract_page_index("https://www.bilibili.com/video/BV1xx411c7mD?p=nope")

    def test_formats_durations(self):
        self.assertEqual(format_duration(65), "1:05")
        self.assertEqual(format_duration(3661), "1:01:01")


class BiliClientParsingTests(TestCase):
    def test_resolves_selected_page(self):
        client = client_returning(
            {
                "bvid": "BV1xx411c7mD",
                "aid": 1,
                "title": "Example",
                "pages": [
                    {"cid": 10, "part": "First"},
                    {"cid": 20, "part": "Second"},
                ],
            }
        )
        self.addCleanup(client.close)

        selection = client.resolve_video(
            "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
        )

        self.assertEqual(
            selection,
            VideoSelection(
                bvid="BV1xx411c7mD",
                title="Example",
                page_title="Second",
                page_index=2,
            ),
        )

    def test_parses_history_entry(self):
        client = client_returning(
            {
                "list": [
                    {
                        "title": "Watched video",
                        "author_name": "Uploader",
                        "duration": 120,
                        "progress": 30,
                        "history": {
                            "bvid": "BV1xx411c7mD",
                            "page": 1,
                            "part": "",
                        },
                    }
                ],
                "cursor": {"max": 99, "view_at": 123},
            }
        )
        self.addCleanup(client.close)

        entries, next_max, next_view_at, has_more = client.list_history_items()

        self.assertEqual(
            entries,
            [
                MediaEntry(
                    title="Watched video",
                    bvid="BV1xx411c7mD",
                    page_index=1,
                    page_title="",
                    author="Uploader",
                    duration=120,
                    context="progress 0:30 / 2:00",
                )
            ],
        )
        self.assertEqual((next_max, next_view_at, has_more), (99, 123, True))
