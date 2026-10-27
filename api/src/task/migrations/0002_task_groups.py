from django.contrib.auth.management import create_permissions
from django.db import migrations

# タスクの段階で足す初期のGroupとpermission（authorization.md §11）。最初に一度だけ作り、後から名前・中身を変えても、
# 消しても作り直さない。既にあるGroup（全件管理者・システム管理者）には、このマイグレーションで一度だけ足す
NEW_GROUPS = {
    "部門のタスク管理者": ["task.can_manage_department_tasks"],
    "全社のタスク管理者": ["task.can_manage_company_tasks"],
}
ADDED = {
    "全件管理者": ["task.can_manage_department_tasks", "task.can_manage_company_tasks"],
    "システム管理者": ["task.can_manage_system_tasks"],
}


def create_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    # permissionは通常 migrate の最後（post_migrate）に作られるので、Groupに入れる前にここで作る
    app_config = apps.get_app_config("task")
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None

    def permissions(perms):
        return [
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
            for app_label, codename in (perm.split(".", 1) for perm in perms)
        ]

    for name, perms in NEW_GROUPS.items():
        group, created = Group.objects.get_or_create(name=name)
        if created:
            group.permissions.add(*permissions(perms))
    for name, perms in ADDED.items():
        group = Group.objects.filter(name=name).first()
        if group is not None:
            group.permissions.add(*permissions(perms))


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("user", "0007_initial_groups"),
        ("task", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_groups, migrations.RunPython.noop),
    ]
