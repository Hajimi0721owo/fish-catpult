"""绑定文件锁定机器人身份；准备媒体不会发送消息。"""
import json
from .qqbot import QQBotClient
from .errors import QQBotError, GROUP_PERMISSION_HINT
from ..core.delivery_errors import DeliveryError


def load_binding(config):
    path = config.data_dir / "binding.json"
    if not path.is_file():
        raise ValueError("请先运行 bind，在目标群发送本次绑定口令")
    binding = json.loads(path.read_text())
    app_id, _ = config.credentials()
    if binding.get("app_id") != app_id or not binding.get("group_openid"):
        raise ValueError("绑定的机器人与当前凭据不一致，请使用独立数据目录重新绑定")
    return binding


class Target:
    def __init__(self, config):
        binding = load_binding(config)
        self.client = QQBotClient(*config.credentials())
        self.group = binding["group_openid"]
        self.max_bytes = config.max_video_bytes

    def prepare(self, path):
        if path.stat().st_size > self.max_bytes:
            raise ValueError("文件超过视频消息大小预算")
        return self.client.upload_group_video(self.group, path)["file_info"]

    def send(self, file_info):
        try:
            return str(self.client.send_group_media(self.group, file_info).get("id") or "")
        except QQBotError as error:
            # 仅已知的业务拒绝可确认未送达；5xx 即使携带相同码仍按结果不明处理。
            rejected = error.code == 40034105 and (error.http_status is None or 400 <= error.http_status < 500)
            message = GROUP_PERMISSION_HINT if rejected else "QQ 未确认发送结果。请先核对目标群是否收到视频，再处理送达不明的任务。"
            raise DeliveryError("target_permission_denied" if rejected else "target_error", message,
                                rejected=rejected, http_status=error.http_status, code=error.code) from None
