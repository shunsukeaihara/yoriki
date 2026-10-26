import re
from collections import defaultdict
from typing import Any

from django.contrib.auth import authenticate
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, Throttled

from utils.backends import LOCKOUT_MESSAGE
from utils.crypto import is_encryption_configured
from utils.serializers import ReadOnlyModelSerializer

from .models import (
    Department,
    DepartmentMembership,
    Invitation,
    SSOKind,
    SSOProvider,
    User,
    can_manage_account,
    can_use_password,
)

# ドメイン名（ラベルを . でつないだもの）。メールアドレスやURLを書いたものを入力エラーにするための簡単な確認
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class MinimumUserSerializer(ReadOnlyModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "display_name"]


class MyDepartmentSerializer(ReadOnlyModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)

    class Meta:
        model = Department
        fields = ["id", "name"]


class MyUserSerializer(ReadOnlyModelSerializer):
    """ログイン中の利用者。画面の出し分けの元になるもの（permissionと所属）をすべて返す（authorization.md §4.1.1）"""

    id = serializers.CharField(source="uid", read_only=True)
    all_permissions = serializers.SerializerMethodField()
    departments = serializers.SerializerMethodField(help_text=_("実際に所属している部門"))
    usable_departments = serializers.SerializerMethodField(help_text=_("使える部門（一覧の絞り込みの選択肢）"))
    manageable_departments = serializers.SerializerMethodField(
        help_text=_("管理できる部門（部門のSkill・タスクを作るときの選択肢）")
    )

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "display_name",
            "all_permissions",
            "departments",
            "usable_departments",
            "manageable_departments",
        )

    @extend_schema_field(MyDepartmentSerializer(many=True))
    def get_departments(self, obj: User) -> list[Any]:
        return list(MyDepartmentSerializer(Department.objects.joined_by(obj).order_by("name", "pk"), many=True).data)

    @extend_schema_field(MyDepartmentSerializer(many=True))
    def get_usable_departments(self, obj: User) -> list[Any]:
        return list(MyDepartmentSerializer(Department.objects.usable_by(obj).order_by("name", "pk"), many=True).data)

    @extend_schema_field(MyDepartmentSerializer(many=True))
    def get_manageable_departments(self, obj: User) -> list[Any]:
        return list(
            MyDepartmentSerializer(Department.objects.manageable_by(obj).order_by("name", "pk"), many=True).data
        )

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_all_permissions(self, obj: User) -> list[str]:
        """Djangoのpermission（"<app_label>.<codename>"）。Groupから得たものを含む。画面の出し分けに使う"""
        return sorted(obj.get_all_permissions())


class SessionSerializer(serializers.Serializer):
    is_authenticated = serializers.BooleanField(read_only=True)


class LoginSerializer(serializers.Serializer):
    """パスワードでのログイン。有効なSSOの設定があれば基盤管理者だけ（utils.backends.PasswordBackend）"""

    email = serializers.CharField(write_only=True, max_length=254, help_text=_("メールアドレス"))
    password = serializers.CharField(write_only=True, max_length=4096, trim_whitespace=False)

    def validate(self, attrs):
        # django-axes は Django の HttpRequest に、止めたかどうか（axes_locked_out）を書き込む
        request = self.context["request"]._request
        user = authenticate(request, username=attrs["email"].strip(), password=attrs["password"])
        if getattr(request, "axes_locked_out", False):
            raise Throttled(detail=LOCKOUT_MESSAGE)
        if user is None:
            raise serializers.ValidationError(_("メールアドレスかパスワードが正しくありません。"))
        attrs["user"] = user
        return attrs


class AuthProviderSerializer(serializers.Serializer):
    """ログイン画面に並べる認証基盤。ログインは `login_url` へPOSTのフォームで送って始める"""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    login_url = serializers.CharField(read_only=True)


