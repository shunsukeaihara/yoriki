"""Skill（design.md §3）。

Skillは可変の入れ物で、公開範囲（個人・部門・全社）と、公開中の版（current_version）を持つ。中身（説明、手順、
使ってよいTool、必要な能力、入出力のSchema、資料）は、編集中のSkillDraftと、公開した不変のSkillVersionが持つ。
DraftはSkillごとに1つで、revisionで同時の編集を検出し、公開すると閉じる（design.md §3.7）。
公開・切戻し・停止はSkillChangeに記録し、operation keyで再送による重複を防ぐ。

誰が使え、誰が編集・公開できるかは公開範囲で決まる（design.md §3.6。`Skill.access_of()`）。
Skillの本文は権限の根拠にしない。実行するときのToolの許可は、利用者・Skill・Runの範囲をサーバーで照合して決める。
"""

from enum import IntEnum
from typing import IO, Any

from auditlog.registry import auditlog
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models, transaction
from django.db.models import Max, Q
from django.utils.translation import gettext_lazy as _

from resources.models import Resource, ResourceVersion
from user.models import Department, DepartmentRole
from utils.fields import NanoidField
from utils.permissions import AppPermission

from .errors import DraftExists, InvalidDraft, VersionConflict


class SkillScope(models.IntegerChoices):
    PERSONAL = 1, _("個人")
    DEPARTMENT = 2, _("部門")
    COMPANY = 3, _("全社")


class SkillAccess(IntEnum):
    """利用者がSkillに対してできること。上は下を含む（design.md §3.6）"""

    NONE = 0
    USE = 1  # 使う・見る
    EDIT = 2  # Draftを変更する（本文・説明・Schema・資料）
    PUBLISH = 3  # 公開・切戻し・停止・削除。Toolを変える


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


# Draftと版が持つ中身。版を作るときにDraftからそのまま写す
CONTENT_FIELDS = ("description", "instructions", "tools", "requires", "input_schema", "output_schema")
DEPARTMENT_ACCESS = {
    DepartmentRole.MEMBER: SkillAccess.USE,
    DepartmentRole.EDITOR: SkillAccess.EDIT,
    DepartmentRole.PUBLISHER: SkillAccess.PUBLISH,
}


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

    class Meta:
        abstract = True

    def content(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in CONTENT_FIELDS}


class SkillQuerySet(models.QuerySet):
    def visible_to(self, user) -> "SkillQuerySet":
        """利用者が使える（見える）Skill。個人は本人、部門は所属する人、全社は全社員（design.md §3.6）"""
        if not user.is_active:
            return self.none()
        departments = Department.objects.filter(deleted=False)
        if not user.is_superuser:
            departments = departments.filter(memberships__user=user)
        return self.filter(
            Q(scope=SkillScope.PERSONAL, owner=user)
            | Q(scope=SkillScope.DEPARTMENT, department__in=departments)
            | Q(scope=SkillScope.COMPANY),
            deleted=False,
        )


