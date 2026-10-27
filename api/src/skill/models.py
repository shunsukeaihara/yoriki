"""Skill（design.md §3）。

Skillは可変の入れ物で、範囲（個人・部門・全社・システム）と、公開中の版（current_version）を持つ。中身（説明、手順、
使ってよいTool、必要な能力、入出力のSchema、資料）は、編集中のSkillDraftと、公開した不変のSkillVersionが持つ。
DraftはSkillごとに1つで、revisionで同時の編集を検出し、公開すると閉じる（design.md §3.7）。
公開・切戻し・停止はSkillChangeに記録し、operation keyで再送による重複を防ぐ。

誰が見て・使い・編集・公開できるかは、範囲と所属とpermissionで決まる（authorization.md §5.1。一覧は
`SkillQuerySet`、1件は `Skill.can()`。どちらも `SCOPE_PERMISSIONS` と部門の判定を読む）。
Skillの本文は権限の根拠にしない。実行するときのToolの許可は、利用者・Skill・Runの範囲をサーバーで照合して決める。
"""

import hashlib
import io
import operator
from collections.abc import Callable
from enum import Enum
from functools import reduce
from typing import IO, Any

from auditlog.registry import auditlog
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models, transaction
from django.db.models import Max, Q
from django.utils.translation import gettext_lazy as _

from artifact.models import clean_filename
from resources import formats
from resources.models import Resource, ResourceVersion
from user.models import Department, manageable_department_ids, usable_department_ids
from utils.fields import NanoidField
from utils.permissions import AppPermission

from .errors import DraftExists, EvaluationRequired, InvalidDraft, VersionConflict


class SkillScope(models.IntegerChoices):
    PERSONAL = 1, _("個人")
    DEPARTMENT = 2, _("部門")
    COMPANY = 3, _("全社")
    SYSTEM = 4, _("システム")


# 業務画面と会話に出す範囲。システムのSkillはシステムタスクだけが使い、管理画面で扱う（authorization.md §2）
CONVERSATION_SCOPES = (SkillScope.PERSONAL, SkillScope.DEPARTMENT, SkillScope.COMPANY)


class SkillAction(Enum):
    """Skillに対する操作（authorization.md §5.1）。`Skill.can()` で判定する"""

    VIEW = "view"  # 一覧・詳細で見る。使うだけの人には公開中のものだけ
    USE = "use"  # 会話で使う（公開中で停止していないもの。システムのSkillは会話で使わない）
    EDIT = "edit"  # Draftを変更する（本文・資料・評価ケース）
    PUBLISH = "publish"  # 公開・切戻し・停止・削除、名前とToolの変更、資料の検証の条件と必須の評価ケースの変更


class SkillRequirement(models.IntegerChoices):
    """Skillが使うモデルに要る能力（design.md §4.8）。使うモデル設定の版の能力と照らし合わせる"""

    TOOL_CALLING = 1, _("Tool呼出し")
    IMAGE_INPUT = 2, _("画像入力")
    DOCUMENT_INPUT = 3, _("文書入力")
    STRUCTURED_OUTPUT = 4, _("構造化出力")


class SkillAssetRole(models.IntegerChoices):
    """資料の用途（design.md §3.8）"""

    REFERENCE = 1, _("参照資料")
    MASTER = 2, _("マスターデータ")
    TEMPLATE = 3, _("テンプレート")
    SCRIPT = 4, _("スクリプト")


class SkillChangeKind(models.IntegerChoices):
    PUBLISH = 1, _("公開")
    ROLLBACK = 2, _("切戻し")
    SUSPEND = 3, _("停止")
    RESUME = 4, _("再開")


# 必要な能力と、それを持つかを示すモデル設定の版の項目
REQUIREMENT_CAPABILITIES = {
    SkillRequirement.TOOL_CALLING: "supports_tools",
    SkillRequirement.IMAGE_INPUT: "supports_images",
    SkillRequirement.DOCUMENT_INPUT: "supports_documents",
    SkillRequirement.STRUCTURED_OUTPUT: "supports_structured_output",
}

# Draftと版が持つ中身。版を作るときにDraftからそのまま写す
CONTENT_FIELDS = (
    "description",
    "instructions",
    "tools",
    "requires",
    "input_schema",
    "output_schema",
    "connectors",
    "is_sensitive",
)
# 範囲ごとの規則（authorization.md §5.1）。個人は本人がすべてでき、ほかの範囲はここで決まる
# - 使う：部門は使える部門の人、全社は全員、システムは会話で使わない
# - 編集・公開：範囲ごとの（編集、公開）のpermission。部門は管理できる部門のSkillにだけ効く。公開できる人は編集もできる
SCOPE_PERMISSIONS = {
    SkillScope.DEPARTMENT: (AppPermission.EDIT_DEPARTMENT_SKILLS, AppPermission.PUBLISH_DEPARTMENT_SKILLS),
    SkillScope.COMPANY: (AppPermission.EDIT_COMPANY_SKILLS, AppPermission.PUBLISH_COMPANY_SKILLS),
    SkillScope.SYSTEM: (AppPermission.EDIT_SYSTEM_SKILLS, AppPermission.PUBLISH_SYSTEM_SKILLS),
}


def _scope_has_perm(user, scope: SkillScope, *, publish: bool) -> bool:
    edit_perm, publish_perm = SCOPE_PERMISSIONS[scope]
    return user.has_perm(publish_perm.perm) or (not publish and user.has_perm(edit_perm.perm))


