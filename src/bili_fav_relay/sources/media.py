"""独立进程限制下载总时长；只发布经过完整解码的 MP4。"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from ..vendor.bili_api import BiliError, Client
from ..vendor.download import fetch_page


def download_one(config, bvid):
    if not re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid):
        raise ValueError("无效的视频 ID")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise BiliError("ffmpeg_missing", "需要安装 ffmpeg/ffprobe")
    output = config.data_dir / "media" / bvid
    output.mkdir(parents=True, exist_ok=True)
    for report in output.glob("result.json"):
        result = json.loads(report.read_text())
        path = Path(result["output_file"])
        if path.parent == output and path.is_file():
            from ..vendor.download import digest
            if digest(path) == result["validation"]["sha256"]:
                return path
    used = sum(p.stat().st_size for p in output.parent.rglob("*") if p.is_file())
    if used + config.max_video_bytes * 3 > config.max_cache_bytes:
        raise BiliError("cache_budget", "本地缓存预算不足，请清理已发送媒体或增加预算")
    # 限额作为数字传递，无 shell，无 Cookie；所有中间文件只在本订阅数据目录。
    command = [sys.executable, "-m", "bili_fav_relay.sources.media", bvid, str(output),
               str(config.max_video_bytes), str(config.max_duration_seconds), config.multipart_policy]
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        raise BiliError("download_timeout", "下载达到 15 分钟总时限") from None
    if run.returncode:
        try:
            kind = json.loads(run.stdout)["error"]
        except (ValueError, KeyError):
            kind = "media_subprocess"
        raise BiliError(kind, "视频下载或校验失败")
    result = json.loads((output / "result.json").read_text())
    return Path(result["output_file"])


def main():
    bvid, directory, max_bytes, max_duration, policy = sys.argv[1:]
    output = Path(directory)
    client = Client()
    client.max_transfer_bytes = int(max_bytes)
    try:
        view = client.view({"bvid": bvid})
        if view.get("rights", {}).get("pay") or view.get("is_upower_exclusive"):
            raise BiliError("authorization_required", "只处理公开普通投稿")
        if len(view["pages"]) > 1 and policy != "first":
            raise BiliError("multipart_requires_choice", "多 P 视频需要明确选择 first 策略")
        page = view["pages"][0]
        if not 0 < float(page.get("duration") or 0) <= int(max_duration):
            raise BiliError("duration_budget", "视频超过本地时长预算或时长未知")
        destination = output / f"{bvid}_P{int(page['page']):03d}.mp4"
        if destination.exists():
            # 下载发布成功与写报告之间崩溃时，复验现有文件，避免重新下载或覆盖。
            from ..vendor.download import verify
            result = {"output_file": str(destination), "validation": verify(destination, float(page['duration'])),
                      "status": "download_complete", "recovered": True}
        else:
            result = fetch_page(client, view, page, 16, output)
        validation = result["validation"]
        if validation["bytes"] > int(max_bytes):
            raise BiliError("size_budget", "MP4 超过视频消息预算")
        if validation["video"]["codec_name"] != "h264" or any(c != "aac" for c in validation["audio_codecs"]):
            raise BiliError("codec_incompatible", "需要 H.264/AAC 视频")
        result["total_pages"] = len(view["pages"])
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({"status": "download_complete"}))
        return 0
    except BiliError as error:
        print(json.dumps({"error": error.kind}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
