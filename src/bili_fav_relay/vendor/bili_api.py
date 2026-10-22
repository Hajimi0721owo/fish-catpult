"""Bilibili public webpage APIs; standard WBI requests, no browser extraction."""
import hashlib
import http.cookiejar
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, HTTPCookieProcessor, Request

API = "https://api.bilibili.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
# WBI protocol permutation, as used by the public web client.
MIXIN = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]


class BiliError(Exception):
    def __init__(self, kind, message, **details):
        super().__init__(message)
        self.kind, self.details = kind, details


def allowed_url(url, media=False):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    domains = ["bilibili.com", "b23.tv"]
    if media:
        domains += ["bilivideo.com", "bilivideo.cn", "akamaized.net", "biliapi.net"]
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.port not in {None, 80, 443} or not any(host == d or host.endswith("." + d) for d in domains):
        raise BiliError("invalid_host", "URL is outside the supported Bilibili domains")
    return url


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_url(newurl, media=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def sign_wbi(params, key, now=None):
    values = {**params, "wts": int(time.time() if now is None else now)}
    clean = {k: re.sub(r"[!'()*]", "", str(v)) for k, v in sorted(values.items())}
    clean["w_rid"] = hashlib.md5((urlencode(clean) + key).encode()).hexdigest()
    return clean


class Client:
    def __init__(self, cookies=None, timeout=25, opener=None):
        if cookies:
            raise BiliError("cookies_disabled", "This standalone tool only supports anonymous access")
        jar = http.cookiejar.MozillaCookieJar()
        if cookies:
            try:
                jar.load(str(Path(cookies).expanduser()), ignore_discard=True, ignore_expires=False)
            except (OSError, http.cookiejar.LoadError):
                raise BiliError("cookie_file", "Cannot read the explicitly supplied Netscape cookie file") from None
        self.opener = opener or build_opener(SafeRedirect())
        self.timeout, self.cookie_supplied = timeout, bool(cookies)
        self._key, self._key_time = None, 0

    def open(self, url, referer="https://www.bilibili.com/", media=False):
        allowed_url(url, media)
        request = Request(url, headers={"User-Agent": UA, "Referer": referer})
        try:
            response = self.opener.open(request, timeout=self.timeout)
            allowed_url(response.geturl(), media)
            return response
        except HTTPError as error:
            kind = "rate_or_risk_control" if error.code in {412, 429} else "http_error"
            raise BiliError(kind, "Bilibili request was rejected; no proxy rotation or repeated retry", http_status=error.code) from None
        except (URLError, TimeoutError, OSError):
            raise BiliError("network", "Request could not complete; preserve this attempt before retrying") from None

    def api(self, route, params=None, guest_nav=False):
        with self.open(API + route + ("?" + urlencode(params) if params else "")) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise BiliError("response_size", "API response exceeded the metadata size limit")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise BiliError("response_format", "Expected JSON; do not treat an HTML error page as video") from None
        if not isinstance(result, dict):
            raise BiliError("response_format", "Unexpected API response shape")
        code = result.get("code")
        if code != 0 and not (guest_nav and code == -101 and isinstance(result.get("data"), dict)):
            kind = "rate_or_risk_control" if code in {-352, -412, -509} else "api_error"
            raise BiliError(kind, "Public API did not grant the requested resource", api_code=code, endpoint=route)
        return result.get("data")

    def wbi(self, params):
        if not self._key or time.time() - self._key_time > 30:
            data = self.api("/x/web-interface/nav", guest_nav=True)
            images = (data or {}).get("wbi_img", {})
            joined = "".join(Path(urlsplit(images.get(k, "")).path).stem for k in ("img_url", "sub_url"))
            if len(joined) < 64:
                raise BiliError("wbi_metadata", "Public navigation response did not contain usable WBI keys")
            self._key, self._key_time = "".join(joined[i] for i in MIXIN)[:32], time.time()
        return sign_wbi(params, self._key)

    def resolve(self, source):
        value = source.strip()
        if value.startswith(("http://", "https://")):
            allowed_url(value)
            parsed = urlsplit(value)
            if parsed.hostname in {"b23.tv", "www.b23.tv"}:
                with self.open(value) as response:
                    value = response.geturl()
                parsed = urlsplit(value)
            match = re.search(r"/video/(BV[A-Za-z0-9]{10}|av[0-9]+)(?:/|$)", parsed.path, re.I)
            if not match:
                raise BiliError("unsupported_source", "Use an ordinary upload BV/av link; live and bangumi links are separate workflows")
            identifier = match.group(1)
            raw_page = parse_qs(parsed.query).get("p", ["1"])[0]
            if not raw_page.isdigit() or int(raw_page) < 1:
                raise BiliError("page", "URL p must be a positive page number")
            page = int(raw_page)
        else:
            identifier, page = value, 1
        if re.fullmatch(r"BV[A-Za-z0-9]{10}", identifier, re.I):
            return {"bvid": "BV" + identifier[2:]}, page
        if re.fullmatch(r"av[1-9][0-9]{0,19}", identifier, re.I):
            return {"aid": int(identifier[2:])}, page
        raise BiliError("identifier", "Expected BV identifier, av identifier, or supported video URL")

    def view(self, identity):
        data = self.api("/x/web-interface/wbi/view/detail", self.wbi({**identity, "platform": "web"}))
        view = data.get("View", data) if isinstance(data, dict) else None
        if not isinstance(view, dict) or not view.get("bvid") or not view.get("pages"):
            raise BiliError("video_metadata", "Video metadata has no usable BV and page list")
        return view

    def play(self, bvid, cid, quality=80, progressive=False):
        params = {"bvid": bvid, "cid": cid, "qn": quality, "fnval": 1 if progressive else 4048, "fnver": 0, "fourk": 1}
        route = "/x/player/playurl" if progressive else "/x/player/wbi/playurl"
        data = self.api(route, params if progressive else self.wbi(params))
        if not isinstance(data, dict):
            raise BiliError("play_metadata", "Playback API returned no stream metadata")
        return data


def media_urls(item):
    first = item.get("baseUrl") or item.get("base_url") or item.get("url")
    backups = item.get("backupUrl") or item.get("backup_url") or []
    urls = [first] + (backups if isinstance(backups, list) else [])
    accepted = []
    for url in urls:
        if not isinstance(url, str) or not url:
            continue
        try:
            accepted.append(allowed_url(url, media=True))
        except (BiliError, ValueError):
            # 部分媒体包含非标准端口候选；仅丢弃该候选，不使合法 CDN 一起失效。
            continue
    return accepted


def select_streams(play):
    dash = play.get("dash") or {}
    if dash.get("video"):
        videos = [s for s in dash["video"] if media_urls(s)]
        if not videos:
            raise BiliError("no_video", "No downloadable video representation")
        limit = int(play.get("quality") or max(int(s.get("id", 0)) for s in videos))
        videos = [s for s in videos if int(s.get("id", 0)) <= limit]
        if not videos:
            raise BiliError("no_video", "No permitted video stream at the returned quality")
        video = max(videos, key=lambda s: (int(s.get("id", 0)), int(s.get("height", 0)), str(s.get("codecs", "")).startswith("avc"), int(s.get("bandwidth", 0))))
        audios = [s for s in dash.get("audio") or [] if media_urls(s)]
        if dash.get("audio") and not audios:
            raise BiliError("no_audio", "Declared audio has no supported media URL")
        audio = max(audios, key=lambda s: (str(s.get("codecs", "")).startswith("mp4a"), int(s.get("bandwidth", 0)))) if audios else None
        special_audio = (dash.get("flac") or {}).get("audio") or (dash.get("dolby") or {}).get("audio")
        if audio is None and special_audio:
            raise BiliError("audio_format", "No ordinary audio representation; inspect the supplied audio formats")
        return {"kind": "dash", "video": video, "audio": audio, "quality": video.get("id")}
    segments = play.get("durl") or []
    if segments and all(media_urls(s) for s in segments):
        return {"kind": "durl", "segments": sorted(segments, key=lambda s: s.get("order", 0)), "quality": play.get("quality")}
    raise BiliError("no_streams", "API returned no downloadable media; authentication or availability may be required")
