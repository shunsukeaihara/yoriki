import json

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from plugin import builtin, registry
from plugin.errors import PluginError, PluginUnavailable
from plugin.fields import ExtensionField
from plugin.spec import ExtensionType
from user.models import Department
from user.serializers import MinimumDepartmentSerializer, MinimumUserSerializer
from utils.exceptions import Conflict
from utils.serializers import ReadOnlyModelSerializer

from .adapters import NotificationAdapter, adapter_for
from .errors import VersionConflict
from .models import (
    DESTINATION_REVISION_FIELDS,
    ChannelConnection,
    ChannelEvent,
    Delivery,
    DeliveryAttempt,
    DeliveryStatus,
    Destination,
    DestinationRevision,
    DestinationScope,
    FileFetch,
    Notification,
    NotifyEvent,
)
from .network import BlockedAddress, resolve

MAX_URL_LENGTH = 2000


class DestinationSerializer(serializers.ModelSerializer):
    """出口の作成と表示（連携の管理者。authorization.md §5.5）。中身は今の版の値を平らに出す。

    種類・範囲・部門は作成時に決めて変えない。送り先のURLは書くだけで、表示ではホストと末尾の4文字だけを返す。
    署名の鍵は作成時と作り直したときに一度だけ返す。更新では、編集を始めた時点の `version` を送り、その間に他の人が
    版を作っていたら409になる
    """

    id = serializers.CharField(source="uid", read_only=True)
    extension = ExtensionField(ExtensionType.NOTIFICATION, ExtensionType.CHANNEL)
    extension_name = serializers.CharField(read_only=True)
    is_external = serializers.BooleanField(read_only=True, help_text=_("外へ送る種類か（宛先のURL・対話の出口）"))
    is_channel = serializers.BooleanField(
        read_only=True, help_text=_("対話の出口（Slack等のチャネル）か。タスクの対話の出口にだけ選べる")
    )
    connection: "serializers.SlugRelatedField[ChannelConnection]" = serializers.SlugRelatedField(
        source="current_revision.connection",
        slug_field="uid",
        queryset=ChannelConnection.objects.alive(),
        required=False,
        allow_null=True,
        help_text=_("対話の出口の連携の接続（対話の出口では必須）"),
    )
    connection_name = serializers.CharField(
        source="current_revision.connection.name", read_only=True, allow_null=True, default=None
    )
    channel_id = serializers.CharField(
        source="current_revision.channel_id",
        max_length=100,
        required=False,
        allow_blank=True,
        help_text=_(
            "対話の出口のチャネルのID（Slackでは C から始まるID）。登録のときに、botが参加しているかを確かめる"
        ),
    )
    channel_name = serializers.CharField(
        source="current_revision.channel_name", read_only=True, help_text=_("確かめたときのチャネルの名前")
    )
    scope = serializers.ChoiceField(
        # 個人の出口（画面の通知）はYorikiが作る
        choices=[c for c in DestinationScope.choices if c[0] != DestinationScope.PERSONAL],
        help_text=_("使ってよい範囲。部門の出口はその部門のタスクだけが使う。作成した後は変えられない"),
    )
    scope_display = serializers.CharField(source="get_scope_display", read_only=True)
    department: "serializers.SlugRelatedField[Department]" = serializers.SlugRelatedField(
        slug_field="uid",
        queryset=Department.objects.active(),
        required=False,
        allow_null=True,
        help_text=_("部門の範囲では必須。作成した後は変えられない"),
    )
    department_info = MinimumDepartmentSerializer(source="department", read_only=True, allow_null=True)
    name = serializers.CharField(max_length=100)
    version = serializers.IntegerField(
        source="version_number",
        min_value=0,
        required=False,
        help_text=_("今の版。更新では、編集を始めた時点の値を送る（作成では送らない）"),
    )
    url = serializers.CharField(
        source="current_revision.url",
        max_length=MAX_URL_LENGTH,
        required=False,
        allow_blank=True,
        write_only=True,
        help_text=_("送り先のURL（https）。Webhook・Zapierでは必須、画面の通知では送らない。表示では返さない"),
    )
    url_display = serializers.CharField(
        source="current_revision.url_display", read_only=True, help_text=_("送り先のホストと末尾の4文字")
    )
    allows_sensitive_results = serializers.BooleanField(
        source="current_revision.allows_sensitive_results",
        required=False,
        default=False,
        help_text=_(
            "機密（個人情報・機密情報を扱う）のタスクの結果を送ってよい。画面の通知は認証の要る画面の中だけで、受け手を"
            "結果を見られる人に限るので、この指定によらず機密のタスクにも使える"
        ),
    )
    change_reason = serializers.CharField(max_length=500, required=False, allow_blank=True, write_only=True)
    created_by = MinimumUserSerializer(read_only=True)

    class Meta:
        model = Destination
        fields = [
            "id",
            "extension",
            "extension_name",
            "is_external",
            "is_channel",
            "scope",
            "scope_display",
            "department",
            "department_info",
            "name",
            "suspended",
            "version",
            "url",
            "url_display",
            "connection",
            "connection_name",
            "channel_id",
            "channel_name",
            "allows_sensitive_results",
            "change_reason",
            "secret_rotated",
            "created_by",
            "created",
            "updated",
        ]
        read_only_fields = ["suspended", "secret_rotated", "created", "updated"]

    def validate(self, attrs):
        if self.instance is None:
            scope = DestinationScope(attrs["scope"])
            if scope == DestinationScope.DEPARTMENT and attrs.get("department") is None:
                raise serializers.ValidationError({"department": _("部門を選んでください")})
            if scope != DestinationScope.DEPARTMENT:
                attrs["department"] = None
            extension = attrs["extension"]
        else:
            if "version_number" not in attrs:
                raise serializers.ValidationError({"version": _("編集を始めた時点の版を送ってください")})
            extension = self.instance.extension
        if "name" in attrs:
            attrs["name"] = attrs["name"].strip()
        sent = attrs.pop("current_revision", {})
        content = self._revision_content(sent, self.instance.current_revision if self.instance else None)
        found = registry.declared(extension)
        if found is not None and found[0].type == ExtensionType.CHANNEL:
            self._check_channel(extension, sent, content)
        else:
            self._check_url(extension, sent, content)
            content.update(connection=None, channel_id="", channel_name="")
        attrs["content"] = content
        return attrs

    @staticmethod
    def _revision_content(sent: dict, current: DestinationRevision | None) -> dict:
        """新しい版の中身。送らなかった項目は今の版の値（作成では空）"""
        empty = {"url": "", "allows_sensitive_results": False, "connection": None, "channel_id": "", "channel_name": ""}
        content = {}
        for name in DESTINATION_REVISION_FIELDS:
            if name in sent:
                content[name] = sent[name]
            elif current is None:
                content[name] = empty[name]
            else:
                content[name] = current.url.reveal() if name == "url" else getattr(current, name)
        return content

    @staticmethod
    def _check_channel(extension: str, sent: dict, content: dict) -> None:
        """対話の出口：URLは持たず、接続とチャネルを確かめる（design.md §4.6）"""
        if str(sent.get("url") or "").strip():
            raise serializers.ValidationError({"url": _("対話の出口には送り先のURLを指定しません")})
        content["url"] = ""
        connection, channel_id = content.get("connection"), str(content.get("channel_id") or "").strip()
        if connection is None:
            raise serializers.ValidationError({"connection": _("連携の接続を選んでください")})
        if connection.extension != extension:
            raise serializers.ValidationError({"connection": _("この種類の接続を選んでください")})
        if not channel_id:
            raise serializers.ValidationError({"channel_id": _("チャネルのIDを入力してください")})
        content["channel_id"] = channel_id
        if "connection" not in sent and "channel_id" not in sent:
            return
        if not connection.in_service():
            raise serializers.ValidationError({"connection": _("この接続は今は使えません（停止中等）")})
        from .channels.outbound import channel_adapter  # noqa: PLC0415  channelsがserializerのModelを使うため

        try:
            info = channel_adapter(extension).check_channel(connection.secret_values(), channel_id)
        except PluginUnavailable as exc:
            raise serializers.ValidationError({"extension": _("この種類は今は使えません（停止中等）")}) from exc
        except PluginError as exc:
            raise serializers.ValidationError({"channel_id": str(exc)}) from exc
        content["channel_name"] = info.name

    @staticmethod
    def _check_url(extension: str, sent: dict, content: dict) -> None:
        if not registry.has_capability(extension, builtin.URL):
            # 画面の通知は送り先を持たない
            if str(sent.get("url") or "").strip():
                raise serializers.ValidationError({"url": _("画面の通知には送り先のURLを指定しません")})
            content["url"] = ""
            return
        if not str(content["url"]).strip():
            raise serializers.ValidationError({"url": _("送り先のURLを入力してください")})
        if "url" in sent:
            # 登録するときも、送り先の形と、名前を引いた先が送ってよいアドレスかを確かめる（design.md §4.4）
            content["url"] = content["url"].strip()
            try:
                adapter = adapter_for(extension)
                assert isinstance(adapter, NotificationAdapter)
                resolve(adapter.validate_url(content["url"]))
            except BlockedAddress as exc:
                raise serializers.ValidationError({"url": str(exc)}) from exc
            except PluginUnavailable as exc:
                raise serializers.ValidationError({"extension": _("この種類は今は使えません（停止中等）")}) from exc

    @transaction.atomic
    def create(self, validated_data) -> Destination:
        destination, secret = Destination.create_with_revision(
            extension=validated_data["extension"],
            scope=validated_data["scope"],
            department=validated_data["department"],
            name=validated_data["name"],
            actor=validated_data["actor"],
            change_reason=validated_data.get("change_reason", ""),
            content=validated_data["content"],
        )
        destination.issued_secret = secret  # type: ignore[attr-defined]
        return destination

    @transaction.atomic
    def update(self, instance: Destination, validated_data) -> Destination:
        try:
            instance.revise(
                actor=validated_data["actor"],
                expected_version=validated_data["version_number"],
                change_reason=validated_data.get("change_reason", ""),
                **validated_data["content"],
            )
        except VersionConflict as exc:
            raise Conflict() from exc
        fields = ["current_revision", "updated"]
        if "name" in validated_data:
            instance.name = validated_data["name"]
            fields.append("name")
        instance.save(update_fields=fields)
        return instance


