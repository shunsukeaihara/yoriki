from collections import defaultdict
from typing import Any

from django.db.models import Prefetch
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from connector.models import Operation
from skill.serializers import AssetDiffCountSerializer, asset_diff_items
from utils.exceptions import Conflict

from .errors import AlreadyAnswered, InvalidTransition
from .models import (
    ClarificationKind,
    ClarificationRequest,
    ClarificationStatus,
    Run,
    RunEndReason,
    RunEvent,
    RunEventKind,
    RunKind,
    RunLaunch,
    RunStatus,
    RunTrigger,
    ToolInvocation,
    ToolStatus,
)


def todo_events():
    """作業の計画（todo_write の成功した結果）。新しいものから"""
    return RunEvent.objects.filter(
        kind=RunEventKind.TOOL_RESULT, data__name="todo_write", data__is_error=False
    ).order_by("-sequence")


def prefetch_todos() -> Prefetch:
    """RunSerializer の todos を一覧で先に読む。Runごとに最新の1件だけを読む（PostgreSQLの DISTINCT ON）"""
    latest = todo_events().order_by("run_id", "-sequence").distinct("run_id")
    return Prefetch("events", queryset=latest, to_attr="todo_events")


class TodoSerializer(serializers.Serializer):
    """作業の計画の1項目（harness/tools/todo_write.py）"""

    content = serializers.CharField(read_only=True)
    status = serializers.ChoiceField(
        choices=[("pending", _("未着手")), ("in_progress", _("作業中")), ("completed", _("完了"))], read_only=True
    )


class RunSkillSerializer(serializers.Serializer):
    """Runが使うSkillと版（Manifestに固定・追記したもの）"""

    skill = serializers.CharField(read_only=True, help_text=_("SkillのID"))
    name = serializers.CharField(read_only=True)
    number = serializers.IntegerField(
        read_only=True, allow_null=True, help_text=_("版（Draftの評価のRunのDraftでは空）")
    )
    chosen = serializers.BooleanField(read_only=True, help_text=_("利用者が `/` で指定した"))


class RunSkillChangeSummarySerializer(serializers.Serializer):
    fields = serializers.ListField(child=serializers.CharField(), read_only=True, required=False)  # type: ignore[assignment]
    assets_added = serializers.ListField(child=serializers.CharField(), read_only=True, required=False)
    assets_removed = serializers.ListField(child=serializers.CharField(), read_only=True, required=False)
    assets_replaced = serializers.ListField(child=serializers.CharField(), read_only=True, required=False)
    assets_changed = serializers.ListField(child=serializers.CharField(), read_only=True, required=False)


class RunSkillChangeSerializer(serializers.Serializer):
    """Skillの編集のRunで変えたSkill（Skill管理のToolの結果。harness.md §10.9）。Skillごとに最後の状態"""

    skill = serializers.CharField(read_only=True, help_text=_("SkillのID"))
    name = serializers.CharField(read_only=True)
    scope = serializers.CharField(read_only=True, help_text=_("範囲（「部門 経理部」等）"))
    state = serializers.ChoiceField(
        choices=[
            ("created", _("作成")),
            ("draft_saved", _("変更案を保存")),
            ("published", _("公開")),
            ("rolled_back", _("切戻し")),
        ],
        read_only=True,
    )
    version = serializers.IntegerField(read_only=True, allow_null=True, help_text=_("公開・切戻しでできた版"))
    revision = serializers.IntegerField(read_only=True, allow_null=True, help_text=_("保存したDraftのrevision"))
    summary = RunSkillChangeSummarySerializer(read_only=True, allow_null=True, help_text=_("変わった項目と資料のkey"))
    asset_diffs = serializers.SerializerMethodField(help_text=_("置き換えた資料の差分の数"))

    @extend_schema_field(AssetDiffCountSerializer(many=True))
    def get_asset_diffs(self, obj: dict[str, Any]) -> list[dict[str, Any]]:
        return asset_diff_items(obj.get("asset_diffs"))


# 結果の値がSkillの変更（RunSkillChangeSerializer）になるTool
SKILL_CHANGE_TOOLS = (
    "create_skill",
    "update_skill_draft",
    "stage_skill_asset",
    "remove_skill_asset",
    "publish_skill_draft",
    "rollback_skill",
)


