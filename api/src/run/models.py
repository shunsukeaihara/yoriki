"""実行（Run）とその記録（design.md §5、harness.md §4・§5）。

Runの状態はWorkerとRunnerが進める。APIが受け付けるのは依頼（停止、回答、追加指示）だけで、状態はその結果として
Modelのメソッドの中で変わる。RunEventは追記だけの記録で、モデルへの入力はすべてここから組み立てる。
"""

import hashlib
import json
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from utils.fields import NanoidField

from .errors import AlreadyAnswered, InvalidTransition, LeaseLost


class RunStatus(models.IntegerChoices):
    QUEUED = 1, _("順番待ち")
    RUNNING = 2, _("作業中")
    WAITING_USER = 3, _("確認待ち")
    RETRY_WAIT = 4, _("再試行待ち")
    CANCELLING = 5, _("停止中")
    COMPLETED = 10, _("完了")
    FAILED = 20, _("失敗")
    CANCELLED = 30, _("停止済み")


ALLOWED_TRANSITIONS: dict[int, set[int]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.WAITING_USER,
        RunStatus.RETRY_WAIT,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLING,
    },
    RunStatus.WAITING_USER: {RunStatus.QUEUED, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.RETRY_WAIT: {RunStatus.QUEUED, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.CANCELLING: {RunStatus.CANCELLED, RunStatus.FAILED},
}

CANCELLABLE_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_USER, RunStatus.RETRY_WAIT)
ACTIVE_STATUSES = (*CANCELLABLE_STATUSES, RunStatus.CANCELLING)
TERMINAL_STATUSES = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)

# Attemptがleaseを更新しないまま、これだけ経つとReconcilerが復旧の対象にする
LEASE_SECONDS = 90
# 障害でAttemptが終わったときに、やり直す回数の上限
MAX_ATTEMPTS = 3


class RunTrigger(models.IntegerChoices):
    CONVERSATION = 1, _("会話")
    TASK = 2, _("タスク")


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
    INTERNAL = 99, _("内部のエラー")


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
    end_reason = models.PositiveSmallIntegerField(_("終わり方"), choices=RunEndReason.choices, null=True, blank=True)
    error = models.TextField(_("失敗の理由"), blank=True)
    step_count = models.PositiveIntegerField(_("ステップ数"), default=0)

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    finished = models.DateTimeField(_("終了日時"), null=True, blank=True)

    class Meta:
        verbose_name = _("実行")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["status", "-created"]), models.Index(fields=["conversation", "-created"])]

    def __str__(self):
        return self.uid

    def can_transit(self, status: int) -> bool:
        return status in ALLOWED_TRANSITIONS.get(self.status, set())

    def is_cancellable(self) -> bool:
        return self.status in CANCELLABLE_STATUSES

    @property
    def is_interactive(self) -> bool:
        """答える人がいる（会話のRun）。タスクのRunは、対話Bindingがある場合だけ（後で加える）"""
        return self.trigger == RunTrigger.CONVERSATION

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

            UserInstruction.objects.filter(
                run=self, kind=InstructionKind.CURRENT, state=InstructionState.RECEIVED
            ).update(kind=InstructionKind.NEXT, run=None, updated=timezone.now())
            start_next_request(self.conversation)
        return event

    @transaction.atomic
    def request_cancel(self, reason: str, actor) -> "RunEvent":
        """停止の依頼を受け付ける。実行中ならWorkerの停止確認を待つため停止中にする"""
        current = Run.locked(self.pk)
        if current.status == RunStatus.RUNNING:
            return self.set_status(RunStatus.CANCELLING, comment=reason, actor=actor)
        ClarificationRequest.objects.filter(run=self, status=ClarificationStatus.OPEN).update(
            status=ClarificationStatus.CANCELLED
        )
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

    @contextmanager
    def fenced(self, attempt: "ExecutionAttempt"):
        """Attemptがまだ実行の権利を持つことを、行ロックの中で確かめる。Runnerの書込みはすべてこの中で行う"""
        with transaction.atomic():
            current = Run.locked(self.pk)
            if not attempt.holds(current):
                raise LeaseLost(f"run={self.uid} attempt={attempt.number}")
            self.status = current.status
            yield current