class DestinationUpdateSerializer(DestinationSerializer):
    """出口の更新。種類・範囲・部門は変えられない"""

    extension = ExtensionField(ExtensionType.NOTIFICATION, ExtensionType.CHANNEL, read_only=True)
    scope = serializers.ChoiceField(choices=DestinationScope.choices, read_only=True)
    department = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta(DestinationSerializer.Meta):
        pass

    def get_fields(self):
        # 部分更新でも、省いた項目に既定値を入れて今の版を上書きしないようにする
        fields = super().get_fields()
        for field in fields.values():
            if not field.read_only:
                field.required = False
                field.default = serializers.empty
        return fields


class DestinationSecretSerializer(DestinationSerializer):
    """作成・署名の鍵の作り直しの応答。鍵はこのときだけ返す（再表示はできない）"""

    signing_secret = serializers.CharField(
        source="issued_secret",
        read_only=True,
        help_text=_("署名の鍵（Standard Webhooksの webhook-signature を確かめる鍵）。いま一度だけ表示する"),
    )

    class Meta(DestinationSerializer.Meta):
        fields = [*DestinationSerializer.Meta.fields, "signing_secret"]


class DestinationStateSerializer(serializers.Serializer):
    """停止・再開・署名の鍵の作り直し（本文なし）"""

    def update(self, instance: Destination, validated_data) -> Destination:
        if "suspended" in validated_data:
            current = Destination.objects.select_for_update(of=("self",)).get(pk=instance.pk)
            current.suspended = validated_data["suspended"]
            current.save(update_fields=["suspended", "updated"])
            instance.suspended = current.suspended
        if validated_data.get("rotate_secret"):
            instance.issued_secret = instance.rotate_secret()  # type: ignore[attr-defined]
        return instance

    def create(self, validated_data):
        raise NotImplementedError


