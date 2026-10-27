from django.apps import AppConfig


class UserConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "user"

    def ready(self):
        # Modelの読み込み後でないとimportできないので、ready()の中でimportする
        from . import checks  # noqa: F401, PLC0415  system checkを登録する