class AuthMethodsSerializer(serializers.Serializer):
    """ログイン画面に出すログインの方法"""

    providers = AuthProviderSerializer(many=True, read_only=True)
    password_login = serializers.BooleanField(
        read_only=True,
        help_text=_(
            "パスワードの欄を出すか。有効なSSOの設定があれば出さない（基盤管理者は /login?method=password で使う）"
        ),
    )


# --- 招待・再設定のリンク ---

INVALID_INVITATION = _(
    "このリンクは使えません。期限が切れたか、使用済みです。基盤管理者に発行し直してもらってください。"
)
SSO_ONLY = _("SSOが有効になったため、このリンクは使えません。SSOでログインしてください。")


class InvitationLinkSerializer(serializers.Serializer):
    """発行したリンク。URLはこの応答でしか分からない（tokenはDBにハッシュだけを保存する）"""

    url = serializers.CharField(read_only=True, help_text=_("本人に渡すURL。tokenはURLのフラグメントに入れる"))
    purpose = serializers.IntegerField(read_only=True)
    purpose_display = serializers.CharField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)


def _invitation_link(request, invitation: Invitation, token: str) -> dict:
    # tokenはアクセスログに残らないよう、URLのフラグメントに置く（サーバーへは送られない）
    return {
        "url": f"{request.build_absolute_uri('/invitation')}#{token}",
        "purpose": invitation.purpose,
        "purpose_display": invitation.get_purpose_display(),
        "expires_at": invitation.expires_at,
    }


class InvitationTokenSerializer(serializers.Serializer):
    """リンクの確認。宛先のメールアドレスと期限を返す"""

    token = serializers.CharField(write_only=True, max_length=200)
    email = serializers.EmailField(source="user.email", read_only=True)
    display_name = serializers.CharField(source="user.display_name", read_only=True)
    purpose = serializers.IntegerField(read_only=True)
    purpose_display = serializers.CharField(source="get_purpose_display", read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)

    def _find(self, token: str, *, for_update: bool = False) -> Invitation:
        invitation = Invitation.find_usable(token, for_update=for_update)
        if invitation is None:
            raise serializers.ValidationError({"token": [INVALID_INVITATION]})
        # SSOが有効な間は、基盤管理者以外にリンクを使わせない（SSOだけでログインする規則を迂回させない）
        if not can_use_password(invitation.user):
            raise serializers.ValidationError({"token": [SSO_ONLY]})
        # 発行の後に、発行した人が権限を失った・対象が昇格したなら、残ったリンクでアカウントを奪わせない
        # （authorization.md §10）
        if not can_manage_account(invitation.created_by, invitation.user):
            raise serializers.ValidationError({"token": [INVALID_INVITATION]})
        return invitation

    def validate(self, attrs):
        attrs["invitation"] = self._find(attrs["token"])
        return attrs


