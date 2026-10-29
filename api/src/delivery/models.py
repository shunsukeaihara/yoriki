"""タスクの結果の配信（design.md §4.3〜§4.5、authorization.md §5.5・§7）。

- 出口（Destination）：結果を送る先。連携の管理者（Modelのpermission。所属は問わない）が作り、使ってよい範囲（部門・
  全社・システム）を作成時に決めて変えない。送り先のURLと機密の結果を送ってよいかは不変の版（DestinationRevision）が
  持ち、保存のたびに新しい版を作る。署名の鍵は版に入れず、作り直すと次の送信から効く。
- タスクの版が、出口・対象のイベント・通知の内容を持つ（初期は1つのタスクに1つ。design.md §4.1）。Runは受付時に出口の
  版を固定し、新しい宛先へ自動で向けない（authorization.md §7.2）。
- 配信（Delivery）：Runが終わったときに、Runの状態と同じtransactionで作る（配信のOutbox）。送る内容はそのときに組み
  立てて保存し、再試行でLLMを動かし直さない。送るたびに DeliveryAttempt を記録する。送る直前に、出口が停止・削除・
  タスクの範囲の外・機密の結果を送れない、になっていないかを確かめ直し、そうなら送らない。
"""

import base64
import secrets
from datetime import timedelta
from typing import Any, Self
from urllib.parse import urlsplit

from auditlog.registry import auditlog
from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from user.models import Department
from utils.fields import EncryptedSecretField, NanoidField

from .errors import VersionConflict


class DestinationKind(models.IntegerChoices):
    """出口の種類。画面の通知（in_app）とTeams・Slack等は後で足す"""

    WEBHOOK = 1, _("Webhook")
    ZAPIER = 2, _("Zapier")


class DestinationScope(models.IntegerChoices):
    """使ってよい範囲。値はタスクの範囲と同じにする。個人の出口（画面の通知・本人へのDM）は後で足す"""

    DEPARTMENT = 2, _("部門")
    COMPANY = 3, _("全社")
    SYSTEM = 4, _("システム")


class NotifyEvent(models.TextChoices):
    """通知するイベント（design.md §4.5）。確認待ち（run.waiting_user）は対話の連携の段階で足す"""

    COMPLETED = "run.completed", _("完了")
    FAILED = "run.failed", _("失敗")
    CANCELLED = "run.cancelled", _("停止")


class NotifyContent(models.TextChoices):
    """通知の内容（design.md §4.3）"""

    STATUS = "status", _("状態だけ")
    RESULT = "result", _("業務結果")
    STATUS_AND_RESULT = "status_and_result", _("状態と業務結果")


# 署名の鍵の形（Standard Webhooks。`whsec_` とbase64の鍵）
SIGNING_SECRET_PREFIX = "whsec_"


class DestinationQuerySet(models.QuerySet):
    def alive(self) -> Self:
        return self.filter(deleted=False)


