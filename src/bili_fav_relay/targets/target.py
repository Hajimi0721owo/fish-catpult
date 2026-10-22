"""绑定文件锁定机器人身份；准备媒体不会发送消息。"""
import json
from .qqbot import QQBotClient


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
        return str(self.client.send_group_media(self.group, file_info).get("id") or "")