class InvitationAcceptSerializer(InvitationTokenSerializer):
    """パスワードを設定してリンクを使用済みにする。Viewのtransactionの中で、リンクを行ロックして確かめる"""

    password = serializers.CharField(write_only=True, max_length=4096, trim_whitespace=False)

    def validate(self, attrs):
        invitation = self._find(attrs["token"], for_update=True)
        try:
            validate_password(attrs["password"], invitation.user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        attrs["invitation"] = invitation
        return attrs

    def create(self, validated_data) -> Invitation:
        invitation: Invitation = validated_data["invitation"]
        invitation.accept(validated_data["password"])
        return invitation


# --- ユーザー（管理） ---


class GroupSerializer(serializers.ModelSerializer):
    """Group（permissionの集まり）。ユーザーへの割り当てでは名前で指定する"""

    class Meta:
        model = Group
        fields = ["name"]
        read_only_fields = fields


class ManageUserSerializer(serializers.ModelSerializer):
    """ユーザーの作成・表示。Yorikiのアカウントのパスワードは、本人が招待のリンクで設定する。

    書ける項目は表示名・有効・Groupだけ（permissionの直接の付与、`is_superuser`、SSOの識別子は出さない）。
    管理を任された人が自分の権限を超えられないよう、自分の持たないpermissionを含むGroupは付け外しできず、
    自分の持たないpermissionを持つアカウントと基盤管理者のアカウントは変更できない（authorization.md §10）
    """

    id = serializers.CharField(source="uid", read_only=True)
    email = serializers.EmailField(max_length=254)
    groups: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(
        many=True, slug_field="name", queryset=Group.objects.all(), required=False, help_text=_("Groupの名前")
    )
    has_password = serializers.SerializerMethodField(help_text=_("パスワードを設定済みか"))
    sso_providers = serializers.SerializerMethodField(help_text=_("紐付いているSSOの認証基盤"))
    can_change = serializers.SerializerMethodField(help_text=_("操作する人がこのアカウントを変更できるか"))
    can_issue_invitation = serializers.SerializerMethodField(
        help_text=_("操作する人がこのアカウントに招待・再設定のリンクを発行できるか")
    )

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "display_name",
            "groups",
            "is_active",
            "has_password",
            "sso_providers",
            "can_change",
            "can_issue_invitation",
            "last_login",
            "created",
            "updated",
        ]
        read_only_fields = ["last_login", "created", "updated"]

    def get_has_password(self, obj: User) -> bool:
        return obj.has_usable_password()

    def get_sso_providers(self, obj: User) -> list[str]:
        # allauthの SocialAccount の逆参照（django-stubs は allauth の型を知らない）
        return sorted({account.provider for account in obj.socialaccount_set.all()})  # type: ignore[attr-defined]

    def get_can_change(self, obj: User) -> bool:
        actor = self.context["request"].user
        return actor.has_perm("user.change_user") and can_manage_account(actor, obj)

    def get_can_issue_invitation(self, obj: User) -> bool:
        return self.get_can_change(obj) and can_use_password(obj)

    def validate_email(self, value: str) -> str:
        value = value.strip().lower()
        others = User.objects.filter(email__iexact=value) | User.objects.filter(username=value)
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError(_("同じメールアドレスのユーザーがいます"))
        return value

    def validate_is_active(self, value: bool) -> bool:
        request = self.context.get("request")
        if not value and self.instance is not None and request and self.instance.pk == request.user.pk:
            raise serializers.ValidationError(_("自分のアカウントは無効にできません"))
        return value

    def validate_groups(self, value: list[Group]) -> list[Group]:
        actor = self.context["request"].user
        current = set(self.instance.groups.all()) if self.instance is not None else set()
        changed = current.symmetric_difference(value)
        if changed and self.instance is not None and self.instance.pk == actor.pk:
            raise serializers.ValidationError(_("自分のGroupは変えられません"))
        own = actor.get_all_permissions()
        for group in sorted(changed, key=lambda g: g.name):
            perms = {
                f"{app_label}.{codename}"
                for app_label, codename in group.permissions.values_list("content_type__app_label", "codename")
            }
            if not perms <= own:
                raise serializers.ValidationError(
                    _("自分の持たないpermissionを含むGroup「%(name)s」は付け外しできません") % {"name": group.name}
                )
        return value

    def validate(self, attrs):
        if self.instance is not None and not can_manage_account(self.context["request"].user, self.instance):
            raise PermissionDenied(_("自分の持たない権限を持つアカウントは変更できません"))
        return attrs


class ManageUserCreateSerializer(ManageUserSerializer):
    """ユーザーの作成。パスワードでログインできる場合（SSOが無効）は、招待のリンクを発行して一度だけ返す"""

    invitation = serializers.SerializerMethodField()

    class Meta(ManageUserSerializer.Meta):
        fields = [*ManageUserSerializer.Meta.fields, "invitation"]

    @extend_schema_field(InvitationLinkSerializer(allow_null=True))
    def get_invitation(self, obj: User) -> dict | None:
        return getattr(obj, "invitation_link", None)

    def create(self, validated_data) -> User:
        actor = validated_data.pop("created_by")
        groups = validated_data.pop("groups", [])
        # ユーザー名はメールアドレスにする（Django管理画面のログインでも使える）
        user = User(username=validated_data["email"], **validated_data)
        user.set_unusable_password()
        user.save()
        user.groups.set(groups)
        if can_use_password(user):
            invitation, token = Invitation.issue(user=user, actor=actor)
            # 作成の応答（get_invitation）でだけ返す。保存しない
            user.invitation_link = _invitation_link(self.context["request"], invitation, token)  # type: ignore[attr-defined]
        # SSOが有効なら、本人がSSOで初めてログインしたときに、このアカウントに紐付く
        return user


