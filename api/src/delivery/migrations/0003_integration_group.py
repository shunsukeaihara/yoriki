from django.contrib.auth.management import create_permissions
from django.db import migrations

# 出口のModelのpermissionを、入口の段階で作った初期のGroup「連携の管理者」に一度だけ足す（authorization.md §11）。
# Groupを消していたら作り直さない
GROUP = "連携の管理者"
PERMS = [
    "delivery.view_destination",
    "delivery.add_destination",
    "delivery.change_destination",
    "delivery.delete_destination",
]


def add_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    # permissionは通常 migrate の最後（post_migrate）に作られるので、Groupに入れる前にここで作る
    app_config = apps.get_app_config("delivery")
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None

    group = Group.objects.filter(name=GROUP).first()
    if group is None:
        return
    group.permissions.add(
        *[
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
            for app_label, codename in (perm.split(".", 1) for perm in PERMS)
        ]
    )


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("delivery", "0002_reconcile_schedule"),
        ("task", "0004_integration_group"),
    ]

    operations = [
        migrations.RunPython(add_permissions, migrations.RunPython.noop),
    ]
