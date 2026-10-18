"""テスト用の設定。

ローカルではSQLiteで実行する（PostgreSQLを起動しなくてよい）。CIの結合テストでは
`YORIKI_TEST_DATABASE=postgres` を設定し、`.envrc` と同じ接続先のPostgreSQLで実行する（development.md §3.1）。
PostgreSQLの機能に依存するテストには `@pytest.mark.postgres` を付ける（SQLiteでは飛ばす）。
"""

import os

from .settings import *  # noqa: F403  基本設定をすべて取り込んで上書きする

if os.getenv("YORIKI_TEST_DATABASE", "sqlite") != "postgres":
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

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
