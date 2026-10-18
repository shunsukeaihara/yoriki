"""テスト用の設定。

テストも本番と同じPostgreSQLで実行する（`select_for_update`、advisory lock、LISTEN/NOTIFY等に依存するため）。
接続先は `.envrc` と同じで、pytest-djangoが `test_<DB名>` のDBを作って使う（development.md §3.1）。
"""

from .settings import *  # noqa: F403  基本設定をすべて取り込んで上書きする

# テストではcollectstaticしないので、ハッシュ付きのファイル名を使わない
STATIC_ROOT = None
STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

SOCIALACCOUNT_PROVIDERS = {
    "microsoft": {
        "APPS": [{"client_id": "test-client", "secret": "test-secret", "settings": {"tenant": "test-tenant"}}],
    },
    "google": {
        "APPS": [{"client_id": "test-client", "secret": "test-secret"}],
    },
}
YORIKI_SSO_PROVIDERS = ["microsoft", "google"]
YORIKI_SSO_ALLOWED_EMAIL_DOMAINS = ["example.co.jp"]
YORIKI_SSO_GOOGLE_HOSTED_DOMAIN = "example.co.jp"