class RunTaskFieldChangeSerializer(serializers.Serializer):
    field = serializers.ChoiceField(
        choices=[
            ("prompt_template", _("依頼文")),
            ("notification_instructions", _("通知の書き方")),
            ("input_description", _("入力の項目の説明")),
        ],
        read_only=True,
    )
    input = serializers.CharField(read_only=True, help_text=_("入力の項目の説明では、項目の表示名"))
    before = serializers.CharField(read_only=True)
    after = serializers.CharField(read_only=True)


class RunTaskChangeSerializer(serializers.Serializer):
    """タスクの編集のRunで保存したタスクの版（update_task の結果。harness.md §10.10）"""

    task = serializers.CharField(read_only=True, help_text=_("タスクのID"))
    name = serializers.CharField(read_only=True)
    scope = serializers.CharField(read_only=True, help_text=_("範囲（「部門 経理部」等）"))
    version = serializers.IntegerField(read_only=True, help_text=_("保存した版"))
    changes = RunTaskFieldChangeSerializer(many=True, read_only=True, help_text=_("変えた項目の変更の前後"))


def task_changes(run: Run) -> list[dict[str, Any]]:
    """タスクの編集のRunで保存した版。何度か保存したら、最後の版にまとめ、項目ごとに最初の前と最後の後を出す"""
    events = RunEvent.objects.filter(
        run=run, kind=RunEventKind.TOOL_RESULT, data__name="update_task", data__is_error=False
    ).order_by("sequence")
    merged: dict[str, Any] | None = None
    for event in events:
        value = event.data.get("value") or {}
        if "task" not in value:
            continue
        if merged is None:
            merged = {**value, "changes": list(value["changes"])}
            continue
        changes = {(c["field"], c["input"]): c for c in merged["changes"]}
        for change in value["changes"]:
            key = (change["field"], change["input"])
            changes[key] = {**change, "before": changes[key]["before"]} if key in changes else change
        merged = {**value, "changes": [c for c in changes.values() if c["before"] != c["after"]]}
    return [merged] if merged is not None else []


def skill_changes(run: Run) -> list[dict[str, Any]]:
    """Skillの編集のRunで変えたSkillの、Skillごとの最後の状態。公開していない変更は、前の公開の後の変更点をまとめる"""
    events = RunEvent.objects.filter(
        run=run, kind=RunEventKind.TOOL_RESULT, data__name__in=SKILL_CHANGE_TOOLS, data__is_error=False
    ).order_by("sequence")
    changes: dict[str, dict[str, Any]] = {}
    for event in events:
        value = event.data.get("value") or {}
        if "skill" not in value:
            continue
        before = changes.get(value["skill"])
        if value["state"] == "draft_saved" and before is not None and before["state"] in ("draft_saved", "created"):
            merged: dict[str, list[str]] = {k: list(v) for k, v in (before.get("summary") or {}).items()}
            for key, names in (value.get("summary") or {}).items():
                merged[key] = list(dict.fromkeys([*merged.get(key, []), *names]))
            # 後で外した・置き換え直した資料は、後の操作の差分だけにする
            touched = ("assets_added", "assets_replaced", "assets_removed")
            later = {k for name in touched for k in (value.get("summary") or {}).get(name) or []}
            diffs = {k: v for k, v in (before.get("asset_diffs") or {}).items() if k not in later}
            diffs |= value.get("asset_diffs") or {}
            value = {**value, "summary": merged, "asset_diffs": diffs}
        changes[value["skill"]] = value
    return list(changes.values())


class RunNotificationSerializer(serializers.Serializer):
    """通知の候補（prepare_notification で決めたもの）"""

    message = serializers.CharField(read_only=True)
    files = serializers.ListField(child=serializers.CharField(), read_only=True, required=False, allow_null=True)


class RunTaskInfoSerializer(serializers.Serializer):
    """タスクのRunの、受付時のタスクと版、起動のしかた、通知（Manifestに固定したもの）"""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    number = serializers.IntegerField(read_only=True, help_text=_("受付時のタスクの版"))
    launch = serializers.ChoiceField(choices=RunLaunch.choices, read_only=True, allow_null=True)
    launch_display = serializers.CharField(read_only=True)
    delivers = serializers.BooleanField(read_only=True, help_text=_("出口へ送るか（修正チャットのRunは送らない）"))
    destination_name = serializers.CharField(
        read_only=True, help_text=_("タスクの版の結果の通知先の名前（なければ空）")
    )
    notification = RunNotificationSerializer(
        read_only=True, allow_null=True, help_text=_("通知の候補（修正チャットでは、送るはずだった内容）")
    )


