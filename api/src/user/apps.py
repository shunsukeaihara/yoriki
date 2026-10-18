from django.apps import AppConfig
from django.db.models.signals import post_migrate


class UserConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "user"

    def ready(self):
        # Modelの読み込み後でないとimportできないので、ready()の中でimportする
        from .roles import sync_role_groups  # noqa: PLC0415

        # すべてのアプリのpermissionが作られた後に同期するため、senderを限定しない
        post_migrate.connect(sync_role_groups, dispatch_uid="user.sync_role_groups")
