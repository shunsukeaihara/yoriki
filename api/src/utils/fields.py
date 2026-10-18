import string

from django.db import models
from nanoid import generate
from rest_framework import serializers

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
