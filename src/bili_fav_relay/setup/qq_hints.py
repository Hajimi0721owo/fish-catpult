"""把 QQ 开放平台的错误码翻译成用户能处理的下一步；只使用数字码，不回显响应原文。"""
import re
from ..targets.errors import QQBotError


def qq_reason(error, stage="验证"):
    if not isinstance(error, QQBotError):
        return None
    message = str(error)
    if "等待绑定超时" in message:
        return ("口令已过期，没有在群里收到它。请确认机器人已在群里、发送时先 @ 了机器人，"
                "然后重新生成口令。")
    if "事件权限" in message:
        return "QQ 拒绝了消息通道。请在机器人后台确认已开通群聊 @ 消息事件，再重新生成口令。"
    match = re.search(r"rejected: (-?\d+)", message)
    code = int(match.group(1)) if match else None
    if code == 11298:
        return ("QQ 开放平台拒绝了这台电脑的 IP（11298）。在机器人后台“开发设置 → IP 白名单”"
                "添加本机公网 IP 后再试；家里宽带的 IP 变了也要重新添加。")
    if "network" in message:
        return "连不上 QQ 开放平台。请检查网络或代理后再试。"
    status = re.search(r"HTTP (\d{3})", message)
    if status and status.group(1).startswith("5"):
        return "QQ 开放平台暂时不可用，请过几分钟再试。"
    detail = f"（代码 {code}）" if code is not None else (f"（HTTP {status.group(1)}）" if status else "")
    if stage == "绑定":
        return f"机器人连接群消息通道失败{detail}。请确认机器人已加入目标群，并已开通群聊 @ 消息事件。"
    return (f"QQ 开放平台没有通过{stage}{detail}。请核对 AppID 和 AppSecret 来自同一个机器人；"
            "重置过密钥的话要用新的那一个。")
