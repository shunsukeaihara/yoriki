from django.contrib.auth.management import create_permissions
from django.db import migrations

# 初期のGroup「予算の管理者」を一度だけ作る（authorization.md §11）。Groupを消していても作り直さない
# （このマイグレーションが一度動いた後は動かない）
GROUP = "予算の管理者"
PERMS = ["usage.view_budget", "usage.change_budget"]


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    # permissionは通常 migrate の最後（post_migrate）に作られるので、Groupに入れる前にここで作る
    app_config = apps.get_app_config("usage")
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None

    group, _created = Group.objects.get_or_create(name=GROUP)
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
        ("usage", "0005_budget_required"),
    ]

    operations = [
        migrations.RunPython(create_group, migrations.RunPython.noop),
    ]
