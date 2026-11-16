from django.db import migrations, models

# 種別の整数から、Pluginのextension_idへ（design.md §3.11.1）
KINDS = {1: "yoriki.connector.postgresql", 2: "yoriki.connector.mysql", 3: "yoriki.connector.sqlserver"}


def forwards(apps, schema_editor):
    DatabaseConnection = apps.get_model("connector", "DatabaseConnection")
    for kind, extension in KINDS.items():
        DatabaseConnection.objects.filter(kind=kind).update(extension=extension)


class Migration(migrations.Migration):
    dependencies = [("connector", "0004_connectionlease")]

    operations = [
        migrations.AddField(
            model_name="databaseconnection",
            name="extension",
            field=models.CharField(default="", max_length=200, verbose_name="種別"),
            preserve_default=False,
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.RemoveField(model_name="databaseconnection", name="kind"),
    ]
