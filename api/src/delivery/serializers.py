from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from user.models import Department
from user.serializers import MinimumDepartmentSerializer, MinimumUserSerializer
from utils.exceptions import Conflict
from utils.serializers import ReadOnlyModelSerializer

from .adapters import adapter_for
from .errors import VersionConflict
from .models import (
    DESTINATION_REVISION_FIELDS,
    URL_KINDS,
    Delivery,
    DeliveryAttempt,
    DeliveryStatus,
    Destination,
    DestinationKind,
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
    kind = serializers.ChoiceField(choices=DestinationKind.choices, help_text=_("種類。作成した後は変えられない"))
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
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
            "機密（個人情報・機密情報を扱う）のタスクの結果を送ってよい。画面の通知は認証の要る画面への参照だけなので、"
            "この指定によらず機密のタスクにも使える"
        ),
    )
    change_reason = serializers.CharField(max_length=500, required=False, allow_blank=True, write_only=True)
    created_by = MinimumUserSerializer(read_only=True)

    class Meta:
        model = Destination
        fields = [
            "id",
            "kind",
            "kind_display",
            "scope",
            "scope_display",
            "department",
            "department_info",
            "name",
            "suspended",
            "version",
            "url",
            "url_display",
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
            kind = attrs["kind"]
        else:
            if "version_number" not in attrs:
                raise serializers.ValidationError({"version": _("編集を始めた時点の版を送ってください")})
            kind = self.instance.kind
        if "name" in attrs:
            attrs["name"] = attrs["name"].strip()
        sent = attrs.pop("current_revision", {})
        current = self.instance.current_revision if self.instance else None
        content = {}
        for name in DESTINATION_REVISION_FIELDS:
            if name in sent:
                content[name] = sent[name]
            elif name == "url":
                content[name] = current.url.reveal() if current else ""
            else:
                content[name] = getattr(current, name)
        self._check_url(kind, sent, content)
        attrs["content"] = content
        return attrs

    @staticmethod
    def _check_url(kind: int, sent: dict, content: dict) -> None:
        if kind not in URL_KINDS:
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
                resolve(adapter_for(kind).validate_url(content["url"]))
            except BlockedAddress as exc:
                raise serializers.ValidationError({"url": str(exc)}) from exc

    @transaction.atomic
    def create(self, validated_data) -> Destination:
        destination, secret = Destination.create_with_revision(
            kind=validated_data["kind"],
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

    kind = serializers.ChoiceField(choices=DestinationKind.choices, read_only=True)
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
        fields = ["id", "number", "url_display", "allows_sensitive_results", "change_reason", "created_by", "created"]


class DestinationChoiceSerializer(ReadOnlyModelSerializer):
    """タスクの編集画面で選ぶ出口の候補（ID・名前・種類・範囲だけ。送り先・鍵は返さない。authorization.md §7.2）"""

    id = serializers.CharField(source="uid", read_only=True)
    kind = serializers.ChoiceField(choices=DestinationKind.choices, read_only=True)
    scope = serializers.ChoiceField(choices=DestinationScope.choices, read_only=True)
    department = MinimumDepartmentSerializer(read_only=True, allow_null=True)
    allows_sensitive_results = serializers.BooleanField(
        source="current_revision.allows_sensitive_results", read_only=True
    )

    class Meta:
        model = Destination
        fields = ["id", "name", "kind", "scope", "department", "allows_sensitive_results"]


class FileFetchSerializer(ReadOnlyModelSerializer):
    """出口へ渡したファイルの取得の記録（身に覚えのない取得に気づけるように）"""

    file = serializers.CharField(source="link.artifact.name", read_only=True)

    class Meta:
        model = FileFetch
        fields = ["file", "served", "refusal", "ip_address", "user_agent", "created"]


class DeliveryAttemptSerializer(ReadOnlyModelSerializer):
    file_fetches = serializers.SerializerMethodField(help_text=_("この送信で渡したファイルの取得の記録"))

    class Meta:
        model = DeliveryAttempt
        fields = ["number", "outcome", "http_status", "error", "started", "finished", "file_fetches"]

    @extend_schema_field(FileFetchSerializer(many=True))
    def get_file_fetches(self, attempt: DeliveryAttempt) -> list[dict]:
        fetches = [fetch for link in attempt.file_links.all() for fetch in link.fetches.all()]
        return list(FileFetchSerializer(sorted(fetches, key=lambda f: (f.created, f.pk)), many=True).data)


class DeliverySerializer(ReadOnlyModelSerializer):
    """タスクのRunの配信の履歴（タスクを管理できる人。authorization.md §7.1）。送った内容と試行の記録を返す"""

    id = serializers.CharField(source="uid", read_only=True)
    run = serializers.CharField(source="run.uid", read_only=True)
    event_type = serializers.ChoiceField(choices=NotifyEvent.choices, read_only=True)
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