class SkillContent(models.Model):
    """DraftとSkillVersionに共通の中身"""

    # エージェントと社員がSkillを選ぶための説明（何をするか、どんな依頼のときに使うか）
    description = models.TextField(_("説明"), blank=True)
    instructions = models.TextField(_("手順"), blank=True)
    # 使ってよいTool。空ならコマンド不要のSkill（design.md §3.4）。利用者の権限を超えるToolは与えない
    tools = ArrayField(models.CharField(max_length=100), verbose_name=_("使ってよいTool"), default=list, blank=True)
    requires = ArrayField(
        models.PositiveSmallIntegerField(choices=SkillRequirement.choices),
        verbose_name=_("必要な能力"),
        default=list,
        blank=True,
    )
    input_schema = models.JSONField(_("入力のSchema"), null=True, blank=True)
    output_schema = models.JSONField(_("業務結果のSchema"), null=True, blank=True)
    # 使うConnectorと操作・表の宣言（skill/connectors.py。design.md §4.9.1）
    connectors = models.JSONField(_("使うConnector"), default=list, blank=True)
    # 資料そのものに個人情報・機密情報を含むSkill。機密の会話・タスクでだけ使う（design.md §4冒頭）。
    # 機密のConnectorを宣言したSkillは、このフラグがなくても機密のSkillとして扱う
    is_sensitive = models.BooleanField(_("機密"), default=False)

    class Meta:
        abstract = True

    def content(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in CONTENT_FIELDS}

    def unsupported_by(self, model_version) -> str:
        """使うモデルが、この版の必要な能力を持たない理由（持てば空文字）。Toolを使うSkillと、業務結果を
        submit_result で確定するSkillは、Tool呼出しも要る"""
        requires = set(self.requires)
        if self.tools or self.output_schema:
            requires.add(SkillRequirement.TOOL_CALLING)
        missing = [
            str(SkillRequirement(r).label)
            for r in sorted(requires)
            if not getattr(model_version, REQUIREMENT_CAPABILITIES[SkillRequirement(r)])
        ]
        if not missing:
            return ""
        return str(_("このモデルは、Skillに必要な能力（%(missing)s）を持ちません") % {"missing": "、".join(missing)})


class SkillQuerySet(models.QuerySet):
    """一覧の絞り込み。1件の判定（`Skill.can()`）と同じ規則をSQLの条件にする"""

    def _managed(self, user, *, publish: bool) -> Q:
        """編集（publish=Trueなら公開）できるSkillの条件"""
        conditions = [Q(scope=SkillScope.PERSONAL, owner=user)]
        for scope in SCOPE_PERMISSIONS:
            if _scope_has_perm(user, scope, publish=publish):
                condition = Q(scope=scope)
                if scope == SkillScope.DEPARTMENT:
                    condition &= Q(department__in=Department.objects.manageable_by(user))
                conditions.append(condition)
        return reduce(operator.or_, conditions)

    def _usable(self, user) -> Q:
        """範囲から使えるSkillの条件（公開済みか・停止中かは見ない）"""
        return (
            Q(scope=SkillScope.PERSONAL, owner=user)
            | Q(scope=SkillScope.DEPARTMENT, department__in=Department.objects.usable_by(user))
            | Q(scope=SkillScope.COMPANY)
        )

    def visible_to(self, user) -> "SkillQuerySet":
        """見えるSkill（`SkillAction.VIEW`）。使うだけの人には公開中のものだけ、編集できる人には未公開のものも"""
        if not user.is_active:
            return self.none()
        return self.filter(
            self._managed(user, publish=False) | (self._usable(user) & Q(current_version__isnull=False)),
            deleted=False,
        )

    def usable_by(self, user) -> "SkillQuerySet":
        """会話で使えるSkill（`SkillAction.USE`）：公開中の版があり、停止していないもの。システムのSkillは含まない"""
        if not user.is_active:
            return self.none()
        return self.filter(self._usable(user), current_version__isnull=False, suspended=False, deleted=False)

    def editable_by(self, user) -> "SkillQuerySet":
        """Draftを変更できるSkill（`SkillAction.EDIT`）"""
        if not user.is_active:
            return self.none()
        return self.filter(self._managed(user, publish=False), deleted=False)

    def in_conversation(self) -> "SkillQuerySet":
        """業務画面と会話に出す範囲のSkill（システムのSkillを除く）"""
        return self.filter(scope__in=CONVERSATION_SCOPES)


