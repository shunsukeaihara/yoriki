"""役割（Group）の初期定義。

役割は、permissionの組に名前を付けたGroup。管理画面でGroupをユーザーに割り当てる。
APIや画面では役割の名前で判定せず、個々のpermissionで判定する（`utils.permissions.require_permission`）。

`migrate` のたびに、ここに書いたpermissionを同じ名前のGroupへ追加する。管理画面でGroupに足した
permissionは消さないので、導入先で役割の中身を調整してよい。
"""

from django.contrib.auth.models import Group, Permission

from utils.permissions import AppPermission, to_perm_str

# キーはGroupの名前（DBに保存するデータなので翻訳しない）。値は `AppPermission` か "<app_label>.<codename>"
ROLES: dict[str, list[AppPermission | str]] = {
    "業務管理者": [
        # 例：AppPermission.MANAGE_DESTINATIONS
    ],
    "Plugin公開者": [
        # 例：AppPermission.PUBLISH_COMPANY_SKILLS
    ],
}


def sync_role_groups(**kwargs) -> None:
    """役割のGroupを作り、定義したpermissionを付ける（post_migrateで呼ぶ）"""
    for name, perms in ROLES.items():
        group, _created = Group.objects.get_or_create(name=name)
        for perm in perms:
            app_label, codename = to_perm_str(perm).split(".", 1)
            group.permissions.add(Permission.objects.get(content_type__app_label=app_label, codename=codename))
