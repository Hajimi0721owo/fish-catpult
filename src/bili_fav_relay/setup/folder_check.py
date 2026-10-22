"""收藏夹预检与安全文案；请求预算由 folder_setup 预约，错误保留种类供退避。"""
import math
import re
from urllib.parse import urlsplit
from ..config import folder_id
from ..vendor.bili_api import BiliError, Client, allowed_url

LINK = re.compile(r"https?://[^\s　【】「」《》\"'<>，。]+")
SHORT_HOSTS = {"b23.tv", "www.b23.tv"}
MAX_ITEMS = 1000  # 50 页 × 20 条，与 max_pages 上限一致。


def extract_link(text):
    """手机 App 复制的是“【标题】 https://b23.tv/xxx”这类文本，只取其中的链接。"""
    value = str(text or "").strip()
    if re.fullmatch(r"[1-9]\d{0,19}", value):
        return value
    match = LINK.search(value)
    return match.group(0).rstrip(".,!?)）！？") if match else value


def resolve_folder(text, client_factory=Client):
    value = extract_link(text)
    if not value:
        raise ValueError("请粘贴收藏夹链接")
    if (urlsplit(value).hostname or "").lower() in SHORT_HOSTS:
        allowed_url(value)
        with client_factory().open(value) as response:
            value = response.geturl()
    return folder_id(value)


def explain(error):
    """把 B 站错误种类翻译成用户能处理的说明；不回显接口原文。"""
    code = error.details.get("api_code") if isinstance(error, BiliError) else None
    kind = getattr(error, "kind", "")
    if code in {-403, 11010, -404, 11001}:
        return "读不到这个收藏夹：它可能是私密的或已删除。在 B 站把收藏夹设为公开后再试。"
    if kind == "rate_or_risk_control":
        return "B 站暂时限制了访问，已暂停。请等页面冷却结束、确认访问恢复后再继续。"
    if kind == "network":
        return "连不上 B 站。请检查网络或代理后再试。"
    if kind == "favorite_identity":
        return "链接里的收藏夹和 B 站返回的不一致；请重新复制收藏夹页面链接。"
    suffix = f"（代码 {code}）" if isinstance(code, int) else ""
    return f"B 站没有返回这个收藏夹{suffix}。请确认链接正确、收藏夹已设为公开。"


def pages_for(count):
    """按收藏数量预留分页预算，多留一页应对检查后的新增。"""
    return max(5, min(50, math.ceil(count / 20) + 1))


def check_folder(source):
    """source 需提供 probe() -> {title, count}；失败抛出带说明的 ValueError。"""
    info = source.probe()
    count = info.get("count")
    if type(count) is not int or count < 0:
        raise ValueError("B 站没有返回收藏数量，请稍后再试。")
    if count > MAX_ITEMS:
        raise ValueError(f"这个收藏夹有 {count} 个内容，超过 {MAX_ITEMS} 个的上限。"
                         "建议新建一个专门用来分享的公开收藏夹。")
    return {"title": str(info.get("title") or "")[:80], "count": count}


SCAN_REASONS = {
    "api_error": "B 站拒绝读取这个收藏夹，可能刚改成了私密。回到第 1 步确认公开后重新保存。",
    "page_budget": "收藏夹内容比预计的多。回到第 1 步重新保存，会按实际数量调整。",
    "rate_or_risk_control": "B 站暂时限制了访问。已自动暂停读取，请过一段时间再让 agent 处理。",
    "network": "连不上 B 站。请检查网络后再试。",
    "favorite_changed": "读取期间收藏夹有变化，这次没有记录。请稍后再试。",
    "favorite_incomplete": "B 站返回的列表不完整，这次没有记录。请稍后再试。",
}


def scan_reason(kind):
    return SCAN_REASONS.get(kind, "收藏夹读取未完成；可以让 agent 运行 status 查看具体原因。")