class Skill(models.Model):
    uid = NanoidField(unique=True)
    scope = models.PositiveSmallIntegerField(_("範囲"), choices=SkillScope.choices)
    # 個人の範囲では持ち主、部門の範囲では部門。範囲は作成時に決めて変えない（authorization.md §5.1）
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
    # 公開範囲の最新の公開版。まだ公開していなければNone
    current_version = models.ForeignKey(
        "SkillVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("公開中の版"),
    )
    # 緊急停止。新しいRunでも、進行中のRunの次の呼出しでも使わせない（design.md §11.7）
    suspended = models.BooleanField(_("停止中"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    objects = SkillQuerySet.as_manager()

    class Meta:
        verbose_name = _("Skill")
        verbose_name_plural = verbose_name
        permissions = [perm.meta for pair in SCOPE_PERMISSIONS.values() for perm in pair]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(scope=SkillScope.PERSONAL, owner__isnull=False, department__isnull=True)
                    | Q(scope=SkillScope.DEPARTMENT, owner__isnull=True, department__isnull=False)
                    | Q(scope__in=[SkillScope.COMPANY, SkillScope.SYSTEM], owner__isnull=True, department__isnull=True)
                ),
                name="skill_scope_owner",
            ),
            # `/` での指定とエージェントへの一覧で名前を使うので、同じ範囲の中では名前を重ねない
            models.UniqueConstraint(
                fields=["owner", "name"],
                condition=Q(scope=SkillScope.PERSONAL, deleted=False),
                name="uniq_personal_skill_name",
            ),
            models.UniqueConstraint(
                fields=["department", "name"],
                condition=Q(scope=SkillScope.DEPARTMENT, deleted=False),
                name="uniq_department_skill_name",
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(scope=SkillScope.COMPANY, deleted=False),
                name="uniq_company_skill_name",
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(scope=SkillScope.SYSTEM, deleted=False),
                name="uniq_system_skill_name",
            ),
        ]

    def __str__(self):
        return self.name

    def can(self, user, action: SkillAction) -> bool:
        """利用者がこの操作をできるか（authorization.md §5.1）。一覧の SkillQuerySet と同じ規則。
        作る前の判定にも使う（保存していないSkillでも、範囲・持ち主・部門があれば判定できる）"""
        if not user.is_active or self.deleted:
            return False
        scope = SkillScope(self.scope)
        if scope == SkillScope.PERSONAL:
            # 個人のものは持ち主だけ（permissionを見ないので、基盤管理者にも見えない）
            mine = self.owner_id == user.pk
            return mine and (action != SkillAction.USE or self._in_service())
        if action in (SkillAction.VIEW, SkillAction.USE):
            if scope == SkillScope.DEPARTMENT:
                usable = self.department_id in usable_department_ids(user)
            else:
                usable = scope == SkillScope.COMPANY
            if action == SkillAction.USE:
                return usable and self._in_service()
            return (usable and self.current_version_id is not None) or self.can(user, SkillAction.EDIT)
        if scope == SkillScope.DEPARTMENT and self.department_id not in manageable_department_ids(user):
            return False
        return _scope_has_perm(user, scope, publish=action == SkillAction.PUBLISH)

    def _in_service(self) -> bool:
        return self.current_version_id is not None and not self.suspended

    def unusable_reason(self, user, model_version, *, sensitive: bool, connectors: set[str] | None = None) -> str:
        """利用者がこのモデル設定の版で使えない理由（使えれば空文字）。Run開始時とエージェントが読み込むときに、
        その時点の公開中の版で確かめる（design.md §3.2・§4.8）。`sensitive` はRunが機密か、`connectors` はRunの
        Connectorの一覧のID（宣言したConnectorがなければ使えない。design.md §4.9.1）"""
        if self.scope not in CONVERSATION_SCOPES or not self.can(user, SkillAction.VIEW):
            return str(_("このSkillを使う権限がありません"))
        if self.suspended:
            return str(_("このSkillは停止中です"))
        version = self.current_version
        if version is None:
            return str(_("このSkillはまだ公開されていません"))
        if not self.can(user, SkillAction.USE):
            return str(_("このSkillを使う権限がありません"))
        if reason := version.unsupported_by(model_version):
            return reason
        from .connectors import unusable_reason  # noqa: PLC0415  skill.connectorsがこのモジュールを使うため

        return unusable_reason(version, user, sensitive=sensitive, available=connectors)

    @property
    def version_number(self) -> int:
        return self.current_version.number if self.current_version else 0

    @classmethod
    @transaction.atomic
    def create_with_draft(
        cls, *, scope: SkillScope, name: str, actor, department: Department | None = None, **content: Any
    ) -> "Skill":
        """Skillと最初のDraftを作る。公開するまで、誰も使えない"""
        skill = cls.objects.create(
            scope=scope,
            owner=actor if scope == SkillScope.PERSONAL else None,
            department=department if scope == SkillScope.DEPARTMENT else None,
            name=name,
            created_by=actor,
        )
        SkillDraft.objects.create(skill=skill, created_by=actor, updated_by=actor, **content)
        return skill

    def _lock(self) -> "Skill":
        return Skill.objects.select_for_update(of=("self",)).select_related("current_version").get(pk=self.pk)

    def _recorded(
        self, operation_key: str, kind: "SkillChangeKind", same: Callable[["SkillChange"], bool]
    ) -> "SkillVersion | None":
        """同じoperation keyの操作が済んでいれば、その結果の版（再送で版を重ねて作らない。design.md §3.7）。

        同じkeyが別の操作（種別か対象の違うもの）に使われていたら、要求した操作をせずに前の結果を返すことになるので
        VersionConflict
        """
        if not operation_key:
            return None
        done = (
            SkillChange.objects.filter(skill=self, operation_key=operation_key)
            .select_related("version__rolled_back_from")
            .first()
        )
        if done is None:
            return None
        if done.kind != kind or done.version is None or not same(done):
            raise VersionConflict(f"the operation key {operation_key} was used for another operation")
        return done.version

    def _next_number(self) -> int:
        return (self.versions.aggregate(n=Max("number"))["n"] or 0) + 1

    @transaction.atomic
    def publish_draft(
        self,
        draft_uid: str,
        *,
        actor,
        expected_revision: int,
        change_reason: str = "",
        operation_key: str = "",
        run=None,
    ) -> "SkillVersion":
        """Draftから新しい版を作り、公開中の版にする。Draftは閉じる（消す）。

        Draftを開いた後に別の版が公開されていたら（Draftの元の版が公開中の版でなければ）、または編集を始めた後に
        Draftが変わっていたら VersionConflict。本文と資料の組合せは、1つのtransactionでまとめて確定する
        （design.md §3.10）
        """
        current = self._lock()
        if done := current._recorded(
            operation_key, SkillChangeKind.PUBLISH, lambda change: change.draft_uid == draft_uid
        ):
            return done
        draft = (
            SkillDraft.objects.select_for_update()
            .filter(skill=current, uid=draft_uid)
            .prefetch_related("assets__resource_version")
            .first()
        )
        if draft is None:
            raise VersionConflict("the draft is already closed")
        if draft.revision != expected_revision:
            raise VersionConflict(f"expected revision={expected_revision} latest={draft.revision}")
        if draft.base_version_id != current.current_version_id:
            raise VersionConflict("another version was published after the draft was opened")
        draft.check_publishable()
        draft.check_connectors(actor)
        draft.check_evaluated()

        previous = current.current_version
        version = SkillVersion.objects.create(
            skill=current,
            number=current._next_number(),
            change_reason=change_reason,
            created_by=actor,
            **draft.content(),
        )
        SkillAssetBinding.objects.bulk_create(
            SkillAssetBinding(
                version=version,
                key=a.key,
                role=a.role,
                description=a.description,
                resource_version=a.resource_version,
                checks=a.checks,
                validation=a.validation,
            )
            for a in draft.assets.all()
        )
        summary = summarize_changes(previous, draft.content(), _asset_map(draft.assets.all()))
        # 置き換えた資料の差分の数（CSVの変更行数、MDの行数、PPTXのスライド数。design.md §11.8）
        summary["asset_diffs"] = {
            a.key: counts
            for a in draft.assets.all()
            if a.key in summary["assets_replaced"] and (counts := formats.diff_counts(a.validation))
        }
        current._make_current(version)
        SkillChange.objects.create(
            skill=current,
            kind=SkillChangeKind.PUBLISH,
            version=version,
            previous_version=previous,
            actor=actor,
            reason=change_reason,
            summary=summary,
            draft_uid=draft.uid,
            operation_key=operation_key,
            run=run,
        )
        draft.delete()
        self.current_version = version
        return version

    @transaction.atomic
    def rollback(
        self,
        number: int,
        *,
        actor,
        expected_version: int,
        change_reason: str = "",
        operation_key: str = "",
        run=None,
    ) -> "SkillVersion":
        """選んだ過去の版と同じ中身・資料の版を新しく作り、公開中の版にする（履歴は消さない。design.md §3.7）"""
        current = self._lock()
        if done := current._recorded(
            operation_key,
            SkillChangeKind.ROLLBACK,
            lambda change: (
                change.version is not None
                and change.version.rolled_back_from is not None
                and change.version.rolled_back_from.number == number
            ),
        ):
            return done
        if current.version_number != expected_version:
            raise VersionConflict(f"expected version={expected_version} latest={current.version_number}")
        source = current.versions.prefetch_related("assets__resource_version").filter(number=number).first()
        if source is None:
            raise InvalidDraft(_("切り戻す版がありません"))
        if source.pk == current.current_version_id:
            raise InvalidDraft(_("公開中の版には切り戻せません"))
        # 過去の版の宣言も、今のConnectorの境界と切り戻す人の権限で確かめる（公開と同じ。design.md §4.9.1）
        from .connectors import publish_errors  # noqa: PLC0415  skill.connectorsがこのモジュールを使うため

        if errors := publish_errors(source, current, actor):
            raise InvalidDraft(
                _("この版が使うConnectorに問題があるので、切り戻せません：%(errors)s") % {"errors": "／".join(errors)}
            )

        previous = current.current_version
        version = SkillVersion.objects.create(
            skill=current,
            number=current._next_number(),
            change_reason=change_reason,
            created_by=actor,
            rolled_back_from=source,
            **source.content(),
        )
        before = {a.key: a for a in previous.assets.select_related("resource_version__blob")} if previous else {}
        validations = {a.key: _rolled_back_validation(a, before.get(a.key)) for a in source.assets.all()}
        SkillAssetBinding.objects.bulk_create(
            SkillAssetBinding(
                version=version,
                key=a.key,
                role=a.role,
                description=a.description,
                resource_version=a.resource_version,
                checks=a.checks,
                validation=validations[a.key],
            )
            for a in source.assets.all()
        )
        summary = summarize_changes(previous, source.content(), _asset_map(source.assets.all()))
        summary["asset_diffs"] = {
            key: counts
            for key in summary["assets_replaced"]
            if (counts := formats.diff_counts(validations.get(key) or {}))
        }
        current._make_current(version)
        SkillChange.objects.create(
            skill=current,
            kind=SkillChangeKind.ROLLBACK,
            version=version,
            previous_version=previous,
            actor=actor,
            reason=change_reason,
            summary=summary,
            operation_key=operation_key,
            run=run,
        )
        self.current_version = version
        return version

    def _make_current(self, version: "SkillVersion") -> None:
        self.current_version = version
        self.save(update_fields=["current_version", "updated"])

    @transaction.atomic
    def set_suspended(self, suspended: bool, *, actor, reason: str = "") -> None:
        """緊急停止・再開する。既に同じ状態なら何もしない"""
        current = self._lock()
        if current.suspended == suspended:
            self.suspended = suspended
            return
        current.suspended = suspended
        current.save(update_fields=["suspended", "updated"])
        SkillChange.objects.create(
            skill=current,
            kind=SkillChangeKind.SUSPEND if suspended else SkillChangeKind.RESUME,
            version=current.current_version,
            actor=actor,
            reason=reason,
        )
        self.suspended = suspended