class DestinationRevisionSerializer(ReadOnlyModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    url_display = serializers.CharField(read_only=True)
    created_by = MinimumUserSerializer(read_only=True)

    class Meta:
        model = DestinationRevision
        fields = [
            "id",
            "number",
            "url_display",
            "allows_sensitive_results",
            "channel_id",
            "channel_name",
            "change_reason",
            "created_by",
            "created",
        ]


class DestinationChoiceSerializer(ReadOnlyModelSerializer):
    """タスクの編集画面で選ぶ出口の候補（ID・名前・種類・範囲だけ。送り先・鍵は返さない。authorization.md §7.2）"""

    id = serializers.CharField(source="uid", read_only=True)
    extension = serializers.CharField(read_only=True)
    extension_name = serializers.CharField(read_only=True)
    is_external = serializers.BooleanField(read_only=True, help_text=_("外へ送る種類か（宛先のURL・対話の出口）"))
    is_channel = serializers.BooleanField(read_only=True, help_text=_("対話の出口（Slack等のチャネル）か"))
    scope = serializers.ChoiceField(choices=DestinationScope.choices, read_only=True)
    department = MinimumDepartmentSerializer(read_only=True, allow_null=True)
    allows_sensitive_results = serializers.BooleanField(
        source="current_revision.allows_sensitive_results", read_only=True
    )

    class Meta:
        model = Destination
        fields = [
            "id",
            "name",
            "extension",
            "extension_name",
            "is_external",
            "is_channel",
            "scope",
            "department",
            "allows_sensitive_results",
        ]


class FileFetchSerializer(ReadOnlyModelSerializer):
    """出口へ渡したファイルの取得の記録（身に覚えのない取得に気づけるように）"""

    file = serializers.CharField(source="link.artifact.name", read_only=True)

    class Meta:
        model = FileFetch
        fields = ["file", "served", "refusal", "ip_address", "user_agent", "created"]


class DeliveryAttemptSerializer(ReadOnlyModelSerializer):
    outcome_display = serializers.CharField(source="get_outcome_display", read_only=True, allow_null=True)
    file_fetches = serializers.SerializerMethodField(help_text=_("この送信で渡したファイルの取得の記録"))

    class Meta:
        model = DeliveryAttempt
        fields = [
            "number",
            "outcome",
            "outcome_display",
            "http_status",
            "error",
            "started",
            "finished",
            "file_fetches",
        ]

    @extend_schema_field(FileFetchSerializer(many=True))
    def get_file_fetches(self, attempt: DeliveryAttempt) -> list[dict]:
        fetches = [fetch for link in attempt.file_links.all() for fetch in link.fetches.all()]
        return list(FileFetchSerializer(sorted(fetches, key=lambda f: (f.created, f.pk)), many=True).data)


class DeliverySerializer(ReadOnlyModelSerializer):
    """タスクのRunの配信の履歴（タスクを管理できる人。authorization.md §7.1）。送った内容と試行の記録を返す"""

    id = serializers.CharField(source="uid", read_only=True)
    run = serializers.CharField(source="run.uid", read_only=True)
    event_type = serializers.ChoiceField(choices=NotifyEvent.choices + ChannelEvent.choices, read_only=True)
    status = serializers.ChoiceField(choices=DeliveryStatus.choices, read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    destination = DestinationChoiceSerializer(source="destination_revision.destination", read_only=True)
    destination_version = serializers.IntegerField(source="destination_revision.number", read_only=True)
    attempts = DeliveryAttemptSerializer(many=True, read_only=True)

    class Meta:
        model = Delivery
        fields = [
            "id",
            "run",
            "event_type",
            "status",
            "status_display",
            "destination",
            "destination_version",
            "payload",
            "attempt_count",
            "next_attempt",
            "last_error",
            "attempts",
            "created",
            "updated",
            "delivered",
        ]


class NotificationSerializer(ReadOnlyModelSerializer):
    """画面の通知（本人の通知欄）。タスクの名前とRunの状態と、通知の書き方に従って決めた通知の文章（なければ空文字）を返す"""

    id = serializers.CharField(source="uid", read_only=True)
    event_type = serializers.ChoiceField(choices=NotifyEvent.choices, read_only=True)
    event_display = serializers.CharField(source="get_event_type_display", read_only=True)
    task = serializers.CharField(source="task.uid", read_only=True)
    run = serializers.CharField(source="run.uid", read_only=True)

    class Meta:
        model = Notification
        fields = ["id", "event_type", "event_display", "task", "task_name", "message", "run", "read", "created"]


class NotificationCountSerializer(serializers.Serializer):
    unread = serializers.IntegerField(read_only=True)


# 連携の接続の資格情報として受け取るもの。種類ごとに使うものはPluginの宣言（Extension.secrets）で決まる
CHANNEL_SECRET_FIELDS = ("bot_token", "signing_secret", "public_key", "guild_id")


class ChannelListenerStatusSerializer(serializers.Serializer):
    """常時接続で受ける接続（Discord）の受信の状態（channel-listener。design.md §10）"""

    listens = serializers.BooleanField(help_text=_("channel-listenerで受ける種類か"))
    active = serializers.BooleanField(help_text=_("いずれかのlistenerが担当している（期限の中）"))
    owner = serializers.CharField(help_text=_("担当のlistener（ホスト名:プロセス）"))
    connected = serializers.DateTimeField(allow_null=True, help_text=_("最後につないだ日時"))
    error = serializers.CharField(help_text=_("つなげなかった理由（つないだら空）"))


class ChannelConnectionSerializer(serializers.ModelSerializer):
    """連携の接続（Slackのアプリ、DiscordのBotとサーバー等。連携の管理者。design.md §4.6、authorization.md §5.5）。

    資格情報は書くだけで返さない。作るときと資格情報を変えるときに接続を確かめ（Slackは `auth.test`）、Workspace等と
    botのユーザーIDを保存する。別のWorkspaceの資格情報への変更は断る（作ったスレッドの照合が合わなくなるため）。
    `receive_url` は、チャットツールのアプリに設定する受け口（Slackでは Event Subscriptions と Interactivity、
    Discordでは Interactions Endpoint URL）"""

    id = serializers.CharField(source="uid", read_only=True)
    extension = ExtensionField(ExtensionType.CHANNEL)
    extension_name = serializers.CharField(read_only=True)
    name = serializers.CharField(max_length=100)
    bot_token = serializers.CharField(
        write_only=True, required=False, allow_blank=True, help_text=_("Bot User OAuth Token（xoxb-）。返さない")
    )
    signing_secret = serializers.CharField(
        write_only=True, required=False, allow_blank=True, help_text=_("Signing Secret（Slack）。返さない")
    )
    public_key = serializers.CharField(
        write_only=True, required=False, allow_blank=True, help_text=_("Public Key（Discord）。返さない")
    )
    guild_id = serializers.CharField(
        write_only=True, required=False, allow_blank=True, help_text=_("サーバーのID（Discord）。返さない")
    )
    listener = serializers.SerializerMethodField(help_text=_("受信の状態（channel-listenerで受ける種類）"))
    receive_url = serializers.SerializerMethodField(help_text=_("チャットツールのアプリに設定する受け口のURL"))
    created_by = MinimumUserSerializer(read_only=True)

    class Meta:
        model = ChannelConnection
        fields = [
            "id",
            "extension",
            "extension_name",
            "name",
            "bot_token",
            "signing_secret",
            "public_key",
            "guild_id",
            "workspace_id",
            "workspace_name",
            "bot_user_id",
            "suspended",
            "receive_url",
            "listener",
            "created_by",
            "created",
            "updated",
        ]
        read_only_fields = ["workspace_id", "workspace_name", "bot_user_id", "suspended", "created", "updated"]

    def get_receive_url(self, connection: ChannelConnection) -> str:
        from django.conf import settings  # noqa: PLC0415

        path = f"/api/hooks/channels/{connection.extension.rsplit('.', 1)[-1]}/{connection.uid}/"
        return (settings.YORIKI_PUBLIC_URL or "") + path

    @extend_schema_field(ChannelListenerStatusSerializer)
    def get_listener(self, connection: ChannelConnection) -> dict:
        from django.utils import timezone  # noqa: PLC0415

        return {
            "listens": registry.has_capability(connection.extension, builtin.LISTEN),
            "active": bool(connection.listener_until and connection.listener_until > timezone.now()),
            "owner": connection.listener_owner,
            "connected": connection.listener_connected,
            "error": connection.listener_error,
        }

    def validate(self, attrs):
        extension = attrs.get("extension") or (self.instance.extension if self.instance else "")
        if "name" in attrs:
            attrs["name"] = attrs["name"].strip()
        secrets = self.instance.secret_values() if self.instance else {}
        found = registry.declared(extension)
        accepted = set(found[0].secrets) if found is not None else set()
        changed = False
        for key in CHANNEL_SECRET_FIELDS:
            value = str(attrs.pop(key, "") or "").strip()
            if value and key in accepted:
                secrets[key], changed = value, True
        if self.instance is None or changed:
            from .channels.outbound import channel_adapter  # noqa: PLC0415  channelsがserializerのModelを使うため

            try:
                info = channel_adapter(extension).check_connection(secrets)
            except PluginUnavailable as exc:
                raise serializers.ValidationError({"extension": _("この種類は今は使えません（停止中等）")}) from exc
            except PluginError as exc:
                raise serializers.ValidationError({"bot_token": str(exc)}) from exc
            if self.instance is not None and info.workspace_id != self.instance.workspace_id:
                raise serializers.ValidationError(
                    {
                        "bot_token": _(
                            "別のWorkspace・サーバーの資格情報です。別のWorkspace・サーバーには新しい接続を作ってください"
                        )
                    }
                )
            attrs.update(
                secrets=json.dumps(secrets),
                workspace_id=info.workspace_id,
                workspace_name=info.workspace_name,
                bot_user_id=info.bot_user_id,
            )
        return attrs


class ChannelConnectionUpdateSerializer(ChannelConnectionSerializer):
    """連携の接続の変更。種類は変えられない。資格情報は送ったものだけを変える"""

    extension = ExtensionField(ExtensionType.CHANNEL, read_only=True)
    name = serializers.CharField(max_length=100, required=False)


class ChannelConnectionChoiceSerializer(ReadOnlyModelSerializer):
    """対話の出口の作成で選ぶ接続（名前と種類とWorkspaceだけ）"""

    id = serializers.CharField(source="uid", read_only=True)
    extension = serializers.CharField(read_only=True)

    class Meta:
        model = ChannelConnection
        fields = ["id", "name", "extension", "workspace_name", "suspended"]
