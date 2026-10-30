"""業務のタスク（Task Definition。design.md §4.1、authorization.md §5.3〜§5.4）。

タスクは可変の入れ物で、範囲（個人・部門・全社・システム）と、今の版（current_revision）を持つ。中身（説明、
プロンプトのテンプレート、使うSkillの固定の版、Connector、Tool、モデル設定とeffort、入力の項目、業務結果のSchema、
機密）は不変の版（TaskDefinitionRevision）が持ち、保存のたびにサーバーが新しい版を作る。Runは受付時の版に固定する
（authorization.md §6）。SkillのようなDraftと評価は持たない。

部門・全社・システムのタスクの定義は、管理できる人だけに見せる（一覧は `TaskQuerySet.manageable_by()`、1件は
`TaskDefinition.can()`。どちらも `SCOPE_PERMISSIONS` と部門の判定を読む）。個人のタスクは本人だけ。
登録できるSkill・Connectorは範囲で決まる（task/checks.py）。
"""

import operator
import secrets
from enum import Enum
from functools import reduce
from typing import TYPE_CHECKING, Any, Self

from auditlog.registry import auditlog
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from user.models import Department, manageable_department_ids
from utils.fields import NanoidField
from utils.permissions import AppPermission

from .errors import VersionConflict

if TYPE_CHECKING:
    from django_stubs_ext import StrPromise


class TaskScope(models.IntegerChoices):
    """値はSkill・Connectorの範囲と同じにする（範囲ごとの表を突き合わせるため）"""

    PERSONAL = 1, _("個人")
    DEPARTMENT = 2, _("部門")
    COMPANY = 3, _("全社")
    SYSTEM = 4, _("システム")


# 業務画面に出す範囲。システムのタスクは管理画面で扱う（authorization.md §7.3）
BUSINESS_SCOPES = (TaskScope.PERSONAL, TaskScope.DEPARTMENT, TaskScope.COMPANY)


class TaskAction(Enum):
    """タスクに対する操作（authorization.md §5.3・§6）。`TaskDefinition.can()` で判定する"""

    MANAGE = "manage"  # 定義を見る・作る・変える・停止する・削除する
    RUN = "run"  # タスクの画面から手動で実行する（停止中・版のないタスクは実行できない）


class TaskInputType(models.TextChoices):
    """入力の項目の型（authorization.md §5.4の入力の制限）。ファイルは /workspace/input に置き、値はそのパスにする"""

    STRING = "string", _("文字")
    NUMBER = "number", _("数値")
    INTEGER = "integer", _("整数")
    BOOLEAN = "boolean", _("真偽")
    DATE = "date", _("日付")
    CHOICE = "choice", _("選択肢")
    FILE = "file", _("ファイル")


# 範囲ごとの管理のpermission（authorization.md §5.3）。個人は本人だけで、permissionを見ない
SCOPE_PERMISSIONS = {
    TaskScope.DEPARTMENT: AppPermission.MANAGE_DEPARTMENT_TASKS,
    TaskScope.COMPANY: AppPermission.MANAGE_COMPANY_TASKS,
    TaskScope.SYSTEM: AppPermission.MANAGE_SYSTEM_TASKS,
}


def manageable_condition(
    user, *, scope_field: str = "scope", owner_field: str = "owner", department_field: str = "department"
) -> Q:
    """タスクを管理できる条件（SQL）。Runの結果・ログを見られる人の判定でも、Runに記録した範囲と部門で使う
    （authorization.md §7.1）"""
    conditions = [Q(**{scope_field: TaskScope.PERSONAL, owner_field: user})]
    for scope, perm in SCOPE_PERMISSIONS.items():
        if user.has_perm(perm.perm):
            condition = Q(**{scope_field: scope})
            if scope == TaskScope.DEPARTMENT:
                condition &= Q(**{f"{department_field}__in": Department.objects.manageable_by(user)})
            conditions.append(condition)
    return reduce(operator.or_, conditions)