class SkillVersion(SkillContent):
    """公開したSkillの版。本文と資料の版の組合せを固定して持つ。作成後に変更・削除しない"""

    uid = NanoidField(unique=True)
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT, related_name="versions", verbose_name=_("Skill"))
    number = models.PositiveIntegerField(_("版"))
    # 切戻しで作った版は、元にした版
    rolled_back_from = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("切戻し元の版")
    )
    change_reason = models.CharField(_("変更理由"), max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("公開者")
    )

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Skillの版")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["skill", "number"], name="uniq_skill_version_number")]

    def __str__(self):
        return f"{self.skill} v{self.number}"


class SkillAssetBinding(models.Model):
    """版が使う資料。Skillの中の資料のkey・用途と、資料の版を結び付ける。作成後に変更・削除しない"""

    version = models.ForeignKey(SkillVersion, on_delete=models.PROTECT, related_name="assets", verbose_name=_("版"))
    # Skillの中で資料を指す安定した名前。置換しても変えない（Sandboxでは /workspace/master/skills/<Skill>/<key>/）
    key = models.CharField(_("資料のkey"), max_length=63)
    role = models.PositiveSmallIntegerField(_("用途"), choices=SkillAssetRole.choices)
    description = models.CharField(_("説明"), max_length=500, blank=True)
    resource_version = models.ForeignKey(
        ResourceVersion, on_delete=models.PROTECT, related_name="+", verbose_name=_("資料の版")
    )
    # 検証の条件（CSVの必須列・キー列・型等。formats.normalize_checks()）と、公開したときの検証の結果
    checks = models.JSONField(_("検証の条件"), default=dict, blank=True)
    validation = models.JSONField(_("検証の結果"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Skillの版の資料")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["version", "key"], name="uniq_skill_asset_binding_key")]

    def __str__(self):
        return f"{self.version} {self.key}"


class SkillDraft(SkillContent):
    """編集中のSkill。Skillごとに1つで、公開すると閉じる（design.md §3.7）。

    base_versionは開いたときの公開中の版。公開するときに、それが今も公開中の版であることを確かめる。
    revisionは変更のたびに1つ増え、更新・公開では編集を始めた時点の値を送らせる
    """

    uid = NanoidField(unique=True)
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="drafts", verbose_name=_("Skill"))
    base_version = models.ForeignKey(
        SkillVersion, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("元の版")
    )
    revision = models.PositiveIntegerField(_("revision"), default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("更新者")
    )

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("SkillのDraft")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["skill"], name="uniq_skill_draft")]

    def __str__(self):
        return f"{self.skill} draft r{self.revision}"

    @classmethod
    @transaction.atomic
    def open(cls, skill: Skill, *, actor, base: SkillVersion | None = None) -> "SkillDraft":
        """指定した版（省けば公開中の版）を元にDraftを開く。既にDraftがあれば DraftExists"""
        current = skill._lock()
        if cls.objects.filter(skill=current).exists():
            raise DraftExists()
        base = base or current.current_version
        draft = cls.objects.create(
            skill=current,
            base_version=current.current_version,
            created_by=actor,
            updated_by=actor,
            **(base.content() if base else {}),
        )
        if base:
            SkillDraftAsset.objects.bulk_create(
                SkillDraftAsset(
                    draft=draft,
                    key=a.key,
                    role=a.role,
                    description=a.description,
                    resource_version_id=a.resource_version_id,
                    checks=formats.normalize_checks(a.checks),
                    validation=draft._opened_validation(a),
                )
                for a in base.assets.select_related("resource_version__blob")
            )
        return draft

    def _opened_validation(self, asset: "SkillAssetBinding") -> dict[str, Any]:
        """開いたDraftの資料の検証の結果。公開中の版と同じ原本なら、その版の結果を差分なしで使う。過去の版から開いて
        原本が違えば（検証する前に公開した資料も）、公開中の版の同じ資料と比べて検証し直す"""
        current = self.base_asset(asset.key)
        if (
            asset.validation
            and current is not None
            and current.resource_version_id == asset.resource_version_id
            and formats.normalize_checks(current.checks) == formats.normalize_checks(asset.checks)
        ):
            return _without_diff(asset.validation)
        resource = asset.resource_version
        return self._inspect(asset.key, resource, resource.read(), asset.checks, False)

    def _lock(self, expected_revision: int) -> "SkillDraft":
        """行をロックし、編集を始めた後にDraftが変わっていない（公開で閉じてもいない）ことを確かめる"""
        current = SkillDraft.objects.select_for_update().filter(pk=self.pk).first()
        if current is None:
            raise VersionConflict("the draft is already closed")
        if current.revision != expected_revision:
            raise VersionConflict(f"expected revision={expected_revision} latest={current.revision}")
        return current

    def _bump(self, current: "SkillDraft", actor, fields: list[str]) -> None:
        current.revision += 1
        current.updated_by = actor
        current.save(update_fields=[*fields, "revision", "updated_by", "updated"])
        for name in (*fields, "revision", "updated_by", "updated"):
            setattr(self, name, getattr(current, name))

    @transaction.atomic
    def revise(self, *, actor, expected_revision: int, **content: Any) -> None:
        """中身を変える。編集を始めた後にDraftが変わっていたら VersionConflict"""
        current = self._lock(expected_revision)
        for name, value in content.items():
            setattr(current, name, value)
        self._bump(current, actor, list(content))

    @transaction.atomic
    def add_asset(
        self,
        *,
        key: str,
        role: SkillAssetRole,
        description: str,
        src: IO[bytes],
        filename: str,
        actor,
        expected_revision: int,
        checks: dict[str, Any] | None = None,
        edited: bool = False,
    ) -> "SkillDraftAsset":
        """アップロードしたファイルを新しい資料としてDraftに加える。原本のbytesをそのまま保存し、検証する。

        edited は、エージェントが元の資料を編集して作った候補か（formats.inspect()）
        """
        current = self._lock(expected_revision)
        if current.assets.filter(key=key).exists():
            raise InvalidDraft(_("同じkeyの資料があります"))
        data = src.read()
        resource_version = Resource.create_from_upload(io.BytesIO(data), filename, actor=actor)
        # 外してから同じkeyで加え直しても、元の版の検証の条件を引き継ぐ（条件を変えるのは公開の権限を持つ人だけ）
        base = current.base_asset(key)
        checks = formats.normalize_checks(checks if checks is not None else base.checks if base else None)
        asset = SkillDraftAsset.objects.create(
            draft=current,
            key=key,
            role=role,
            description=description,
            resource_version=resource_version,
            checks=checks,
            validation=current._inspect(key, resource_version, data, checks, edited),
        )
        self._bump(current, actor, [])
        return asset

    def base_asset(self, key: str) -> "SkillAssetBinding | None":
        if self.base_version_id is None:
            return None
        return (
            SkillAssetBinding.objects.select_related("resource_version__blob")
            .filter(version_id=self.base_version_id, key=key)
            .first()
        )

    def _inspect(
        self, key: str, resource_version: ResourceVersion, data: bytes, checks: dict[str, Any], edited: bool
    ) -> dict[str, Any]:
        """資料を検証し、Draftの元の版の同じkeyの資料との差分を取る"""
        base = self.base_asset(key)
        before = (base.resource_version.read(), base.resource_version.name) if base else None
        return formats.inspect(data, resource_version.name, checks=checks, before=before, edited=edited)

    @transaction.atomic
    def change_asset(
        self,
        asset: "SkillDraftAsset",
        *,
        actor,
        expected_revision: int,
        role: SkillAssetRole | None = None,
        description: str | None = None,
        src: IO[bytes] | None = None,
        filename: str = "",
        checks: dict[str, Any] | None = None,
        edited: bool = False,
    ) -> "SkillDraftAsset":
        """資料の用途・説明・検証の条件を変える。ファイルを送れば、同じ資料の新しい版として置き換える（keyと資料のIDは
        保つ。中身とファイル名が今の版と同じなら版を増やさない）。原本か検証の条件が変われば検証し直す"""
        current = self._lock(expected_revision)
        locked = (
            SkillDraftAsset.objects.select_related("resource_version__resource", "resource_version__blob")
            .filter(pk=asset.pk, draft=current)
            .first()
        )
        if locked is None:
            raise VersionConflict("the asset was removed")
        if role is not None:
            locked.role = role
        if description is not None:
            locked.description = description
        data: bytes | None = None
        if src is not None:
            data = src.read()
            name = clean_filename(filename or locked.resource_version.name)
            same = (
                name == locked.resource_version.name
                and hashlib.sha256(data).hexdigest() == locked.resource_version.sha256
            )
            if not same:
                locked.resource_version = locked.resource_version.resource.add_version(
                    io.BytesIO(data), name, actor=actor
                )
        else:
            edited = bool(locked.validation.get("edited"))
        if checks is not None and (checks := formats.normalize_checks(checks)) != formats.normalize_checks(
            locked.checks
        ):
            locked.checks = checks
            data = data if data is not None else locked.resource_version.read()
        if data is not None:
            locked.validation = current._inspect(locked.key, locked.resource_version, data, locked.checks, edited)
        locked.save()
        self._bump(current, actor, [])
        return locked

    @transaction.atomic
    def discard(self, *, expected_revision: int) -> None:
        """Draftを破棄する。編集を始めた後に他の人が変えていたら VersionConflict（その変更を黙って消さない）"""
        self._lock(expected_revision).delete()

    @transaction.atomic
    def remove_asset(self, asset: "SkillDraftAsset", *, actor, expected_revision: int) -> None:
        current = self._lock(expected_revision)
        SkillDraftAsset.objects.filter(pk=asset.pk, draft=current).delete()
        self._bump(current, actor, [])

    def check_publishable(self) -> None:
        if not self.description.strip():
            raise InvalidDraft(_("説明を入力してください（エージェントと社員がSkillを選ぶのに使います）"))
        if not self.instructions.strip():
            raise InvalidDraft(_("手順を入力してください"))
        base = {a.key: a for a in self.base_version.assets.all()} if self.base_version else {}
        for asset in self.assets.all():
            # 元の版から原本も条件も変えていない資料は、公開済みのものなので止めない
            prior = base.get(asset.key)
            if (
                prior
                and prior.resource_version_id == asset.resource_version_id
                and formats.normalize_checks(prior.checks) == formats.normalize_checks(asset.checks)
            ):
                continue
            if not asset.validation:
                # 検証の仕組みを入れる前から開いていたDraftで変えた資料は、ここで検証する（版にも写す）
                resource = asset.resource_version
                asset.validation = self._inspect(asset.key, resource, resource.read(), asset.checks, False)
                asset.save(update_fields=["validation"])
            errors = asset.validation.get("errors")
            if not errors:
                continue
            raise InvalidDraft(
                _("資料「%(key)s」に検証の誤りがあります：%(errors)s") % {"key": asset.key, "errors": "／".join(errors)}
            )

    def check_connectors(self, actor) -> None:
        """宣言したConnectorが、公開する人の権限とSkillの範囲とConnectorの今の版の境界に合うか（design.md §4.9.1）"""
        from .connectors import publish_errors  # noqa: PLC0415  skill.connectorsがこのモジュールを使うため

        if errors := publish_errors(self, self.skill, actor):
            raise InvalidDraft("／".join(errors))

    def unevaluated_cases(self) -> list["SkillEvaluationCase"]:
        """公開の前に必須の評価ケースのうち、今のrevisionで通過していないもの（design.md §3.7.1）。
        個人のSkillには必須のケースを求めない。評価の後に変えたケースは、評価し直すまで満たさない"""
        if self.skill.scope == SkillScope.PERSONAL:
            return []
        required = list(SkillEvaluationCase.objects.filter(skill_id=self.skill_id, required=True).order_by("name"))
        if not required:
            return []
        # ケースごとに、今の中身で試した直近の結果で決める（後で失敗したら、前の通過で公開させない）
        results = SkillEvaluationResult.objects.filter(
            evaluation__skill_id=self.skill_id,
            evaluation__draft_uid=self.uid,
            evaluation__draft_revision=self.revision,
            evaluation__status=EvaluationStatus.FINISHED,
            case__in=required,
        ).order_by("-evaluation__created", "-evaluation_id")
        latest: dict[int, bool] = {}
        updated = {c.pk: c.updated for c in required}
        for case_id, case_updated, passed in results.values_list("case_id", "case_updated", "passed"):
            if case_updated == updated[case_id]:
                latest.setdefault(case_id, bool(passed))
        return [c for c in required if not latest.get(c.pk)]

    def check_evaluated(self) -> None:
        if cases := self.unevaluated_cases():
            names = [c.name for c in cases]
            raise EvaluationRequired(
                _("公開の前に必須の評価ケースが、今のDraftで通過していません：%(names)s") % {"names": "、".join(names)},
                names,
            )


