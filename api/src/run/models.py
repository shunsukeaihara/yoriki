"""実行（Run）とその記録（design.md §5、harness.md §4・§5）。

Runの状態はWorkerとRunnerが進める。APIが受け付けるのは依頼（停止、回答、追加指示）だけで、状態はその結果として
Modelのメソッドの中で変わる。RunEventは追記だけの記録で、モデルへの入力はすべてここから組み立てる。
"""

import hashlib
import json
import logging
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import models, transaction
from django.db.models import Exists, Max, OuterRef, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from artifact.storage import StorageError, get_storage
from utils.fields import NanoidField

from .errors import AlreadyAnswered, InvalidTransition, LeaseLost

logger = logging.getLogger(__name__)


class RunStatus(models.IntegerChoices):
    QUEUED = 1, _("順番待ち")
    RUNNING = 2, _("作業中")
    WAITING_USER = 3, _("確認待ち")
    RETRY_WAIT = 4, _("再試行待ち")
    CANCELLING = 5, _("停止中")
    WAITING_BACKGROUND = 6, _("裏で作業を待っています")
    COMPLETED = 10, _("完了")
    FAILED = 20, _("失敗")
    CANCELLED = 30, _("停止済み")


ALLOWED_TRANSITIONS: dict[int, set[int]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.WAITING_USER,
        RunStatus.WAITING_BACKGROUND,
        RunStatus.RETRY_WAIT,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLING,
    },
    # ジョブが動いている間はAttemptを手放さないので、回答で同じAttemptへ戻る・停止中を経る（design.md §5.3）
    RunStatus.WAITING_USER: {
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.CANCELLING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    },
    # サブエージェントだけを待つ間はAttemptを手放すので、発言・子の完了で順番待ちへ戻る・そのまま停止する
    # （design.md §5.3）
    RunStatus.WAITING_BACKGROUND: {
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.RETRY_WAIT,
        RunStatus.CANCELLING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    },
    RunStatus.RETRY_WAIT: {RunStatus.QUEUED, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.CANCELLING: {RunStatus.CANCELLED, RunStatus.FAILED},
}

CANCELLABLE_STATUSES = (
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.WAITING_USER,
    RunStatus.WAITING_BACKGROUND,
    RunStatus.RETRY_WAIT,
)
ACTIVE_STATUSES = (*CANCELLABLE_STATUSES, RunStatus.CANCELLING)
TERMINAL_STATUSES = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)

# Attemptがleaseを更新しないまま、これだけ経つとReconcilerが復旧の対象にする
LEASE_SECONDS = 90
# 障害でAttemptが終わったときに、やり直す回数の上限
MAX_ATTEMPTS = 3


class RunTrigger(models.IntegerChoices):
    CONVERSATION = 1, _("会話")
    TASK = 2, _("タスク")
    EVALUATION = 3, _("Skillの評価")


class RunLaunch(models.IntegerChoices):
    """タスクのRunの起動のしかた（authorization.md §6）。会話・評価のRunでは空"""

    MANUAL = 1, _("タスクの画面からの手動実行")
    WEBHOOK = 2, _("Webhook")
    CHAT = 3, _("チャットからの起動")
    # タスクの修正チャット（design.md §11.11）。タスクを管理できる人が、会話の中で動かす。出口へは送らない
    FIX = 4, _("修正チャット")


class RunKind(models.IntegerChoices):
    """Runの種類（harness.md §7.5の1）。Skill管理のToolは、Skillの編集のRunにだけ出す（design.md §3.6）。
    タスク管理のToolは、修正チャットのタスクの編集のRunにだけ出す（design.md §11.11）"""

    WORK = 1, _("業務")
    SKILL_EDIT = 2, _("Skillの編集")
    EVALUATION = 3, _("Draftの評価")
    TASK_EDIT = 4, _("タスクの編集")


class RunEndReason(models.IntegerChoices):
    """Runの終わり方（harness.md §4.3）"""

    COMPLETED = 1, _("完了")
    CANCELLED = 2, _("停止")
    STEP_LIMIT = 10, _("ステップ数の上限")
    BUDGET = 11, _("予算の上限")
    CONTEXT = 12, _("文脈に収まらない")
    PROVIDER = 13, _("モデルにつながらない")
    REPEATED_ERRORS = 14, _("同じ誤りの繰返し")
    NO_RESULT = 15, _("業務結果がない")
    ATTEMPTS = 16, _("再試行の上限")
    EXPIRED = 17, _("回答期限切れ")
    SANDBOX = 18, _("実行環境を使えない")
    SKILL = 19, _("Skillを使えない")
    TASK = 20, _("タスクを続けられない")
    INTERNAL = 99, _("内部のエラー")


def _visible_condition(user) -> Q:
    from task.models import manageable_condition  # noqa: PLC0415  taskのModelがrunを参照するため

    own = Q(owner=user, conversation__owner=user, conversation__deleted=False) & ~Q(trigger=RunTrigger.TASK)
    task = Q(trigger=RunTrigger.TASK) & manageable_condition(
        user, scope_field="task_scope", owner_field="owner", department_field="task_department"
    )
    return own | task


class RunQuerySet(models.QuerySet):
    def visible_to(self, user) -> "RunQuerySet":
        """結果とログ（イベント・確認・実行中の進み具合）を見られるRun（authorization.md §7.1）。会話・評価のRunは
        依頼した人。タスクのRunは、実行したときのタスクの範囲と部門を管理できる人（個人のタスクは本人）で、タスクの
        今の範囲では判定しない"""
        if not user.is_active:
            return self.none()
        return self.filter(_visible_condition(user))

    def results_visible_to(self, user) -> "RunQuerySet":
        """結果（状態、最後の応答、業務結果、成果物）だけでも見られるRun。結果とログを見られるRun（`visible_to()`）に、
        チャットから起動した人の、自分が起動したRunを足す（起動した後に権限を失っても見られる。
        authorization.md §7.1）"""
        if not user.is_active:
            return self.none()
        return self.filter(_visible_condition(user) | Q(trigger=RunTrigger.TASK, launch=RunLaunch.CHAT, owner=user))

    def with_logs_visible(self, user) -> "RunQuerySet":
        """ログも見られるか（`logs_visible()`）を注釈する。結果だけを見られるRun（`results_visible_to()`）で使う"""
        return self.annotate(logs_visible=Exists(Run.objects.visible_to(user).filter(pk=OuterRef("pk"))))


