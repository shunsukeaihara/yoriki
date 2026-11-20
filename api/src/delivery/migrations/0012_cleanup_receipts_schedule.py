"""Runに結び付かなかった対話の受信の記録の清掃を1日1回行うQ2のSchedule"""

from django.db import migrations

FUNC = "delivery.tasks.cleanup_receipts"


def create_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.update_or_create(
        func=FUNC, defaults={"name": "cleanup_channel_receipts", "schedule_type": "D", "repeats": -1}
    )


def delete_schedule(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(func=FUNC).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("delivery", "0011_thread_url"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
    ]

    operations = [migrations.RunPython(create_schedule, delete_schedule)]