class Skill(models.Model):
    uid = NanoidField(unique=True)
    scope = models.PositiveSmallIntegerField(_("公開範囲"), choices=SkillScope.choices)
    # 個人の範囲では持ち主、部門の範囲では部門。範囲は作成時に決めて変えない（design.md §3.6）
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
        permissions = [AppPermission.EDIT_COMPANY_SKILLS.meta, AppPermission.PUBLISH_COMPANY_SKILLS.meta]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(scope=SkillScope.PERSONAL, owner__isnull=False, department__isnull=True)
                    | Q(scope=SkillScope.DEPARTMENT, owner__isnull=True, department__isnull=False)
                    | Q(scope=SkillScope.COMPANY, owner__isnull=True, department__isnull=True)
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
        ]

    def __str__(self):
        return self.name

    def access_of(self, user) -> SkillAccess:
        """利用者がこのSkillに対してできること（design.md §3.6）"""
        if not user.is_active or self.deleted:
            return SkillAccess.NONE
        if self.scope == SkillScope.PERSONAL:
            return SkillAccess.PUBLISH if self.owner_id == user.pk else SkillAccess.NONE
        if self.scope == SkillScope.DEPARTMENT:
            assert self.department is not None
            role = self.department.role_of(user)
            return DEPARTMENT_ACCESS[role] if role is not None else SkillAccess.NONE
        if user.has_perm(AppPermission.PUBLISH_COMPANY_SKILLS.perm):
            return SkillAccess.PUBLISH
        if user.has_perm(AppPermission.EDIT_COMPANY_SKILLS.perm):
            return SkillAccess.EDIT
        return SkillAccess.USE

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

    def _recorded(self, operation_key: str) -> "SkillChange | None":
        """同じoperation keyの操作が済んでいれば、その記録（再送で版を重ねて作らない。design.md §3.7）"""
        if not operation_key:
            return None
        return SkillChange.objects.filter(skill=self, operation_key=operation_key).select_related("version").first()

    def _next_number(self) -> int:
        return (self.versions.aggregate(n=Max("number"))["n"] or 0) + 1

    @transaction.atomic
    def publish_draft(
        self, draft_uid: str, *, actor, expected_revision: int, change_reason: str = "", operation_key: str = ""
    ) -> "SkillVersion":
        """Draftから新しい版を作り、公開中の版にする。Draftは閉じる（消す）。

        Draftを開いた後に別の版が公開されていたら（Draftの元の版が公開中の版でなければ）、または編集を始めた後に
        Draftが変わっていたら VersionConflict。本文と資料の組合せは、1つのtransactionでまとめて確定する
        （design.md §3.10）
        """
        current = self._lock()
        if done := current._recorded(operation_key):
            assert done.version is not None
            return done.version
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
            )
            for a in draft.assets.all()
        )
        summary = summarize_changes(previous, draft.content(), _asset_map(draft.assets.all()))
        current._make_current(version)
        SkillChange.objects.create(
            skill=current,
            kind=SkillChangeKind.PUBLISH,
            version=version,
            previous_version=previous,
            actor=actor,
            reason=change_reason,
            summary=summary,
            operation_key=operation_key,
        )
        draft.delete()
        self.current_version = version
        return version

    @transaction.atomic
    def rollback(
        self, number: int, *, actor, expected_version: int, change_reason: str = "", operation_key: str = ""
    ) -> "SkillVersion":
        """選んだ過去の版と同じ中身・資料の版を新しく作り、公開中の版にする（履歴は消さない。design.md §3.7）"""
        current = self._lock()
        if done := current._recorded(operation_key):
            assert done.version is not None
            return done.version
        if current.version_number != expected_version:
            raise VersionConflict(f"expected version={expected_version} latest={current.version_number}")
        source = current.versions.prefetch_related("assets__resource_version").filter(number=number).first()
        if source is None:
            raise InvalidDraft(_("切り戻す版がありません"))
        if source.pk == current.current_version_id:
            raise InvalidDraft(_("公開中の版には切り戻せません"))

        previous = current.current_version
        version = SkillVersion.objects.create(
            skill=current,
            number=current._next_number(),
            change_reason=change_reason,
            created_by=actor,
            rolled_back_from=source,
            **source.content(),
        )
        SkillAssetBinding.objects.bulk_create(
            SkillAssetBinding(
                version=version,
                key=a.key,
                role=a.role,
                description=a.description,
                resource_version=a.resource_version,
            )
            for a in source.assets.all()
        )
        summary = summarize_changes(previous, source.content(), _asset_map(source.assets.all()))
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
                )
                for a in base.assets.all()
            )
        return draft

    def _lock(self, expected_revision: int | None = None) -> "SkillDraft":
        current = SkillDraft.objects.select_for_update().get(pk=self.pk)
        if expected_revision is not None and current.revision != expected_revision:
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
        self, *, key: str, role: SkillAssetRole, description: str, src: IO[bytes], filename: str, actor
    ) -> "SkillDraftAsset":
        """アップロードしたファイルを新しい資料としてDraftに加える。原本のbytesをそのまま保存する"""
        current = self._lock()
        if current.assets.filter(key=key).exists():
            raise InvalidDraft(_("同じkeyの資料があります"))
        resource_version = Resource.create_from_upload(src, filename, actor=actor)
        asset = SkillDraftAsset.objects.create(
            draft=current, key=key, role=role, description=description, resource_version=resource_version
        )
        self._bump(current, actor, [])
        return asset

    @transaction.atomic
    def change_asset(
        self,
        asset: "SkillDraftAsset",
        *,
        actor,
        role: SkillAssetRole | None = None,
        description: str | None = None,
        src: IO[bytes] | None = None,
        filename: str = "",
    ) -> "SkillDraftAsset":
        """資料の用途・説明を変える。ファイルを送れば、同じ資料の新しい版として置き換える（keyと資料のIDは保つ）"""
        current = self._lock()
        asset = SkillDraftAsset.objects.select_related("resource_version__resource").get(pk=asset.pk, draft=current)
        if role is not None:
            asset.role = role
        if description is not None:
            asset.description = description
        if src is not None:
            asset.resource_version = asset.resource_version.resource.add_version(
                src, filename or asset.resource_version.name, actor=actor
            )
        asset.save()
        self._bump(current, actor, [])
        return asset

    @transaction.atomic
    def remove_asset(self, asset: "SkillDraftAsset", *, actor) -> None:
        current = self._lock()
        SkillDraftAsset.objects.filter(pk=asset.pk, draft=current).delete()
        self._bump(current, actor, [])

    def check_publishable(self) -> None:
        if not self.description.strip():
            raise InvalidDraft(_("説明を入力してください（エージェントと社員がSkillを選ぶのに使います）"))
        if not self.instructions.strip():
            raise InvalidDraft(_("手順を入力してください"))


class SkillDraftAsset(models.Model):
    """Draftの資料。公開するとSkillAssetBindingに写す"""

    draft = models.ForeignKey(SkillDraft, on_delete=models.CASCADE, related_name="assets", verbose_name=_("Draft"))
    key = models.CharField(_("資料のkey"), max_length=63)
    role = models.PositiveSmallIntegerField(_("用途"), choices=SkillAssetRole.choices)
    description = models.CharField(_("説明"), max_length=500, blank=True)
    resource_version = models.ForeignKey(
        ResourceVersion, on_delete=models.PROTECT, related_name="+", verbose_name=_("資料の版")
    )

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


def _asset_map(assets) -> dict[str, tuple[int, str, int]]:
    return {a.key: (a.role, a.description, a.resource_version_id) for a in assets}


def summarize_changes(
    before: SkillVersion | None, content: dict[str, Any], assets: dict[str, tuple[int, str, int]]
) -> dict[str, Any]:
    """前の版から変わった項目と資料のkey（SkillChangeの記録と画面の表示に使う）"""
    before_content = before.content() if before else {}
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


auditlog.register(Skill, exclude_fields=["updated"])
auditlog.register(SkillVersion)
auditlog.register(SkillAssetBinding)
auditlog.register(SkillChange)
