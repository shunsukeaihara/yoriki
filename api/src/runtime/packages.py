"""Pythonのpackageの追加（design.md §7.6・§7.7）。

カタログで確かめ、Sandboxと同じイメージの短命の取得用コンテナでwheelを依存ごと取得し、Worker側で確かめ直してから
保存先へ置く。Sandboxへは、wheelとlockを /tmp へ写してpipのローカル取得とhash検証で入れる。DBに触る処理と、
Dockerだけを使う処理（別のスレッドでleaseを延ばしながら動かせる）を分けておく。
"""

import hashlib
import io
import json
import logging
import shutil
import tarfile
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import IntegrityError, transaction
from packaging.requirements import Requirement
from packaging.specifiers import InvalidSpecifier, Specifier, SpecifierSet
from packaging.tags import Tag
from packaging.utils import InvalidName, InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from artifact.models import BlobObject
from artifact.storage import StoredObject

from .launcher import Launcher, Sandbox, SandboxSpec, SandboxUnavailable
from .models import EnvironmentRevision, PackageBundle, PythonPackage, PythonWheel

if TYPE_CHECKING:
    from run.models import Run, Subagent

logger = logging.getLogger(__name__)

# 1回に頼めるpackageの数、取得する（依存を含めた）wheelの数と合計の大きさ、取得の時間の上限（design.md §7.6）
MAX_REQUESTED = 10
MAX_WHEELS = 100
MAX_TOTAL_BYTES = 500 * 1024 * 1024
FETCH_TIMEOUT = 300
# Sandboxへの導入の時間の上限（秒）。1回に /tmp へ写すのは /tmp の大きさのこの割合まで
INSTALL_TIMEOUT = 600
STAGING_RATIO = 0.8
STAGING = "/tmp/yoriki-packages"
ENV_PYTHON = "/workspace/env/bin/python"
# 決まった操作のコマンドに渡す環境変数。イメージの環境変数は引き継がない
COMMAND_ENV = ["/usr/bin/env", "-i", "HOME=/tmp", "PATH=/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8"]