class ManageUserUpdateSerializer(ManageUserSerializer):
    """ユーザーの更新。メールアドレスはSSOの紐付けに使うので変えない"""

    # 宣言したフィールドには Meta.read_only_fields が効かないので、宣言し直す
    email = serializers.EmailField(read_only=True)


class IssueInvitationSerializer(serializers.Serializer):
    """招待・再設定のリンクの発行。SSOが有効な間は基盤管理者のアカウントにだけ発行できる"""

    def validate(self, attrs):
        if not can_manage_account(self.context["request"].user, self.context["user"]):
            # 再設定のリンクで、自分より強いアカウント（基盤管理者を含む）を乗っ取らせない
            raise PermissionDenied(_("自分の持たない権限を持つアカウントには、リンクを発行できません"))
        if not can_use_password(self.context["user"]):
            raise serializers.ValidationError(
                _(
                    "SSOが有効な間は、基盤管理者以外のアカウントにはリンクを発行できません。SSOでログインしてもらってください。"
                )
            )
        return attrs

    def create(self, validated_data) -> dict:
        invitation, token = Invitation.issue(user=self.context["user"], actor=self.context["request"].user)
        return _invitation_link(self.context["request"], invitation, token)


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


# --- 部門 ---


class MinimumDepartmentSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)

    class Meta:
        model = Department
        fields = ["id", "name"]
        read_only_fields = fields


class DepartmentSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    member_count = serializers.SerializerMethodField(help_text=_("所属する人数"))

    class Meta:
        model = Department
        fields = ["id", "name", "member_count", "created", "updated"]
        read_only_fields = ["created", "updated"]

    def get_member_count(self, obj: Department) -> int:
        # 一覧・詳細は件数を注釈で付けて読む（DepartmentViewSet）。作成・更新の応答では数える
        count = getattr(obj, "member_count", None)
        return count if count is not None else obj.memberships.count()

    def validate_name(self, value: str) -> str:
        value = value.strip()
        others = Department.objects.filter(deleted=False, name=value)
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError(_("同じ名前の部門があります"))
        return value


def check_membership_change(actor: User, target: User) -> None:
    """所属の付け外しは、ユーザーの変更と同じ防止策に従う（authorization.md §10）"""
    if actor.pk == target.pk:
        raise PermissionDenied(_("自分の所属は変えられません"))
    if not can_manage_account(actor, target):
        raise PermissionDenied(_("自分の持たない権限を持つアカウントの所属は変えられません"))


class DepartmentMembershipSerializer(serializers.ModelSerializer):
    """部門の所属。変えるときは、所属を消して作り直す"""

    id = serializers.CharField(source="uid", read_only=True)
    user: "serializers.SlugRelatedField[User]" = serializers.SlugRelatedField(
        slug_field="uid", queryset=User.objects.filter(is_active=True)
    )
    user_info = MinimumUserSerializer(source="user", read_only=True)

    class Meta:
        model = DepartmentMembership
        fields = ["id", "user", "user_info", "created", "updated"]
        read_only_fields = ["created", "updated"]

    def validate_user(self, value: User) -> User:
        department = self.context["department"]
        if DepartmentMembership.objects.filter(department=department, user=value).exists():
            raise serializers.ValidationError(_("この社員は既に部門に所属しています"))
        check_membership_change(self.context["request"].user, value)
        return value
