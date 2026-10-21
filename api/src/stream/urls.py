from django.urls import path

from .views import run_stream, user_stream

urlpatterns = [
    path("app/v1/stream/", user_stream, name="user_stream"),
    path("app/v1/runs/<str:run>/stream/", run_stream, name="run_stream"),
]
