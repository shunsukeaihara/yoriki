"""レジストリのAPI（OCI Distribution）でSandboxのイメージを確かめる（design.md §7.3）。

Runtime Profileの登録のときに、image index（manifest list）とplatformごとのmanifest・configを読み、
digest、対応platform、契約番号のラベルを確かめる。apiのコンテナにDockerのソケットを渡さないので、Dockerは使わない。
読んだ内容はSHA-256を計算して、指定・参照されたdigestと一致することを確かめる。
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings
from django.utils.translation import gettext as _

from .contract import CONTRACT_LABEL, PROFILE_LABEL, SUPPORTED_CONTRACTS
from .errors import RegistryError

TIMEOUT = 15
# manifestとconfigの大きさの上限（通常は数KB）
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024

INDEX_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
MANIFEST_TYPES = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
ACCEPT = ", ".join((*INDEX_TYPES, *MANIFEST_TYPES))

DOCKER_HUB = "docker.io"
DOCKER_HUB_API = "registry-1.docker.io"

# [registry/]name[:tag]@sha256:<64桁>
REFERENCE = re.compile(
    r"^(?:(?P<registry>[a-zA-Z0-9.-]+(?::[0-9]+)?)/)?(?P<name>[a-z0-9]+(?:[._/-][a-z0-9]+)*)"
    r"(?::(?P<tag>[\w][\w.-]{0,127}))?@(?P<digest>sha256:[a-f0-9]{64})$"
)


@dataclass(frozen=True)
class ImageReference:
    registry: str
    name: str
    digest: str

    @property
    def repository(self) -> str:
        return f"{self.registry}/{self.name}"

    def __str__(self) -> str:
        return f"{self.repository}@{self.digest}"


@dataclass(frozen=True)
class InspectedImage:
    reference: ImageReference
    contract: int
    platforms: list[str]
    platform_digests: dict[str, str]
    python_version: str
    profile_label: str


def parse_reference(value: str) -> ImageReference:
    """`yoriki/yoriki-sandbox-docs@sha256:...` を、レジストリを含む正規の形にする（タグは捨てる）"""
    match = REFERENCE.match(value.strip())
    if match is None:
        raise RegistryError(_("イメージは「名前@sha256:digest」の形で指定してください（タグだけの指定はできません）"))
    registry, name = match["registry"], match["name"]
    # 最初の要素に「.」「:」がなく、localhostでもなければ、Docker Hubの名前の一部
    if registry and "." not in registry and ":" not in registry and registry != "localhost":
        name, registry = f"{registry}/{name}", None
    registry = registry or DOCKER_HUB
    if registry == DOCKER_HUB and "/" not in name:
        name = f"library/{name}"
    return ImageReference(registry, name, match["digest"])


class RegistryClient:
    def __init__(self, reference: ImageReference, transport: httpx.BaseTransport | None = None):
        self.reference = reference
        host = DOCKER_HUB_API if reference.registry == DOCKER_HUB else reference.registry
        insecure = reference.registry in settings.YORIKI_SANDBOX_INSECURE_REGISTRIES
        self.base = f"{'http' if insecure else 'https'}://{host}/v2/{reference.name}"
        self.client = httpx.Client(timeout=TIMEOUT, follow_redirects=True, transport=transport)
        self.token: str | None = None

    def close(self) -> None:
        self.client.close()

    def _get(self, path: str, accept: str | None = None) -> httpx.Response:
        headers = {"Accept": accept} if accept else {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self.client.get(f"{self.base}/{path}", headers=headers)
            if response.status_code == 401 and self.token is None:
                self.token = self._token(response.headers.get("WWW-Authenticate", ""))
                headers["Authorization"] = f"Bearer {self.token}"
                response = self.client.get(f"{self.base}/{path}", headers=headers)
        except httpx.HTTPError as exc:
            raise RegistryError(_("レジストリに接続できませんでした（%(error)s）") % {"error": exc}) from exc
        if response.status_code in (401, 403):
            raise RegistryError(_("このイメージを読む権限がありません。公開されたイメージを指定してください"))
        if response.status_code == 404:
            raise RegistryError(_("イメージが見つかりません。名前とdigestを確かめてください"))
        if response.status_code != 200:
            raise RegistryError(_("レジストリがエラーを返しました（%(status)s）") % {"status": response.status_code})
        if len(response.content) > MAX_DOCUMENT_BYTES:
            raise RegistryError(_("レジストリの応答が大きすぎます"))
        return response

    def _token(self, challenge: str) -> str:
        """`Bearer realm="...",service="...",scope="..."` から、読取りのtokenを得る（公開イメージは匿名で読める）"""
        if not challenge.lower().startswith("bearer "):
            raise RegistryError(_("このイメージを読む権限がありません。公開されたイメージを指定してください"))
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = params.pop("realm", "")
        if (
            not realm.startswith("https://")
            and self.reference.registry not in settings.YORIKI_SANDBOX_INSECURE_REGISTRIES
        ):
            raise RegistryError(_("レジストリの認証の設定を読めませんでした"))
        params.setdefault("scope", f"repository:{self.reference.name}:pull")
        try:
            response = self.client.get(realm, params=params)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RegistryError(_("レジストリの認証に失敗しました（%(error)s）") % {"error": exc}) from exc
        token = body.get("token") or body.get("access_token")
        if not isinstance(token, str) or not token:
            raise RegistryError(_("レジストリの認証に失敗しました"))
        return token

    def document(self, kind: str, digest: str, accept: str | None = None) -> tuple[str, dict[str, Any]]:
        """manifest か blob を読み、内容がdigestと一致することを確かめてから返す"""
        response = self._get(f"{kind}/{digest}", accept)
        if "sha256:" + hashlib.sha256(response.content).hexdigest() != digest:
            raise RegistryError(_("レジストリが返した内容がdigestと一致しません"))
        try:
            body = json.loads(response.content)
        except ValueError as exc:
            raise RegistryError(_("レジストリが返した内容を読めませんでした")) from exc
        media_type = body.get("mediaType") or response.headers.get("Content-Type", "").split(";")[0]
        return media_type, body


def _platform(spec: dict[str, Any]) -> str:
    platform = f"{spec.get('os', '')}/{spec.get('architecture', '')}"
    return f"{platform}/{spec['variant']}" if spec.get("variant") else platform


def _python_version(config: dict[str, Any]) -> str:
    for item in config.get("config", {}).get("Env") or []:
        if item.startswith("PYTHON_VERSION="):
            return item.split("=", 1)[1]
    return ""


def inspect_image(value: str, transport: httpx.BaseTransport | None = None) -> InspectedImage:
    """イメージを確かめる。対応しない契約番号や、ラベルのないイメージは RegistryError"""
    reference = parse_reference(value)
    client = RegistryClient(reference, transport)
    try:
        media_type, top = client.document("manifests", reference.digest, ACCEPT)
        if media_type in INDEX_TYPES:
            manifests = [
                (m["digest"], _platform(m.get("platform", {})))
                for m in top.get("manifests", [])
                # provenance・SBOMのattestationは、platformが unknown/unknown の別のmanifestになっている
                if m.get("platform", {}).get("os") not in (None, "unknown")
            ]
        elif media_type in MANIFEST_TYPES:
            manifests = [(reference.digest, "")]
        else:
            raise RegistryError(_("このイメージの形式には対応していません（%(type)s）") % {"type": media_type})
        if not manifests:
            raise RegistryError(_("イメージに、実行できるplatformがありません"))
        platform_digests: dict[str, str] = {}
        contracts, profiles, pythons = set(), set(), set()
        for digest, listed in manifests:
            _type, manifest = (
                (media_type, top) if digest == reference.digest else client.document("manifests", digest, ACCEPT)
            )
            _ctype, config = client.document("blobs", manifest.get("config", {}).get("digest", ""))
            platform = listed or _platform(config)
            labels = config.get("config", {}).get("Labels") or {}
            contracts.add(labels.get(CONTRACT_LABEL))
            profiles.add(labels.get(PROFILE_LABEL, ""))
            pythons.add(_python_version(config))
            platform_digests[platform] = digest
    finally:
        client.close()
    if len(contracts) != 1 or None in contracts:
        raise RegistryError(
            _("イメージに契約番号のラベル（%(label)s）がないか、platformごとに違います") % {"label": CONTRACT_LABEL}
        )
    raw = str(contracts.pop())
    contract = int(raw) if raw.isdigit() else 0
    if contract not in SUPPORTED_CONTRACTS:
        raise RegistryError(
            _("このイメージの契約番号（%(contract)s）には、このバージョンのYorikiは対応していません")
            % {"contract": raw}
        )
    if not any(p.startswith("linux/") for p in platform_digests):
        raise RegistryError(_("イメージに、Linuxのplatformがありません"))
    return InspectedImage(
        reference=reference,
        contract=contract,
        platforms=sorted(platform_digests),
        platform_digests=platform_digests,
        python_version=sorted(pythons)[-1] if pythons else "",
        profile_label=sorted(profiles)[-1] if profiles else "",
    )
