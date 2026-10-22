"""系统凭据库适配；不接受明文文件后端，不把密钥作为命令行参数。"""
import json
import re
import sys

SERVICE = "bili-fav-relay"


class VaultError(ValueError):
    pass


def native_backend():
    try:
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring
        elif sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring as Keyring
        else:
            from keyring.backends.SecretService import Keyring
        backend = Keyring()
        if backend.priority <= 0:
            raise RuntimeError("unavailable")
        return backend
    except Exception:
        raise VaultError("系统凭据库不可用；请解锁钥匙串/凭据库。无桌面的服务器请使用环境变量配置。") from None


class CredentialVault:
    def __init__(self, backend=None):
        self.backend = backend

    def _backend(self):
        return self.backend if self.backend is not None else native_backend()

    def _key(self, reference):
        if not isinstance(reference, str) or not re.fullmatch(r"[a-f0-9]{32}", reference):
            raise VaultError("凭据引用无效，请重新填写机器人凭证")
        return reference

    def save(self, reference, app_id, secret):
        key = self._key(reference)
        try:
            self._backend().set_password(SERVICE, key, json.dumps({"app_id": app_id, "secret": secret}))
        except VaultError:
            raise
        except Exception:
            raise VaultError("密钥未能保存到系统凭据库；请解锁或允许本程序访问后重试。") from None

    def load(self, reference):
        key = self._key(reference)
        try:
            raw = self._backend().get_password(SERVICE, key)
            data = json.loads(raw or "{}")
            app_id, secret = data.get("app_id", ""), data.get("secret", "")
            if not isinstance(app_id, str) or not isinstance(secret, str) or not app_id or not secret:
                raise ValueError("missing")
            return app_id, secret
        except VaultError:
            raise
        except Exception:
            raise VaultError("系统凭据库中没有可用密钥，请重新填写；备份配置文件不会包含密钥。") from None

    def delete(self, reference):
        key = self._key(reference)
        try:
            self._backend().delete_password(SERVICE, key)
        except Exception:
            raise VaultError("系统凭据库条目未删除，请在系统凭据管理器中处理。") from None