def can_manage(user, scope: int, *, owner_id: int | None, department_id: int | None) -> bool:
    """1件の判定。`manageable_condition()` と同じ規則"""
    if not user.is_active:
        return False
    if scope == TaskScope.PERSONAL:
        return owner_id == user.pk
    perm = SCOPE_PERMISSIONS[TaskScope(scope)]
    if not user.has_perm(perm.perm):
        return False
    return scope != TaskScope.DEPARTMENT or department_id in manageable_department_ids(user)


class TaskQuerySet(models.QuerySet):
    def manageable_by(self, user) -> Self:
        """管理できるタスク（`TaskAction.MANAGE`）"""
        if not user.is_active:
            return self.none()
        return self.filter(manageable_condition(user), deleted=False)


class TaskDefinition(models.Model):
    uid = NanoidField(unique=True)
    scope = models.PositiveSmallIntegerField(_("範囲"), choices=TaskScope.choices)
    # 個人の範囲では持ち主、部門の範囲では部門。範囲は作成時に決めて変えない（authorization.md §9）
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("持ち主"),
    )
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("部門")
    )
    name = models.CharField(_("名前"), max_length=100)
    current_revision = models.ForeignKey(
        "TaskDefinitionRevision",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("今の版"),
    )
    # 停止。新しいRunを受け付けず、順番待ちのRunも動かさず、実行中のRunも次の区切りで止める（authorization.md §6）
    suspended = models.BooleanField(_("停止中"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    objects = TaskQuerySet.as_manager()

    class Meta:
        verbose_name = _("タスク")
        verbose_name_plural = verbose_name
        permissions = [perm.meta for perm in SCOPE_PERMISSIONS.values()]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(scope=TaskScope.PERSONAL, owner__isnull=False, department__isnull=True)
                    | Q(scope=TaskScope.DEPARTMENT, owner__isnull=True, department__isnull=False)
                    | Q(scope__in=[TaskScope.COMPANY, TaskScope.SYSTEM], owner__isnull=True, department__isnull=True)
                ),
                name="task_scope_owner",
            ),
            # チャットからの起動の候補で名前を使うので、同じ範囲の中では名前を重ねない
            models.UniqueConstraint(
                fields=["owner", "name"],
                condition=Q(scope=TaskScope.PERSONAL, deleted=False),
                name="uniq_personal_task_name",
            ),
            models.UniqueConstraint(
                fields=["department", "name"],
                condition=Q(scope=TaskScope.DEPARTMENT, deleted=False),
                name="uniq_department_task_name",
            ),
            models.UniqueConstraint(
                fields=["scope", "name"],
                condition=Q(scope__in=[TaskScope.COMPANY, TaskScope.SYSTEM], deleted=False),
                name="uniq_shared_task_name",
            ),
        ]

    def __str__(self):
        return self.name

    def can(self, user, action: TaskAction) -> bool:
        """利用者がこの操作をできるか（authorization.md §5.3・§6）。一覧の `TaskQuerySet.manageable_by()` と同じ規則。
        作る前の判定にも使う（保存していないタスクでも、範囲・持ち主・部門があれば判定できる）"""
        if self.deleted or not can_manage(user, self.scope, owner_id=self.owner_id, department_id=self.department_id):
            return False
        return action != TaskAction.RUN or self.in_service()

    def in_service(self) -> bool:
        return not self.deleted and not self.suspended and self.current_revision_id is not None

    @property
    def version_number(self) -> int:
        return self.current_revision.number if self.current_revision else 0

    @classmethod
    @transaction.atomic
    def create_with_revision(
        cls,
        *,
        scope: TaskScope,
        name: str,
        actor,
        department: Department | None = None,
        change_reason: str = "",
        content: dict[str, Any],
    ) -> "TaskDefinition":
        task = cls.objects.create(
            scope=scope,
            owner=actor if scope == TaskScope.PERSONAL else None,
            department=department if scope == TaskScope.DEPARTMENT else None,
            name=name,
            created_by=actor,
        )
        task.revise(actor=actor, expected_version=0, change_reason=change_reason, **content)
        return task

    @transaction.atomic
    def revise(
        self, *, actor, expected_version: int, change_reason: str = "", **fields: Any
    ) -> "TaskDefinitionRevision":
        """中身を変えた新しい版を作り、今の版にする。次のRunから使う（順番待ち・実行中のRunは受付時の版のまま）。

        編集を始めた後に他の人が版を作っていた・削除されていたら VersionConflict。中身が今の版と同じなら版を作らず、
        今の版を返す。タスクの行のロックは呼び出し元のtransactionの終わりまで続く
        """
        current = (
            TaskDefinition.objects.select_for_update(of=("self",)).select_related("current_revision").get(pk=self.pk)
        )
        if current.deleted:
            raise VersionConflict("deleted")
        latest = current.current_revision
        latest_number = latest.number if latest else 0
        if latest_number != expected_version:
            raise VersionConflict(f"expected={expected_version} latest={latest_number}")
        if latest and all(getattr(latest, name) == fields[name] for name in REVISION_FIELDS):
            self.current_revision = latest
            return latest
        revision = TaskDefinitionRevision.objects.create(
            task=current, number=latest_number + 1, created_by=actor, change_reason=change_reason, **fields
        )
        current.current_revision = revision
        current.save(update_fields=["current_revision", "updated"])
        self.current_revision = revision
        return revision

    @transaction.atomic
    def set_suspended(self, suspended: bool, *, actor=None) -> None:
        """停止・再開する。停止では、順番待ち・実行中のRunにも停止を依頼する（すぐ再開しても、停止の前に受け付けた
        Runは動かさない。authorization.md §6）"""
        current = TaskDefinition.objects.select_for_update(of=("self",)).get(pk=self.pk)
        if current.suspended != suspended:
            current.suspended = suspended
            current.save(update_fields=["suspended", "updated"])
        self.suspended = suspended
        if suspended:
            current.stop_runs(_("タスクが停止されました"), actor=actor)

    @transaction.atomic
    def soft_delete(self, *, actor=None) -> None:
        current = TaskDefinition.objects.select_for_update(of=("self",)).get(pk=self.pk)
        current.deleted = True
        current.save(update_fields=["deleted", "updated"])
        self.deleted = True
        # 入口も消す（鍵を使えなくする。監査ログに残すため1件ずつ保存する）
        for binding in TriggerBinding.objects.alive().filter(task=current):
            binding.deleted = True
            binding.save(update_fields=["deleted", "updated"])
        current.stop_runs(_("タスクが削除されました"), actor=actor)

    def stop_runs(self, reason: "str | StrPromise", *, actor=None) -> None:
        """このタスクの順番待ち・実行中のRunに停止を依頼する（実行中のRunはWorkerが次の区切りで止める）"""
        from run.models import CANCELLABLE_STATUSES, Run  # noqa: PLC0415  runのModelがtaskを参照するため

        for run in Run.objects.filter(task=self, status__in=CANCELLABLE_STATUSES).order_by("pk"):
            run.request_cancel(str(reason), actor)


