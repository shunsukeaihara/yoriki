import re
from collections import defaultdict
from typing import Any

from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from utils.crypto import is_encryption_configured
from utils.serializers import ReadOnlyModelSerializer

from .models import SSOKind, SSOProvider, User

# ドメイン名（ラベルを . でつないだもの）。メールアドレスやURLを書いたものを入力エラーにするための簡単な確認
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


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
            "is_platform_admin",
            "all_permissions",
        )

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_all_permissions(self, obj: User) -> list[str]:
        """Djangoのpermission（"<app_label>.<codename>"）。Groupから得たものを含む。画面の出し分けに使う"""
        return sorted(obj.get_all_permissions())


class SessionSerializer(serializers.Serializer):
    is_authenticated = serializers.BooleanField(read_only=True)


class AuthProviderSerializer(serializers.Serializer):
    """ログイン画面に並べる認証基盤。ログインは `login_url` へPOSTのフォームで送って始める"""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    login_url = serializers.CharField(read_only=True)


# --- SSOの設定 ---


class SSOProviderSerializer(serializers.ModelSerializer):
    """SSOの設定の作成と表示。client secretは書き込み専用で、応答には末尾の4文字（client_secret_hint）だけを出す"""

    id = serializers.CharField(source="uid", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    client_secret = serializers.CharField(
        write_only=True,
        required=False,
        max_length=1000,
        help_text=_("作成では必須。更新では、送らなければ変更しない"),
    )
    has_client_secret = serializers.SerializerMethodField()
    client_secret_hint = serializers.SerializerMethodField()
    allowed_email_domains = serializers.ListField(
        child=serializers.CharField(max_length=253),
        allow_empty=False,
        help_text=_("受け付けるメールアドレスのドメイン（例：example.co.jp）"),
    )
    callback_url = serializers.SerializerMethodField(
        help_text=_("認証基盤のアプリの登録に、リダイレクトURIとして登録するURL")
    )
    created_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = SSOProvider
        fields = [
            "id",
            "kind",
            "kind_display",
            "client_id",
            "client_secret",
            "has_client_secret",
            "client_secret_hint",
            "tenant_id",
            "hosted_domain",
            "allowed_email_domains",
            "callback_url",
            "is_active",
            "created_by",
            "created",
            "updated",
        ]
        read_only_fields = ["created", "updated"]

    def get_has_client_secret(self, obj: SSOProvider) -> bool:
        return bool(obj.client_secret)

    def get_client_secret_hint(self, obj: SSOProvider) -> str:
        return obj.client_secret.hint

    def get_callback_url(self, obj: SSOProvider) -> str:
        path = reverse(f"{obj.kind}_callback")
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request else path

    def validate_kind(self, value: str) -> str:
        if SSOProvider.objects.filter(deleted=False, kind=value).exists():
            raise serializers.ValidationError(_("この種別のSSOの設定はすでにあります"))
        return value

    def validate_client_id(self, value: str) -> str:
        return value.strip()

    def validate_client_secret(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError(_("client secretを入力してください"))
        if not is_encryption_configured():
            raise serializers.ValidationError(
                _("client secretを保存するための暗号化の鍵（YORIKI_SECRET_ENCRYPTION_KEYS）が設定されていません")
            )
        return value

    def validate_tenant_id(self, value: str) -> str:
        return value.strip()

    def validate_hosted_domain(self, value: str) -> str:
        value = value.strip().lower()
        if value and not DOMAIN_RE.match(value):
            raise serializers.ValidationError(_("ドメインの形式が正しくありません"))
        return value

    def validate_allowed_email_domains(self, value: list[str]) -> list[str]:
        domains = list(dict.fromkeys(d.strip().lower().lstrip("@") for d in value if d.strip()))
        if not domains:
            raise serializers.ValidationError(_("ドメインを1つ以上入力してください"))
        invalid = [d for d in domains if not DOMAIN_RE.match(d)]
        if invalid:
            raise serializers.ValidationError(_("ドメインの形式が正しくありません：%s") % "、".join(invalid))
        return domains

    def validate(self, attrs):
        # 種別は作成時だけ入力できる。更新（SSOProviderUpdateSerializer）では既存の値で検証する
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        tenant_id = attrs.get("tenant_id", getattr(self.instance, "tenant_id", ""))
        hosted_domain = attrs.get("hosted_domain", getattr(self.instance, "hosted_domain", ""))

        errors = defaultdict(list)
        if self.instance is None and not attrs.get("client_secret"):
            errors["client_secret"].append(_("client secretを入力してください"))
        if kind == SSOKind.MICROSOFT:
            # テナントを固定しないと、他社や個人のMicrosoftアカウントも戻ってくる
            if not tenant_id:
                errors["tenant_id"].append(_("Microsoft 365では、テナントIDを入力してください"))
            if hosted_domain:
                errors["hosted_domain"].append(_("この種別では、Workspaceのドメインを指定できません"))
        if kind == SSOKind.GOOGLE:
            # hdを確かめないと、個人のGoogleアカウントも受け付けてしまう
            if not hosted_domain:
                errors["hosted_domain"].append(_("Google Workspaceでは、Workspaceのドメインを入力してください"))
            if tenant_id:
                errors["tenant_id"].append(_("この種別では、テナントIDを指定できません"))
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class SSOProviderUpdateSerializer(SSOProviderSerializer):
    """SSOの設定の更新。種別は変えられないので、読み取り専用にする"""

    class Meta(SSOProviderSerializer.Meta):
        read_only_fields = ["kind", "created", "updated"]
