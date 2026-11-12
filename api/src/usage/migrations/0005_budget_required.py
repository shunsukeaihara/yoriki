import django.db.models.deletion
from django.db import migrations, models

SYSTEM = 2


def fill(apps, schema_editor):
    """予算より前の利用量は、システムの枠に計上したことにする"""
    Budget = apps.get_model("usage", "Budget")
    UsageLedger = apps.get_model("usage", "UsageLedger")
    if not UsageLedger.objects.filter(budget__isnull=True).exists():
        return
    system, _created = Budget.objects.get_or_create(kind=SYSTEM)
    UsageLedger.objects.filter(budget__isnull=True).update(budget=system)


class Migration(migrations.Migration):
    dependencies = [
        ("usage", "0004_budgetreservation_budgetsettings_budget_and_more"),
    ]

    operations = [
        migrations.RunPython(fill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="usageledger",
            name="budget",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="usages", to="usage.budget"
            ),
        ),
    ]
