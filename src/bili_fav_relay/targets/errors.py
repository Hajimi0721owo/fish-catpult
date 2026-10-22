GROUP_PERMISSION_HINT = (
    "QQ 拒绝主动发送（40034105）。请到目标 QQ 群的机器人设置中开启“主动发言”权限；"
    "若无权操作，请联系群主或管理员。开启后让 agent 重试这条视频，程序不会自动重发。"
)


def numeric_code(value):
    """只保留有界整数；不让平台错误字段带入令牌或响应原文。"""
    if isinstance(value, str) and len(value) <= 19 and value.lstrip("-").isascii() and value.lstrip("-").isdigit():
        value = int(value)
    return value if type(value) is int and abs(value) <= 2**63 - 1 else None


class QQBotError(RuntimeError):
    """QQ API error; only numeric diagnostic fields may leave the adapter."""
    def __init__(self, message, *, http_status=None, code=None):
        super().__init__(message)
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.code = numeric_code(code)