# 取得用のコンテナの中で動かすスクリプト（イメージのPythonを -I で）。標準入力でJSONを受け取り、標準出力にJSONを返す。
# Sandboxと同じ薄い仮想環境で、会話のそれまでの追加の版を制約にして足りないものだけを解決し、決まった版のwheelを
# hashつきで取得する。ソース配布と、依存に直接URLを書いたもの（PEP 508の `name @ URL`）は扱わない。直接URLは解決の
# 途中でpipが読みに行くのを止める手段がないので、解決の結果で断る（PyPIはこの依存を持つpackageを受け付けない。
# 社内のミラーは導入先が管理する。design.md §7.6）
FETCH_SCRIPT = r"""
import json, os, re, subprocess, sys, venv

request = json.load(sys.stdin)
env = {
    "HOME": "/tmp", "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
    "PIP_INDEX_URL": request["index_url"], "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_CACHE_DIR": "1",
    "PIP_NO_INPUT": "1", "PIP_PROGRESS_BAR": "off",
}

def fail(stage, text):
    print(json.dumps({"ok": False, "stage": stage, "error": text[-4000:]}))
    sys.exit(0)

def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()

def pip(*args):
    return subprocess.run(["/tmp/env/bin/python", "-m", "pip", *args], env=env, capture_output=True, text=True)

venv.EnvBuilder(system_site_packages=True, symlinks=True, with_pip=False).create("/tmp/env")
with open("/tmp/constraints.txt", "w") as f:
    f.write("\n".join(request["constraints"]) + "\n")
with open("/tmp/requirements.txt", "w") as f:
    f.write("\n".join(request["requirements"]) + "\n")
result = pip("install", "--dry-run", "--quiet", "--report", "/tmp/report.json", "--only-binary=:all:",
             "-c", "/tmp/constraints.txt", "-r", "/tmp/requirements.txt")
if result.returncode != 0:
    fail("resolve", result.stderr or result.stdout)
with open("/tmp/report.json") as f:
    report = json.load(f)
skip = set(request["skip"])
items, lines = [], []
for entry in report.get("install", []):
    metadata = entry.get("metadata") or {}
    info = entry.get("download_info") or {}
    name, version = metadata.get("name", ""), metadata.get("version", "")
    sha256 = ((info.get("archive_info") or {}).get("hashes") or {}).get("sha256", "")
    url = info.get("url", "")
    if entry.get("is_direct"):
        fail("resolve", f"{name} {version} is referenced by a direct URL ({url}); only the package index is allowed")
    if f"{canonical(name)}=={version}" in skip:
        continue
    if not sha256 or not url.split("#")[0].endswith(".whl"):
        fail("resolve", f"{name} {version} has no wheel with a SHA-256 hash in the index ({url})")
    items.append({"name": name, "version": version, "sha256": sha256, "requested": bool(entry.get("requested"))})
    lines.append(f"{name}=={version} --hash=sha256:{sha256}")
os.makedirs("/tmp/wheels")
if lines:
    with open("/tmp/download.txt", "w") as f:
        f.write("\n".join(lines) + "\n")
    result = pip("download", "--no-deps", "--only-binary=:all:", "--require-hashes", "-d", "/tmp/wheels",
                 "-r", "/tmp/download.txt")
    if result.returncode != 0:
        fail("download", result.stderr or result.stdout)
# 頼んだもののうち、イメージかそれまでの追加ですでに入っていたものの版（取り直すときに同じ版にするため）
fetched = {canonical(item["name"]) for item in items}
probe = "\n".join([
    "import importlib.metadata as m, json, sys",
    "out = {}",
    "for n in sys.argv[1:]:",
    "    try:",
    "        out[n] = m.version(n)",
    "    except m.PackageNotFoundError:",
    "        pass",
    "print(json.dumps(out))",
])
names = [n for n in request["names"] if n not in fetched]
result = subprocess.run(["/tmp/env/bin/python", "-c", probe, *names], env=env, capture_output=True, text=True)
installed = json.loads(result.stdout or "{}") if result.returncode == 0 else {}
from pip._vendor.packaging import tags
print(json.dumps({
    "ok": True,
    "installed": installed,
    "python": sys.version.split()[0],
    "tags": [str(t) for t in tags.sys_tags()],
    "items": items,
}))
"""


class PackageError(Exception):
    """モデルに返す誤り（英語）。Runは失敗にしない"""


@dataclass(frozen=True)
class Requested:
    name: str  # 正規化した名前
    version: str  # 頼んだ版（空なら範囲の中の最新）


@dataclass(frozen=True)
class LocalWheel:
    """取得してWorker側で確かめたwheel"""

    path: Path
    filename: str
    name: str
    version: str
    sha256: str
    size: int
    requested: bool


@dataclass(frozen=True)
class FetchResult:
    wheels: list[LocalWheel]
    python_version: str
    # 頼んだもののうち、取得せずにすでに入っていたもの（正規化した名前 → 版）
    installed: dict[str, str] = field(default_factory=dict)


# --- カタログ ---


def normalize(name: str) -> str:
    try:
        return canonicalize_name(name, validate=True)
    except InvalidName as exc:
        raise PackageError(f"{name!r} is not a valid package name.") from exc


def check_specifier(value: str) -> str:
    """カタログの版の範囲（PEP 440のspecifier）。正規化して返す。誤りは ValueError"""
    # SpecifierSet の文字列は並びが変わるので、書いた順のまま1つずつ正規化する
    try:
        return ",".join(str(Specifier(part.strip())) for part in value.split(",") if part.strip())
    except InvalidSpecifier as exc:
        raise ValueError(str(exc)) from exc