# 版に入れる項目。変えると新しい版になる
REVISION_FIELDS = (
    "description",
    "prompt_template",
    "skills",
    "connectors",
    "tools",
    "model_profile",
    "effort",
    "handles_sensitive_data",
    "inputs",
    "output_schema",
    "destination",
    "notify_events",
    "notify_content",
    "notification_skill",
)


class TaskDefinitionRevision(models.Model):
    """タスクの版。作成後に変更・削除しない。

    - `skills`：使うSkillと固定の版。`[{"skill": "<SkillのID>", "version": <版の番号>}]`。Skillが新しい版を公開しても
      切り替わらない（authorization.md §5.4）
    - `connectors`：使うConnectorのID。タスクのRunでは、エージェントはここにあるものだけを使う
    - `tools`：使ってよいTool。Skillの「使ってよいTool」と両方に入っているものだけを使う
    - `inputs`：入力の項目。`[{"name": "vendor", "label": "取引先", "type": "string", "required": true,
      "choices": [], "max_length": 200}]`。プロンプトのテンプレートに `{{ inputs.vendor }}` で埋め込む
    - `output_schema`：業務結果のSchema。なければSkillのもの
    - `destination`・`notify_events`・`notify_content`：結果の通知（出口、通知するイベント、通知の内容）。初期は
      1つのタスクに1つ（design.md §4.1）。Runは受付時に出口の版を固定する
    - `notification_skill`：通知Skillと固定の版（`{"skill": "<SkillのID>", "version": <版の番号>}`。任意）。業務のRunの
      中で読み込み、`prepare_notification` で通知の文章と渡すファイルを決めさせる（design.md §4.3）
    """

    uid = NanoidField(unique=True)
    task = models.ForeignKey(
        TaskDefinition, on_delete=models.PROTECT, related_name="revisions", verbose_name=_("タスク")
    )
    number = models.PositiveIntegerField(_("版"))
    description = models.TextField(_("説明"), blank=True)
    prompt_template = models.TextField(_("プロンプトのテンプレート"))
    skills = models.JSONField(_("使うSkillと版"), default=list)
    connectors = ArrayField(models.CharField(max_length=30), verbose_name=_("使うConnector"), default=list)
    tools = ArrayField(models.CharField(max_length=50), verbose_name=_("使ってよいTool"), default=list)
    model_profile = models.ForeignKey(
        "llm.ModelProfile", on_delete=models.PROTECT, related_name="+", verbose_name=_("モデル設定")
    )
    # 空ならモデル設定の既定のeffort
    effort = models.CharField(_("effort"), max_length=50, blank=True)
    # 機密のタスク。機密のSkill・Connectorを使え、ローカルLLMのモデルだけを使う（design.md §4冒頭）
    handles_sensitive_data = models.BooleanField(_("個人情報・機密情報を扱う"), default=False)
    inputs = models.JSONField(_("入力の項目"), default=list)
    output_schema = models.JSONField(_("業務結果のSchema"), null=True, blank=True)
    destination = models.ForeignKey(
        "delivery.Destination",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("結果の通知先"),
    )
    notify_events = ArrayField(models.CharField(max_length=30), verbose_name=_("通知するイベント"), default=list)
    notify_content = models.CharField(_("通知の内容"), max_length=30, default="status_and_result")
    notification_skill = models.JSONField(_("通知Skillと版"), null=True, blank=True)
    change_reason = models.CharField(_("変更理由"), max_length=500, blank=True)
    # 最後にタスクを保存した人（authorization.md §6。Runに記録する）
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("保存した人")
    )

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("タスクの版")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["task", "number"], name="uniq_task_revision_number")]

    def __str__(self):
        return f"{self.task} v{self.number}"