def logs_visible(run: "Run") -> bool:
    """`RunQuerySet.with_logs_visible()` で読んだRunのログを見られるか。注釈がなければ見られないとする"""
    return bool(getattr(run, "logs_visible", False))


class Run(models.Model):
    uid = NanoidField(unique=True)
    conversation = models.ForeignKey(
        "conversation.Conversation", on_delete=models.PROTECT, related_name="runs", verbose_name=_("会話")
    )
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children", verbose_name=_("親の実行")
    )
    trigger = models.PositiveSmallIntegerField(
        _("起動方法"), choices=RunTrigger.choices, default=RunTrigger.CONVERSATION
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="runs", verbose_name=_("実行主体")
    )
    kind = models.PositiveSmallIntegerField(_("種類"), choices=RunKind.choices, default=RunKind.WORK)
    status = models.PositiveSmallIntegerField(_("状態"), choices=RunStatus.choices, default=RunStatus.QUEUED)
    # 今使っているモデル設定の版とeffort。開始時に固定し、利用者が切り替えたときだけ変わる（Manifestにも追記する）
    model_version = models.ForeignKey(
        "llm.ModelProfileVersion", on_delete=models.PROTECT, related_name="+", verbose_name=_("モデル設定の版")
    )
    effort = models.CharField(_("effort"), max_length=50, blank=True)
    # 開始時に固定したRuntime Profileの版（design.md §7.3）。既定がなければRunを始めないので、空なのは
    # この項目を足す前のRunだけ
    runtime_version = models.ForeignKey(
        "runtime.RuntimeProfileVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Runtime Profileの版"),
    )
    handles_sensitive_data = models.BooleanField(_("個人情報・機密情報を扱う"), default=False)
    # 開始時に固定したもの（fixed）と、実行中に追記したもの（appended。順序と時刻つき）。design.md §3.2
    manifest = models.JSONField(_("Manifest"), default=dict)
    # 業務結果。SkillかTask Definitionが業務結果のSchemaを持つRunで、submit_result が確定する（harness.md §10.5）
    result = models.JSONField(_("業務結果"), null=True, blank=True)
    # 通知の候補。通知の書き方のあるタスクのRunで、prepare_notification が決める（`{"message": "...", "files": [...]}`。
    # `files` は任意）。完了時の配信の内容に載せる（design.md §4.3）
    notification = models.JSONField(_("通知の候補"), null=True, blank=True)
    end_reason = models.PositiveSmallIntegerField(_("終わり方"), choices=RunEndReason.choices, null=True, blank=True)
    error = models.TextField(_("失敗の理由"), blank=True)
    step_count = models.PositiveIntegerField(_("ステップ数"), default=0)
    # タスクのRun：受付時のタスクの版と起動のしかた、実行したときのタスクの範囲と部門（結果・ログを見られる人の
    # 判定に使う。authorization.md §7.1）
    task = models.ForeignKey(
        "task.TaskDefinition",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="runs",
        verbose_name=_("タスク"),
    )
    task_revision = models.ForeignKey(
        "task.TaskDefinitionRevision",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("タスクの版"),
    )
    launch = models.PositiveSmallIntegerField(_("起動のしかた"), choices=RunLaunch.choices, null=True, blank=True)
    task_scope = models.PositiveSmallIntegerField(_("実行したときのタスクの範囲"), null=True, blank=True)
    task_department = models.ForeignKey(
        "user.Department",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("実行したときのタスクの部門"),
    )
    # 予算の計上先。受付時に決めて変えない（design.md §9.1）
    budget = models.ForeignKey(
        "usage.Budget", on_delete=models.PROTECT, related_name="runs", verbose_name=_("予算の計上先")
    )
    # 受付時に固定した結果の通知先の版（タスクの版で通知を設定したとき。authorization.md §7.2）
    destination_revision = models.ForeignKey(
        "delivery.DestinationRevision",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("結果の通知先の版"),
    )

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    finished = models.DateTimeField(_("終了日時"), null=True, blank=True)

    objects = RunQuerySet.as_manager()

    class Meta:
        verbose_name = _("実行")
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=["status", "-created"]),
            models.Index(fields=["conversation", "-created"]),
            models.Index(fields=["task", "-created"]),
        ]

    def __str__(self):
        return self.uid

    def can_transit(self, status: int) -> bool:
        return status in ALLOWED_TRANSITIONS.get(self.status, set())

    def has_live_attempt(self) -> bool:
        """親がWorkerを手放していないAttemptがある（Runの行ロックの中で呼ぶ）。leaseの切れたものはWorkerが落ちたものとして
        数えない（Reconcilerが閉じる）。サブエージェントのAttemptは数えない（`has_live_subagents()`）"""
        return self.attempts.filter(
            subagent__isnull=True, ended__isnull=True, lease_expires__gt=timezone.now()
        ).exists()

    def has_live_subagents(self) -> bool:
        """動いている・動き出すサブエージェントがある（Runの行ロックの中で呼ぶ）。止めた子も、Workerが止まったことを
        確かめるまで（子のAttemptが終わるまで）は動いているものとして数える（design.md §5.3）"""
        if self.subagents.filter(status__in=LIVE_SUBAGENT_STATUSES).exists():
            return True
        return self.attempts.filter(
            subagent__isnull=False, ended__isnull=True, lease_expires__gt=timezone.now()
        ).exists()

    def settle_cancelling(self) -> None:
        """停止中で、親もサブエージェントも全部止まったなら、停止済みにする（Runの行ロックの中で呼ぶ。
        design.md §5.3）"""
        if self.status == RunStatus.CANCELLING and not self.has_live_attempt() and not self.has_live_subagents():
            self.set_status(RunStatus.CANCELLED, comment=str(_("停止しました。")), end_reason=RunEndReason.CANCELLED)

    def wake_for_background(self) -> None:
        """サブエージェントが終わった・発言が届いた（Runの行ロックの中で呼ぶ）。サブエージェントだけを待ってWorkerを
        手放した親を、順番待ちに戻して起こす。手放していない親は、見張りの中で気づく（design.md §5.3）"""
        from .dispatch import enqueue_run  # noqa: PLC0415  dispatchがこのModelをimportするため

        if self.status == RunStatus.WAITING_BACKGROUND and not self.has_live_attempt():
            self.set_status(RunStatus.QUEUED, comment=str(_("裏の作業の続きを始めます。")))
            enqueue_run(self)
        elif self.status == RunStatus.CANCELLING:
            self.settle_cancelling()

    def is_cancellable(self) -> bool:
        return self.status in CANCELLABLE_STATUSES

    @property
    def is_interactive(self) -> bool:
        """答える人がいる（会話のRunと、修正チャットのタスクのRun）。ほかのタスクのRunは、対話Bindingがある場合だけ
        （後で加える）。答える人がいないRunは続きがないので、Workspaceを保存しない（design.md §7.9・§8）"""
        return self.trigger == RunTrigger.CONVERSATION or self.is_task_fix

    @property
    def is_task_fix(self) -> bool:
        """修正チャットのタスクのRun（design.md §11.11）"""
        return self.trigger == RunTrigger.TASK and self.launch == RunLaunch.FIX

    @property
    def isolated_workspace(self) -> bool:
        """/workspace/input・成果物の回収・Checkpointを、会話ではなくこのRunの単位にする。タスクのRunは会話の文脈も
        Workspaceも引き継がない（修正チャットでは、同じ会話の前の実行のものを見せない。harness.md §10.10）"""
        return self.trigger == RunTrigger.TASK

    def fix_chat_runs(self) -> "RunQuerySet":
        """タスクの編集のRunが引き継ぐ、修正チャットのRun（直前のタスクのRunから、このRunまで）。添付と
        Connectorの結果はこの中のものだけを /workspace/input に置く（タスクのRunごとに名前が重なりうるため。
        harness.md §10.10）"""
        runs = Run.objects.filter(conversation_id=self.conversation_id, pk__lte=self.pk)
        last_task = runs.filter(trigger=RunTrigger.TASK).order_by("-pk").values_list("pk", flat=True).first()
        return runs.filter(pk__gte=last_task or 0)

    @classmethod
    def locked(cls, pk: int) -> "Run":
        """行ロックを取って読む。ロックの順序はいつも 会話 → Run → そのほか（Attempt、確認、依頼）にし、
        逆の順で取る経路を作らない（メッセージの投稿は会話から、Runnerの書込みはRunから入るため）"""
        from conversation.models import Conversation  # noqa: PLC0415  conversationのModelがrunを参照するため

        conversation_id = cls.objects.filter(pk=pk).values_list("conversation_id", flat=True).get()
        list(Conversation.objects.select_for_update().filter(pk=conversation_id).values_list("pk", flat=True))
        return cls.objects.select_for_update().get(pk=pk)

    @transaction.atomic
    def set_status(
        self,
        status: RunStatus,
        comment: str = "",
        actor=None,
        *,
        end_reason: RunEndReason | None = None,
        error: str = "",
    ) -> "RunEvent":
        """状態を変えてイベントを記録する。API・Worker・Runnerのどこから呼ぶ場合もこのメソッドを通す"""
        current = Run.locked(self.pk)
        if not current.can_transit(status):
            raise InvalidTransition(f"{current.get_status_display()} -> {RunStatus(status).label}")
        self.status = status
        fields = ["status", "updated"]
        if status in TERMINAL_STATUSES:
            self.finished = timezone.now()
            self.end_reason = end_reason or (
                RunEndReason.COMPLETED if status == RunStatus.COMPLETED else RunEndReason.CANCELLED
            )
            self.error = error
            fields += ["finished", "end_reason", "error"]
        self.save(update_fields=fields)
        if status in TERMINAL_STATUSES:
            self._close_subagents()
        event = RunEvent.record(
            self,
            RunEventKind.STATUS,
            {"status": status, "end_reason": self.end_reason if status in TERMINAL_STATUSES else None},
            comment=comment,
            actor=actor,
        )
        if status in TERMINAL_STATUSES:
            # 終わる直前に届いて取り込めなかった追加指示は、次の依頼として続けて始める（design.md §5.5）
            from conversation.dispatch import start_next_request  # noqa: PLC0415  conversationがrunをimportするため
            from conversation.models import InstructionKind, InstructionState, UserInstruction  # noqa: PLC0415

            unread = UserInstruction.objects.filter(
                run=self, kind=InstructionKind.CURRENT, state=InstructionState.RECEIVED
            )
            # 回答に添えた指定の本文は確認への回答なので、新しい依頼にはしない
            unread.filter(is_answer=True).update(state=InstructionState.DISCARDED, updated=timezone.now())
            unread.update(kind=InstructionKind.NEXT, run=None, updated=timezone.now())
            start_next_request(self.conversation)
            if self.destination_revision_id is not None:
                # 結果の通知の配信を、終わりの状態と同じtransactionで作る（design.md §4.3）
                from delivery.outbox import queue_deliveries  # noqa: PLC0415  deliveryがrunのModelを参照するため

                queue_deliveries(self, event)
            if self.kind in (RunKind.EVALUATION, RunKind.SKILL_EDIT):
                # 評価のRunが終わったら評価を進め、編集のRunが終わったら待っている評価を止める（design.md §3.7.1）
                from conversation.evaluation import on_run_finished  # noqa: PLC0415

                on_run_finished(self)
            if not self.is_interactive:
                transaction.on_commit(lambda: Checkpoint.discard_through(self))
            elif self.conversation.workspace_discarded is not None:
                # アーカイブ・削除した会話のRunが止まった。全部止まっていれば、ワークスペースを消す（design.md §8）
                conversation_id = self.conversation_id
                transaction.on_commit(lambda: Checkpoint.discard_closed(conversation_id))
        return event

    def _close_subagents(self) -> None:
        """Runが終わる。親が受け取っていない子の結果と、止めることになる子を記録に書き、次のRunの文脈に入れる
        （harness.md §5.6）。動いている子は次の区切りで止まる（書込みは親のRunの状態を確かめて断る）。一時のコピーは、
        子のWorkerかReconcilerが消す（harness.md §10.11）"""
        from harness.subagents import record_finished, unreported  # noqa: PLC0415  harnessがrunのModelを使うため

        for subagent in unreported(self):
            record_finished(self, subagent)
        live = Subagent.objects.filter(run=self, status__in=LIVE_SUBAGENT_STATUSES)
        if names := ", ".join(f"{s.uid} ({s.description})" for s in live.order_by("pk")):
            text = f"[These subagents were stopped when this work ended: {names}. Start them again if needed.]"
            RunEvent.record(self, RunEventKind.CONTEXT, {"text": text})
        live.update(status=SubagentStatus.CANCELLED, finished=timezone.now(), reported=True)

    @transaction.atomic
    def request_cancel(self, reason: str, actor) -> "RunEvent":
        """停止の依頼を受け付ける。実行中ならWorkerの停止確認を待つため停止中にする"""
        current = Run.locked(self.pk)
        ClarificationRequest.objects.filter(run=self, status=ClarificationStatus.OPEN).update(
            status=ClarificationStatus.CANCELLED
        )
        # 動き出す前のサブエージェントは、ここで止める（Workerはclaimしない）
        Subagent.objects.filter(run=self, status=SubagentStatus.QUEUED).update(
            status=SubagentStatus.CANCELLED, finished=timezone.now(), reported=True
        )
        # 確認待ち・裏の作業の待ちでも、ジョブかサブエージェントが動いていれば、全部止まったことを確かめてから停止済みに
        # する（design.md §5.3）
        if current.status == RunStatus.RUNNING or (
            current.status in (RunStatus.WAITING_USER, RunStatus.WAITING_BACKGROUND)
            and (current.has_live_attempt() or current.has_live_subagents())
        ):
            return self.set_status(RunStatus.CANCELLING, comment=reason, actor=actor)
        return self.set_status(RunStatus.CANCELLED, comment=reason, actor=actor, end_reason=RunEndReason.CANCELLED)

    @transaction.atomic
    def switch_model(self, version, effort: str, *, actor=None, trigger: str) -> "RunEvent":
        """モデル設定・effortを切り替える。次のステップから使う（design.md §4.8）。条件の確認は呼び出し側で行う"""
        current = Run.locked(self.pk)
        before = {"model_version": current.model_version_id, "effort": current.effort}
        self.model_version = version
        self.effort = effort
        self.manifest = current.manifest
        self.append_manifest(
            "model", {"model_version": version.pk, "effort": effort, "trigger": trigger, "before": before}
        )
        self.save(update_fields=["model_version", "effort", "manifest", "updated"])
        return RunEvent.record(
            self,
            RunEventKind.MODEL_SWITCHED,
            {"model_version": version.pk, "effort": effort, "trigger": trigger, "before": before},
            actor=actor,
        )

    def append_manifest(self, kind: str, value: dict[str, Any]) -> None:
        """Manifestへ追記する（保存は呼び出し側）。開始時に固定したものと区別し、順序と時刻を残す"""
        appended = self.manifest.setdefault("appended", [])
        appended.append({"kind": kind, "at": timezone.now().isoformat(), **value})

    def current_catalog(self, kind: str) -> dict[str, Any] | None:
        """今の一覧（`skill_catalog`・`connector_catalog`）。発言を取り込むときに作り直していれば、その最後のもの
        （harness.md §10.1・§10.2）"""
        manifest = self.manifest or {}
        for entry in reversed(manifest.get("appended", [])):
            if entry.get("kind") == kind:
                return entry["value"]
        return (manifest.get("fixed") or {}).get(kind)

    @contextmanager
    def fenced(self, attempt: "ExecutionAttempt"):
        """Attemptがまだ実行の権利を持つことを、行ロックの中で確かめる。Runnerの書込みはすべてこの中で行う"""
        with transaction.atomic():
            current = Run.locked(self.pk)
            if not attempt.holds(current):
                raise LeaseLost(f"run={self.uid} attempt={attempt.number}")
            self.status = current.status
            yield current


