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
from enum import Enum
from functools import reduce
from typing import Any, Self

from auditlog.registry import auditlog
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models, transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from user.models import Department, manageable_department_ids
from utils.fields import NanoidField
from utils.permissions import AppPermission

from .errors import VersionConflict


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
    """入力の項目の型（authorization.md §5.4の入力の制限）。ファイルはWebhookの入口の段階で足す"""

    STRING = "string", _("文字")
    NUMBER = "number", _("数値")
    INTEGER = "integer", _("整数")
    BOOLEAN = "boolean", _("真偽")
    DATE = "date", _("日付")
    CHOICE = "choice", _("選択肢")


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
    def set_suspended(self, suspended: bool) -> None:
        current = TaskDefinition.objects.select_for_update(of=("self",)).get(pk=self.pk)
        if current.suspended != suspended:
            current.suspended = suspended
            current.save(update_fields=["suspended", "updated"])
        self.suspended = suspended


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


auditlog.register(TaskDefinition)
