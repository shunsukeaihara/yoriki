"""Runの復旧（leaseの切れたRun、回答期限、送れずに残った通知）を1分ごとに行うQ2のSchedule"""

from django.db import migrations

FUNC = "run.tasks.reconcile_runs"


def create_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.update_or_create(
        func=FUNC, defaults={"name": "reconcile_runs", "schedule_type": "I", "minutes": 1, "repeats": -1}
    )


def delete_schedule(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(func=FUNC).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("run", "0001_initial"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
    ]

    operations = [migrations.RunPython(create_schedule, delete_schedule)]