class SubagentStatus(models.IntegerChoices):
    QUEUED = 1, _("順番待ち")
    RUNNING = 2, _("作業中")
    COMPLETED = 10, _("完了")
    FAILED = 20, _("失敗")
    CANCELLED = 30, _("停止")


LIVE_SUBAGENT_STATUSES = (SubagentStatus.QUEUED, SubagentStatus.RUNNING)


class Subagent(models.Model):
    """親のワークスペースのコピーの上で、別のAttemptとして動く子（harness.md §10.11）。返すのはテキストと
    ファイルだけで、親は終わった子を `subagent.finished` として受け取る"""

    uid = NanoidField(unique=True)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="subagents", verbose_name=_("親の実行"))
    # 起動した `subagent` の呼出し
    tool_call_id = models.CharField(_("Tool呼出しのID"), max_length=200)
    description = models.CharField(_("説明"), max_length=200)
    prompt = models.TextField(_("指示"))
    # 起動したときに固定したモデル設定の版とeffort
    model_version = models.ForeignKey(
        "llm.ModelProfileVersion", on_delete=models.PROTECT, related_name="+", verbose_name=_("モデル設定の版")
    )
    effort = models.CharField(_("effort"), max_length=50, blank=True)
    status = models.PositiveSmallIntegerField(_("状態"), choices=SubagentStatus.choices, default=SubagentStatus.QUEUED)
    step_count = models.PositiveIntegerField(_("ステップ数"), default=0)
    # 親のワークスペースの一時のコピー（work・outputのtar）。子が終わったら消して空にする
    copy_storage = models.CharField(_("コピーの保存先"), max_length=100, blank=True)
    copy_key = models.CharField(_("コピーの保存先のkey"), max_length=300, blank=True)
    # 子が使うSkill（Manifestの項目の形）。起動したときの親の読込みを写し、子が `skill` で読み込んだものを足す。
    # 親のManifestには追記しない（harness.md §10.11）
    skills = models.JSONField(_("使うSkill"), default=list, blank=True)
    # コピーの時点の /workspace/output のファイルと中身（SHA-256）。増えた・変わったものだけを返す
    baseline = models.JSONField(_("コピーの時点の成果物"), default=dict, blank=True)
    result_text = models.TextField(_("返すテキスト"), blank=True)
    # 返すファイル（[{"path", "artifact"}]。path は親の /workspace/input/subagents/<子のID>/ からの相対パス）
    result_files = models.JSONField(_("返すファイル"), default=list, blank=True)
    error = models.TextField(_("失敗の理由"), blank=True)
    # 親に終わったことを知らせた（subagent.finished を記録した、または親が自分で止めた）
    reported = models.BooleanField(_("親に知らせた"), default=False)

    created = models.DateTimeField(auto_now_add=True)
    started = models.DateTimeField(_("開始日時"), null=True, blank=True)
    finished = models.DateTimeField(_("終了日時"), null=True, blank=True)

    class Meta:
        verbose_name = _("サブエージェント")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["run", "status"])]

    def __str__(self):
        return self.uid

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_SUBAGENT_STATUSES


