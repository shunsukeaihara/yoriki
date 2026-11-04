from django.apps import AppConfig


class DevtoolsConfig(AppConfig):
    """開発の確認用の道具（偽のLLM、確認用のデータ）。Modelを持たない。管理コマンドは DEBUG のときだけ動く"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "devtools"
