"""APIのpermission（backend-conventions.md §6.3）。

権限はDjangoのpermissionで判定する。Yorikiで定義するpermissionは `AppPermission` にまとめ、
Modelの `Meta.permissions` で作り、Group（役割。`user/roles.py`）に割り当て、Groupをユーザーに割り当てる。
役割の名前やUserのフラグでは判定しない。基盤管理者（`is_superuser`）はすべてのpermissionを持つ。
"""

from enum import Enum

from django.utils.functional import Promise
from rest_framework.permissions import BasePermission, IsAuthenticated, OperandHolder


class AppPermission(Enum):
    """Yorikiで定義するpermission。値は（app_label、codename、表示名）。

    codenameは `can_<動詞>_<対象>` にする。追加したら、対象アプリのModelの `Meta.permissions` に
    `AppPermission.XXX.meta` を書き、`user/roles.py` で役割に割り当てる。

    例：PUBLISH_COMPANY_SKILLS = "skill", "can_publish_company_skills", _("全社向けのSkillを公開できる")
    """

    def __init__(self, app_label: str, codename: str, label: str | Promise):
        self.app_label = app_label
        self.codename = codename
        self.label = label

    @property
    def perm(self) -> str:
        """`user.has_perm()` に渡す "<app_label>.<codename>" """
        return f"{self.app_label}.{self.codename}"

    @property
    def meta(self) -> tuple[str, str | Promise]:
        """Modelの `Meta.permissions` に書く（codename、表示名）"""
        return (self.codename, self.label)


def to_perm_str(perm: AppPermission | str) -> str:
    """`AppPermission` または "<app_label>.<codename>" を、`user.has_perm()` に渡す文字列にする"""
    return perm.perm if isinstance(perm, AppPermission) else perm


class _AppUser(BasePermission):
    def has_permission(self, request, view) -> bool:
        return request.user.is_active


class _PlatformAdmin(BasePermission):
    def has_permission(self, request, view) -> bool:
        return request.user.is_superuser


AppUser = IsAuthenticated & _AppUser  # ログインしている社員
PlatformAdmin = IsAuthenticated & _PlatformAdmin  # 基盤管理者


def require_permission(perm: AppPermission | str) -> OperandHolder:
    """Djangoのpermissionを持つログインユーザーだけを許可する。

    Yorikiで定義したものは `AppPermission`、Django標準のもの（`user.view_user` 等）は "<app_label>.<codename>" で渡す。
    """
    perm_str = to_perm_str(perm)

    class _HasPermission(BasePermission):
        def has_permission(self, request, view) -> bool:
            return request.user.has_perm(perm_str)

    return IsAuthenticated & _HasPermission
