"""ログイン済みのセッションを作り、Cookieの値を表示するコマンド。Playwright等で画面を確かめるときに使う。

Usage:
    python manage.py dev_session check-member
    python manage.py dev_session check-admin@example.co.jp
"""

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from django.core.management.base import CommandError
from django.db.models import Q

from devtools.commands import DevCommand
from user.models import User


class Command(DevCommand):
    help = "ログイン済みのセッションを作り、Cookieの名前と値を表示する"

    def add_arguments(self, parser):
        parser.add_argument("user", help="ユーザーのusernameかメールアドレス")

    def handle(self, *args, **options):
        user = User.objects.filter(Q(username=options["user"]) | Q(email=options["user"]), is_active=True).first()
        if user is None:
            raise CommandError(f"有効なユーザー {options['user']} がいません（seed_dev で作れます）")
        session = SessionStore()
        session[SESSION_KEY] = str(user.pk)
        session[BACKEND_SESSION_KEY] = "utils.backends.PasswordBackend"
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
        session.create()
        self.stdout.write(f"{settings.SESSION_COOKIE_NAME}={session.session_key}")
