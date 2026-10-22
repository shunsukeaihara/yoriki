"""Yorikiの基本設定。

秘密情報と環境依存の値は、すべて環境変数（`YORIKI_` で始まる）から読む。
ローカル開発ではリポジトリルートの `.envrc`（direnv）、本番では実行環境から渡す（backend-conventions.md §6.1）。
"""

import os
import sys
from datetime import timedelta
from pathlib import Path

from django.utils.translation import gettext_lazy as _


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ["YORIKI_SECRET_KEY"]
DEBUG = _env_bool("YORIKI_DEBUG")
ALLOWED_HOSTS = _env_list("YORIKI_ALLOWED_HOSTS", "localhost,127.0.0.1")
# 画面とAPIは同じオリジンに置く。HTTPSの公開URL（例：https://yoriki.example.co.jp）を指定する
CSRF_TRUSTED_ORIGINS = _env_list("YORIKI_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "app.apps.YorikiAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "django_extensions",
    "django_q",
    "auditlog",
    "axes",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.microsoft",
    "common",
    "user",
    "llm",
    "conversation",
    "run",
    "usage",
    "harness",
    "stream",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "auditlog.middleware.AuditlogMiddleware",
    # ログインを止めたときの応答を返す。最後に置く（django-axesの指定）
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "app.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

ASGI_APPLICATION = "app.asgi.application"
WSGI_APPLICATION = "app.wsgi.application"

# Yoriki自身のDB。Uvicorn（ASGI）では同期のviewがスレッドごとに動くので、psycopg 3の接続プールを使う（§6.1）。
# qclusterはworkerをforkで作り、プールが開いた接続をforkした先のプロセスと共有してしまうので、プールを使わない
# （Q2がtaskの前後で古い接続を閉じ、taskごとに接続する）
USE_DATABASE_POOL = "qcluster" not in sys.argv
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("YORIKI_DATABASE_NAME"),
        "USER": os.getenv("YORIKI_DATABASE_USER"),
        "PASSWORD": os.getenv("YORIKI_DATABASE_PASSWORD"),
        "HOST": os.getenv("YORIKI_DATABASE_HOST"),
        "PORT": os.getenv("YORIKI_DATABASE_PORT", "5432"),
        "CONN_MAX_AGE": 0,  # プールを使うので、Djangoの側では接続を持ち続けない
        "OPTIONS": {
            "pool": {
                "min_size": int(os.getenv("YORIKI_DATABASE_POOL_MIN", "2")),
                "max_size": int(os.getenv("YORIKI_DATABASE_POOL_MAX", "10")),
                "timeout": 10,
            },
        }
        if USE_DATABASE_POOL
        else {},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "user.User"

# パスワードでのログインは、有効なSSOの設定があれば基盤管理者だけに許可する（§6.2）。
# django-axes のバックエンドを先頭に置き、試行回数の制限で止めたログインは後ろのバックエンドへ進ませない
AUTHENTICATION_BACKENDS = ["axes.backends.AxesStandaloneBackend", "utils.backends.PasswordBackend"]

# --- ログインの試行回数の制限（django-axes。記録はPostgreSQL） ---
# アカウントごとと送信元のIPアドレスごとに別々に数える。組だけで数えると、多数のIPアドレスから
# 同じアカウントのパスワードを推測できるため（design.md §10.0）
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
AXES_FAILURE_LIMIT = 10
# アカウントごとの制限は他人がそのアカウントを止められることにもなるので、止める時間は短くする。
# 解除は `axes_reset_username` 等の管理コマンドで行う
AXES_COOLOFF_TIME = timedelta(minutes=15)
# 止めている間の失敗で止める時間を延ばさない（延ばし続けて、ずっと止められないように）
AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT = False
AXES_RESET_ON_SUCCESS = True
AXES_USERNAME_CALLABLE = "utils.backends.axes_username"
AXES_CLIENT_IP_CALLABLE = "utils.backends.client_ip"
AXES_LOCKOUT_CALLABLE = "utils.backends.lockout_response"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- セッションとCSRF ---
# 画面とAPIは同じオリジンに置くので、CORSは使わない。CSRFのCookieは画面のJavaScriptが読んでヘッダーに付ける
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# 開発時（http://localhost）だけSecureを外せるようにする。本番では必ずtrue
SESSION_COOKIE_SECURE = _env_bool("YORIKI_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SESSION_COOKIE_AGE = int(os.getenv("YORIKI_SESSION_COOKIE_AGE", str(60 * 60 * 24 * 14)))
# nginx（やCloudflare Tunnel）の後ろでHTTPSを判定する（deployment.md §4）
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- 国際化 ---
# 翻訳元の言語は日本語（§10）
LANGUAGE_CODE = "ja"
LANGUAGES = [("ja", _("日本語"))]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = os.getenv("YORIKI_TIME_ZONE", "Asia/Tokyo")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/api/static/"
# 本番はイメージのビルド時に collectstatic し、nginxが /api/static/ を配る（deployment.md §2、§4.1）。
# 開発時（DEBUG）は collectstatic せず、app/urls.py で各アプリから直接配る
STATIC_ROOT = None if DEBUG else os.getenv("YORIKI_STATIC_ROOT", str(BASE_DIR / "static"))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # 本番はファイル名に内容のハッシュを付け、nginxで長くキャッシュさせる。
    # 開発時は collectstatic しない（manifest がない）ので、ハッシュを付けない
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage."
        + ("StaticFilesStorage" if DEBUG else "ManifestStaticFilesStorage")
    },
}

