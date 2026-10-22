"""匿名读取公开收藏夹；分页失败不把半份结果当成完整基线。"""
import re
import time
from ..vendor.bili_api import Client, BiliError


class Favorites:
    def __init__(self, config, client=None, sleep=time.sleep):
        self.config, self.client, self.sleep = config, client or Client(), sleep

    def page(self, page=1, size=20):
        data = self.client.api("/x/v3/fav/resource/list", {
            "media_id": self.config.folder, "pn": page, "ps": size,
            "order": "mtime", "platform": "web", "type": 0})
        if not isinstance(data, dict) or not isinstance(data.get("info"), dict):
            raise BiliError("favorite_shape", "收藏夹返回格式异常")
        if str(data["info"].get("id")) != self.config.folder:
            raise BiliError("favorite_identity", "接口返回了不同收藏夹")
        if type(data.get("has_more")) is not bool or not isinstance(data.get("medias") or [], list):
            raise BiliError("favorite_shape", "收藏夹缺少分页状态")
        self.title = str(data["info"].get("title") or "")
        return data

    def probe(self):
        """设置预检：单次请求一条，只确认公开并取得标题与数量。"""
        info = self.page(1, size=1)["info"]
        return {"title": self.title, "count": info.get("media_count")}

    def snapshot(self):
        items, identities, count = [], set(), None
        for page in range(1, self.config.max_pages + 1):
            if page > 1:
                self.sleep(self.config.page_gap_seconds)
            data = self.page(page)
            observed_count = data["info"].get("media_count")
            if count is not None and observed_count != count:
                raise BiliError("favorite_changed", "分页期间收藏夹数量变化，下个周期再核对")
            count = observed_count
            rows = data.get("medias") or []
            if not rows and data["has_more"]:
                raise BiliError("favorite_paging", "空页仍声明有下一页，已停止")
            for row in rows:
                identity = (row.get("type"), row.get("id"))
                if identity in identities:
                    raise BiliError("favorite_changed", "分页结果重叠，本次不提交基线")
                identities.add(identity)
                bvid = row.get("bvid") or row.get("bv_id") or ""
                if row.get("type") == 2 and re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid):
                    items.append({"id": bvid, "title": str(row.get("title") or bvid),
                                  "fav_time": int(row.get("fav_time") or 0)})
            if not data["has_more"]:
                if type(count) is not int or len(identities) > count:
                    raise BiliError("favorite_incomplete", "列表数量与收藏夹总数不符，本次不提交")
                if len(identities) < count:
                    # 明细可能不返回某些收藏；用完整 ID 清单补齐历史，而非忽略数量差异。
                    self.sleep(self.config.page_gap_seconds)
                    complete = self.complete_ids(count)
                    known = {item["id"] for item in items}
                    all_ids = {item["id"] for item in complete}
                    if not known <= all_ids:
                        raise BiliError("favorite_changed", "明细与 ID 清单不一致，下轮再核对")
                    items.extend(item for item in complete if item["id"] not in known)
                return sorted(items, key=lambda item: (item["fav_time"], item["id"]))
        raise BiliError("page_budget", "收藏夹超过本次分页预算；请调整 max_pages 后重新运行")

    def complete_ids(self, expected_count):
        if type(expected_count) is not int or not 0 <= expected_count <= self.config.max_pages * 20:
            raise BiliError("page_budget", "收藏夹数量超出本次预算")
        rows = self.client.api("/x/v3/fav/resource/ids", {"media_id": self.config.folder, "platform": "web"})
        if not isinstance(rows, list) or len(rows) != expected_count:
            raise BiliError("favorite_incomplete", "完整 ID 清单仍与收藏夹数量不一致")
        identities, result = set(), []
        for row in rows:
            identity = (row.get("type"), row.get("id"))
            if identity in identities:
                raise BiliError("favorite_incomplete", "完整 ID 清单含重复项")
            identities.add(identity)
            if row.get("type") != 2:
                continue
            bvid = row.get("bvid") or row.get("bv_id") or ""
            if not re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid):
                raise BiliError("favorite_incomplete", "完整 ID 清单缺少可用视频标识")
            result.append({"id": bvid, "title": bvid, "fav_time": 0})
        return result
