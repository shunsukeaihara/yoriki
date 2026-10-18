from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from utils.views import PingAPIView

urlpatterns = [
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