def parse_requests(packages: list[dict[str, Any]]) -> list[Requested]:
    """Toolの引数を確かめる。同じpackageを2回頼んだら誤り"""
    if not packages:
        raise PackageError("Specify at least one package.")
    if len(packages) > MAX_REQUESTED:
        raise PackageError(f"Request at most {MAX_REQUESTED} packages at a time.")
    result: list[Requested] = []
    for item in packages:
        name = normalize(str(item.get("name") or ""))
        version = str(item.get("version") or "").strip()
        if version:
            try:
                version = str(Version(version))
            except InvalidVersion as exc:
                raise PackageError(f"{version!r} is not a valid version for {name}; give an exact version.") from exc
        if any(r.name == name for r in result):
            raise PackageError(f"{name} is requested more than once.")
        result.append(Requested(name, version))
    return result


def catalog_problems(requests: Iterable[Requested]) -> list[str]:
    """カタログにない・停止している・範囲の外のもの（英語の説明の並び）"""
    requests = list(requests)
    entries = {p.name: p for p in PythonPackage.objects.filter(name__in=[r.name for r in requests], deleted=False)}
    problems = []
    for request in requests:
        entry = entries.get(request.name)
        if entry is None or not entry.is_active:
            problems.append(f"{request.name} is not in the package catalog")
        elif request.version and not SpecifierSet(entry.specifier).contains(request.version, prereleases=True):
            problems.append(f"{request.name} {request.version} is outside the allowed versions ({entry.specifier})")
    return problems


def requirements(requests: Iterable[Requested]) -> list[str]:
    """pipに渡す指定。版がなければカタログの範囲で最新"""
    requests = list(requests)
    entries = dict(
        PythonPackage.objects.filter(name__in=[r.name for r in requests], deleted=False).values_list(
            "name", "specifier"
        )
    )
    return [f"{r.name}=={r.version}" if r.version else f"{r.name}{entries.get(r.name, '')}" for r in requests]


def allowed(name: str, version: str) -> bool:
    entry = PythonPackage.objects.filter(name=name, deleted=False, is_active=True).first()
    return entry is not None and SpecifierSet(entry.specifier).contains(version, prereleases=True)


# --- 環境の版 ---


def scope(run: "Run", subagent: "Subagent | None" = None) -> dict[str, Any]:
    """環境を引き継ぐ単位。会話（タスクのRunはそのRun）、サブエージェントはその子"""
    return {
        "conversation_id": run.conversation_id,
        "run": run if run.isolated_workspace else None,
        "subagent": subagent,
    }


def current_revision(run: "Run", subagent: "Subagent | None" = None) -> EnvironmentRevision | None:
    """Sandboxに入れる環境の版。子は、自分が足したものがなければ、起動したときの親（会話）の版"""
    revisions = EnvironmentRevision.objects.prefetch_related("wheels")
    if subagent is not None:
        own = revisions.filter(**scope(run, subagent)).order_by("-pk").first()
        if own is not None:
            return own
        return revisions.filter(**scope(run), created__lte=subagent.created).order_by("-pk").first()
    return revisions.filter(**scope(run)).order_by("-pk").first()


@transaction.atomic
def record_revision(
    run: "Run",
    subagent: "Subagent | None",
    *,
    parent: EnvironmentRevision | None,
    spec: SandboxSpec,
    python_version: str,
    requested: list[dict[str, str]],
    added: list[PythonWheel],
    base: Iterable[PythonWheel],
    bundle_requested: list[dict[str, str]],
) -> EnvironmentRevision:
    revision = EnvironmentRevision.objects.create(
        **scope(run, subagent),
        parent=parent,
        image=spec.image,
        platform=spec.platform,
        python_version=python_version,
        requested=requested,
    )
    wheels = {w.pk: w for w in base}
    # 同じ名前の古い版は外す（イメージより新しい版で上書きしたもの・取り直したもの）
    names = {w.name for w in added}
    wheels = {pk: w for pk, w in wheels.items() if w.name not in names}
    wheels.update({w.pk: w for w in added})
    revision.wheels.set(wheels.values())
    bundle = PackageBundle.objects.create(revision=revision, requested=bundle_requested)
    bundle.wheels.set(added)
    return revision


