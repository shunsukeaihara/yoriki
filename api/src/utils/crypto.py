"""Secret（LLMのAPI key、Connectorのパスワード等）をDBに保存するための暗号化。

暗号化の鍵は環境変数 `YORIKI_SECRET_ENCRYPTION_KEYS` から読み、DBには置かない（DBやそのバックアップだけでは読めない）。
平文は必要な処理の中でだけ復号し、APIの応答・画面・ログ・Sandboxへ出さない（design.md §4.8）。
"""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

__all__ = [
    "InvalidToken",
    "decrypt_secret",
    "encrypt_secret",
    "is_encryption_configured",
    "rotate_secret",
    "secret_hint",
]


def _fernet() -> MultiFernet:
    keys = settings.YORIKI_SECRET_ENCRYPTION_KEYS
    if not keys:
        raise ImproperlyConfigured("YORIKI_SECRET_ENCRYPTION_KEYS が設定されていません")
    try:
        return MultiFernet([Fernet(key) for key in keys])
    except ValueError as exc:
        raise ImproperlyConfigured("YORIKI_SECRET_ENCRYPTION_KEYS の鍵の形式が正しくありません") from exc


def is_encryption_configured() -> bool:
    return bool(settings.YORIKI_SECRET_ENCRYPTION_KEYS)


def encrypt_secret(value: str) -> str:
    """先頭の鍵で暗号化する"""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    """いずれかの鍵で復号する。どの鍵でも復号できなければ InvalidToken"""
    return _fernet().decrypt(token.encode()).decode()


def rotate_secret(token: str) -> str:
    """先頭の鍵で暗号化し直す（鍵の交換）"""
    return _fernet().rotate(token.encode()).decode()


def secret_hint(value: str) -> str:
    """画面に出す末尾の4文字。短い値は推測されやすいので出さない"""
    return value[-4:] if len(value) >= 12 else ""
