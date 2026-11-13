"""ワークスペースの清掃（保存期限を過ぎたsnapshot、消し残したsnapshot）を1日に1回行うQ2のSchedule"""

from django.db import migrations

FUNC = "run.tasks.cleanup_workspaces"


def create_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.update_or_create(
        func=FUNC, defaults={"name": "cleanup_workspaces", "schedule_type": "D", "repeats": -1}
    )


def delete_schedule(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(func=FUNC).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("run", "0020_run_cost_limit"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
    ]

    operations = [migrations.RunPython(create_schedule, delete_schedule)]
