"""APIのpermission（backend-conventions.md §6.3）。

権限は「所属」と「permission」の2軸で判定する（authorization.md）。Yorikiで定義するpermissionは `AppPermission` に
まとめ、Modelの `Meta.permissions` で作り、Group（permissionの集まり）か個別にユーザーへ付ける。
Groupの名前やUserのフラグでは判定しない。基盤管理者（`is_superuser`）は、Djangoの `has_perm()` で
すべてのpermissionを持つ。
"""

from enum import Enum
from typing import TYPE_CHECKING

from django.utils.translation import gettext_lazy as _
from rest_framework import authentication
from rest_framework.permissions import BasePermission, IsAuthenticated, OperandHolder

if TYPE_CHECKING:
    from django_stubs_ext import StrPromise


class AppPermission(Enum):
    """Yorikiで定義するpermission。値は（app_label、codename、表示名）。

    codenameは `can_<動詞>_<対象>` にする。追加したら、対象アプリのModelの `Meta.permissions` に
    `AppPermission.XXX.meta` を書く。初期のGroupに入れるものは、`user` のデータのマイグレーションで割り当てる。

    例：PUBLISH_COMPANY_SKILLS = "skill", "can_publish_company_skills", _("全社向けのSkillを公開できる")
    """

    # 使える部門・管理できる部門を、すべての部門に広げる（authorization.md §4.1）
    USE_ALL_DEPARTMENTS = "user", "can_use_all_departments", _("すべての部門のものを使える")
    MANAGE_ALL_DEPARTMENTS = "user", "can_manage_all_departments", _("すべての部門の業務を管理できる")
    # 範囲ごとのSkillの編集・公開（authorization.md §5.1）。部門の範囲は、管理できる部門のSkillだけに効く
    EDIT_DEPARTMENT_SKILLS = "skill", "can_edit_department_skills", _("部門のSkillを編集できる")
    PUBLISH_DEPARTMENT_SKILLS = "skill", "can_publish_department_skills", _("部門のSkillを公開できる")
    EDIT_COMPANY_SKILLS = "skill", "can_edit_company_skills", _("全社のSkillを編集できる")
    PUBLISH_COMPANY_SKILLS = "skill", "can_publish_company_skills", _("全社のSkillを公開できる")
    EDIT_SYSTEM_SKILLS = "skill", "can_edit_system_skills", _("システムのSkillを編集できる")
    PUBLISH_SYSTEM_SKILLS = "skill", "can_publish_system_skills", _("システムのSkillを公開できる")
    # 範囲ごとのタスクの管理（authorization.md §5.3）。部門の範囲は、管理できる部門のタスクだけに効く
    MANAGE_DEPARTMENT_TASKS = "task", "can_manage_department_tasks", _("部門のタスクを管理できる")
    MANAGE_COMPANY_TASKS = "task", "can_manage_company_tasks", _("全社のタスクを管理できる")
    MANAGE_SYSTEM_TASKS = "task", "can_manage_system_tasks", _("システムのタスクを管理できる")

    def __init__(self, app_label: str, codename: str, label: "str | StrPromise"):
        self.app_label = app_label
        self.codename = codename
        self.label = label

    @property
    def perm(self) -> str:
        """`user.has_perm()` に渡す "<app_label>.<codename>" """
        return f"{self.app_label}.{self.codename}"

    @property
    def meta(self) -> tuple[str, "str | StrPromise"]:
        """Modelの `Meta.permissions` に書く（codename、表示名）"""
        return (self.codename, self.label)


def to_perm_str(perm: AppPermission | str) -> str:
    """`AppPermission` または "<app_label>.<codename>" を、`user.has_perm()` に渡す文字列にする"""
    return perm.perm if isinstance(perm, AppPermission) else perm


class _AppUser(BasePermission):
    def has_permission(self, request, view) -> bool:
        return request.user.is_active


class DenyAll(BasePermission):
    """どの要求も通さない（基盤管理者も）。`permission_classes` の書き忘れを閉じる既定（DEFAULT_PERMISSION_CLASSES）"""

    def has_permission(self, request, view) -> bool:
        return False


AppUser = IsAuthenticated & _AppUser  # ログインしている社員


def require_permission(perm: AppPermission | str) -> OperandHolder:
    """Djangoのpermissionを持つログインユーザーだけを許可する。

    Yorikiで定義したものは `AppPermission`、Django標準のもの（`user.view_user` 等）は "<app_label>.<codename>" で渡す。
    """
    perm_str = to_perm_str(perm)

    class _HasPermission(BasePermission):
        def has_permission(self, request, view) -> bool:
            return request.user.has_perm(perm_str)

    return IsAuthenticated & _HasPermission


class _ModelPermission(BasePermission):
    """ViewSetの操作を、対象のModelのDjango標準のpermission（view・add・change・delete）で判定する。

    対象のModelは `view.permission_model`（なければ `view.queryset.model`）。`@action` は
    `view.action_permissions = {"check": "view"}` のように、どの種類のpermissionで判定するかを書く。
    書いていない操作は断る（書き忘れたactionを開けない）。所属は問わない（基盤の管理。authorization.md §3）。
    Djangoの管理画面と同じく、`change_*` を持つ人は `view` の操作もできる
    """

    ACTIONS = {
        "list": "view",
        "retrieve": "view",
        "create": "add",
        "update": "change",
        "partial_update": "change",
        "destroy": "delete",
    }

    def has_permission(self, request, view) -> bool:
        kind = {**self.ACTIONS, **getattr(view, "action_permissions", {})}.get(getattr(view, "action", None))
        if kind is None:
            return False
        model = getattr(view, "permission_model", None) or view.queryset.model
        kinds = ("view", "change") if kind == "view" else (kind,)
        return any(request.user.has_perm(f"{model._meta.app_label}.{k}_{model._meta.model_name}") for k in kinds)


# 操作ごとに、対象のModelの標準のpermission（view・add・change・delete）を持つログインユーザーだけを許可する
ModelPermissions = IsAuthenticated & _ModelPermission


class CsrfProtected(BasePermission):
    """未ログインの要求でもCSRFを確かめる（ログイン、招待の受け入れ等）。

    DRFは、ログインしていない要求ではCSRFを確かめないので、ログインCSRF（攻撃者のアカウントでログインさせる）を防げない
    """

    def has_permission(self, request, view) -> bool:
        authentication.SessionAuthentication().enforce_csrf(request)
        return True
