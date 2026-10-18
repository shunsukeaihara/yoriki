from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from utils.serializers import ReadOnlyModelSerializer

from .models import User


class MyUserSerializer(ReadOnlyModelSerializer):
    """ログイン中の利用者。画面の表示と権限の判定に使う"""

    id = serializers.CharField(source="uid", read_only=True)
    is_platform_admin = serializers.BooleanField(read_only=True)
    all_permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "display_name",
            "is_business_admin",
            "is_plugin_publisher",
            "is_platform_admin",
            "all_permissions",
        )

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_all_permissions(self, obj: User) -> list[str]:
        """Djangoのpermission（"<app_label>.<codename>"）。画面の出し分けに使う"""
        return sorted(obj.get_all_permissions())


class SessionSerializer(serializers.Serializer):
    is_authenticated = serializers.BooleanField(read_only=True)


class AuthProviderSerializer(serializers.Serializer):
    """ログイン画面に並べる認証基盤。ログインは `login_url` へPOSTのフォームで送って始める"""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    login_url = serializers.CharField(read_only=True)
