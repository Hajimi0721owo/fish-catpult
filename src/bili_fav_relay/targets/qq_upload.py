# 改编自 dy experiment/ux-polish 当前工作区，原 MIT 许可见 THIRD_PARTY_LICENSES.md。
from __future__ import annotations
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable
from .errors import QQBotError

class UploadMixin:
    def _put_upload_part(self, url: str, data: bytes, *, attempts: int = 3) -> None:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443} or not any(
            host == d or host.endswith("." + d) for d in ("myqcloud.com", "qq.com", "qpic.cn")) :
            raise QQBotError("QQ 分片响应中没有有效的预签名 URL")
        from .qqbot import NoRedirect
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(data)),
                },
                method="PUT",
            )
            try:
                with urllib.request.build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                    response.read()
                return
            except urllib.error.HTTPError as error:
                failure = f"HTTP {error.code}"
                if error.code < 500:
                    raise QQBotError(f"QQ 分片上传拒绝：{failure}") from None
            except urllib.error.URLError as error:
                failure = "network error"
            if attempt < attempts:
                time.sleep(min(attempt, 2))
        # Never include the presigned URL in errors or logs.
        raise QQBotError(f"QQ 视频分片上传失败（重试 {attempts} 次）：{failure}")

    @staticmethod
    def _file_checksums(path: Path) -> tuple[str, str, str]:
        whole_md5 = hashlib.md5(usedforsecurity=False)
        whole_sha1 = hashlib.sha1(usedforsecurity=False)
        first_md5 = hashlib.md5(usedforsecurity=False)
        first_remaining = 10_002_432
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                whole_md5.update(chunk)
                whole_sha1.update(chunk)
                if first_remaining:
                    prefix = chunk[:first_remaining]
                    first_md5.update(prefix)
                    first_remaining -= len(prefix)
        return whole_md5.hexdigest(), whole_sha1.hexdigest(), first_md5.hexdigest()

    def upload_group_video(
        self,
        group_openid: str,
        video_path: Path,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Upload one local MP4 with QQ's documented multipart flow."""
        notify = progress or (lambda _: None)
        path = Path(video_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"找不到视频文件：{path}")
        if path.suffix.lower() != ".mp4":
            raise ValueError("QQ 视频消息目前只支持 MP4 文件")
        file_size = path.stat().st_size
        if file_size <= 0:
            raise ValueError("视频文件不能为空")
        if file_size > 200 * 1024 * 1024:
            raise ValueError("视频超过 QQ 官方接口的 200 MB 硬限制")
        encoded_group = self._group_path(group_openid)

        notify("正在计算视频校验值…")
        md5, sha1, md5_10m = self._file_checksums(path)
        notify("正在向 QQ 申请分片上传…")
        prepared = self.call_api(
            "POST",
            f"/v2/groups/{encoded_group}/upload_prepare",
            payload={
                "file_type": 2,
                "file_size": str(file_size),
                "file_name": path.name,
                "md5": md5,
                "sha1": sha1,
                "md5_10m": md5_10m,
            },
        )
        upload_id = str(prepared.get("upload_id") or "").strip()
        raw_parts = prepared.get("parts")
        if not upload_id or not isinstance(raw_parts, list) or not raw_parts:
            raise QQBotError("QQ 预上传响应缺少 upload_id 或分片列表")
        try:
            default_block_size = int(prepared.get("block_size") or 5 * 1024 * 1024)
        except (TypeError, ValueError) as error:
            raise QQBotError("QQ 预上传响应中的 block_size 无效") from error
        if default_block_size <= 0:
            raise QQBotError("QQ 预上传响应中的 block_size 无效")

        parts: list[dict[str, Any]] = []
        for raw_part in raw_parts:
            if not isinstance(raw_part, dict):
                raise QQBotError("QQ 预上传响应中的分片格式无效")
            try:
                index = int(raw_part["index"])
                block_size = int(raw_part.get("block_size") or default_block_size)
            except (KeyError, TypeError, ValueError) as error:
                raise QQBotError("QQ 预上传响应中的分片参数无效") from error
            parts.append({
                "index": index,
                "block_size": block_size,
                "presigned_url": str(raw_part.get("presigned_url") or ""),
            })
        parts.sort(key=lambda item: item["index"])
        if len({part["index"] for part in parts}) != len(parts):
            raise QQBotError("QQ 返回重复分片编号")

        bytes_consumed = 0
        with path.open("rb") as stream:
            for position, part in enumerate(parts, start=1):
                # `index` is an identifier that must be echoed to QQ, not a
                # byte offset. Although the docs describe zero-based indexes,
                # production responses may use one-based part numbers.
                remaining = file_size - bytes_consumed
                expected_size = min(part["block_size"], remaining)
                if part["index"] < 0 or part["block_size"] <= 0 or expected_size <= 0:
                    raise QQBotError("QQ 预上传响应包含越界分片")
                chunk = stream.read(expected_size)
                if len(chunk) != expected_size:
                    raise QQBotError("读取本地视频分片失败")
                notify(f"正在上传视频分片 {position}/{len(parts)}…")
                self._put_upload_part(part["presigned_url"], chunk)
                self.call_api(
                    "POST",
                    f"/v2/groups/{encoded_group}/upload_part_finish",
                    payload={
                        "upload_id": upload_id,
                        "part_index": part["index"],
                        "block_size": str(len(chunk)),
                        "md5": hashlib.md5(chunk, usedforsecurity=False).hexdigest(),
                    },
                )
                bytes_consumed += len(chunk)

        if bytes_consumed != file_size:
            raise QQBotError(
                f"QQ 预上传分片不足：仅覆盖 {bytes_consumed}/{file_size} 字节"
            )

        notify("分片上传完成，正在合并视频…")
        merged = self.call_api(
            "POST",
            f"/v2/groups/{encoded_group}/files",
            payload={
                "file_type": 2,
                "srv_send_msg": False,
                "file_name": path.name,
                "upload_id": upload_id,
            },
        )
        if not str(merged.get("file_info") or "").strip():
            raise QQBotError("QQ 合并视频成功，但响应中没有 file_info")
        return merged