def merge_requested(
    base: list[dict[str, str]], added: list[LocalWheel], installed: dict[str, str] | None = None
) -> list[dict[str, str]]:
    """それまでに直接頼んだものに、今回頼んだもの（決まった版）を足す。同じ名前は今回の版にする。すでに入っていて
    取得しなかったもの（`installed`）も、イメージが変わったときに取り直せるよう記録する"""
    merged = {r["name"]: r["version"] for r in base}
    merged.update(installed or {})
    for wheel in added:
        if wheel.requested:
            merged[wheel.name] = wheel.version
    return [{"name": n, "version": v} for n, v in sorted(merged.items())]


# --- 取得（Dockerだけを使う。別のスレッドで動かせる） ---


def fetch(
    launcher: Launcher,
    sandbox_spec: SandboxSpec,
    labels: dict[str, str],
    *,
    requirement_lines: list[str],
    base: Iterable[tuple[str, str]],
    workdir: Path,
    name: str,
) -> FetchResult:
    """取得用のコンテナでwheelを取得し、Worker側の `workdir` に置いて確かめる。`base` は会話でそれまでに入れたもの
    （名前、版）で、版を変えずに残し、取り直さない"""
    base = list(base)
    request = {
        "index_url": settings.YORIKI_PYTHON_PACKAGE_INDEX_URL,
        "requirements": requirement_lines,
        "constraints": [f"{n}=={v}" for n, v in base],
        "skip": [f"{n}=={v}" for n, v in base],
        "names": [_requirement_name(line) for line in requirement_lines],
    }
    spec = SandboxSpec.for_fetch(sandbox_spec, labels=labels)
    container = launcher.start(spec, name)
    try:
        stdout, stderr, exit_code = container.run(
            [*COMMAND_ENV, "python3", "-I", "-c", FETCH_SCRIPT],
            json.dumps(request).encode(),
            timeout=FETCH_TIMEOUT,
        )
        if exit_code != 0:
            raise SandboxUnavailable(f"the package fetch exited {exit_code}: {stderr[-2000:]!r}")
        try:
            result = json.loads(stdout)
        except ValueError as exc:
            raise SandboxUnavailable(f"the package fetch returned invalid JSON: {stdout[:500]!r}") from exc
        if not result.get("ok"):
            raise PackageError(_fetch_error(result))
        workdir.mkdir(parents=True, exist_ok=True)
        _receive_wheels(container, workdir)
    except SandboxUnavailable as exc:
        if "did not finish" in str(exc):
            raise PackageError(f"Fetching the packages took longer than {FETCH_TIMEOUT} seconds.") from exc
        raise
    finally:
        container.remove()
    installed = {str(canonicalize_name(str(k))): str(v) for k, v in (result.get("installed") or {}).items()}
    return FetchResult(_verify(result, workdir), str(result.get("python") or ""), installed)


def _requirement_name(line: str) -> str:
    return str(canonicalize_name(Requirement(line).name))


def _fetch_error(result: dict[str, Any]) -> str:
    error = str(result.get("error") or "").strip()
    lines = [line for line in error.splitlines() if line.strip()][-15:]
    stage = "resolve" if result.get("stage") == "resolve" else "download"
    return f"The packages could not be {'resolved' if stage == 'resolve' else 'downloaded'}:\n" + "\n".join(lines)


def _receive_wheels(container: Sandbox, workdir: Path) -> None:
    """取得用のコンテナの /tmp/wheels を取り出し、通常のファイル（`*.whl`）だけを `workdir` に置く"""
    total = 0

    with tempfile.TemporaryFile(dir=workdir) as archive:

        def sink(chunk: bytes) -> None:
            nonlocal total
            total += len(chunk)
            if total > MAX_TOTAL_BYTES + 10 * 1024 * 1024:
                raise PackageError(f"The packages are larger than {MAX_TOTAL_BYTES // (1024 * 1024)} MB in total.")
            archive.write(chunk)

        _out, stderr, exit_code = container.run(
            ["tar", "--create", "--file=-", "--directory=/tmp/wheels", "."], timeout=FETCH_TIMEOUT, stdout_sink=sink
        )
        if exit_code != 0:
            raise SandboxUnavailable(f"cannot read the fetched packages (exit {exit_code}): {stderr[-1000:]!r}")
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode="r:") as tar:
            for member in tar:
                filename = Path(member.name).name
                if not member.isfile() or not filename.endswith(".whl") or member.name.strip("./") != filename:
                    continue
                src = tar.extractfile(member)
                assert src is not None
                with src, (workdir / filename).open("wb") as dest:
                    shutil.copyfileobj(src, dest, 1024 * 1024)