class ExecutionAttempt(models.Model):
    """Run（親）かサブエージェントを1つのWorkerで実行する試み。numberがfencing tokenを兼ねる（design.md §6.3）。
    番号は親とサブエージェントごとに数える"""

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="attempts")
    subagent = models.ForeignKey(
        Subagent,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="attempts",
        verbose_name=_("サブエージェント"),
    )
    number = models.PositiveIntegerField(_("試行番号"))
    worker = models.CharField(_("Worker"), max_length=200, blank=True)
    lease_expires = models.DateTimeField(_("leaseの期限"))
    started = models.DateTimeField(auto_now_add=True)
    ended = models.DateTimeField(_("終了日時"), null=True, blank=True)
    failure = models.TextField(_("失敗の理由"), blank=True)
    # Sandboxを起動したときのWorkerのplatformと、使ったイメージ（platformのmanifestのdigest。design.md §7.7）
    sandbox_platform = models.CharField(_("Sandboxのplatform"), max_length=50, blank=True)
    sandbox_image = models.CharField(_("Sandboxのイメージ"), max_length=400, blank=True)

    class Meta:
        verbose_name = _("実行の試み")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["run", "subagent", "number"], name="uniq_attempt_number", nulls_distinct=False
            )
        ]

    def __str__(self):
        return f"{self.run_id}#{self.number}"

    @classmethod
    def start(cls, run: Run, worker: str = "") -> "ExecutionAttempt":
        """claimの中（Runの行ロックの中）で呼ぶ"""
        parents = run.attempts.filter(subagent__isnull=True)
        last = parents.aggregate(last=Max("number"))["last"] or 0
        parents.filter(ended__isnull=True).update(ended=timezone.now(), failure="superseded")
        return cls.objects.create(
            run=run, number=last + 1, worker=worker, lease_expires=timezone.now() + timedelta(seconds=LEASE_SECONDS)
        )

    @classmethod
    def start_subagent(cls, subagent: Subagent, worker: str = "") -> "ExecutionAttempt":
        """サブエージェントのclaimの中（Runの行ロックの中）で呼ぶ。子は自動で再試行しないので、Attemptは1つだけ"""
        return cls.objects.create(
            run_id=subagent.run_id,
            subagent=subagent,
            number=1,
            worker=worker,
            lease_expires=timezone.now() + timedelta(seconds=LEASE_SECONDS),
        )

    def holds(self, run: Run) -> bool:
        """このAttemptが最新で、終わっておらず、leaseが切れていない。Runは実行中か停止中か、ジョブを見張って
        待っている（確認待ちはWorkerを手放すときにAttemptを終えるので、終わっていなければ見張っている）。
        サブエージェントのAttemptは `_holds_subagent()`"""
        if self.subagent_id is not None:
            return self._holds_subagent(run)
        if run.status not in (
            RunStatus.RUNNING,
            RunStatus.CANCELLING,
            RunStatus.WAITING_USER,
            RunStatus.WAITING_BACKGROUND,
        ):
            return False
        latest = run.attempts.filter(subagent__isnull=True).aggregate(last=Max("number"))["last"]
        return latest == self.number and self._alive()

    def _holds_subagent(self, run: Run) -> bool:
        """子が動いていて（止められていない）、親のRunが続いている。停止中・終わったRunでは、子の操作と結果を断る
        （design.md §5.3）"""
        if run.status not in (RunStatus.RUNNING, RunStatus.WAITING_USER, RunStatus.WAITING_BACKGROUND):
            return False
        status = Subagent.objects.filter(attempts=self).values_list("status", flat=True).first()
        return status == SubagentStatus.RUNNING and self._alive()

    def _alive(self) -> bool:
        current = ExecutionAttempt.objects.filter(pk=self.pk).values("ended", "lease_expires").first()
        return current is not None and current["ended"] is None and current["lease_expires"] > timezone.now()

    def heartbeat(self) -> None:
        """leaseを延ばす。切れていたら LeaseLost"""
        updated = ExecutionAttempt.objects.filter(
            pk=self.pk, ended__isnull=True, lease_expires__gt=timezone.now()
        ).update(lease_expires=timezone.now() + timedelta(seconds=LEASE_SECONDS))
        if not updated:
            raise LeaseLost(f"attempt={self.pk}")

    def end(self, failure: str = "") -> None:
        ExecutionAttempt.objects.filter(pk=self.pk, ended__isnull=True).update(ended=timezone.now(), failure=failure)


