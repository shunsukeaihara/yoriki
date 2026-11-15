"""Runtime Profile（design.md §7.3）。Sandboxのイメージと資源の上限を、不変の版で持つ。

SkillごとではなくRunの単位で決める。基盤管理者が既定を1つ決め、SandboxのToolを使うRunは、Runを始めるときに
既定のその時点の版を固定する。新しい版を登録したり既定を替えたりしても、始まっているRunは替わらない。
"""

from decimal import Decimal
from typing import Any

from auditlog.registry import auditlog
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from utils.fields import NanoidField

from .errors import VersionConflict

# 版の中身。どれかが変わったら新しい版を作る
VERSION_FIELDS = (
    "image_repository",
    "image_digest",
    "contract",
    "platforms",
    "platform_digests",
    "python_version",
    "memory_mb",
    "cpus",
    "pids_limit",
    "tmp_mb",
    "work_mb",
    "output_mb",
    "env_mb",
    "command_timeout_seconds",
    "job_timeout_seconds",
    "subagent_timeout_seconds",
)


class RuntimeProfile(models.Model):
    uid = NanoidField(unique=True)
    name = models.CharField(_("名前"), max_length=100)
    current_version = models.ForeignKey(
        "RuntimeProfileVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("現在の版"),
    )
    is_active = models.BooleanField(_("有効"), default=True)
    # SandboxのToolを使うRunが使うRuntime Profile。基盤管理者が1つだけ決める（design.md §7.3）
    is_default = models.BooleanField(_("既定"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("Runtime Profile")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(deleted=False), name="uniq_runtime_profile_name"
            ),
            models.UniqueConstraint(
                fields=["is_default"], condition=models.Q(is_default=True, deleted=False), name="uniq_default_runtime"
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def version_number(self) -> int:
        return self.current_version.number if self.current_version else 0

    @classmethod
    def default_version(cls) -> "RuntimeProfileVersion | None":
        """Runを始めるときに固定する版。既定がなければNone（Runを始めない。design.md §7.3）"""
        profile = (
            cls.objects.filter(deleted=False, is_active=True, is_default=True).select_related("current_version").first()
        )
        return profile.current_version if profile else None

    @transaction.atomic
    def make_default(self) -> None:
        """既定のRuntime Profileにする。これまでの既定は外す。

        同時に別のProfileを既定にしても一意の制約に当たらないよう、すべての行をpkの順にロックして直列にする
        """
        list(RuntimeProfile.objects.select_for_update().order_by("pk").values_list("pk", flat=True))
        RuntimeProfile.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        self.is_default = True
        self.save(update_fields=["is_default", "updated"])

    @classmethod
    @transaction.atomic
    def create_with_version(cls, *, name: str, actor, change_reason: str = "", **fields: Any) -> "RuntimeProfile":
        profile = cls.objects.create(name=name, created_by=actor)
        profile.revise(actor=actor, expected_version=0, change_reason=change_reason, **fields)
        return profile

    @transaction.atomic
    def revise(
        self, *, actor, expected_version: int, change_reason: str = "", **fields: Any
    ) -> "RuntimeProfileVersion":
        """中身を変えた新しい版を作り、現在の版にする。

        編集を始めた後に他の人が版を作っていたら VersionConflict。中身が現在の版と同じなら版を作らず、現在の版を返す
        """
        current = (
            RuntimeProfile.objects.select_for_update(of=("self",)).select_related("current_version").get(pk=self.pk)
        )
        latest = current.current_version
        latest_number = latest.number if latest else 0
        if latest_number != expected_version:
            raise VersionConflict(f"expected={expected_version} latest={latest_number}")
        if latest and all(getattr(latest, name) == fields[name] for name in VERSION_FIELDS):
            self.current_version = latest
            return latest
        version = RuntimeProfileVersion.objects.create(
            profile=current, number=latest_number + 1, created_by=actor, change_reason=change_reason, **fields
        )
        current.current_version = version
        current.save(update_fields=["current_version", "updated"])
        self.current_version = version
        return version


class RuntimeProfileVersion(models.Model):
    """Runtime Profileの版。作成後に変更・削除しない。Runはこの版を固定して使う"""

    uid = NanoidField(unique=True)
    profile = models.ForeignKey(
        RuntimeProfile, on_delete=models.PROTECT, related_name="versions", verbose_name=_("Runtime Profile")
    )
    number = models.PositiveIntegerField(_("版"))
    # イメージは repository と index（manifest list）の digest で固定する。タグは使わない（design.md §10.1）
    image_repository = models.CharField(_("イメージのrepository"), max_length=300)
    image_digest = models.CharField(_("イメージのdigest"), max_length=100)
    # 以下は登録のときにレジストリから読んだ値（sandbox/CONTRACT.md）
    contract = models.PositiveSmallIntegerField(_("契約番号"))
    platforms = ArrayField(models.CharField(max_length=50), verbose_name=_("対応platform"))
    # platform（linux/amd64 等）ごとのmanifestのdigest。Workerは自分のplatformのものを使い、Attemptに記録する
    platform_digests = models.JSONField(_("platformごとのdigest"), default=dict)
    python_version = models.CharField(_("Pythonの版"), max_length=20, blank=True)
    # 資源の上限（sandbox/CONTRACT.md §2）。tmpfs（/tmp・work・output・env）はメモリの上限に含まれる
    memory_mb = models.PositiveIntegerField(_("メモリ（MB）"), default=2048)
    cpus = models.DecimalField(_("CPU"), max_digits=4, decimal_places=2, default=Decimal("2.00"))
    pids_limit = models.PositiveIntegerField(_("プロセス数の上限"), default=256)
    tmp_mb = models.PositiveIntegerField(_("/tmp（MB）"), default=256)
    work_mb = models.PositiveIntegerField(_("work（MB）"), default=512)
    output_mb = models.PositiveIntegerField(_("output（MB）"), default=256)
    env_mb = models.PositiveIntegerField(_("env（MB）"), default=512)
    # shellの1回の待ち時間の上限と、バックグラウンドのジョブの実行時間の上限（harness.md §7.8、§8.3）
    command_timeout_seconds = models.PositiveIntegerField(_("コマンドの待ち時間の上限（秒）"), default=600)
    job_timeout_seconds = models.PositiveIntegerField(_("ジョブの実行時間の上限（秒）"), default=1800)
    # サブエージェントの実行時間の上限。子は新しいAttemptへ移らないので、Attemptの上限より短くする（harness.md §4.4）
    subagent_timeout_seconds = models.PositiveIntegerField(_("サブエージェントの実行時間の上限（秒）"), default=1800)
    change_reason = models.CharField(_("変更理由"), max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Runtime Profileの版")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["profile", "number"], name="uniq_runtime_profile_version_number"),
        ]

    def __str__(self):
        return f"{self.profile} v{self.number}"

    @property
    def image(self) -> str:
        return f"{self.image_repository}@{self.image_digest}"


auditlog.register(RuntimeProfile, exclude_fields=["updated"])
auditlog.register(RuntimeProfileVersion)


class PlatformSettings(models.Model):
    """基盤の設定（1行だけ。design.md §5.1）"""

    # 会話・修正チャットのワークスペース（snapshot）を、最後のRunが終わってから残す日数。空なら自動では消さない
    # （design.md §8。run.cleanup）
    workspace_retention_days = models.PositiveIntegerField(
        _("ワークスペースの保存期限（日数）"), null=True, blank=True, default=30
    )

    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("基盤の設定")
        verbose_name_plural = verbose_name
        default_permissions = ("view", "change")

    def __str__(self):
        return str(_("基盤の設定"))

    @classmethod
    def load(cls) -> "PlatformSettings":
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj


auditlog.register(PlatformSettings, exclude_fields=["updated"])


class PythonPackage(models.Model):
    """追加してよいPythonのpackageのカタログ（design.md §7.6）。モデルが直接頼めるのは、ここにあって停止していない
    packageの、範囲の中の版だけ。推移依存は、登録したpackageの依存として許す"""

    uid = NanoidField(unique=True)
    # PEP 503で正規化した名前（小文字、`-`・`_`・`.` の並びを `-` に）
    name = models.CharField(_("名前"), max_length=100)
    # 許す版の範囲（PEP 440のspecifier。例：`>=3,<4`）。空ならどの版も
    specifier = models.CharField(_("版の範囲"), max_length=200, blank=True)
    note = models.CharField(_("メモ"), max_length=500, blank=True)
    is_active = models.BooleanField(_("有効"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("Pythonのpackage")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(deleted=False), name="uniq_python_package_name"
            ),
        ]

    def __str__(self):
        return self.name


auditlog.register(PythonPackage, exclude_fields=["updated"])


class PythonWheel(models.Model):
    """取得したwheel。内容のSHA-256で1つにし、会話・Runをまたいで使い回す（design.md §7.6）"""

    filename = models.CharField(_("ファイル名"), max_length=300)
    name = models.CharField(_("名前"), max_length=100)
    version = models.CharField(_("版"), max_length=100)
    sha256 = models.CharField(_("SHA-256"), max_length=64, unique=True)
    size = models.PositiveBigIntegerField(_("大きさ"))
    blob = models.ForeignKey("artifact.BlobObject", on_delete=models.PROTECT, related_name="+")

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Pythonのwheel")
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.filename


class EnvironmentRevision(models.Model):
    """追加のPython環境の版（design.md §7.6・§7.7）。会話（タスクのRunはそのRun、サブエージェントはその子）の今の版は
    最新のもの。不変で、追加・取り直しのたびに新しい版を作る"""

    uid = NanoidField(unique=True)
    conversation = models.ForeignKey(
        "conversation.Conversation", on_delete=models.CASCADE, related_name="environment_revisions"
    )
    # Workspaceを会話で引き継がないRun（タスクのRun）なら、そのRun
    run = models.ForeignKey("run.Run", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    # サブエージェントが足したものなら、その子（会話の環境は変えない）
    subagent = models.ForeignKey("run.Subagent", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # 入れたSandboxのイメージ（platformのmanifestのdigest）・platform・Pythonの版。違うSandboxでは取り直す
    image = models.CharField(_("イメージ"), max_length=400)
    platform = models.CharField(_("platform"), max_length=50)
    python_version = models.CharField(_("Pythonの版"), max_length=20)
    # 直接頼んだpackageと決まった版（[{"name", "version"}]。それまでの追加を含む）。取り直すときに使う
    requested = models.JSONField(_("頼んだpackage"), default=list)
    # 入っているwheelの全件
    wheels = models.ManyToManyField(PythonWheel, related_name="+")

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Python環境の版")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["conversation", "run", "subagent", "-id"])]

    def __str__(self):
        return self.uid


class PackageBundle(models.Model):
    """1回の追加・取り直しで入れたwheelの完全なlock（design.md §7.6）"""

    revision = models.OneToOneField(EnvironmentRevision, on_delete=models.CASCADE, related_name="bundle")
    # この回に頼んだもの（[{"name", "version"}]。取り直しなら空）
    requested = models.JSONField(_("頼んだpackage"), default=list)
    wheels = models.ManyToManyField(PythonWheel, related_name="+")

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("package bundle")
        verbose_name_plural = verbose_name

    def __str__(self):
        return str(self.revision)
