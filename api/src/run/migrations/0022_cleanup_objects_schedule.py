"""記録のない保存先の実体（確定する前に落ちたsnapshot等）の清掃を1時間ごとに行うQ2のSchedule"""

from django.db import migrations

FUNC = "run.tasks.cleanup_objects"


def create_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.update_or_create(
        func=FUNC, defaults={"name": "cleanup_objects", "schedule_type": "H", "repeats": -1}
    )


def delete_schedule(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(func=FUNC).delete()


class Migration(migrations.Migration):
    dependencies = [("run", "0021_cleanup_schedule")]

    operations = [migrations.RunPython(create_schedule, delete_schedule)]
