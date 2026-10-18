"""テスト用の設定。テストもPostgreSQLで実行する（development.md §3.1）"""

from .settings import *  # noqa: F403  基本設定をすべて取り込んで上書きする

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
