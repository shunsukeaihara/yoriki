from django.contrib.auth.management import create_permissions
from django.db import migrations

# 入口の段階で作る初期のGroup（authorization.md §11）。最初に一度だけ作り、後から名前・中身を変えても、消しても
# 作り直さない。出口（配信）のModelのpermissionは、配信の段階のマイグレーションで一度だけ足す
NEW_GROUPS = {
    "連携の管理者": [
        "task.view_triggerbinding",
        "task.add_triggerbinding",
        "task.change_triggerbinding",
        "task.delete_triggerbinding",
    ],
}


def create_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    # permissionは通常 migrate の最後（post_migrate）に作られるので、Groupに入れる前にここで作る
    app_config = apps.get_app_config("task")
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None

    for name, perms in NEW_GROUPS.items():
        group, created = Group.objects.get_or_create(name=name)
        if created:
            group.permissions.add(
                *[
                    Permission.objects.get(content_type__app_label=app_label, codename=codename)
                    for app_label, codename in (perm.split(".", 1) for perm in perms)
                ]
            )


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("task", "0003_trigger_bindings"),
    ]

    operations = [
        migrations.RunPython(create_groups, migrations.RunPython.noop),
    ]
