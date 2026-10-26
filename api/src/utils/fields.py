import string
from typing import TYPE_CHECKING

from django.db import models
from django.db.models.query_utils import DeferredAttribute
from nanoid import generate
from rest_framework import serializers

from .crypto import decrypt_secret, encrypt_secret, secret_hint

NANOID_ALPHABET = string.ascii_letters + string.digits
NANOID_SIZE = 22


def generate_uid() -> str:
    """APIやURLに出すID（22文字の英数字）を生成する"""
    return generate(NANOID_ALPHABET, NANOID_SIZE)


class NanoidField(models.CharField):
    """APIやURLに出すID。内部の整数PKの代わりに `id` としてAPIに出す（backend-conventions.md §2.1）"""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", NANOID_SIZE)
        kwargs.setdefault("default", generate_uid)
        kwargs.setdefault("editable", False)
        super().__init__(*args, **kwargs)


class NormalizedTextField(serializers.CharField):
    """改行コードをLFに揃え、行末の空白を除く"""

    def to_internal_value(self, data) -> str:
        value = super().to_internal_value(data)
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        return "\n".join(line.rstrip() for line in value.split("\n"))


class SecretValue:
    """暗号化して保存したSecret（API key、パスワード等）の値。

    平文は `reveal()` でだけ取り出す。`str()` や `repr()` は末尾の4文字だけを含むので、
    ログやSerializerの出力に紛れても平文は出ない。
    """

    __slots__ = ("ciphertext",)

    def __init__(self, ciphertext: str):
        self.ciphertext = ciphertext

    def __bool__(self) -> bool:
        return bool(self.ciphertext)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SecretValue) and other.ciphertext == self.ciphertext

    def __hash__(self) -> int:
        return hash(self.ciphertext)

    def __str__(self) -> str:
        return f"****{self.hint}" if self else ""

    def __repr__(self) -> str:
        return f"SecretValue({str(self)!r})"

    def reveal(self) -> str:
        """平文を返す。外部へ接続する処理（Provider Adapter、Connector）の中でだけ呼び、戻り値をログや応答に含めない"""
        return decrypt_secret(self.ciphertext) if self else ""

    @property
    def hint(self) -> str:
        """画面に出す末尾の4文字（短い値は出さない）"""
        return secret_hint(self.reveal()) if self else ""


# 鍵の交換（rotate_secret_encryption）で回る、Secretを持つ全フィールド。EncryptedSecretField が自分を登録する
ENCRYPTED_SECRET_FIELDS: list[tuple[type[models.Model], str]] = []


if TYPE_CHECKING:
    # django-stubs に「代入は文字列か SecretValue、読み出しは SecretValue」と教える（実行時は TextField）
    _EncryptedSecretFieldBase = models.TextField[str | SecretValue, SecretValue]
else:
    _EncryptedSecretFieldBase = models.TextField


class _SecretDescriptor(DeferredAttribute):
    """代入された平文をその場で暗号化し、インスタンスに平文を残さない"""

    def __set__(self, instance, value):
        instance.__dict__[self.field.attname] = self.field.to_python(value)


class EncryptedSecretField(_EncryptedSecretFieldBase):
    """Secretを暗号化して保存するModelのフィールド（backend-conventions.md §6.4）。

    値は `SecretValue`。`instance.api_key = "sk-..."` のように平文の文字列を代入すると、その場で暗号化する。
    空文字・None は「Secretなし」。Django管理画面には出さない（`editable=False`）。
    """

    descriptor_class = _SecretDescriptor

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("blank", True)
        kwargs.setdefault("editable", False)
        super().__init__(*args, **kwargs)

    def contribute_to_class(self, cls, name, private_only=False):
        super().contribute_to_class(cls, name, private_only=private_only)
        # マイグレーションの中で作る過去の形のモデル（モジュールが "__fake__"）は登録しない。
        # 今のDBにない列を持ちうるので、鍵の入れ替え（rotate_secret_encryption）で読むと失敗する
        if not cls._meta.abstract and cls.__module__ != "__fake__":
            ENCRYPTED_SECRET_FIELDS.append((cls, name))

    def from_db_value(self, value, expression, connection) -> SecretValue:
        return SecretValue(value or "")

    def to_python(self, value) -> SecretValue:
        if isinstance(value, SecretValue):
            return value
        return SecretValue(encrypt_secret(value)) if value else SecretValue("")

    def get_prep_value(self, value) -> str:
        return self.to_python(value).ciphertext