class TriggerKind(models.IntegerChoices):
    """入口の種類。スケジュールは後で足す"""

    WEBHOOK = 1, _("Webhook")


# Webhookの入口を付けられるタスクの範囲（個人のタスクの入口はスケジュールだけ。authorization.md §5.4）
WEBHOOK_TASK_SCOPES = (TaskScope.DEPARTMENT, TaskScope.COMPANY, TaskScope.SYSTEM)
# payloadの大きさの上限（入口ごとに設定する。既定1MiB・最大10MiB。nginxの client_max_body_size も合わせる）
DEFAULT_MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_PAYLOAD_BYTES = 10 * 1024 * 1024
# 鍵の形。見分け用に先頭の数文字（KEY_PREFIX_LENGTH）だけを保存して画面に出す
WEBHOOK_KEY_PREFIX = "yrk_wh_"
KEY_PREFIX_LENGTH = len(WEBHOOK_KEY_PREFIX) + 6


class TriggerBindingQuerySet(models.QuerySet):
    def alive(self) -> Self:
        return self.filter(deleted=False)


class TriggerBinding(models.Model):
    """タスクの入口（design.md §4.1・§4.2、authorization.md §5.5）。連携の管理者（Modelのpermission）が作る。

    Webhookの入口とタスクは1対1で、タスクは作成時に決めて変えない（鍵を持つ送り元が、後から付けたタスクまで起動
    できないように。付け替えるときは入口を作り直す）。鍵はハッシュと見分け用の先頭の数文字だけを保存し、作成時と
    作り直したときに一度だけ返す。受け取り方（重複の判定、項目の取り出し、payloadの上限）は不変の版
    （TriggerBindingRevision）が持ち、保存のたびに新しい版を作る。受け付けたRunは受付時の版を記録する
    """

    uid = NanoidField(unique=True)
    kind = models.PositiveSmallIntegerField(_("種類"), choices=TriggerKind.choices, default=TriggerKind.WEBHOOK)
    task = models.ForeignKey(
        TaskDefinition, on_delete=models.PROTECT, related_name="triggers", verbose_name=_("タスク")
    )
    name = models.CharField(_("名前"), max_length=100)
    key_hash = models.CharField(_("鍵のハッシュ"), max_length=64, unique=True, editable=False)
    key_prefix = models.CharField(_("鍵の先頭"), max_length=20, editable=False)
    current_revision = models.ForeignKey(
        "TriggerBindingRevision",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("今の版"),
    )
    # 停止。新しい要求を受け付けない（受け付けたRunは止めない。止めたいときはタスクを停止する）
    suspended = models.BooleanField(_("停止中"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )
    key_rotated = models.DateTimeField(_("鍵を作った日時"), null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    objects = TriggerBindingQuerySet.as_manager()

    class Meta:
        verbose_name = _("タスクの入口")
        verbose_name_plural = verbose_name
        constraints = [
            # Webhookの入口とタスクは1対1（authorization.md §5.5）
            models.UniqueConstraint(
                fields=["task"], condition=Q(kind=TriggerKind.WEBHOOK, deleted=False), name="uniq_task_webhook"
            ),
        ]

    def __str__(self):
        return self.name

    def in_service(self) -> bool:
        return not self.deleted and not self.suspended and self.current_revision_id is not None

    @property
    def version_number(self) -> int:
        return self.current_revision.number if self.current_revision else 0

    @staticmethod
    def new_key() -> tuple[str, str, str]:
        """新しい鍵。（鍵、ハッシュ、先頭）"""
        from utils.authentication import hash_hook_key  # noqa: PLC0415  utilsのModelに依らない関数

        key = WEBHOOK_KEY_PREFIX + secrets.token_urlsafe(32)
        return key, hash_hook_key(key), key[:KEY_PREFIX_LENGTH]

    @classmethod
    @transaction.atomic
    def create_with_revision(
        cls, *, task: TaskDefinition, name: str, actor, change_reason: str = "", content: dict[str, Any]
    ) -> tuple["TriggerBinding", str]:
        """入口と最初の版を作る。（入口、鍵）。鍵はここでだけ返す"""
        key, key_hash, prefix = cls.new_key()
        binding = cls.objects.create(
            task=task,
            name=name,
            key_hash=key_hash,
            key_prefix=prefix,
            key_rotated=timezone.now(),
            created_by=actor,
        )
        binding.revise(actor=actor, expected_version=0, change_reason=change_reason, **content)
        return binding, key

    @transaction.atomic
    def revise(
        self, *, actor, expected_version: int, change_reason: str = "", **fields: Any
    ) -> "TriggerBindingRevision":
        """受け取り方を変えた新しい版を作り、今の版にする。次の要求から使う。競合は VersionConflict。
        中身が今の版と同じなら版を作らない（TaskDefinition.revise() と同じ）"""
        current = (
            TriggerBinding.objects.select_for_update(of=("self",)).select_related("current_revision").get(pk=self.pk)
        )
        if current.deleted:
            raise VersionConflict("deleted")
        latest = current.current_revision
        latest_number = latest.number if latest else 0
        if latest_number != expected_version:
            raise VersionConflict(f"expected={expected_version} latest={latest_number}")
        if latest and all(getattr(latest, name) == fields[name] for name in TRIGGER_REVISION_FIELDS):
            self.current_revision = latest
            return latest
        revision = TriggerBindingRevision.objects.create(
            binding=current, number=latest_number + 1, created_by=actor, change_reason=change_reason, **fields
        )
        current.current_revision = revision
        current.save(update_fields=["current_revision", "updated"])
        self.current_revision = revision
        return revision

    @transaction.atomic
    def rotate_key(self) -> str:
        """鍵を作り直す。前の鍵はすぐに使えなくなる。新しい鍵はここでだけ返す"""
        key, key_hash, prefix = self.new_key()
        current = TriggerBinding.objects.select_for_update(of=("self",)).get(pk=self.pk)
        current.key_hash, current.key_prefix, current.key_rotated = key_hash, prefix, timezone.now()
        current.save(update_fields=["key_hash", "key_prefix", "key_rotated", "updated"])
        self.key_hash, self.key_prefix, self.key_rotated = key_hash, prefix, current.key_rotated
        return key


# 入口の版に入れる項目。変えると新しい版になる
TRIGGER_REVISION_FIELDS = ("event_id_path", "inputs", "max_payload_bytes")


class TriggerBindingRevision(models.Model):
    """入口の受け取り方の版。作成後に変更・削除しない。

    - `event_id_path`：重複の判定に使う送り元のイベントIDの場所（JSONのpath。例 `$.event.id`）。空なら
      `Idempotency-Key` ヘッダーか、推奨の形式の `event_id`（どれもなければ判定しない）
    - `inputs`：項目の取り出し。`[{"name": "vendor", "path": "$.data.vendor_name"}]`。型と必須かどうかは、
      タスクの版の入力の項目の定義で決める。空なら、推奨の形式の `inputs` をそのまま入力の値にする
    """

    uid = NanoidField(unique=True)
    binding = models.ForeignKey(
        TriggerBinding, on_delete=models.PROTECT, related_name="revisions", verbose_name=_("入口")
    )
    number = models.PositiveIntegerField(_("版"))
    event_id_path = models.CharField(_("イベントIDの場所"), max_length=200, blank=True)
    inputs = models.JSONField(_("項目の取り出し"), default=list)
    max_payload_bytes = models.PositiveIntegerField(_("payloadの大きさの上限"), default=DEFAULT_MAX_PAYLOAD_BYTES)
    change_reason = models.CharField(_("変更理由"), max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("保存した人")
    )

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("タスクの入口の版")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["binding", "number"], name="uniq_trigger_revision_number")]

    def __str__(self):
        return f"{self.binding} v{self.number}"


