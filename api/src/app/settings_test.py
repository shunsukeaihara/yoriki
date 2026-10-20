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

# テスト専用の鍵（本番では使わない）
YORIKI_SECRET_ENCRYPTION_KEYS = ["xz4RJ4arr_XhdjE-oj2NJ7ElAq-K30kZvLHtZMOzIPY="]