class SkillDraftAsset(models.Model):
    """Draftの資料。公開するとSkillAssetBindingに写す"""

    draft = models.ForeignKey(SkillDraft, on_delete=models.CASCADE, related_name="assets", verbose_name=_("Draft"))
    key = models.CharField(_("資料のkey"), max_length=63)
    role = models.PositiveSmallIntegerField(_("用途"), choices=SkillAssetRole.choices)
    description = models.CharField(_("説明"), max_length=500, blank=True)
    resource_version = models.ForeignKey(
        ResourceVersion, on_delete=models.PROTECT, related_name="+", verbose_name=_("資料の版")
    )
    # 検証の条件は公開の権限を持つ人だけが変える。検証の結果（formats.inspect()）は、原本か条件を変えるたびに取り直す。
    # 差分は、Draftの元の版の同じkeyの資料とのもの
    checks = models.JSONField(_("検証の条件"), default=dict, blank=True)
    validation = models.JSONField(_("検証の結果"), default=dict, blank=True)

    class Meta:
        verbose_name = _("SkillのDraftの資料")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["draft", "key"], name="uniq_skill_draft_asset_key")]

    def __str__(self):
        return f"{self.draft} {self.key}"


class SkillChange(models.Model):
    """Skillの公開・切戻し・停止の記録。作成後に変更・削除しない（design.md §3.7）"""

    uid = NanoidField(unique=True)
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT, related_name="changes", verbose_name=_("Skill"))
    kind = models.PositiveSmallIntegerField(_("種別"), choices=SkillChangeKind.choices)
    # 操作の後の公開中の版と、その前の版
    version = models.ForeignKey(
        SkillVersion, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("版")
    )
    previous_version = models.ForeignKey(
        SkillVersion, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("前の版")
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("操作者")
    )
    reason = models.CharField(_("理由"), max_length=500, blank=True)
    # 変わった項目と資料（summarize_changes()）
    summary = models.JSONField(_("変更点"), default=dict, blank=True)
    # 公開したDraftのID（Draftは公開で消える）。同じkeyの再送が同じDraftの公開かを確かめる
    draft_uid = models.CharField(_("公開したDraft"), max_length=22, blank=True)
    # 会話から変更したときの、Skillの編集のRun（design.md §3.7）
    run = models.ForeignKey(
        "run.Run", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("編集のRun")
    )
    # 画面・エージェントが操作ごとに付けるkey。同じkeyの再送では、前の結果を返す
    operation_key = models.CharField(_("operation key"), max_length=100, blank=True)

    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("Skillの変更")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["skill", "operation_key"], condition=~Q(operation_key=""), name="uniq_skill_change_operation"
            ),
        ]

    def __str__(self):
        return f"{self.skill} {self.get_kind_display()}"


