from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import URLPattern, URLResolver, include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from utils.views import PingAPIView

urlpatterns: list[URLPattern | URLResolver] = [
    path(
        "api/",
        include(
            [
                path("ping/", PingAPIView.as_view()),
                path("admin/", admin.site.urls),
                path("accounts/", include("allauth.urls")),
                path("schema/", SpectacularAPIView.as_view(), name="schema"),
                path("schema/swagger-ui/", SpectacularSwaggerView.as_view(url_name="schema")),
                path("", include("user.urls")),
            ]
        ),
    ),
]

# 開発時（DEBUG）だけ、Uvicornから静的ファイルを配る。本番はnginxが配る
if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