class RunEventKind(models.IntegerChoices):
    """RunEventの種類（harness.md §5.2）"""

    STATUS = 1, _("状態の遷移")
    USER_MESSAGE = 10, _("利用者の発言")
    ASSISTANT_MESSAGE = 11, _("エージェントの応答")
    ASSISTANT_ATTEMPT = 12, _("失敗した応答")
    TOOL_RESULT = 21, _("Toolの結果")
    JOB_FINISHED = 22, _("ジョブの終了")
    REQUEST_HEADER = 30, _("要求の中身")
    CONTEXT = 31, _("実行時の文脈")
    COMPACTION = 32, _("文脈の圧縮")
    MODEL_SWITCHED = 40, _("モデルの切り替え")
    SKILL_LOADED = 41, _("Skillの読込み")
    CLARIFICATION_REQUESTED = 50, _("確認の質問")
    CLARIFICATION_ANSWERED = 51, _("確認への回答")
    LLM_RETRY = 60, _("モデルの再試行")
    SUBAGENT_FINISHED = 23, _("サブエージェントの終了")
    RUN_FINISHED = 90, _("実行の終了")


class RunEvent(models.Model):
    """Runの記録。追記だけで、作成後に変更・削除しない。モデルへの入力はここから組み立てる"""

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="events")
    # サブエージェントの記録なら、その子。sequenceは親と通しで、子の文脈は子の記録だけから組み立てる
    # （harness.md §10.11）
    subagent = models.ForeignKey(
        Subagent,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="events",
        verbose_name=_("サブエージェント"),
    )
    sequence = models.PositiveIntegerField(_("順序"))
    kind = models.PositiveSmallIntegerField(_("種別"), choices=RunEventKind.choices)
    # モデル用の内容と、表示に使う内容（種類ごとの形は harness/context.py が読む）
    data = models.JSONField(_("内容"), default=dict)
    comment = models.TextField(_("コメント"), blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, verbose_name=_("操作者")
    )

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("実行イベント")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["run", "sequence"], name="uniq_run_event_sequence")]

    def __str__(self):
        return f"{self.run_id}#{self.sequence}"

    @classmethod
    def record(cls, run: Run, kind: RunEventKind, data: dict[str, Any] | None = None, **kwargs) -> "RunEvent":
        """呼び出し側でRunの行ロックを取っていること。同じtransactionで画面へ知らせる（realtime.md §4）"""
        # streamはrunのModelを読むため、循環importを避けて関数内でimportする
        from stream.notifier import notify_run_event  # noqa: PLC0415

        last = cls.objects.filter(run=run).aggregate(last=Max("sequence"))["last"] or 0
        event = cls.objects.create(run=run, sequence=last + 1, kind=kind, data=data or {}, **kwargs)
        notify_run_event(event)
        return event


