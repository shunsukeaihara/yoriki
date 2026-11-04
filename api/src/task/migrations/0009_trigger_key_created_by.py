import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

# 入口を作る・変える人を、連携の管理者からタスクを管理できる人へ移す（authorization.md §13）
REMOVED_PERMISSIONS = ["add_triggerbinding", "change_triggerbinding", "delete_triggerbinding"]


def _integration_group(apps):
    return apps.get_model("auth", "Group").objects.filter(name="連携の管理者").first()


def _permissions(apps):
    return apps.get_model("auth", "Permission").objects.filter(
        content_type__app_label="task", codename__in=REMOVED_PERMISSIONS
    )


def remove_permissions(apps, schema_editor):
    if group := _integration_group(apps):
        group.permissions.remove(*_permissions(apps))


def restore_permissions(apps, schema_editor):
    if group := _integration_group(apps):
        group.permissions.add(*_permissions(apps))


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("task", "0008_chat_launchable"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="triggerbinding",
            name="uniq_task_webhook",
        ),
        # 既存の入口は空のまま（誰の手元に鍵があるか分からないものとして、鍵を作り直すまで警告を出す）
        migrations.AddField(
            model_name="triggerbinding",
            name="key_created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
                verbose_name="鍵を作った人",
            ),
        ),
        migrations.RunPython(remove_permissions, restore_permissions),
    ]