def task_info(run: Run) -> dict[str, Any] | None:
    if run.trigger != RunTrigger.TASK:
        return None
    fixed = ((run.manifest or {}).get("fixed") or {}).get("task") or {}
    revision = run.task_revision
    destination = revision.destination if revision is not None else None
    return {
        "id": fixed.get("task", ""),
        "name": fixed.get("name", ""),
        "number": fixed.get("number", 0),
        "launch": run.launch,
        "launch_display": run.get_launch_display() if run.launch else "",
        "delivers": run.destination_revision_id is not None,
        "destination_name": destination.name if destination is not None else "",
        "notification": run.notification if isinstance(run.notification, dict) else None,
    }


class RunSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    conversation: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)
    kind = serializers.ChoiceField(choices=RunKind.choices, read_only=True)
    status = serializers.ChoiceField(choices=RunStatus.choices, read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    end_reason = serializers.ChoiceField(choices=RunEndReason.choices, read_only=True, allow_null=True)
    end_reason_display = serializers.CharField(source="get_end_reason_display", read_only=True, allow_null=True)
    model_profile_name = serializers.CharField(source="model_version.profile.name", read_only=True)
    todos = serializers.SerializerMethodField(help_text=_("作業の計画（todo_write の最新の内容）"))
    skills = serializers.SerializerMethodField(help_text=_("使っているSkillと版"))
    result = serializers.JSONField(read_only=True, allow_null=True, help_text=_("業務結果（submit_result の内容）"))
    skill_changes = serializers.SerializerMethodField(help_text=_("Skillの編集のRunで変えたSkill"))
    task_changes = serializers.SerializerMethodField(help_text=_("タスクの編集のRunで保存したタスクの版"))
    trigger = serializers.ChoiceField(choices=RunTrigger.choices, read_only=True)
    task_info = serializers.SerializerMethodField(help_text=_("タスクのRunの、受付時のタスクと版（ほかのRunでは空）"))

    class Meta:
        model = Run
        fields = [
            "id",
            "conversation",
            "kind",
            "trigger",
            "task_info",
            "status",
            "status_display",
            "end_reason",
            "end_reason_display",
            "error",
            "model_profile_name",
            "effort",
            "step_count",
            "todos",
            "skills",
            "result",
            "skill_changes",
            "task_changes",
            "created",
            "updated",
            "finished",
        ]
        read_only_fields = fields

    @extend_schema_field(TodoSerializer(many=True))
    def get_todos(self, obj: Run) -> list[dict[str, str]]:
        # 一覧では prefetch_todos() で先に読んだものを使う（件数でクエリを増やさない）
        prefetched = getattr(obj, "todo_events", None)
        if prefetched is not None:
            event = prefetched[0] if prefetched else None
        else:
            event = todo_events().filter(run=obj).first()
        return (event.data.get("value") or {}).get("todos", []) if event else []

    @extend_schema_field(RunSkillSerializer(many=True))
    def get_skills(self, obj: Run) -> list[dict[str, Any]]:
        manifest = obj.manifest or {}
        chosen = [{**e, "chosen": True} for e in (manifest.get("fixed") or {}).get("skills") or []]
        loaded = [{"chosen": False, **e} for e in manifest.get("appended", []) if e.get("kind") == "skill"]
        return list(RunSkillSerializer([*chosen, *loaded], many=True).data)

    @extend_schema_field(RunTaskInfoSerializer(allow_null=True))
    def get_task_info(self, obj: Run) -> dict[str, Any] | None:
        info = task_info(obj)
        return dict(RunTaskInfoSerializer(info).data) if info is not None else None

    @extend_schema_field(RunSkillChangeSerializer(many=True))
    def get_skill_changes(self, obj: Run) -> list[dict[str, Any]]:
        if obj.kind != RunKind.SKILL_EDIT:
            return []
        return list(RunSkillChangeSerializer(skill_changes(obj), many=True).data)

    @extend_schema_field(RunTaskChangeSerializer(many=True))
    def get_task_changes(self, obj: Run) -> list[dict[str, Any]]:
        if obj.kind != RunKind.TASK_EDIT:
            return []
        return list(RunTaskChangeSerializer(task_changes(obj), many=True).data)


class ScriptCallSerializer(serializers.ModelSerializer):
    """Skillのスクリプトからの呼出し（design.md §4.9.6）。作業の記録で、そのときに実行していたToolの呼出し（`during`）の
    下に出す"""

    id = serializers.CharField(source="uid", read_only=True)
    during = serializers.SerializerMethodField(help_text="そのときに実行していたToolの呼出しのID（なければ空）")
    operation = serializers.ChoiceField(source="name", choices=Operation.choices, read_only=True)
    connector = serializers.SerializerMethodField(help_text="Connectorの名前（スクリプトが指定したもの）")
    sql = serializers.SerializerMethodField()
    params = serializers.SerializerMethodField()
    status = serializers.ChoiceField(choices=ToolStatus.choices, read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    rows = serializers.SerializerMethodField(help_text="読んだ行の数（query）")
    truncated = serializers.SerializerMethodField(help_text="上限で打ち切ったか（query）")
    tables = serializers.SerializerMethodField(help_text="表の数（describe）")
    error_code = serializers.SerializerMethodField()

    class Meta:
        model = ToolInvocation
        fields = [
            "id",
            "during",
            "operation",
            "connector",
            "sql",
            "params",
            "status",
            "status_display",
            "rows",
            "truncated",
            "tables",
            "error_code",
            "error",
            "created",
            "ended",
        ]
        read_only_fields = fields

    @staticmethod
    def _value(obj: ToolInvocation) -> dict[str, Any]:
        return obj.value if isinstance(obj.value, dict) else {}

    @staticmethod
    def _argument(obj: ToolInvocation, name: str) -> Any:
        return obj.arguments.get(name) if isinstance(obj.arguments, dict) else None

    def get_during(self, obj: ToolInvocation) -> str:
        return str(self._value(obj).get("during") or "")

    def get_connector(self, obj: ToolInvocation) -> str:
        return str(self._argument(obj, "connector") or "")

    def get_sql(self, obj: ToolInvocation) -> str:
        return str(self._argument(obj, "sql") or "")

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_params(self, obj: ToolInvocation) -> list[str]:
        params = self._argument(obj, "params")
        return [str(p) for p in params] if isinstance(params, list) else []

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_rows(self, obj: ToolInvocation) -> int | None:
        rows = self._value(obj).get("rows")
        return rows if isinstance(rows, int) else None

    def get_truncated(self, obj: ToolInvocation) -> bool:
        return bool(self._value(obj).get("truncated"))

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_tables(self, obj: ToolInvocation) -> int | None:
        tables = self._value(obj).get("tables")
        # queryの `tables` は使った表の名前の一覧なので、describeの数だけを返す
        return tables if isinstance(tables, int) and not isinstance(tables, bool) else None

    def get_error_code(self, obj: ToolInvocation) -> str:
        return str(self._value(obj).get("error") or "")


class RunCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, required=False, allow_blank=True)

    def validate(self, attrs):
        if self.instance is None or not self.instance.is_cancellable():
            raise serializers.ValidationError({"non_field_errors": _("この実行は停止できません")})
        return attrs

    def update(self, instance: Run, validated_data) -> Run:
        actor = validated_data.pop("actor")
        try:
            instance.request_cancel(validated_data.get("reason", ""), actor=actor)
        except InvalidTransition as exc:
            # validate()の後に、Workerが先に状態を変えた場合
            raise Conflict() from exc
        instance.refresh_from_db()
        return instance

    def create(self, validated_data):
        raise NotImplementedError("instanceを渡して使う")

    def to_representation(self, instance: Run):
        return RunSerializer(instance, context=self.context).data


class ClarificationOptionSerializer(serializers.Serializer):
    # Field.label と同じ名前だが、宣言したフィールドはメタクラスがクラス属性から外すので衝突しない
    label = serializers.CharField(read_only=True)  # type: ignore[assignment]
    description = serializers.CharField(read_only=True)


class ClarificationQuestionSerializer(serializers.Serializer):
    header = serializers.CharField(read_only=True)
    question = serializers.CharField(read_only=True)
    options = ClarificationOptionSerializer(many=True, read_only=True)
    multi_select = serializers.BooleanField(read_only=True)


class ClarificationAnswerItemSerializer(serializers.Serializer):
    selected = serializers.ListField(child=serializers.CharField(max_length=200), max_length=10, default=list)
    text = serializers.CharField(max_length=5000, required=False, allow_blank=True, default="")


class ClarificationContextSerializer(serializers.Serializer):
    """確認の対象。Skillの編集の確認で、対象のSkill（新しいSkillならskillが空）と変更の要約。Draftの評価では、
    評価のIDと対象のSkill・Draftのrevision・ケースの数。タスクの編集の確認では、対象のタスクと変更の要約"""

    skill = serializers.CharField(read_only=True, required=False)
    task = serializers.CharField(read_only=True, required=False)
    name = serializers.CharField(read_only=True, required=False)
    scope = serializers.CharField(read_only=True, required=False)
    summary = serializers.CharField(read_only=True, required=False)
    evaluation = serializers.CharField(read_only=True, required=False)
    revision = serializers.IntegerField(read_only=True, required=False)
    cases = serializers.IntegerField(read_only=True, required=False)


class ClarificationRequestSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    run: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)
    kind = serializers.ChoiceField(choices=ClarificationKind.choices, read_only=True)
    status = serializers.ChoiceField(choices=ClarificationStatus.choices, read_only=True)
    questions = ClarificationQuestionSerializer(many=True, read_only=True)
    answers = ClarificationAnswerItemSerializer(many=True, read_only=True)
    answered_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(
        slug_field="uid", read_only=True, allow_null=True
    )
    target = ClarificationContextSerializer(source="context", read_only=True, allow_null=True)

    class Meta:
        model = ClarificationRequest
        fields = [
            "id",
            "run",
            "kind",
            "status",
            "target",
            "questions",
            "answers",
            "comment",
            "answered_by",
            "answered",
            "expires",
            "created",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["target"] = data["target"] or None  # 対象のない確認（ask_user・モデルの切り替え）
        return data


class ClarificationAnswerSerializer(serializers.Serializer):
    """確認への回答。質問ごとの選択（と自由記述）を質問と同じ順に送る。補足だけでもよい"""

    answers = ClarificationAnswerItemSerializer(many=True, required=False, default=list)
    comment = serializers.CharField(max_length=5000, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        clarification = self.instance
        if (
            clarification is None
            or clarification.status != ClarificationStatus.OPEN
            or clarification.kind == ClarificationKind.EVALUATION  # 評価が終わるとサーバーが返す
        ):
            raise serializers.ValidationError({"non_field_errors": _("この確認には回答できません")})
        questions = clarification.questions
        answers = attrs["answers"] or [{"selected": [], "text": ""} for _q in questions]
        errors: dict[str, list] = defaultdict(list)
        if len(answers) != len(questions):
            errors["answers"].append(_("質問と同じ数の回答を送ってください"))
        for question, answer in zip(questions, answers, strict=False):
            labels = {o["label"] for o in question["options"]}
            if any(s not in labels for s in answer["selected"]):
                errors["answers"].append(_("選択肢から選んでください"))
            if not question.get("multi_select") and len(answer["selected"]) > 1:
                errors["answers"].append(_("この質問では1つだけ選んでください"))
        answered = any(a["selected"] or a["text"].strip() for a in answers) or attrs["comment"].strip()
        if not answered:
            errors["non_field_errors"].append(_("回答を選ぶか入力してください"))
        if clarification.kind == ClarificationKind.MODEL_SWITCH and not any(a["selected"] for a in answers):
            errors["answers"].append(_("続け方を選んでください"))
        if errors:
            raise serializers.ValidationError(errors)
        attrs["answers"] = answers
        return attrs

    def update(self, instance: ClarificationRequest, validated_data) -> ClarificationRequest:
        actor = validated_data.pop("actor")
        try:
            instance.accept_answer(validated_data["answers"], validated_data["comment"], actor=actor)
        except AlreadyAnswered as exc:
            # Web画面と外部スレッドから同時に回答された場合
            raise Conflict() from exc
        instance.refresh_from_db()
        return instance

    def create(self, validated_data):
        raise NotImplementedError("instanceを渡して使う")

    def to_representation(self, instance: ClarificationRequest):
        return ClarificationRequestSerializer(instance, context=self.context).data