class ClarificationKind(models.IntegerChoices):
    QUESTION = 1, _("エージェントからの質問")
    MODEL_SWITCH = 2, _("モデルの切り替えの確認")
    SKILL_EDIT = 3, _("Skillの編集の確認")
    # test_skill_draft で評価の完了を待つ。利用者が答えるものではなく、評価が終わるとサーバーが結果を返す
    EVALUATION = 4, _("Draftの評価")
    # request_task_edit で、修正チャットのタスクの定義を直すかを確かめる（harness.md §10.10）
    TASK_EDIT = 5, _("タスクの編集の確認")


class ClarificationStatus(models.IntegerChoices):
    OPEN = 1, _("回答待ち")
    ANSWERED = 2, _("回答済み")
    EXPIRED = 3, _("期限切れ")
    CANCELLED = 4, _("取消")


class ClarificationRequest(models.Model):
    """利用者への確認（design.md §4.7、harness.md §10.3）。QUESTIONは `ask_user` のTool呼出しに対応する"""

    uid = NanoidField(unique=True)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="clarifications")
    kind = models.PositiveSmallIntegerField(_("種類"), choices=ClarificationKind.choices)
    tool_call_id = models.CharField(_("Tool呼出しのID"), max_length=200, blank=True)
    # [{"header", "question", "options": [{"label", "description", "value"}], "multi_select"}]
    questions = models.JSONField(_("質問"), default=list)
    # 確認の対象（種類ごと）。Skillの編集では {"skill", "name", "scope", "summary"}（harness.md §10.9）、
    # タスクの編集では {"task", "name", "summary"}（harness.md §10.10）
    context = models.JSONField(_("確認の対象"), default=dict, blank=True)
    # [{"selected": [label...], "text": ""}]（questionsと同じ順）
    answers = models.JSONField(_("回答"), default=list, blank=True)
    comment = models.TextField(_("補足"), blank=True)
    status = models.PositiveSmallIntegerField(
        _("状態"), choices=ClarificationStatus.choices, default=ClarificationStatus.OPEN
    )
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, verbose_name=_("回答者")
    )
    answered = models.DateTimeField(_("回答日時"), null=True, blank=True)
    expires = models.DateTimeField(_("回答期限"), null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("確認")
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.uid

    @transaction.atomic
    def accept_answer(
        self, answers: list[dict[str, Any]], comment: str, actor, *, attachments: list[dict[str, Any]] | None = None
    ) -> None:
        """回答を保存し、同じtransactionでRunを再キューする。`attachments` は回答に添えた添付の一覧"""
        from .dispatch import enqueue_run  # noqa: PLC0415  dispatchがこのModelをimportするため

        run = Run.locked(self.run_id)
        current = ClarificationRequest.objects.select_for_update().get(pk=self.pk)
        if current.status != ClarificationStatus.OPEN or run.status != RunStatus.WAITING_USER:
            raise AlreadyAnswered(self.uid)
        self.status = ClarificationStatus.ANSWERED
        self.answers = answers
        self.comment = comment
        self.answered_by = actor
        self.answered = timezone.now()
        self.save(update_fields=["status", "answers", "comment", "answered_by", "answered"])
        self.run = run
        data: dict[str, Any] = {"clarification": self.uid, "answers": answers, "comment": comment}
        if attachments:
            data["attachments"] = attachments
        RunEvent.record(run, RunEventKind.CLARIFICATION_ANSWERED, data, actor=actor)
        if self.kind == ClarificationKind.QUESTION:
            # ask_user の呼出しへの結果として記録する。呼出しの直後に結果がある形になる（harness.md §10.3）
            from harness.tools.ask_user import answer_result  # noqa: PLC0415  harnessがrunのModelを使うため

            RunEvent.record(run, RunEventKind.TOOL_RESULT, answer_result(self, actor, attachments or []))
            ToolInvocation.objects.filter(run=run, call_id=self.tool_call_id).update(
                status=ToolStatus.SUCCEEDED, ended=timezone.now()
            )
        elif self.kind == ClarificationKind.EVALUATION:
            # 評価が終わった。結果を test_skill_draft の呼出しへの結果として記録する（harness.md §10.9）
            from harness.tools.test_skill_draft import answer_result as evaluation_result  # noqa: PLC0415

            RunEvent.record(run, RunEventKind.TOOL_RESULT, evaluation_result(self))
            ToolInvocation.objects.filter(run=run, call_id=self.tool_call_id).update(
                status=ToolStatus.SUCCEEDED, ended=timezone.now()
            )
        elif self.kind == ClarificationKind.SKILL_EDIT:
            # 承認されたら、Skillの編集のRunを次の依頼として受け付ける（harness.md §10.9）
            from conversation.dispatch import accept_skill_edit  # noqa: PLC0415  conversationがrunをimportするため
            from harness.tools.request_skill_edit import answer_result as skill_edit_result  # noqa: PLC0415

            RunEvent.record(run, RunEventKind.TOOL_RESULT, skill_edit_result(self, actor))
            ToolInvocation.objects.filter(run=run, call_id=self.tool_call_id).update(
                status=ToolStatus.SUCCEEDED, ended=timezone.now()
            )
            accept_skill_edit(self, actor)
        elif self.kind == ClarificationKind.TASK_EDIT:
            # 承認されたら、タスクの編集のRunを次の依頼として受け付ける（harness.md §10.10）
            from conversation.dispatch import accept_task_edit  # noqa: PLC0415  conversationがrunをimportするため
            from harness.tools.request_task_edit import answer_result as task_edit_result  # noqa: PLC0415

            RunEvent.record(run, RunEventKind.TOOL_RESULT, task_edit_result(self, actor))
            ToolInvocation.objects.filter(run=run, call_id=self.tool_call_id).update(
                status=ToolStatus.SUCCEEDED, ended=timezone.now()
            )
            accept_task_edit(self, actor)
        else:
            from harness.recovery import apply_model_switch_answer  # noqa: PLC0415

            apply_model_switch_answer(run, self, actor)
        comment = (
            str(_("Draftの評価が終わりました"))
            if self.kind == ClarificationKind.EVALUATION
            else str(_("確認への回答を受け付けました"))
        )
        if run.has_live_attempt():
            # ジョブが動いていてWorkerを手放していない。同じAttempt・同じコンテナで続ける（design.md §5.3）。
            # Workerが落ちていれば、leaseが切れたときにReconcilerがやり直す
            run.set_status(RunStatus.RUNNING, comment=str(comment), actor=actor)
            return
        run.set_status(RunStatus.QUEUED, comment=str(comment), actor=actor)
        enqueue_run(run)


class ToolStatus(models.IntegerChoices):
    REQUESTED = 1, _("要求")
    RUNNING = 2, _("実行中")
    SUCCEEDED = 10, _("成功")
    FAILED = 20, _("失敗")
    CANCELLED = 30, _("実行せず")
    UNKNOWN = 40, _("結果不明")


# Skillのスクリプトからの呼出し（harness/script_connectors.py）のToolInvocationの `call_id` の始まり
SCRIPT_CALL_PREFIX = "script-"


class ToolInvocation(models.Model):
    """Toolの呼出し。実行の前に作り（harness.md §7.6）、結果の記録で終える"""

    uid = NanoidField(unique=True)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="tool_invocations")
    subagent = models.ForeignKey(
        Subagent, on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name=_("サブエージェント")
    )
    attempt = models.ForeignKey(ExecutionAttempt, on_delete=models.PROTECT, related_name="+")
    call_id = models.CharField(_("呼出しのID"), max_length=200)
    name = models.CharField(_("Tool"), max_length=100)
    arguments = models.JSONField(_("引数"), default=dict)
    status = models.PositiveSmallIntegerField(_("状態"), choices=ToolStatus.choices, default=ToolStatus.REQUESTED)
    value = models.JSONField(_("結果の値"), null=True, blank=True)
    error = models.TextField(_("エラー"), blank=True)
    # 外部の確定した変更を伴うToolだけ。送り先の重複のキーとして渡す（harness.md §11.1）
    operation_key = models.CharField(_("operation key"), max_length=100, blank=True)

    created = models.DateTimeField(auto_now_add=True)
    ended = models.DateTimeField(_("終了日時"), null=True, blank=True)

    class Meta:
        verbose_name = _("Toolの呼出し")
        verbose_name_plural = verbose_name
        # 呼出しのIDはプロバイダが付けるので、親と子で重なりうる
        constraints = [
            models.UniqueConstraint(
                fields=["run", "subagent", "call_id"], name="uniq_tool_call_id", nulls_distinct=False
            )
        ]

    def __str__(self):
        return f"{self.name}({self.call_id})"