class Destination(models.Model):
    """出口（authorization.md §5.5）。部門の出口はその部門のタスク、全社の出口は全社のタスク、システムの出口は
    システムのタスクだけが使う（部門のデータを全社向けの出口へ流させない。authorization.md §7）"""

    uid = NanoidField(unique=True)
    kind = models.PositiveSmallIntegerField(_("種類"), choices=DestinationKind.choices)
    scope = models.PositiveSmallIntegerField(_("使ってよい範囲"), choices=DestinationScope.choices)
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("部門")
    )
    name = models.CharField(_("名前"), max_length=100)
    current_revision = models.ForeignKey(
        "DestinationRevision",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("今の版"),
    )
    # 停止。新しく受け付けるRunに使わず、まだ送っていない配信も送らない
    suspended = models.BooleanField(_("停止中"), default=False)
    # 送る要求の署名（Standard Webhooksの webhook-signature）に使う鍵。作成時と作り直したときに一度だけ返す
    signing_secret = EncryptedSecretField(_("署名の鍵"))
    secret_rotated = models.DateTimeField(_("署名の鍵を作った日時"), null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    objects = DestinationQuerySet.as_manager()

    class Meta:
        verbose_name = _("出口")
        verbose_name_plural = verbose_name
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(scope=DestinationScope.DEPARTMENT, department__isnull=False)
                    | Q(scope__in=[DestinationScope.COMPANY, DestinationScope.SYSTEM], department__isnull=True)
                ),
                name="destination_scope_department",
            ),
        ]

    def __str__(self):
        return self.name

    def in_service(self) -> bool:
        return not self.deleted and not self.suspended and self.current_revision_id is not None

    @property
    def version_number(self) -> int:
        return self.current_revision.number if self.current_revision else 0

    def allowed_for(self, task) -> bool:
        """このタスクの範囲で使ってよい出口か（停止・削除は見ない）。タスクは保存前でもよい"""
        if self.scope != task.scope:
            return False
        if self.scope != DestinationScope.DEPARTMENT:
            return True
        # 無効にした部門の出口は使わない（authorization.md §9）
        return self.department_id == task.department_id and self.department is not None and not self.department.deleted

    def problem_for(self, task, *, sensitive: bool) -> str:
        """このタスクのRunが今この出口へ送れない理由（送れれば空文字）"""
        if self.deleted:
            return gettext("出口が削除されました")
        if self.suspended or self.current_revision is None:
            return gettext("出口が停止中です")
        if task is None or not self.allowed_for(task):
            return gettext("出口がタスクの範囲で使えなくなりました")
        if sensitive and not self.current_revision.allows_sensitive_results:
            return gettext("出口が機密の結果を送ってよい先ではありません")
        return ""

    @staticmethod
    def new_secret() -> str:
        return SIGNING_SECRET_PREFIX + base64.b64encode(secrets.token_bytes(32)).decode()

    @classmethod
    @transaction.atomic
    def create_with_revision(
        cls,
        *,
        kind: DestinationKind,
        scope: DestinationScope,
        department: Department | None,
        name: str,
        actor,
        change_reason: str = "",
        content: dict[str, Any],
    ) -> tuple["Destination", str]:
        """出口と最初の版を作る。（出口、署名の鍵）。鍵はここでだけ返す"""
        secret = cls.new_secret()
        destination = cls.objects.create(
            kind=kind,
            scope=scope,
            department=department if scope == DestinationScope.DEPARTMENT else None,
            name=name,
            signing_secret=secret,
            secret_rotated=timezone.now(),
            created_by=actor,
        )
        destination.revise(actor=actor, expected_version=0, change_reason=change_reason, **content)
        return destination, secret

    @transaction.atomic
    def revise(self, *, actor, expected_version: int, change_reason: str = "", **fields: Any) -> "DestinationRevision":
        """中身を変えた新しい版を作り、今の版にする。次に受け付けるRunから使う（受け付けたRunは固定した版のまま）。
        競合は VersionConflict。中身が今の版と同じなら版を作らない"""
        current = Destination.objects.select_for_update(of=("self",)).select_related("current_revision").get(pk=self.pk)
        if current.deleted:
            raise VersionConflict("deleted")
        latest = current.current_revision
        latest_number = latest.number if latest else 0
        if latest_number != expected_version:
            raise VersionConflict(f"expected={expected_version} latest={latest_number}")
        if latest and latest.same_content(fields):
            self.current_revision = latest
            return latest
        revision = DestinationRevision.objects.create(
            destination=current, number=latest_number + 1, created_by=actor, change_reason=change_reason, **fields
        )
        current.current_revision = revision
        current.save(update_fields=["current_revision", "updated"])
        self.current_revision = revision
        return revision

    @transaction.atomic
    def rotate_secret(self) -> str:
        """署名の鍵を作り直す。次の送信から新しい鍵で署名する。新しい鍵はここでだけ返す"""
        secret = self.new_secret()
        current = Destination.objects.select_for_update(of=("self",)).get(pk=self.pk)
        current.signing_secret, current.secret_rotated = secret, timezone.now()
        current.save(update_fields=["signing_secret", "secret_rotated", "updated"])
        self.signing_secret, self.secret_rotated = current.signing_secret, current.secret_rotated
        return secret


