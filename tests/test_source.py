from pathlib import Path
import unittest
from bili_fav_relay.config import Config, folder_id
from bili_fav_relay.sources.favorites import Favorites
from bili_fav_relay.vendor.bili_api import BiliError, Client


def response(ids, more=False, count=3):
    return {"info": {"id": 1, "media_count": count}, "has_more": more,
            "medias": [{"id": n, "type": 2, "bvid": f"BV{n:010d}", "fav_time": n} for n in ids]}


class ClientFixture:
    def __init__(self, pages):
        self.pages, self.calls = pages, 0

    def api(self, route, params):
        self.calls += 1
        if route.endswith('/ids'):
            return self.pages[-1]
        value = self.pages[params["pn"] - 1]
        if isinstance(value, Exception):
            raise value
        return value


class FavoritesTest(unittest.TestCase):
    def source(self, pages, max_pages=5):
        self.waits = []
        return Favorites(Config(Path("."), "1", max_pages=max_pages), ClientFixture(pages), self.waits.append)

    def test_all_pages_oldest_first_and_paced(self):
        source = self.source([response([3, 2], True), response([1])])
        rows = source.snapshot()
        self.assertEqual([r["id"] for r in rows], [f"BV{n:010d}" for n in [1, 2, 3]])
        self.assertEqual(self.waits, [3])

    def test_page_failure_does_not_return_partial_list(self):
        source = self.source([response([3], True), BiliError("network", "failed")])
        with self.assertRaises(BiliError):
            source.snapshot()

    def test_page_budget_not_silent_truncation(self):
        with self.assertRaisesRegex(BiliError, "分页预算"):
            self.source([response([3], True)], max_pages=1).snapshot()

    def test_changed_count_or_overlap_aborts_snapshot(self):
        for second in [response([1], count=4), response([3])]:
            with self.subTest(second=second), self.assertRaises(BiliError):
                self.source([response([3, 2], True), second]).snapshot()

    def test_empty_folder_valid(self):
        self.assertEqual(self.source([response([], count=0)]).snapshot(), [])

    def test_folder_url_and_no_arbitrary_host(self):
        self.assertEqual(folder_id("https://space.bilibili.com/123/favlist?fid=456"), "456")
        for value in ["https://evil.example/?fid=123", "../123", "0"]:
            with self.assertRaises(ValueError):
                folder_id(value)

    def test_cookie_files_refused(self):
        with self.assertRaisesRegex(BiliError, "anonymous"):
            Client("cookies.txt")

    def test_complete_ids_fill_missing_detail_without_losing_baseline(self):
        ids = [{"id": n, "type": 2, "bvid": f"BV{n:010d}"} for n in [1, 2, 3]]
        source = self.source([response([3, 2]), ids])
        self.assertEqual({r['id'] for r in source.snapshot()}, {r['bvid'] for r in ids})
        self.assertEqual(source.client.calls, 2)
        self.assertEqual(self.waits, [3])

    def test_incomplete_id_list_still_rejects_snapshot(self):
        source = self.source([response([3, 2]), []])
        with self.assertRaises(BiliError):
            source.snapshot()