def _asset_map(assets) -> dict[str, tuple[int, str, int, dict[str, Any]]]:
    return {a.key: (a.role, a.description, a.resource_version_id, formats.normalize_checks(a.checks)) for a in assets}


def _rolled_back_validation(asset: "SkillAssetBinding", before: "SkillAssetBinding | None") -> dict[str, Any]:
    """切戻しの版の資料の検証の結果。切戻しの前の版と原本が違えば、その版と比べて差分を取り直す（元の版の差分は、
    元の版の前の版とのものなので写さない）"""
    if before is None or before.resource_version_id == asset.resource_version_id:
        return _without_diff(asset.validation)
    resource = asset.resource_version
    return formats.inspect(
        resource.read(),
        resource.name,
        checks=asset.checks,
        before=(before.resource_version.read(), before.resource_version.name),
    )


def _without_diff(validation: dict[str, Any]) -> dict[str, Any]:
    return {**validation, "diff": None} if validation else {}


def summarize_changes(
    before: SkillVersion | None, content: dict[str, Any], assets: dict[str, tuple[int, str, int, dict[str, Any]]]
) -> dict[str, Any]:
    """前の版から変わった項目と資料のkey（SkillChangeの記録と画面の表示に使う）"""
    # 最初の版は、空のDraftからの変更として数える（空のToolや未指定のSchemaを変わった項目にしない）
    before_content = before.content() if before else SkillDraft().content()
    before_assets = _asset_map(before.assets.all()) if before else {}
    fields = [name for name in CONTENT_FIELDS if before_content.get(name) != content[name]]
    replaced = sorted(k for k in before_assets.keys() & assets.keys() if before_assets[k][2] != assets[k][2])
    changed = sorted(
        k
        for k in before_assets.keys() & assets.keys()
        if before_assets[k][2] == assets[k][2] and before_assets[k] != assets[k]
    )
    return {
        "fields": fields,
        "assets_added": sorted(assets.keys() - before_assets.keys()),
        "assets_removed": sorted(before_assets.keys() - assets.keys()),
        "assets_replaced": replaced,
        "assets_changed": changed,
    }


