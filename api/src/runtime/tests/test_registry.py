import hashlib
import json

import httpx
import pytest
from django.test import override_settings

from runtime.errors import RegistryError
from runtime.registry import inspect_image, parse_reference

INDEX_TYPE = "application/vnd.oci.image.index.v1+json"
MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"


def _digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


class FakeRegistry:
    """OCI Distribution の manifest・blob と、Docker Hub の匿名のtokenを返す"""

    def __init__(self, labels_by_platform: dict[str, dict], *, index: bool = True, python: str = "3.13.16"):
        self.documents: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        entries: list[dict] = []
        for platform, labels in labels_by_platform.items():
            os_name, arch = platform.split("/")[:2]
            config = json.dumps(
                {"os": os_name, "architecture": arch, "config": {"Labels": labels, "Env": [f"PYTHON_VERSION={python}"]}}
            ).encode()
            manifest = json.dumps(
                {"mediaType": MANIFEST_TYPE, "config": {"digest": self.add(config)}, "layers": []}
            ).encode()
            entries.append({"digest": self.add(manifest), "platform": {"os": os_name, "architecture": arch}})
        if index:
            # provenance・SBOMのattestation（実行できるplatformではない）
            entries.append(
                {"digest": self.add(b'{"mediaType": "x"}'), "platform": {"os": "unknown", "architecture": "unknown"}}
            )
            self.top = self.add(json.dumps({"mediaType": INDEX_TYPE, "manifests": entries}).encode())
        else:
            self.top = entries[0]["digest"]

    def add(self, body: bytes) -> str:
        digest = _digest(body)
        self.documents[digest] = body
        return digest

    def route(self, request: httpx.Request) -> httpx.Response:
        # テストが handler を差し替えられるよう、呼ぶときに引く
        return self.handler(request)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "auth.docker.io":
            return httpx.Response(200, json={"token": "anonymous"})
        # Docker Hub（と、認証先が別のホストにあるレジストリ）は、auth.docker.io の匿名のtokenを求める
        needs_token = request.url.host in ("registry-1.docker.io", "registry.example.com")
        if needs_token and request.headers.get("Authorization") != "Bearer anonymous":
            challenge = 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"'
            return httpx.Response(401, headers={"WWW-Authenticate": challenge})
        digest = request.url.path.rsplit("/", 1)[-1]
        if digest not in self.documents:
            return httpx.Response(404)
        return httpx.Response(200, content=self.documents[digest])

    def inspect(self, image: str | None = None, resolver=lambda _host: ["93.184.216.34"]):
        """名前解決は、渡さなければ公開のアドレスを返す（テストで実際のDNSを引かない）"""
        return inspect_image(
            image or f"yoriki/yoriki-sandbox-docs@{self.top}",
            transport=httpx.MockTransport(self.route),
            resolver=resolver,
        )


LABELS = {"org.yoriki.sandbox-contract": "1", "org.yoriki.sandbox-profile": "docs"}


class TestParseReference:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("yoriki/sandbox@sha256:" + "a" * 64, "docker.io/yoriki/sandbox@sha256:" + "a" * 64),
            ("python:3.13@sha256:" + "a" * 64, "docker.io/library/python@sha256:" + "a" * 64),
            ("ghcr.io/org/sandbox:2026.10.22@sha256:" + "a" * 64, "ghcr.io/org/sandbox@sha256:" + "a" * 64),
            ("localhost:5001/sandbox@sha256:" + "a" * 64, "localhost:5001/sandbox@sha256:" + "a" * 64),
        ],
    )
    def test_canonical_form(self, value, expected):
        assert str(parse_reference(value)) == expected

    @pytest.mark.parametrize(
        "value", ["yoriki/sandbox:latest", "yoriki/sandbox@sha256:abc", "", "UPPER@sha256:" + "a" * 64]
    )
    def test_requires_a_digest(self, value):
        with pytest.raises(RegistryError):
            parse_reference(value)


