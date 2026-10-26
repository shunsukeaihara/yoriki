from django.contrib.auth.management import create_permissions
from django.db import migrations

# 初期のGroup（authorization.md §11）。最初に一度だけ作り、後から名前・中身を変えても、消しても作り直さない。
# タスク・入口・出口のpermissionは、それぞれの段階のマイグレーションで足す。Groupの名前はDBのデータなので翻訳しない
CRUD = ("view", "add", "change", "delete")
DEPARTMENT_SKILLS = ["skill.can_edit_department_skills", "skill.can_publish_department_skills"]
COMPANY_SKILLS = ["skill.can_edit_company_skills", "skill.can_publish_company_skills"]
GROUPS = {
    "部門のSkill編集者": ["skill.can_edit_department_skills"],
    "部門のSkill公開者": DEPARTMENT_SKILLS,
    "全社のSkill公開者": COMPANY_SKILLS,
    "全件利用者": ["user.can_use_all_departments"],
    "全件管理者": ["user.can_manage_all_departments", *DEPARTMENT_SKILLS, *COMPANY_SKILLS],
    "Connector管理者": [
        *(f"connector.{action}_connector" for action in CRUD),
        *(f"connector.{action}_databaseconnection" for action in CRUD),
    ],
    "システム管理者": ["skill.can_edit_system_skills", "skill.can_publish_system_skills", "connector.view_connector"],
    "ユーザー管理者": [
        *(f"user.{action}_user" for action in CRUD),
        *(f"user.{action}_department" for action in CRUD),
    ],
}
# 以前の版が migrate のたびに作っていたGroup
OLD_COMPANY_SKILL_GROUP = "Skill公開者"
OLD_EMPTY_GROUP = "業務管理者"


def create_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    # permissionは通常 migrate の最後（post_migrate）に作られるので、Groupに入れる前にここで作る
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None

    old = Group.objects.filter(name=OLD_COMPANY_SKILL_GROUP).first()
    if old is not None and not Group.objects.filter(name="全社のSkill公開者").exists():
        # 割り当てを残したまま、新しい名前にする（中身は全社のSkillの編集・公開で同じ）
        old.name = "全社のSkill公開者"
        old.save(update_fields=["name"])
    Group.objects.filter(name=OLD_EMPTY_GROUP, permissions__isnull=True).delete()

    for name, perms in GROUPS.items():
        group, created = Group.objects.get_or_create(name=name)
        if not created:
            continue
        for perm in perms:
            app_label, codename = perm.split(".", 1)
            group.permissions.add(Permission.objects.get(content_type__app_label=app_label, codename=codename))


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("user", "0006_department_without_roles"),
        ("skill", "0005_system_scope"),
        ("connector", "0003_connector_scope"),
    ]

    operations = [
        migrations.RunPython(create_groups, migrations.RunPython.noop),
    ]
