from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("task", "0011_run_limits")]

    operations = [
        # 種類はWebhookだけだったので、すべて yoriki.trigger.webhook にする（design.md §3.11.1）
        migrations.AddField(
            model_name="triggerbinding",
            name="extension",
            field=models.CharField(default="yoriki.trigger.webhook", max_length=200, verbose_name="種類"),
        ),
        migrations.RemoveField(model_name="triggerbinding", name="kind"),
    ]