class Checkpoint(models.Model):
    """Workspaceのsnapshotを確定した区切り（design.md §5.4・§7.9、harness.md §11.1）。作成後に変更しない。

    新しいAttemptも、同じ会話の次のRunも、agentセッションの最新のCheckpointからWorkspaceを戻す。残すのは確定した2世代で、
    新しいものを確定した後に、それより古いものを消す。snapshotの中身は保存先（artifact/storage.py）にあり、ここには場所と
    版だけを持つ
    """

    uid = NanoidField(unique=True)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="checkpoints")
    attempt = models.ForeignKey(ExecutionAttempt, on_delete=models.PROTECT, related_name="+")
    # このsnapshotに反映されているRunEventの最後のsequence
    sequence = models.PositiveIntegerField(_("RunEventの順序"))
    storage = models.CharField(_("保存先"), max_length=100)
    key = models.CharField(_("保存先のkey"), max_length=300)
    size = models.PositiveBigIntegerField(_("大きさ"))
    sha256 = models.CharField(_("SHA-256"), max_length=64)
    # 保存の前に止めたバックグラウンドのジョブ（[{"job_id", "command"}]）。再開のときにモデルに知らせる
    stopped_jobs = models.JSONField(_("止めたジョブ"), default=list, blank=True)
    # 確認待ちの間に終わり、まだモデルに知らせていないジョブ。新しいAttemptで知らせる（harness.md §8.3）
    finished_jobs = models.JSONField(_("終わったジョブ"), default=list, blank=True)

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Checkpoint")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["run", "-sequence"])]

    def __str__(self):
        return f"{self.run_id}@{self.sequence}"

    @classmethod
    def latest_for(cls, run: Run) -> "Checkpoint | None":
        return cls.restorable_for(run).first()

    @classmethod
    def restorable_for(cls, run: Run) -> models.QuerySet["Checkpoint"]:
        """このRunが戻せるCheckpointを新しい順に。Sandboxを使わなかったRunにはCheckpointがないので、会話の中でこのRun
        までのRunを探す。アーカイブ・削除でワークスペースを消すと決めた後は、その前に始めたRunのものを戻さない（消せずに
        行が残っていても。design.md §8）"""
        if run.isolated_workspace:
            # タスクのRunは、ほかのRunのWorkspaceを引き継がない（修正チャットの前の実行のものも）
            rows = cls.objects.filter(run=run).order_by("-sequence", "-pk")
        elif run.kind == RunKind.TASK_EDIT:
            # 修正チャットのタスクの編集のRunは、直前のタスクのRunから後のものだけ（直前のタスクのRunがCheckpointを
            # 残さなければ、さらに前の実行のものではなく空のWorkspaceから始める。harness.md §10.10）
            rows = cls.objects.filter(run__in=run.fix_chat_runs()).order_by("-run_id", "-sequence", "-pk")
        else:
            rows = cls.objects.filter(run__conversation_id=run.conversation_id, run_id__lte=run.pk).order_by(
                "-run_id", "-sequence", "-pk"
            )
        if (discarded := run.conversation.workspace_discarded) is not None:
            rows = rows.filter(run__created__gt=discarded)
        return rows

    @classmethod
    def discard_older(cls, checkpoint: "Checkpoint", *, restored: "Checkpoint | None" = None) -> None:
        """確定したCheckpointとその1つ前を残し、ほかのsnapshotを消す（design.md §7.9）。このAttemptが戻したもの
        （`restored`）があれば、それを1つ前として残す（新しい世代が見つからず古い世代から戻したときに、戻せたものを消さない）"""
        rows = cls.objects.filter(run__conversation_id=checkpoint.run.conversation_id, pk__lt=checkpoint.pk)
        previous = restored or rows.order_by("-pk").first()
        if previous is not None:
            cls._discard(rows.exclude(pk=previous.pk))

    @classmethod
    def discard_through(cls, run: Run) -> None:
        """Workspaceを保存しないRunが終わったときに、そのRunまでのsnapshotを消す（design.md §8）"""
        cls._discard(cls.objects.filter(run__conversation_id=run.conversation_id, run_id__lte=run.pk))

    @classmethod
    def discard_closed(cls, conversation_id: int) -> None:
        """アーカイブ・削除した会話のワークスペースを消す（design.md §8）。その時点より前に始めたRunが全部止まってから
        消す。止まっていなければ、最後のRunが終わるときに呼び直す（Run.set_status()）"""
        from conversation.models import Conversation  # noqa: PLC0415  conversationのModelがrunを参照するため

        discarded = Conversation.objects.filter(pk=conversation_id).values_list("workspace_discarded", flat=True).get()
        if discarded is None:
            return
        runs = Run.objects.filter(conversation_id=conversation_id, created__lte=discarded)
        if runs.exclude(status__in=TERMINAL_STATUSES).exists():
            return
        cls._discard(cls.objects.filter(run__in=runs))

    @classmethod
    def _discard(cls, checkpoints: models.QuerySet["Checkpoint"]) -> None:
        """実体を消せたものだけ行を消す。消せなかったものは行が残り、次に消すときにやり直す"""
        for checkpoint in checkpoints:
            try:
                get_storage(checkpoint.storage).delete(checkpoint.key)
            except (StorageError, OSError):
                logger.warning("cannot delete the snapshot of checkpoint %s", checkpoint.uid, exc_info=True)
                continue
            checkpoint.delete()


class RunPrompt(models.Model):
    """system promptとToolの定義の一覧。内容のhashをキーにした不変の行（harness.md §6.1）"""

    hash = models.CharField(_("hash"), max_length=64, unique=True)
    system = models.TextField(_("system prompt"))
    tools = models.JSONField(_("Toolの定義"), default=list)

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("実行のprompt")
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.hash[:12]

    @classmethod
    def store(cls, system: str, tools: list[dict[str, Any]]) -> "RunPrompt":
        body = json.dumps({"system": system, "tools": tools}, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(body.encode()).hexdigest()
        prompt, _created = cls.objects.get_or_create(hash=digest, defaults={"system": system, "tools": tools})
        return prompt


class RunDispatch(models.Model):
    """Runの実行の通知のOutbox。Runの状態と同じtransactionで記録し、commitの後にQ2へ送る（design.md §5.2）"""

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="dispatches")
    created = models.DateTimeField(auto_now_add=True)
    dispatched = models.DateTimeField(_("送信日時"), null=True, blank=True)

    class Meta:
        verbose_name = _("実行の通知")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["dispatched", "created"])]

    def __str__(self):
        return f"{self.run_id}@{self.created}"