# 出口の版に入れる項目。変えると新しい版になる
DESTINATION_REVISION_FIELDS = ("url", "allows_sensitive_results")


class DestinationRevision(models.Model):
    """出口の版。作成後に変更・削除しない。

    - `url`：送り先（ZapierのCatch HookのURL等）。URLそのものが送るための資格情報になる送り先があるので、暗号化して
      保存し、画面・ログ・LLMには出さない（design.md §4.4）
    - `allows_sensitive_results`：機密のタスクの結果を送ってよい（出口の機密のフラグ。design.md §4冒頭）
    """

    uid = NanoidField(unique=True)
    destination = models.ForeignKey(
        Destination, on_delete=models.PROTECT, related_name="revisions", verbose_name=_("出口")
    )
    number = models.PositiveIntegerField(_("版"))
    url = EncryptedSecretField(_("送り先のURL"))
    allows_sensitive_results = models.BooleanField(_("機密の結果を送ってよい"), default=False)
    change_reason = models.CharField(_("変更理由"), max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("保存した人")
    )

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("出口の版")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["destination", "number"], name="uniq_destination_revision_number")
        ]

    def __str__(self):
        return f"{self.destination} v{self.number}"

    def same_content(self, fields: dict[str, Any]) -> bool:
        # URLは暗号文が毎回変わるので、平文で比べる
        return (
            self.url.reveal() == str(fields["url"])
            and self.allows_sensitive_results == fields["allows_sensitive_results"]
        )

    @property
    def url_display(self) -> str:
        """画面に出すURL。パスにも資格情報が入りうるので、送り先のホストと末尾の4文字だけにする"""
        url = self.url.reveal()
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}/…{self.url.hint}" if url else ""


class DeliveryStatus(models.IntegerChoices):
    """配信の状態（design.md §4.5）。Runの完了とは別に持ち、「処理完了・通知失敗」を表す"""

    PENDING = 1, _("配信待ち")
    SENDING = 2, _("配信中")
    RETRY_WAIT = 3, _("再試行待ち")
    DELIVERED = 10, _("受付確認済み")
    FAILED = 20, _("失敗")
    # 送った後に応答が失われた等で、届いたか分からない。自動では送り直さない（authorization.md §6）
    UNKNOWN = 21, _("確認待ち")
    # 送る直前に、出口が停止・削除・範囲の外になっていた
    CANCELLED = 30, _("取消")


# 送る対象になる状態と、人が送り直しを依頼できる状態
SENDABLE_STATUSES = (DeliveryStatus.PENDING, DeliveryStatus.RETRY_WAIT)
RETRYABLE_BY_HAND = (DeliveryStatus.FAILED, DeliveryStatus.UNKNOWN, DeliveryStatus.CANCELLED)


