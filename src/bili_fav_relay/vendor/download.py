"""Download Bilibili ordinary uploads through webpage APIs, mux and validate."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import uuid

from .bili_api import BiliError, Client, media_urls, select_streams


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def transfer(client, item, path, referer):
    # Failover only for network/transient server errors, never authentication/risk-control responses.
    errors = []
    for url in media_urls(item)[:3]:
        try:
            with client.open(url, referer, media=True) as response, path.open("wb") as output:
                expected = response.headers.get("Content-Length")
                limit = getattr(client, "max_transfer_bytes", 30_000_000)
                used = getattr(client, "transferred_bytes", 0)
                if expected and int(expected) + used > limit:
                    raise BiliError("size_budget", "Media exceeds the transfer budget")
                content_type = response.headers.get("Content-Type", "").lower()
                if "text/html" in content_type or "application/json" in content_type:
                    raise BiliError("media_response", "CDN returned text instead of media")
                count = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    if count + len(chunk) + used > limit:
                        raise BiliError("size_budget", "Media exceeds the transfer budget")
                    output.write(chunk)
                    count += len(chunk)
            if not count or (expected and count != int(expected)):
                raise BiliError("incomplete_transfer", "Media transfer was empty or shorter than Content-Length")
            client.transferred_bytes = used + count
            return {"bytes": count, "sha256": digest(path), "cdn_host": urlsplit(url).hostname}
        except BiliError as error:
            errors.append(error.kind)
            if error.kind not in {"network", "incomplete_transfer"} and error.details.get("http_status", 0) < 500:
                raise
        except (OSError, TimeoutError):
            errors.append("transfer_io")
    raise BiliError("transfer_failed", "All supplied media candidates failed", attempt_errors=errors)


def verify(path, expected_duration=None, audio_required=False):
    run = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, text=True, timeout=300)
    if run.returncode:
        raise BiliError("ffprobe", "Downloaded output could not be probed")
    try:
        data = json.loads(run.stdout)
        videos = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
        audios = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
        seconds = float(data.get("format", {}).get("duration") or 0)
    except (ValueError, TypeError):
        raise BiliError("ffprobe", "Invalid media metadata") from None
    if len(videos) != 1 or seconds <= 0 or (audio_required and not audios):
        raise BiliError("media_streams", "Output is missing the expected video/audio streams")
    if expected_duration and abs(seconds - expected_duration) > max(2, expected_duration * .01):
        raise BiliError("duration", "Output duration does not match playback metadata", expected_seconds=expected_duration, actual_seconds=seconds)
    run = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-map", "0:a?", "-fps_mode:v", "passthrough", "-enc_time_base:v", "demux", "-f", "null", "-"], capture_output=True, text=True, timeout=300)
    if run.returncode or run.stderr.strip():
        raise BiliError("decode", "Full video/audio decoding failed; output is not complete")
    return {"duration_seconds": seconds, "video": {k: videos[0].get(k) for k in ["codec_name", "width", "height", "avg_frame_rate", "nb_read_frames"]}, "audio_streams": len(audios), "audio_codecs": [s.get("codec_name") for s in audios], "full_decode_passed": True, "bytes": path.stat().st_size, "sha256": digest(path), "visual_review_performed": False}


def fetch_page(client, view, page, quality, output_dir, progressive=False, probe_only=False):
    bvid, cid = view["bvid"], int(page["cid"])
    play = client.play(bvid, cid, quality, progressive)
    plan = select_streams(play)
    expected = float(play.get("timelength") or 0) / 1000 or float(page.get("duration") or 0)
    source_seconds = float(page.get("duration") or 0)
    if source_seconds and expected < source_seconds - max(2, source_seconds * .01):
        raise BiliError("preview_only", "Playback is shorter than the source page; refuse to label a preview as a full download")
    result = {"bvid": bvid, "cid": cid, "page": page["page"], "part": page.get("part"), "source_url": f"https://www.bilibili.com/video/{bvid}/?p={page['page']}", "requested_quality": quality, "returned_quality": play.get("quality"), "selected_quality": plan["quality"], "representation": plan["kind"], "expected_seconds": expected, "available_quality_descriptions": [{k: f.get(k) for k in ["quality", "new_description", "need_login", "need_vip"]} for f in play.get("support_formats", [])], "status": "probe_complete" if probe_only else "downloading"}
    if plan["kind"] == "dash":
        result["selected_video"] = {k: plan["video"].get(k) for k in ["id", "width", "height", "codecs", "bandwidth"]}
        result["audio_available"] = plan["audio"] is not None
    else:
        result["segment_count"] = len(plan["segments"])
    if probe_only:
        return result
    destination = output_dir / f"{bvid}_P{int(page['page']):03d}.mp4"
    if destination.exists():
        raise BiliError("output_exists", "Output already exists; reuse the verified file or choose a different directory", filename=destination.name)
    work = Path(tempfile.mkdtemp(prefix=f".{bvid}_P{page['page']}_", dir=output_dir))
    result["work_directory"] = str(work)
    muxed = work / "complete.mp4"
    command = ["ffmpeg", "-nostdin", "-v", "error", "-n"]
    try:
        if plan["kind"] == "dash":
            video_path = work / "video.m4s"
            result["video_transfer"] = transfer(client, plan["video"], video_path, result["source_url"])
            command += ["-i", str(video_path)]
            if plan["audio"]:
                audio_path = work / "audio.m4s"
                result["audio_transfer"] = transfer(client, plan["audio"], audio_path, result["source_url"])
                command += ["-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0"]
            else:
                command += ["-map", "0:v:0"]
        else:
            names = []
            result["segment_transfers"] = []
            for index, segment in enumerate(plan["segments"]):
                path = work / f"segment_{index:04d}.bin"
                result["segment_transfers"].append(transfer(client, segment, path, result["source_url"]))
                names.append(path.name)
            playlist = work / "segments.txt"
            playlist.write_text("".join("file '" + name + "'\n" for name in names))
            command += ["-f", "concat", "-safe", "1", "-i", str(playlist), "-map", "0:v:0", "-map", "0:a?"]
        command += ["-c", "copy", "-movflags", "+faststart", str(muxed)]
        run = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if run.returncode:
            raise BiliError("mux", "FFmpeg stream-copy mux failed; original downloaded streams remain in the work directory")
        result["validation"] = verify(muxed, expected, plan["kind"] == "dash" and plan["audio"] is not None)
        # Same-filesystem atomic publication without replacing any existing output.
        os.link(muxed, destination)
        result.update(status="download_complete", output_file=str(destination), transcoded=False)
        shutil.rmtree(work)
        result.pop("work_directory", None)
        return result
    except Exception as error:
        result["status"] = "failed"
        if isinstance(error, BiliError):
            error.details.update(work_directory=str(work), page=page["page"])
            result["error"] = {"kind": error.kind, "message": str(error), **error.details}
        # Retain only a diagnostic file and partial media in this unique attempt directory.
        (work / "attempt.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        if not isinstance(error, BiliError):
            raise BiliError("local_processing", "Local processing failed; partial media retained", work_directory=str(work), error_type=type(error).__name__) from None
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="BV, av, Bilibili video URL or b23.tv URL")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd() / "bilibili-downloads")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--page", type=int)
    group.add_argument("--all-pages", action="store_true")
    parser.add_argument("--quality", type=int, default=80, help="Requested qn, default80; server permission may return lower quality")
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--progressive", action="store_true", help="Use standard non-WBI playurl with durl media, without automatic risk-control retries")
    parser.add_argument("--cookies", type=Path, help="Explicitly supplied Netscape cookie file; never auto-read browsers")
    args = parser.parse_args()
    if args.quality < 1 or (args.page is not None and args.page < 1):
        parser.error("Quality and page must be positive")
    if not args.probe_only and (not shutil.which("ffmpeg") or not shutil.which("ffprobe")):
        parser.error("ffmpeg and ffprobe are required before downloading")
    report = {"observed_at_utc": datetime.now(timezone.utc).isoformat(), "status": "running", "access_mode": "explicit_cookie_file" if args.cookies else "anonymous", "method": "Bilibili webpage APIs", "pages": []}
    output_dir = args.output_dir.expanduser().resolve()
    try:
        client = Client(args.cookies)
        identity, url_page = client.resolve(args.source)
        view = client.view(identity)
        chosen = args.page or url_page
        pages = view["pages"] if args.all_pages else [p for p in view["pages"] if p.get("page") == chosen]
        if not pages:
            raise BiliError("page", "Selected page is not present in video metadata")
        if (view.get("rights", {}).get("pay") or view.get("is_upower_exclusive")) and not args.cookies:
            raise BiliError("authorization_required", "Source is not an ordinary anonymously playable upload")
        report.update(bvid=view["bvid"], aid=view.get("aid"), title=view.get("title"), total_pages=len(view["pages"]), selected_pages=[p["page"] for p in pages])
        if not args.probe_only:
            output_dir.mkdir(parents=True, exist_ok=True)
        for page in pages:
            print(json.dumps({"phase": "probe" if args.probe_only else "download", "bvid": view["bvid"], "page": page["page"]}), file=sys.stderr, flush=True)
            report["pages"].append(fetch_page(client, view, page, args.quality, output_dir, args.progressive, args.probe_only))
        report["status"] = "probe_complete" if args.probe_only else "download_complete"
    except BiliError as error:
        report.update(status="failed", error={"kind": error.kind, "message": str(error), **error.details})
    except Exception as error:
        report.update(status="failed", error={"kind": type(error).__name__, "message": "Unexpected failure; no completed download claimed"})
    if not args.probe_only and output_dir.is_dir():
        record = output_dir / ("download-report-" + uuid.uuid4().hex[:12] + ".json")
        report["report_file"] = str(record)
        record.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"probe_complete", "download_complete"} else 1


if __name__ == "__main__":
    sys.exit(main())
