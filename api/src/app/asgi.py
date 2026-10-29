import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")

application = get_asgi_application()

# 出口へ渡すファイルのURLのtokenを、uvicornのアクセスログに残さない（utils/logs.py）
from utils.logs import install as install_log_filters  # noqa: E402

install_log_filters()
