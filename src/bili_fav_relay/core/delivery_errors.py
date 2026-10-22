"""目标适配器提供的脱敏投递诊断；拒绝与送达不明采用不同恢复流程。"""


class DeliveryError(RuntimeError):
    def __init__(self, kind, message, *, rejected=False, http_status=None, code=None):
        super().__init__(message)
        self.kind, self.public_message, self.rejected = kind, message, rejected
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.code = code if type(code) is int and abs(code) <= 2**63 - 1 else None


def delivery_details(error):
    """只接受目标适配器的显式安全字段，不序列化任意外部异常。"""
    if not isinstance(error, DeliveryError):
        return {}
    result = {"message": error.public_message}
    for key in ("http_status", "code"):
        value = getattr(error, key)
        if value is not None:
            result[key] = value
    return result
