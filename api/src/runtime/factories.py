from decimal import Decimal

import factory
from factory.django import DjangoModelFactory

from user.factories import UserFactory

from .models import RuntimeProfile

DIGEST = "sha256:" + "a" * 64


def version_fields(**overrides) -> dict:
    """版の中身の既定値（sandbox/CONTRACT.md §2の既定の上限）"""
    return {
        "image_repository": "docker.io/yoriki/yoriki-sandbox-docs",
        "image_digest": DIGEST,
        "contract": 1,
        "platforms": ["linux/amd64", "linux/arm64"],
        "platform_digests": {"linux/amd64": "sha256:" + "b" * 64, "linux/arm64": "sha256:" + "c" * 64},
        "python_version": "3.13.16",
        "memory_mb": 2048,
        "cpus": Decimal("2.00"),
        "pids_limit": 256,
        "tmp_mb": 256,
        "work_mb": 512,
        "output_mb": 256,
        "env_mb": 512,
        "command_timeout_seconds": 600,
        "job_timeout_seconds": 1800,
    } | overrides


class RuntimeProfileFactory(DjangoModelFactory[RuntimeProfile]):
    """Runtime Profileと最初の版を作る。版の項目は `version__memory_mb=...` のように指定する"""

    class Meta:
        model = RuntimeProfile
        skip_postgeneration_save = True  # version() の revise() で保存する

    name = factory.Sequence(lambda n: f"docs{n}")
    created_by = factory.SubFactory(UserFactory)

    @factory.post_generation
    def version(obj: RuntimeProfile, create, extracted, **kwargs):
        if not create:
            return
        obj.revise(actor=obj.created_by, expected_version=0, **version_fields(**kwargs))
