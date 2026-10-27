import django.db.models.deletion
from django.db import migrations, models

DEPARTMENT, COMPANY = 2, 3


def to_scope(apps, schema_editor):
    """使える部門（多対多）を範囲と1つの部門にする（authorization.md §12）。

    部門の指定がなければ全社、1つなら部門の範囲。複数の部門を持つものは合う形がないので削除する
    （必要なら部門ごとに作り直す）
    """
    Connector = apps.get_model("connector", "Connector")
    for connector in Connector.objects.all():
        departments = list(connector.departments.order_by("pk").values_list("pk", flat=True))
        if not departments:
            connector.scope = COMPANY
        else:
            connector.scope = DEPARTMENT
            connector.department_id = departments[0]
            connector.deleted = connector.deleted or len(departments) > 1
        connector.save(update_fields=["scope", "department", "deleted"])


class Migration(migrations.Migration):
    dependencies = [
        ("connector", "0002_database_connection"),
        ("user", "0006_department_without_roles"),
    ]

    operations = [
        migrations.AddField(
            model_name="connector",
            name="scope",
            field=models.PositiveSmallIntegerField(
                choices=[(2, "部門"), (3, "全社"), (4, "システム")], null=True, verbose_name="範囲"
            ),
        ),
        migrations.AddField(
            model_name="connector",
            name="department",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="user.department",
                verbose_name="部門",
            ),
        ),
        migrations.RunPython(to_scope, migrations.RunPython.noop),
        migrations.RemoveField(model_name="connector", name="departments"),
        migrations.AlterField(
            model_name="connector",
            name="scope",
            field=models.PositiveSmallIntegerField(
                choices=[(2, "部門"), (3, "全社"), (4, "システム")], verbose_name="範囲"
            ),
        ),
        migrations.AddConstraint(
            model_name="connector",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("department__isnull", False), ("scope", 2)),
                    models.Q(("department__isnull", True), ("scope__in", [3, 4])),
                    _connector="OR",
                ),
                name="connector_scope_department",
            ),
        ),
    ]