class Delivery(models.Model):
    """1つのイベントの、1つの出口への配信（配信のOutboxを兼ねる）。

    配信のキーは「Runのイベント・出口の版」（design.md §4.5）。通知の設定はタスクの版にあり、Runが固定しているので
    キーに含めない。`payload` はRunが終わったときに組み立てた送る内容で、送り直しでも変えない（`event_id` も同じ）"""

    uid = NanoidField(unique=True)
    run = models.ForeignKey("run.Run", on_delete=models.PROTECT, related_name="deliveries", verbose_name=_("実行"))
    run_event = models.ForeignKey(
        "run.RunEvent", on_delete=models.PROTECT, related_name="+", verbose_name=_("Runのイベント")
    )
    event_type = models.CharField(_("イベント"), max_length=30, choices=NotifyEvent.choices)
    destination_revision = models.ForeignKey(
        DestinationRevision, on_delete=models.PROTECT, related_name="deliveries", verbose_name=_("出口の版")
    )
    status = models.PositiveSmallIntegerField(_("状態"), choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING)
    payload = models.JSONField(_("送る内容"))
    attempt_count = models.PositiveIntegerField(_("送った回数"), default=0)
    # 人が送り直しを依頼したときの送った回数。再試行の上限はここから数える
    retry_from = models.PositiveIntegerField(_("送り直しの起点"), default=0)
    next_attempt = models.DateTimeField(_("次に送る日時"), null=True, blank=True)
    # 配信中のWorkerの持ち時間。切れたら、届いたか分からないので確認待ちにする
    lease_expires = models.DateTimeField(_("配信中の期限"), null=True, blank=True)
    last_error = models.CharField(_("最後の誤り"), max_length=500, blank=True)

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    delivered = models.DateTimeField(_("受付確認の日時"), null=True, blank=True)

    class Meta:
        verbose_name = _("配信")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["run_event", "destination_revision"], name="uniq_delivery_key"),
        ]
        indexes = [models.Index(fields=["status", "next_attempt"])]

    def __str__(self):
        return f"{self.run_id} {self.event_type}"

    @transaction.atomic
    def retry(self) -> str:
        """人が送り直しを依頼する（失敗・確認待ち・取消のもの）。送る直前に出口を確かめ直す。受け付けたら空文字、
        受け付けなければ理由"""
        current = Delivery.objects.select_for_update().get(pk=self.pk)
        if current.status not in RETRYABLE_BY_HAND:
            return gettext("この配信は送り直せません（配信待ち・配信中・受付確認済み）")
        # 持ち時間が切れて確認待ちにした後も、元のWorkerがまだ送っているかもしれない。HTTPには送信全体の期限が
        # ないので、その試行が終わるか、Q2がWorkerを止める時間（Q_CLUSTER_TIMEOUT）を過ぎるまでは送り直さない
        running_since = timezone.now() - timedelta(seconds=settings.Q_CLUSTER_TIMEOUT)
        if current.attempts.filter(finished__isnull=True, started__gt=running_since).exists():
            return gettext("前の送信がまだ終わっていません。しばらくしてから送り直してください")
        current.status = DeliveryStatus.PENDING
        current.next_attempt = None
        current.retry_from = current.attempt_count
        current.save(update_fields=["status", "next_attempt", "retry_from", "updated"])
        self.status, self.next_attempt, self.retry_from = current.status, None, current.retry_from
        return ""


class AttemptOutcome(models.IntegerChoices):
    ACCEPTED = 1, _("受付確認")
    RETRYABLE = 2, _("一時的な失敗")
    FAILED = 3, _("失敗")
    UNKNOWN = 4, _("届いたか分からない")


class DeliveryAttempt(models.Model):
    """1回の送信の記録。応答の本文は保存しない（送り先の内部の情報を含みうるため）"""

    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="attempts")
    number = models.PositiveIntegerField(_("回"))
    outcome = models.PositiveSmallIntegerField(_("結果"), choices=AttemptOutcome.choices, null=True, blank=True)
    http_status = models.PositiveSmallIntegerField(_("HTTPの状態コード"), null=True, blank=True)
    error = models.CharField(_("誤り"), max_length=500, blank=True)
    started = models.DateTimeField(_("開始日時"), auto_now_add=True)
    finished = models.DateTimeField(_("終了日時"), null=True, blank=True)

    class Meta:
        verbose_name = _("配信の試行")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["delivery", "number"], name="uniq_delivery_attempt_number")]

    def __str__(self):
        return f"{self.delivery_id}#{self.number}"


auditlog.register(Destination, exclude_fields=["signing_secret", "updated"])
auditlog.register(DestinationRevision, exclude_fields=["url"])