def _verify(result: dict[str, Any], workdir: Path) -> list[LocalWheel]:
    """取得したもの全件を確かめ直す：wheelであること、tagがSandboxに合うこと、hash、件数と合計の大きさ"""
    supported = {Tag(*str(t).split("-", 2)) for t in result.get("tags") or [] if str(t).count("-") == 2}
    items = result.get("items") or []
    if len(items) > MAX_WHEELS:
        raise PackageError(f"The packages need {len(items)} wheels including dependencies (at most {MAX_WHEELS}).")
    files = {p.name: p for p in workdir.glob("*.whl")}
    wheels: list[LocalWheel] = []
    total = 0
    for item in items:
        name, version = canonicalize_name(str(item.get("name") or "")), str(item.get("version") or "")
        path = next(
            (p for filename, p in files.items() if _wheel_matches(filename, name, version)),
            None,
        )
        if path is None:
            raise PackageError(f"The wheel of {name} {version} was not fetched.")
        try:
            _name, _version, _build, tags = parse_wheel_filename(path.name)
        except InvalidWheelFilename as exc:
            raise PackageError(f"{path.name} is not a valid wheel.") from exc
        if not tags & supported:
            raise PackageError(f"{path.name} does not support this sandbox's Python and platform.")
        sha256, size = _hash(path)
        if sha256 != item.get("sha256"):
            raise PackageError(f"The SHA-256 hash of {path.name} does not match the index.")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise PackageError(f"The packages are larger than {MAX_TOTAL_BYTES // (1024 * 1024)} MB in total.")
        wheels.append(LocalWheel(path, path.name, name, version, sha256, size, bool(item.get("requested"))))
    return wheels


def _wheel_matches(filename: str, name: str, version: str) -> bool:
    try:
        wheel_name, wheel_version, _build, _tags = parse_wheel_filename(filename)
    except InvalidWheelFilename:
        return False
    return wheel_name == name and str(wheel_version) == str(Version(version))


def _hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


# --- 保存 ---


def write_wheels(wheels: list[LocalWheel], known: set[str]) -> dict[str, tuple[StoredObject, str]]:
    """まだ保存していない（`known` にない）wheelを保存先に書く（記録はしない。DBに触らないので別のスレッドで
    動かせる）。sha256 → （書いたもの、保存先）"""
    written: dict[str, tuple[StoredObject, str]] = {}
    for wheel in wheels:
        if wheel.sha256 in known or wheel.sha256 in written:
            continue
        with wheel.path.open("rb") as src:
            written[wheel.sha256] = BlobObject.write(src)
    return written


def register_wheels(wheels: list[LocalWheel], written: dict[str, tuple[StoredObject, str]]) -> list[PythonWheel]:
    """書いたwheelを記録する。同じ中身のものが先に記録されていれば、それを使う"""
    result = []
    for wheel in wheels:
        record = PythonWheel.objects.filter(sha256=wheel.sha256).first()
        if record is None:
            stored, backend_id = written[wheel.sha256]
            try:
                with transaction.atomic():
                    record = PythonWheel.objects.create(
                        filename=wheel.filename,
                        name=wheel.name,
                        version=wheel.version,
                        sha256=wheel.sha256,
                        size=wheel.size,
                        blob=BlobObject.register(stored, backend_id),
                    )
            except IntegrityError:
                # 同時に同じwheelを記録した。書いたものは参照のないobjectとして清掃で消える
                record = PythonWheel.objects.get(sha256=wheel.sha256)
        result.append(record)
    return result


# --- Sandboxへの導入（Dockerと保存先だけを使う。別のスレッドで動かせる） ---


