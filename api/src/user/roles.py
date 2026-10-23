"""役割（Group）の初期定義。

役割は、permissionの組に名前を付けたGroup。管理画面でGroupをユーザーに割り当てる。
APIや画面では役割の名前で判定せず、個々のpermissionで判定する（`utils.permissions.require_permission`）。

`migrate` のたびに、ここに書いたpermissionを同じ名前のGroupへ追加する。管理画面でGroupに足した
permissionは消さないので、導入先で役割の中身を調整してよい。
"""

from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.management import create_contenttypes
from django.db import DEFAULT_DB_ALIAS

from utils.permissions import AppPermission, to_perm_str

# キーはGroupの名前（DBに保存するデータなので翻訳しない）。値は `AppPermission` か "<app_label>.<codename>"
ROLES: dict[str, list[AppPermission | str]] = {
    "業務管理者": [
        # 例：AppPermission.MANAGE_DESTINATIONS
    ],
    # 全社の範囲のSkillを編集・公開する（design.md §3.6）。部門の範囲は部門の所属（Department）で決める
    "Skill公開者": [
        AppPermission.EDIT_COMPANY_SKILLS,
        AppPermission.PUBLISH_COMPANY_SKILLS,
    ],
}


def sync_role_groups(using: str = DEFAULT_DB_ALIAS, **kwargs) -> None:
    """役割のGroupを作り、定義したpermissionを付ける（post_migrateで呼ぶ）"""
    perms = {name: [to_perm_str(p).split(".", 1) for p in perm_list] for name, perm_list in ROLES.items()}

    # post_migrateはアプリごとに順に送られ、permissionもその中で作られる。参照するアプリのpermissionが
    # まだ作られていない場合があるので、アプリの順番に依存しないよう、ここで先に作っておく（既にあれば何もしない）
    for app_label in {app_label for perm_list in perms.values() for app_label, _codename in perm_list}:
        app_config = global_apps.get_app_config(app_label)
        create_contenttypes(app_config, verbosity=0, using=using)
        create_permissions(app_config, verbosity=0, using=using)

    for name, perm_list in perms.items():
        group, _created = Group.objects.using(using).get_or_create(name=name)
        for app_label, codename in perm_list:
            permission = Permission.objects.using(using).get(content_type__app_label=app_label, codename=codename)
            group.permissions.add(permission)
