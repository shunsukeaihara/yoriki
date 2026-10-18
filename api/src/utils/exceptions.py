from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = _("他の更新と競合しました。最新の状態を取得してからやり直してください。")
    default_code = "conflict"