class EvaluationStatus(models.IntegerChoices):
    RUNNING = 1, _("評価中")
    FINISHED = 10, _("完了")
    CANCELLED = 30, _("停止")


class EvaluationVerdict(models.IntegerChoices):
    """LLMの判定。公開中の版と比べたDraft（design.md §3.7.1）"""

    BETTER = 1, _("良くなった")
    SAME = 2, _("同じ")
    WORSE = 3, _("悪くなった")


class EvaluationResultState(models.IntegerChoices):
    RUNNING = 1, _("実行中")
    JUDGING = 2, _("判定中")
    DONE = 10, _("完了")


# 1つのSkillの評価ケースの数と、1回の評価で動かすケースの数の上限（design.md §3.7.1）
MAX_EVALUATION_CASES = 20
MAX_CASES_PER_EVALUATION = 10


class SkillEvaluationCase(models.Model):
    """評価ケース（design.md §3.7.1）。Skillごとに持ち、版をまたいで使う。

    `updated` は中身を変えたときだけ変わる。評価の結果はそのときの値を持ち、後で変えたケースは評価し直すまで
    公開の条件を満たさない
    """

    uid = NanoidField(unique=True)
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="evaluation_cases", verbose_name=_("Skill"))
    name = models.CharField(_("名前"), max_length=100)
    prompt = models.TextField(_("依頼の文"))
    files = models.ManyToManyField(ResourceVersion, blank=True, related_name="+", verbose_name=_("添付"))
    # LLMの判定に使う、確認する観点
    criteria = models.TextField(_("確認する観点"), blank=True)
    # 業務結果（submit_result の値）のうち、一致を確かめる項目
    expected_result = models.JSONField(_("期待する業務結果"), null=True, blank=True)
    # 部門・全社のSkillで、公開の前に今のDraftで通過していることを求める。付け外しと変更は公開の権限を持つ人だけ
    required = models.BooleanField(_("公開の前に必須"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("更新者")
    )

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Skillの評価ケース")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["skill", "name"], name="uniq_skill_evaluation_case_name")]

    def __str__(self):
        return f"{self.skill} {self.name}"


class SkillEvaluation(SkillContent):
    """Draftの評価の1回（design.md §3.7.1）。始めた時点のDraftの中身と資料の版を写して固定する。

    ケースごとに、公開中の版（base_version）とこの中身で評価のRunを動かし、SkillEvaluationResultで比べる
    """

    uid = NanoidField(unique=True)
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT, related_name="evaluations", verbose_name=_("Skill"))
    draft_uid = models.CharField(_("評価したDraft"), max_length=22)
    draft_revision = models.PositiveIntegerField(_("Draftのrevision"))
    # 比べた公開中の版。最初の公開の前はNone（Draftだけを動かす）
    base_version = models.ForeignKey(
        SkillVersion, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("公開中の版")
    )
    model_version = models.ForeignKey(
        "llm.ModelProfileVersion", on_delete=models.PROTECT, related_name="+", verbose_name=_("モデル設定の版")
    )
    effort = models.CharField(_("effort"), max_length=50, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("依頼者")
    )
    # test_skill_draft で始めたときの、Skillの編集のRunと呼出し。終わったら結果を返して再開する
    run = models.ForeignKey(
        "run.Run", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("編集のRun")
    )
    tool_call_id = models.CharField(_("Tool呼出しのID"), max_length=200, blank=True)
    status = models.PositiveSmallIntegerField(
        _("状態"), choices=EvaluationStatus.choices, default=EvaluationStatus.RUNNING
    )

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    finished = models.DateTimeField(_("終了日時"), null=True, blank=True)

    class Meta:
        verbose_name = _("Skillの評価")
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.uid

    @property
    def number(self) -> str:
        """Skillの本文（harness/skills.render()）で版の代わりに示すもの"""
        return "draft"