class ExecutionAttempt(models.Model):
    """Runを1つのWorkerで実行する試み。numberがfencing tokenを兼ねる（design.md §6.3）"""

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="attempts")
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
        constraints = [models.UniqueConstraint(fields=["run", "number"], name="uniq_attempt_number")]

    def __str__(self):
        return f"{self.run_id}#{self.number}"

    @classmethod
    def start(cls, run: Run, worker: str = "") -> "ExecutionAttempt":
        """claimの中（Runの行ロックの中）で呼ぶ"""
        last = run.attempts.aggregate(last=Max("number"))["last"] or 0
        cls.objects.filter(run=run, ended__isnull=True).update(ended=timezone.now(), failure="superseded")
        return cls.objects.create(
            run=run, number=last + 1, worker=worker, lease_expires=timezone.now() + timedelta(seconds=LEASE_SECONDS)
        )

    def holds(self, run: Run) -> bool:
        """このAttemptが最新で、終わっておらず、leaseが切れていない。Runは実行中か停止中"""
        if run.status not in (RunStatus.RUNNING, RunStatus.CANCELLING):
            return False
        latest = run.attempts.aggregate(last=Max("number"))["last"]
        current = ExecutionAttempt.objects.filter(pk=self.pk).values("ended", "lease_expires").first()
        return (
            latest == self.number
            and current is not None
            and current["ended"] is None
            and current["lease_expires"] > timezone.now()
        )

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
    CLARIFICATION_REQUESTED = 50, _("確認の質問")
    CLARIFICATION_ANSWERED = 51, _("確認への回答")
    LLM_RETRY = 60, _("モデルの再試行")
    RUN_FINISHED = 90, _("実行の終了")


class RunEvent(models.Model):
    """Runの記録。追記だけで、作成後に変更・削除しない。モデルへの入力はここから組み立てる"""

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="events")
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
        else:
            from harness.recovery import apply_model_switch_answer  # noqa: PLC0415

            apply_model_switch_answer(run, self, actor)
        run.set_status(RunStatus.QUEUED, comment=str(_("確認への回答を受け付けました")), actor=actor)
        enqueue_run(run)


class ToolStatus(models.IntegerChoices):
    REQUESTED = 1, _("要求")
    RUNNING = 2, _("実行中")
    SUCCEEDED = 10, _("成功")
    FAILED = 20, _("失敗")
    CANCELLED = 30, _("実行せず")
    UNKNOWN = 40, _("結果不明")


class ToolInvocation(models.Model):
    """Toolの呼出し。実行の前に作り（harness.md §7.6）、結果の記録で終える"""

    uid = NanoidField(unique=True)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="tool_invocations")
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
        constraints = [models.UniqueConstraint(fields=["run", "call_id"], name="uniq_tool_call_id")]

    def __str__(self):
        return f"{self.name}({self.call_id})"


class Checkpoint(models.Model):
    """Workspaceのsnapshotを確定した区切り（design.md §5.4・§7.9、harness.md §11.1）。作成後に変更・削除しない。

    新しいAttemptは、そのRunの最後のCheckpointからWorkspaceを戻す。同じ会話の次の依頼（子のRun）は、親のRunの最後の
    Checkpointから始める。snapshotの中身は保存先（runtime/storage.py）にあり、ここには場所と版だけを持つ
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

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Checkpoint")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["run", "-sequence"])]

    def __str__(self):
        return f"{self.run_id}@{self.sequence}"

    @classmethod
    def latest_for(cls, run: Run) -> "Checkpoint | None":
        """Runの最後のCheckpoint。なければ同じ会話の前の依頼（親をたどったRun）の最後のもの。
        Sandboxを使わなかった依頼にはCheckpointがないので、さらに前の依頼のものを使う。
        親は同じ会話の1つ前のRunなので、会話の中でこのRunまでのRunを新しい順に探す"""
        return (
            cls.objects.filter(run__conversation_id=run.conversation_id, run_id__lte=run.pk)
            .order_by("-run_id", "-sequence", "-pk")
            .first()
        )


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
