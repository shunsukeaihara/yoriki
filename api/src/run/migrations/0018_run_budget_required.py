import django.db.models.deletion
from django.db import migrations, models

SYSTEM = 2


def fill(apps, schema_editor):
    """予算より前のRunは、システムの枠に計上したことにする"""
    Budget = apps.get_model("usage", "Budget")
    Run = apps.get_model("run", "Run")
    if not Run.objects.filter(budget__isnull=True).exists():
        return
    system, _created = Budget.objects.get_or_create(kind=SYSTEM)
    Run.objects.filter(budget__isnull=True).update(budget=system)


class Migration(migrations.Migration):
    dependencies = [
        ("run", "0017_run_budget"),
    ]

    operations = [
        migrations.RunPython(fill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="run",
            name="budget",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="runs",
                to="usage.budget",
                verbose_name="予算の計上先",
            ),
        ),
    ]
