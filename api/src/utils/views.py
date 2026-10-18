from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import PingSerializer


class SoftDeleteMixin:
    """削除の要求で行を消さず、`deleted=True` にする"""

    def perform_destroy(self, instance):
        instance.deleted = True
        instance.save(update_fields=["deleted", "updated"])


@extend_schema(tags=["ping"])
class PingAPIView(APIView):
    """ヘルスチェック。nginx・Composeから認証なしで呼ぶ"""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(description="ヘルスチェック", request=None, responses=PingSerializer)
    def get(self, request: Request) -> Response:
        return Response(PingSerializer({"status": "ok"}).data)