class SkillEvaluationAsset(models.Model):
    """評価に写したDraftの資料"""

    evaluation = models.ForeignKey(
        SkillEvaluation, on_delete=models.CASCADE, related_name="assets", verbose_name=_("評価")
    )
    key = models.CharField(_("資料のkey"), max_length=63)
    role = models.PositiveSmallIntegerField(_("用途"), choices=SkillAssetRole.choices)
    description = models.CharField(_("説明"), max_length=500, blank=True)
    resource_version = models.ForeignKey(
        ResourceVersion, on_delete=models.PROTECT, related_name="+", verbose_name=_("資料の版")
    )

    class Meta:
        verbose_name = _("Skillの評価の資料")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["evaluation", "key"], name="uniq_skill_evaluation_asset_key")]

    def __str__(self):
        return f"{self.evaluation} {self.key}"


class SkillEvaluationResult(models.Model):
    """評価の中の1つのケース。公開中の版とDraftのRun、機械的な検証、LLMの判定を持つ（design.md §3.7.1）。

    ケースの中身は評価を始めた時点のものを写す（後でケースを変えても、この評価が何を確かめたかを残す）
    """

    evaluation = models.ForeignKey(
        SkillEvaluation, on_delete=models.CASCADE, related_name="results", verbose_name=_("評価")
    )
    case = models.ForeignKey(
        SkillEvaluationCase,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="results",
        verbose_name=_("評価ケース"),
    )
    order = models.PositiveSmallIntegerField(_("順番"))
    case_name = models.CharField(_("ケースの名前"), max_length=100)
    required = models.BooleanField(_("公開の前に必須"), default=False)
    # {"prompt", "criteria", "expected_result", "files": [{"id", "name"}]}
    case_data = models.JSONField(_("ケースの中身"), default=dict)
    case_updated = models.DateTimeField(_("ケースの更新日時"))
    base_run = models.ForeignKey(
        "run.Run", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("公開中の版のRun")
    )
    draft_run = models.ForeignKey(
        "run.Run", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("DraftのRun")
    )
    state = models.PositiveSmallIntegerField(
        _("状態"), choices=EvaluationResultState.choices, default=EvaluationResultState.RUNNING
    )
    # 機械的な検証（conversation/evaluation.check_run()）。Draftの側がすべて通れば passed
    base_check = models.JSONField(_("公開中の版の検証"), default=dict, blank=True)
    draft_check = models.JSONField(_("Draftの検証"), default=dict, blank=True)
    passed = models.BooleanField(_("通過"), null=True, blank=True)
    verdict = models.PositiveSmallIntegerField(_("LLMの判定"), choices=EvaluationVerdict.choices, null=True, blank=True)
    judge_reason = models.TextField(_("判定の理由"), blank=True)
    # 判定しなかった・できなかった理由（公開中の版がない、モデルがToolに対応しない、判定の失敗）
    judge_note = models.CharField(_("判定の補足"), max_length=500, blank=True)
    judging_started = models.DateTimeField(_("判定の開始日時"), null=True, blank=True)

    class Meta:
        verbose_name = _("Skillの評価の結果")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["evaluation", "order"], name="uniq_skill_evaluation_order")]

    def __str__(self):
        return f"{self.evaluation} {self.case_name}"


auditlog.register(Skill, exclude_fields=["updated"])
auditlog.register(SkillVersion)
auditlog.register(SkillAssetBinding)
auditlog.register(SkillChange)
auditlog.register(SkillEvaluationCase, exclude_fields=["updated"])