class TestInspect:
    def test_multi_platform_index(self):
        registry = FakeRegistry({"linux/amd64": LABELS, "linux/arm64": LABELS})
        image = registry.inspect()
        assert image.contract == 1
        assert image.platforms == ["linux/amd64", "linux/arm64"]
        assert set(image.platform_digests) == {"linux/amd64", "linux/arm64"}
        assert image.python_version == "3.13.16"
        assert image.profile_label == "docs"
        # Docker Hubは匿名のtokenで読む
        assert registry.requests[0].url.host == "registry-1.docker.io"
        assert any(r.url.host == "auth.docker.io" for r in registry.requests)

    def test_single_manifest(self):
        image = FakeRegistry({"linux/amd64": LABELS}, index=False).inspect()
        assert image.platforms == ["linux/amd64"]

    def test_unsupported_contract(self):
        registry = FakeRegistry({"linux/amd64": LABELS | {"org.yoriki.sandbox-contract": "2"}})
        with pytest.raises(RegistryError, match="契約番号"):
            registry.inspect()

    def test_missing_or_mixed_labels(self):
        with pytest.raises(RegistryError, match="ラベル"):
            FakeRegistry({"linux/amd64": {}}).inspect()
        mixed = {"linux/amd64": LABELS, "linux/arm64": LABELS | {"org.yoriki.sandbox-contract": "2"}}
        with pytest.raises(RegistryError, match="ラベル"):
            FakeRegistry(mixed).inspect()

    def test_not_found(self):
        registry = FakeRegistry({"linux/amd64": LABELS})
        with pytest.raises(RegistryError, match="見つかりません"):
            registry.inspect("yoriki/yoriki-sandbox-docs@sha256:" + "0" * 64)

    def test_content_must_match_the_digest(self):
        registry = FakeRegistry({"linux/amd64": LABELS})
        registry.documents[registry.top] = registry.documents[registry.top].replace(b"amd64", b"arm64")
        with pytest.raises(RegistryError, match="一致しません"):
            registry.inspect()

    def test_insecure_registry_only_when_configured(self):
        registry = FakeRegistry({"linux/amd64": LABELS})
        image = f"localhost:5001/sandbox@{registry.top}"
        # 許可していないレジストリには、つながない
        with pytest.raises(RegistryError, match="使えません"):
            registry.inspect(image)
        assert registry.requests == []
        with override_settings(YORIKI_SANDBOX_INSECURE_REGISTRIES=["localhost:5001"]):
            registry.inspect(image, resolver=lambda _host: ["127.0.0.1"])
        assert registry.requests[-1].url.scheme == "http"

    def test_refuses_internal_addresses_and_foreign_auth(self):
        registry = FakeRegistry({"linux/amd64": LABELS})
        # 許可したレジストリでも、名前解決した先が内部のアドレスなら断る（DNSの付け替え・リダイレクトの先も同じ）
        with pytest.raises(RegistryError, match="内部のアドレス"):
            registry.inspect(resolver=lambda _host: ["10.0.0.5"])
        # 認証先（realm）は、レジストリ自身でなければ使わない
        with (
            override_settings(YORIKI_SANDBOX_REGISTRIES=["registry.example.com"]),
            pytest.raises(RegistryError, match="認証"),
        ):
            registry.inspect(f"registry.example.com/sandbox@{registry.top}")

    def test_redirects_are_checked(self):
        registry = FakeRegistry({"linux/amd64": LABELS})
        original = registry.handler

        def redirect(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/v2/") and "/blobs/" in request.url.path:
                return httpx.Response(307, headers={"Location": "http://169.254.169.254/latest/meta-data"})
            return original(request)

        registry.handler = redirect  # type: ignore[method-assign]
        with pytest.raises(RegistryError, match="HTTPS"):
            registry.inspect()

    def test_large_responses_are_cut(self):
        registry = FakeRegistry({"linux/amd64": LABELS})
        registry.documents[registry.top] = b"x" * (5 * 1024 * 1024)
        with pytest.raises(RegistryError, match="大きすぎます"):
            registry.inspect()

    def test_index_platform_must_match_the_config(self):
        registry = FakeRegistry({"linux/amd64": LABELS})
        index = json.loads(registry.documents[registry.top])
        index["manifests"][0]["platform"]["architecture"] = "arm64"
        registry.top = registry.add(json.dumps(index).encode())
        with pytest.raises(RegistryError, match="platformの表記"):
            registry.inspect()

    def test_python_must_be_the_same_on_every_platform(self):
        amd = FakeRegistry({"linux/amd64": LABELS}, python="3.13.1")
        arm = FakeRegistry({"linux/arm64": LABELS}, python="3.13.2")
        amd.documents |= arm.documents
        entries = json.loads(amd.documents[amd.top])["manifests"][:1] + json.loads(arm.documents[arm.top])["manifests"]
        amd.top = amd.add(json.dumps({"mediaType": INDEX_TYPE, "manifests": entries}).encode())
        with pytest.raises(RegistryError, match="Pythonの版"):
            amd.inspect()
