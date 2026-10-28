"""配信の送り直し（送れずに残った配信、再試行の時刻、持ち時間の切れた配信）を1分ごとに行うQ2のSchedule"""

from django.db import migrations

FUNC = "delivery.tasks.reconcile_deliveries"


def create_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.update_or_create(
        func=FUNC, defaults={"name": "reconcile_deliveries", "schedule_type": "I", "minutes": 1, "repeats": -1}
    )


def delete_schedule(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(func=FUNC).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("delivery", "0001_initial"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
    ]

    operations = [migrations.RunPython(create_schedule, delete_schedule)]