# --- DRF ---
REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "utils.pagination.CursorPaginationWithPageSizeQuery",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ),
    "DEFAULT_AUTHENTICATION_CLASSES": ("utils.authentication.SessionAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("utils.permissions.PlatformAdmin",),  # 指定漏れは最も厳しい権限で閉じる
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "utils.handlers.exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Yoriki API",
    "VERSION": "0.1.0",
    "COMPONENT_SPLIT_REQUEST": True,
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["utils.permissions.AppUser"],
    "SCHEMA_PATH_PREFIX": "/api/",
    # SSEのストリーム（Djangoの非同期view）を、text/event-streamの応答としてスキーマに加える（realtime.md §4）
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "stream.schema.add_stream_endpoints",
    ],
    # choicesのenumの名前を固定する（項目名から付けると、別のアプリの kind 等と衝突する）
    "ENUM_NAME_OVERRIDES": {
        "ProviderKindEnum": "llm.models.ProviderKind",
        "RunStatusEnum": "run.models.RunStatus",
        "RunEndReasonEnum": "run.models.RunEndReason",
        "RunEventKindEnum": "run.models.RunEventKind",
        "ClarificationKindEnum": "run.models.ClarificationKind",
        "ClarificationStatusEnum": "run.models.ClarificationStatus",
        "InstructionKindEnum": "conversation.models.InstructionKind",
        "InstructionStateEnum": "conversation.models.InstructionState",
        "MessageRoleEnum": "conversation.models.MessageRole",
    },
}

# --- Django Q2 ---
# クラスタは1つで、エージェントの実行（Run）も配信・Outbox・清掃も受け持つ。増やすときはqclusterの数で調整する
# （backend-conventions.md §8.4、deployment.md §3.2）。Runは確認待ちまで長く動くので、timeoutは長くする
Q_CLUSTER_WORKERS = int(os.getenv("YORIKI_Q_WORKERS", "4"))
Q_CLUSTER = {
    "name": "default",
    "orm": "default",
    "workers": Q_CLUSTER_WORKERS,
    "timeout": 3600,
    "retry": 3660,
    "max_attempts": 1,
}
# 1つのqclusterで同時に動かすRunの数。workerを1つは配信等の軽い処理のために残す
YORIKI_RUNS_PER_QCLUSTER = int(os.getenv("YORIKI_RUNS_PER_QCLUSTER", str(max(1, Q_CLUSTER_WORKERS - 1))))

# --- 社内SSO（django-allauth） ---
# パスワード・メールでのアカウント作成とログインは使わず、SSOだけにする（§6.2）
SOCIALACCOUNT_ONLY = True
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_ADAPTER = "user.adapters.AccountAdapter"
SOCIALACCOUNT_ADAPTER = "user.adapters.SocialAccountAdapter"
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_STORE_TOKENS = False
# ログインの開始はPOSTだけにする（画面からCSRFトークン付きのフォームで送る）
SOCIALACCOUNT_LOGIN_ON_GET = False
LOGIN_URL = "/login"
LOGIN_REDIRECT_URL = "/"

# 認証基盤の設定（client ID・secret・テナント・ドメイン）は、基盤管理者が管理画面で登録する（user.models.SSOProvider）。
# allauthへは user.adapters.SocialAccountAdapter.list_apps() で渡すので、ここには認証基盤ごとの固定の設定だけを置く
SOCIALACCOUNT_PROVIDERS: dict[str, dict] = {
    "microsoft": {"SCOPE": ["User.Read"]},
    # Googleのhdは認証画面への指定（SSOProvider.app_settings()）だけなので、
    # 戻ったIDトークンのhdも user.adapters で確認する
    "google": {"SCOPE": ["openid", "profile", "email"]},
}

# --- Secretの暗号化（utils/crypto.py、utils.fields.EncryptedSecretField） ---
# LLMのAPI key、Connectorのパスワード等を暗号化してDBに保存する鍵（Fernet。カンマ区切り）。
# 先頭の鍵で暗号化し、すべての鍵で復号する。鍵を替えるときは新しい鍵を先頭に足し、
# `rotate_secret_encryption` で暗号化し直してから古い鍵を外す（deployment.md §5）
YORIKI_SECRET_ENCRYPTION_KEYS = _env_list("YORIKI_SECRET_ENCRYPTION_KEYS")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.getenv("YORIKI_LOG_LEVEL", "INFO")},
}
