from django.db import migrations, models
from django.db.models import Q

# 種類の整数から、Pluginのextension_idへ（design.md §3.11.1）
KINDS = {1: "yoriki.notification.webhook", 2: "yoriki.notification.zapier", 3: "yoriki.notification.in_app"}


def forwards(apps, schema_editor):
    Destination = apps.get_model("delivery", "Destination")
    for kind, extension in KINDS.items():
        Destination.objects.filter(kind=kind).update(extension=extension)


class Migration(migrations.Migration):
    dependencies = [("delivery", "0006_notification_message")]

    operations = [
        migrations.RemoveConstraint(model_name="destination", name="destination_personal_in_app"),
        migrations.AddField(
            model_name="destination",
            name="extension",
            field=models.CharField(default="", max_length=200, verbose_name="種類"),
            preserve_default=False,
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.RemoveField(model_name="destination", name="kind"),
        migrations.AddConstraint(
            model_name="destination",
            constraint=models.CheckConstraint(
                condition=~Q(scope=1) | Q(extension="yoriki.notification.in_app"),
                name="destination_personal_in_app",
            ),
        ),
    ]