@dataclass(frozen=True)
class WheelSource:
    """導入するwheel。`open` は保存先から読む（DBに触らないよう、場所を先に引いておく）"""

    filename: str
    name: str
    version: str
    sha256: str
    size: int
    storage: str
    key: str


def sources(wheels: Iterable[PythonWheel]) -> list[WheelSource]:
    """DBのあるスレッドで、wheelの保存先を引いておく"""
    result = []
    for wheel in wheels:
        location = wheel.blob.location()
        result.append(
            WheelSource(
                wheel.filename, wheel.name, wheel.version, wheel.sha256, wheel.size, location.backend, location.key
            )
        )
    return result


def install(sandbox: Sandbox, wheels: list[WheelSource], tmp_mb: int, *, storage_factory) -> None:
    """wheelを /tmp へ写し、Run専用の環境（/workspace/env）へ入れる。/tmp に収まるよう分けて写す。
    入れられなければ PackageError（環境は戻していない。呼び出し側で reset_env する）"""
    limit = int(tmp_mb * 1024 * 1024 * STAGING_RATIO)
    batch: list[WheelSource] = []
    size = 0
    for wheel in sorted(wheels, key=lambda w: w.filename):
        if wheel.size > limit:
            raise PackageError(
                f"{wheel.filename} ({wheel.size // (1024 * 1024)} MB) does not fit in /tmp of the sandbox; "
                "ask the administrator to enlarge the runtime profile."
            )
        if batch and size + wheel.size > limit:
            _install_batch(sandbox, batch, storage_factory)
            batch, size = [], 0
        batch.append(wheel)
        size += wheel.size
    if batch:
        _install_batch(sandbox, batch, storage_factory)


def _install_batch(sandbox: Sandbox, wheels: list[WheelSource], storage_factory) -> None:
    lock = "".join(f"{w.name}=={w.version} --hash=sha256:{w.sha256}\n" for w in wheels).encode()
    with tempfile.TemporaryFile() as archive:
        with tarfile.open(fileobj=archive, mode="w:") as tar:
            _add_bytes(tar, "yoriki-packages/requirements.lock", lock)
            for wheel in wheels:
                with storage_factory(wheel.storage).open_read(wheel.key) as src:
                    info = tarfile.TarInfo(f"yoriki-packages/wheels/{wheel.filename}")
                    info.size = wheel.size
                    info.mode = 0o644
                    tar.addfile(info, src)
        archive.seek(0)
        try:
            sandbox.restore(archive, "/tmp")
            _out, stderr, exit_code = sandbox.run(
                [
                    *COMMAND_ENV,
                    ENV_PYTHON,
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    f"--find-links={STAGING}/wheels",
                    "--only-binary=:all:",
                    "--require-hashes",
                    "--no-deps",
                    "--no-cache-dir",
                    "--disable-pip-version-check",
                    "--no-input",
                    "--quiet",
                    "-r",
                    f"{STAGING}/requirements.lock",
                ],
                timeout=INSTALL_TIMEOUT,
            )
        finally:
            sandbox.run(["rm", "-rf", STAGING], timeout=60)
    if exit_code != 0:
        text = stderr.decode(errors="replace")
        if "No space left" in text:
            raise PackageError(
                "The packages do not fit in /workspace/env of the sandbox; ask the administrator to enlarge the "
                "runtime profile."
            )
        lines = [line for line in text.splitlines() if line.strip()][-15:]
        raise PackageError("The packages could not be installed:\n" + "\n".join(lines))


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


def reset_env(sandbox: Sandbox) -> None:
    """Run専用の環境を空にして作り直す（入れている途中で失敗したとき。design.md §7.6）"""
    _out, stderr, exit_code = sandbox.run(["find", "/workspace/env", "-mindepth", "1", "-delete"], timeout=120)
    if exit_code != 0:
        raise SandboxUnavailable(f"cannot reset the python environment: {stderr[-1000:]!r}")
    result = sandbox.helper("init-env", {})
    if not result.get("ok"):
        raise SandboxUnavailable(f"cannot prepare the sandbox: {result.get('error')}")