class TriggerReceipt(models.Model):
    """入口が受け付けた要求（design.md §4.2）。Runと同じtransactionで作る。

    重複のキー（送り元のイベントIDか `Idempotency-Key`）があれば、入口ごとに一意にする。同じキーと同じ入力の再送には
    既存のRunを返し、同じキーで違う入力は断る。入口の版が変わっても、同じ受信の再送で新しいRunを作らない。
    キーがなければ重複の判定をせず、届くたびに新しいRunを作る。キーは今は消さずに持ち続ける
    """

    uid = NanoidField(unique=True)
    binding = models.ForeignKey(
        TriggerBinding, on_delete=models.PROTECT, related_name="receipts", verbose_name=_("入口")
    )
    binding_revision = models.ForeignKey(
        TriggerBindingRevision, on_delete=models.PROTECT, related_name="+", verbose_name=_("入口の版")
    )
    # 空なら重複を判定しない
    # 出所（event:・idempotency:）を付けて持つ
    dedup_key = models.CharField(_("重複のキー"), max_length=220, blank=True)
    payload_sha256 = models.CharField(_("入力のハッシュ"), max_length=64)
    payload_size = models.PositiveIntegerField(_("payloadの大きさ"))
    content_type = models.CharField(_("形式"), max_length=100)
    run = models.OneToOneField(
        "run.Run", on_delete=models.PROTECT, related_name="trigger_receipt", verbose_name=_("実行")
    )

    created = models.DateTimeField(_("受付日時"), auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("入口の受付")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["binding", "dedup_key"], condition=~Q(dedup_key=""), name="uniq_trigger_dedup_key"
            ),
        ]

    def __str__(self):
        return f"{self.binding} {self.dedup_key or self.uid}"


auditlog.register(TaskDefinition)
auditlog.register(TriggerBinding, exclude_fields=["key_hash", "updated"])
auditlog.register(TriggerBindingRevision)
