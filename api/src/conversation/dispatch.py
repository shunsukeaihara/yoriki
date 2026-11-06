"""会話のメッセージの受付と、Runの開始（design.md §5.1、§5.5）"""

from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from artifact.models import Artifact, ArtifactKind
from connector.models import Connector
from harness.catalogs import build_catalogs
from harness.connectors import DATASOURCE_TOOL_NAMES, ConnectorUnavailable
from harness.connectors import explicit_text as explicit_connectors_text
from harness.connectors import resolve_explicit as resolve_explicit_connectors
from harness.skills import SkillUnavailable, declared_connectors, explicit_text, resolve_explicit
from run.dispatch import enqueue_run
from run.models import (
    ACTIVE_STATUSES,
    ClarificationKind,
    ClarificationRequest,
    ClarificationStatus,
    Run,
    RunEvent,
    RunEventKind,
    RunKind,
    RunStatus,
)
from runtime.models import RuntimeProfile
from skill.connectors import skill_is_sensitive
from skill.models import CONVERSATION_SCOPES, Skill, SkillAction, SkillVersion

from .models import Conversation, ConversationMessage, InstructionKind, InstructionState, MessageRole, UserInstruction


class CannotStart(Exception):
    pass


# 新しい依頼の最初の発言に添える、それまでの添付の数の上限（新しいものから）
EARLIER_ATTACHMENTS_LIMIT = 50


def user_message_data(
    message: ConversationMessage,
    text: str,
    *,
    first: bool = False,
    skills: list[dict[str, object]] | None = None,
    connectors: list[dict[str, object]] | None = None,
    connector_catalog: dict[str, Any] | None = None,
    with_attachments: bool = True,
    result_skills: set[str] | None = None,
) -> dict[str, object]:
    """利用者の発言のRunEventの内容。添付は /workspace/input の場所とあわせて残す（harness/context.py が読む）。

    Runの最初の発言（first）には、同じ会話でそれまでに送った添付も添える。/workspace/input には会話の添付が
    すべてあり、作業ファイルも最新のCheckpointから引き継ぐので、前の依頼のファイルを使う依頼に答えられるように。
    `skills` は `/` で指定して解決したSkill（Manifestの内容）。本文も添え、モデルに `skill` を呼ばせない。
    `connectors` は `@` で指定したConnector。公開している表の説明を添える（harness.md §10.2）。
    `connector_catalog` はRunのConnectorの一覧で、Skillの本文のConnectorの名前に使う。
    `with_attachments` が偽なら添付を添えない（確認への回答で渡した）。`result_skills` は explicit_text() を見る
    """
    data: dict[str, object] = {"text": text, "message": message.uid}
    if skills:
        data["skills"] = skills
        data["skills_text"] = explicit_text(skills, connector_catalog, result_skills=result_skills)
    if connectors:
        data["connectors"] = connectors
        data["connectors_text"] = explicit_connectors_text(connectors)
    if with_attachments and (attachments := describe_attachments(message)):
        data["attachments"] = attachments
    if first:
        earlier = Artifact.objects.sent_inputs(message.conversation_id).exclude(messages=message)
        if message.run is not None and message.run.kind == RunKind.TASK_EDIT:
            # 修正チャットのタスクの編集のRunは、直前のタスクのRunから後の添付だけ（/workspace/input と同じ）
            earlier = earlier.filter(messages__run__in=message.run.fix_chat_runs())
        earlier = earlier.select_related("blob").order_by("-pk")[:EARLIER_ATTACHMENTS_LIMIT]
        if earlier_list := [a.describe() for a in reversed(earlier)]:
            data["earlier_attachments"] = earlier_list
    return data


def resolve_effort(conversation: Conversation, version) -> str:
    """利用者が固定したeffortがモデルの受け付ける値ならそれを、なければモデルの既定を使う"""
    if conversation.effort and conversation.effort in version.effort_values:
        return conversation.effort
    return version.default_effort


def active_run(conversation: Conversation) -> Run | None:
    return Run.objects.select_for_update().filter(conversation=conversation, status__in=ACTIVE_STATUSES).first()


def describe_attachments(message: ConversationMessage) -> list[dict[str, object]]:
    """RunEventに残す添付の一覧"""
    return [a.describe() for a in message.attachments.select_related("blob").order_by("pk")]


@transaction.atomic
def post_message(
    conversation: Conversation,
    *,
    body: str,
    client_message_id: str,
    actor,
    attachments: list[Artifact] | None = None,
    skills: list[Skill] | None = None,
    connectors: list[Connector] | None = None,
    skill_edit: dict[str, str] | None = None,
):
    """利用者のメッセージを保存し、新しいRun・親へ渡す発言・確認への回答・編集のRunの依頼のどれかとして受け付ける。

    Skill・Connectorを指定したメッセージも、親が動いていれば親へ渡す。受け付けるときに今の権限で確かめ、使えなければ
    理由を返す（CannotStart。取り込むときにも確かめ直す。harness.md §10.1・§10.2）。確認待ちなら、本文を確認への回答に
    し、指定したものは再開のときに取り込む。Skillの編集の依頼（`skill_edit`。{"id", "name"}、新しいSkillならidが空）は、
    対象とToolを始めるときに固定するので、今のRunが終わってから始める
    """
    conversation = Conversation.objects.select_for_update().get(pk=conversation.pk)
    if conversation.is_task_fix:
        # 再送を返す前にも確かめる（管理できなくなった持ち主に、タスクのRunのメッセージを返さない。
        # authorization.md §7.1）
        from task.checks import fixer_problem  # noqa: PLC0415  taskがconversationをimportするため

        if reason := fixer_problem(actor, conversation.task):
            raise CannotStart(reason)
    if client_message_id:
        sent = ConversationMessage.objects.filter(
            conversation=conversation, client_message_id=client_message_id
        ).first()
        if sent is not None:
            return sent  # 再送。二重に受け付けない
    if conversation.is_task_fix:
        _check_fix_message(conversation, separate=bool(skills or connectors or skill_edit))
    if (skills or connectors) and not skill_edit:
        _check_chosen(conversation, skills or [], connectors or [])
    message = ConversationMessage.objects.create(
        conversation=conversation,
        sender=actor,
        role=MessageRole.USER,
        body=body,
        client_message_id=client_message_id,
        skills=[{"id": s.uid, "name": s.name} for s in skills or []],
        connectors=[{"id": c.uid, "name": c.name} for c in connectors or []],
        skill_edit=skill_edit or {},
    )
    if attachments:
        # 検証の後に、別のメッセージで送られていないかを会話の行ロックの中で確かめる
        unsent = list(
            Artifact.objects.select_for_update()
            .filter(
                pk__in=[a.pk for a in attachments], conversation=conversation, kind=ArtifactKind.ATTACHMENT, path=""
            )
            .order_by("pk")
        )
        if len(unsent) != len(attachments):
            raise CannotStart(_("送信済みの添付が含まれています。添付し直してください。"))
        # 修正チャットで、動いているタスクのRunへの追加の指示・回答の添付は、そのRunの添付の中で名前を決める（Runごとに
        # /workspace/input を分けるため）。タスクの編集の依頼への添付は、会話の添付の中で決める
        # （直前のタスクのRunのものと重ねない）
        Artifact.attach(conversation, unsent, run=_task_run_instructed(conversation))
        message.attachments.set(unsent)
    conversation.last_posted = timezone.now()
    fields = ["last_posted", "updated"]
    if not conversation.title:
        first_line = body.strip().splitlines()[0] if body.strip() else ""
        conversation.title = (first_line or ", ".join(a.name for a in attachments or []))[:50]
        fields.append("title")
    conversation.save(update_fields=fields)

    run = active_run(conversation)
    if run is None:
        start_run(conversation, message, actor)
        return message
    message.run = run
    message.save(update_fields=["run"])
    if attachments and _task_run_instructed(conversation) is not None:
        # 修正チャットでは、追加の指示・回答の添付もタスクのRunの添付にする（Runの画面で、タスクを管理できる人が取得
        # できるように。/workspace/input にはこのRunのものだけを置く。harness.md §10.10）
        Artifact.objects.filter(pk__in=[a.pk for a in attachments]).update(run=run)
    question = ClarificationRequest.objects.filter(
        run=run,
        status=ClarificationStatus.OPEN,
        kind__in=(ClarificationKind.QUESTION, ClarificationKind.SKILL_EDIT, ClarificationKind.TASK_EDIT),
    ).first()
    if question is not None and run.status == RunStatus.WAITING_USER and not skill_edit:
        _answer_question(question, message, actor, chosen=bool(skills or connectors))
        return message
    # 停止中の発言は親が読まないので、終わった後の新しいRunで始める
    kind = InstructionKind.NEXT if run.status == RunStatus.CANCELLING or skill_edit else InstructionKind.CURRENT
    UserInstruction.objects.create(
        conversation=conversation, message=message, run=run if kind == InstructionKind.CURRENT else None, kind=kind
    )
    return message


def _answer_question(question: ClarificationRequest, message: ConversationMessage, actor, *, chosen: bool) -> None:
    """確認待ちのときのメッセージは、その確認への自由記述の回答にする。指定したSkill・Connector（`chosen`）は、再開の
    ときに取り込む（design.md §5.5）"""
    attachments = describe_attachments(message)
    if chosen and not message.body.strip() and not attachments:
        raise CannotStart(_("確認への回答を入力してください。"))
    question.accept_answer(
        [{"selected": [], "text": ""} for _q in question.questions], message.body, actor, attachments=attachments
    )
    if chosen:
        UserInstruction.objects.create(
            conversation=message.conversation,
            message=message,
            run=question.run,
            kind=InstructionKind.CURRENT,
            is_answer=True,
        )


def _check_chosen(conversation: Conversation, skills: list[Skill], connectors: list[Connector]) -> None:
    """動いているRunへ渡す発言で指定したSkill・Connectorを、依頼した人の今の権限で確かめる（会話の行ロックの中で呼ぶ）。
    このRunにないToolを要するSkillは、断らずに、取り込むときに案内させる（harness.md §10.1）"""
    run = active_run(conversation)
    if run is None or run.status == RunStatus.CANCELLING:
        return  # 新しいRunで始めるので、start_run() が確かめる
    owner, version, sensitive = conversation.owner, run.model_version, run.handles_sensitive_data
    try:
        resolve_explicit(skills, owner, version, sensitive=sensitive)
    except SkillUnavailable as exc:
        raise CannotStart(_("指定したSkillを使えません。%(reason)s") % {"reason": exc}) from exc
    try:
        resolve_explicit_connectors(connectors, owner, version, sensitive=sensitive)
    except ConnectorUnavailable as exc:
        raise CannotStart(_("指定したConnectorを使えません。%(reason)s") % {"reason": exc}) from exc
    fixed = (run.manifest or {}).get("fixed", {}).get("skills") or []
    if connectors and fixed and not _allow_connector_tools(fixed):
        raise CannotStart(_("今の作業のSkillはConnectorのToolを使わないので、Connectorを指定できません。"))


def _allow_connector_tools(skills: list[dict[str, Any]]) -> bool:
    tools = SkillVersion.objects.filter(pk__in=[s["version"] for s in skills]).values_list("tools", flat=True)
    return any(name in DATASOURCE_TOOL_NAMES for names in tools for name in names)


def _task_run_instructed(conversation: Conversation) -> Run | None:
    """修正チャットのメッセージが、動いているタスクのRunへの追加の指示か回答になるなら、そのRun（会話の行ロックの
    中で呼ぶ）。タスクの編集のRunへのメッセージではNone"""
    if not conversation.is_task_fix:
        return None
    run = active_run(conversation)
    if run is None or not run.isolated_workspace or run.status == RunStatus.CANCELLING:
        return None
    return run


def _check_fix_message(conversation: Conversation, *, separate: bool) -> None:
    """修正チャット（design.md §11.11）では、Skill・Connectorの指定と、Skillの編集の依頼は受け付けない（タスクを
    今も管理できるかは post_message() が先に確かめる）。動いているRunへのメッセージは追加の指示か回答、動いていない
    ときのメッセージは、タスクの編集の依頼になる（start_run()）"""
    if separate:
        raise CannotStart(_("修正チャットでは、SkillやConnectorを指定できません。"))


def accept_skill_edit(clarification: ClarificationRequest, actor) -> None:
    """request_skill_edit の確認が承認されたら、要約を本文とするSkillの編集の依頼を、次の依頼として受け付ける
    （確認への回答と同じtransactionで呼ぶ。harness.md §10.9）。今のRunが終わった後に始まる"""
    from harness.tools.request_skill_edit import approved  # noqa: PLC0415  harnessがconversationをimportするため

    if not approved(clarification):
        return
    target = clarification.context
    message = ConversationMessage.objects.create(
        conversation=clarification.run.conversation,
        sender=actor,
        role=MessageRole.USER,
        body=target.get("summary", ""),
        skill_edit={"id": target.get("skill", ""), "name": target.get("name", "")},
    )
    UserInstruction.objects.create(conversation=message.conversation, message=message, kind=InstructionKind.NEXT)


def accept_task_edit(clarification: ClarificationRequest, actor) -> None:
    """request_task_edit の確認が承認されたら、要約を本文とするタスクの編集の依頼を、次の依頼として受け付ける
    （確認への回答と同じtransactionで呼ぶ。harness.md §10.10）。修正チャットのタスクのRunが終わった後に始まる"""
    from harness.tools.request_task_edit import approved  # noqa: PLC0415  harnessがconversationをimportするため

    if not approved(clarification):
        return
    message = ConversationMessage.objects.create(
        conversation=clarification.run.conversation,
        sender=actor,
        role=MessageRole.USER,
        body=clarification.context.get("summary", ""),
    )
    UserInstruction.objects.create(conversation=message.conversation, message=message, kind=InstructionKind.NEXT)


def resolve_task_edit(conversation: Conversation, user, version) -> dict[str, str]:
    """修正チャットのタスクの編集の依頼の対象を確かめて、Manifestに固定する内容にする。依頼した人がタスクを今も
    管理できなければ CannotStart（harness.md §10.10）"""
    from task.checks import fixer_problem  # noqa: PLC0415  taskがconversationをimportするため

    task = conversation.task
    if reason := fixer_problem(user, task):
        raise CannotStart(reason)
    assert task is not None  # fixer_problem() が確かめた
    revision = task.current_revision
    if revision is None:
        raise CannotStart(_("このタスクには版がありません。"))
    if revision.handles_sensitive_data and not conversation.handles_sensitive_data:
        # 機密のタスクの定義を、機密でない会話（クラウドのモデル）で読まない
        raise CannotStart(
            _(
                "このタスクは個人情報・機密情報を扱うようになりました。タスクの画面から新しい修正チャットを始めてください。"
            )
        )
    if not version.supports_tools:
        raise CannotStart(_("このモデルはToolを使えないので、タスクを直せません。別のモデルを選んでください。"))
    return {"task": task.uid, "name": task.name}


def resolve_skill_edit(message: ConversationMessage, user, version, *, sensitive: bool) -> dict[str, object]:
    """Skillの編集の依頼の対象を確かめて、Manifestに固定する内容にする。編集できなければ CannotStart。
    機密のSkillは、機密の会話でだけ編集する（harness.md §10.9）"""
    if not version.supports_tools:
        raise CannotStart(_("このモデルはToolを使えないので、Skillを編集できません。別のモデルを選んでください。"))
    uid = message.skill_edit.get("id", "")
    if not uid:
        return {"skill": "", "name": ""}
    skill = Skill.objects.filter(uid=uid, deleted=False).select_related("department").first()
    if skill is None:
        raise CannotStart(_("編集するSkillが削除されました。"))
    if skill.scope not in CONVERSATION_SCOPES or not skill.can(user, SkillAction.EDIT):
        raise CannotStart(_("Skill「%(name)s」を編集する権限がありません。") % {"name": skill.name})
    if not sensitive and skill_is_sensitive(skill):
        raise CannotStart(
            _("Skill「%(name)s」は機密のSkillなので、個人情報・機密情報を扱う会話で編集してください。")
            % {"name": skill.name}
        )
    return {"skill": skill.uid, "name": skill.name}


def chosen_skills(message: ConversationMessage) -> list[Skill]:
    """メッセージで指定したSkill（指定の順）。削除されたものがあれば SkillUnavailable"""
    ids = [s["id"] for s in message.skills]
    skills = Skill.objects.filter(uid__in=ids, deleted=False).select_related("current_version", "department")
    found = {s.uid: s for s in skills}
    missing = [s["name"] for s in message.skills if s["id"] not in found]
    if missing:
        raise SkillUnavailable(_("%(names)s：削除されました") % {"names": "、".join(missing)})
    return [found[uid] for uid in ids]


def chosen_connectors(message: ConversationMessage) -> list[Connector]:
    """メッセージで指定したConnector（指定の順）。削除されたものがあれば ConnectorUnavailable"""
    ids = [c["id"] for c in message.connectors]
    connectors = Connector.objects.filter(uid__in=ids, deleted=False).select_related(
        "connection", "current_version", "department"
    )
    found = {c.uid: c for c in connectors}
    missing = [c["name"] for c in message.connectors if c["id"] not in found]
    if missing:
        raise ConnectorUnavailable(_("%(names)s：削除されました") % {"names": "、".join(missing)})
    return [found[uid] for uid in ids]


def resolve_connectors(
    message: ConversationMessage, user, version, skills: list[dict[str, object]], *, sensitive: bool
):
    """`@` で指定したConnectorを確かめる。`/` で指定したSkillがConnectorのToolを許さなければ使えないので断る"""
    try:
        connectors = resolve_explicit_connectors(chosen_connectors(message), user, version, sensitive=sensitive)
    except ConnectorUnavailable as exc:
        raise CannotStart(_("指定したConnectorを使えません。%(reason)s") % {"reason": exc}) from exc
    if connectors and skills and not _allow_connector_tools(skills):
        raise CannotStart(_("指定したSkillはConnectorのToolを使わないので、Connectorを指定できません。"))
    return connectors


def start_run(conversation: Conversation, message: ConversationMessage, actor, *, parent: Run | None = None) -> Run:
    """呼び出し側のtransactionの中で呼ぶ。モデル設定の版とeffortを固定し、最初の発言を記録して実行を依頼する"""
    profile = conversation.model_profile
    version = profile.current_version
    if version is None or not profile.is_active or profile.deleted:
        raise CannotStart(_("この会話のモデル設定は使えません。別のモデルを選んでください。"))
    if conversation.handles_sensitive_data and not version.is_local:
        raise CannotStart(_("個人情報や機密情報を扱う会話では、ローカルLLMのモデルを選んでください。"))
    effort = resolve_effort(conversation, version)
    # 実行環境はRunの単位で決め、既定のRuntime Profileのその時点の版を固定する（design.md §7.3）。
    # エージェントの作業はほとんどがSandboxの中で動くので、既定がなければRunを始めない
    runtime_version = RuntimeProfile.default_version()
    if runtime_version is None:
        raise CannotStart(
            _("コマンドを動かす環境（Runtime Profile）が登録されていません。基盤管理者に問い合わせてください。")
        )
    if conversation.is_task_fix:
        # 修正チャットのタスクのRunは task.dispatch.start_task_run() で始める。ここで始めるのは、タスクの編集のRun
        # （動いていないときのメッセージと、承認した編集の依頼。タスクのRunが終わる直前に届いて取り込めなかった発言も）
        return _start_task_edit(
            conversation, message, actor, version=version, effort=effort, runtime_version=runtime_version
        )
    # Skillの編集の依頼は、対象のSkillを固定した編集のRunにする（harness.md §10.9）。それ以外では、`/` で指定した
    # Skillをその時点の公開中の版へ解決して固定する（Toolの一覧をRunの中で変えない。harness.md §7.5・§10.1）
    # 機密のConnectorとSkillは、機密の会話のRunにだけ出す（design.md §4冒頭）
    sensitive = conversation.handles_sensitive_data
    skill_edit = (
        resolve_skill_edit(message, conversation.owner, version, sensitive=sensitive) if message.skill_edit else None
    )
    try:
        skills = (
            resolve_explicit(chosen_skills(message), conversation.owner, version, sensitive=sensitive)
            if not skill_edit
            else []
        )
    except SkillUnavailable as exc:
        raise CannotStart(_("指定したSkillを使えません。%(reason)s") % {"reason": exc}) from exc
    # エージェントが使える一覧。`@` で指定したものと、Skillが宣言したものを先に並べる。発言を取り込むたびに作り直し、
    # 使えるかは操作のたびに確かめ直す（harness.md §10.1・§10.2）
    connectors = resolve_connectors(message, conversation.owner, version, skills, sensitive=sensitive)
    catalog, connector_catalog = build_catalogs(
        conversation.owner,
        version,
        sensitive=sensitive,
        list_skills=not skills and skill_edit is None,
        explicit=[*connectors, *declared_connectors(skills)],
    )
    fixed: dict[str, object] = {
        "model_version": version.pk,
        "effort": effort,
        "runtime_version": runtime_version.pk,
        "handles_sensitive_data": conversation.handles_sensitive_data,
        "skills": skills,
        "skill_catalog": catalog,
        "connectors": connectors,
        "connector_catalog": connector_catalog,
    }
    if skill_edit is not None:
        fixed["skill_edit"] = skill_edit
    if parent is None:
        # 同じ会話の前のRun。作業ファイルは会話の最新のCheckpointから引き継ぐ（design.md §7.9）
        parent = Run.objects.filter(conversation=conversation).order_by("-created", "-pk").first()
    run = Run.objects.create(
        conversation=conversation,
        parent=parent,
        owner=conversation.owner,
        kind=RunKind.WORK if skill_edit is None else RunKind.SKILL_EDIT,
        model_version=version,
        effort=effort,
        runtime_version=runtime_version,
        handles_sensitive_data=conversation.handles_sensitive_data,
        manifest={"fixed": fixed, "appended": []},
    )
    message.run = run
    message.save(update_fields=["run"])
    RunEvent.record(run, RunEventKind.STATUS, {"status": RunStatus.QUEUED}, actor=actor)
    data = user_message_data(
        message, message.body, first=True, skills=skills, connectors=connectors, connector_catalog=connector_catalog
    )
    RunEvent.record(run, RunEventKind.USER_MESSAGE, data, actor=actor)
    enqueue_run(run)
    return run


def _start_task_edit(
    conversation: Conversation, message: ConversationMessage, actor, *, version, effort, runtime_version
):
    """修正チャットのタスクの編集のRun（harness.md §10.10）。同じ会話の1つ前のRunを親にして文脈を引き継ぎ（タスクの
    Runは親を持たないので、直前のタスクのRunから始まる）、作業ファイルは会話の最新のCheckpointから戻す。
    Skill・Connectorは使わない"""
    task_edit = resolve_task_edit(conversation, conversation.owner, version)
    fixed: dict[str, object] = {
        "model_version": version.pk,
        "effort": effort,
        "runtime_version": runtime_version.pk,
        "handles_sensitive_data": conversation.handles_sensitive_data,
        "skills": [],
        "skill_catalog": None,
        "connectors": [],
        "connector_catalog": None,
        "task_edit": task_edit,
    }
    run = Run.objects.create(
        conversation=conversation,
        parent=Run.objects.filter(conversation=conversation).order_by("-created", "-pk").first(),
        owner=conversation.owner,
        kind=RunKind.TASK_EDIT,
        model_version=version,
        effort=effort,
        runtime_version=runtime_version,
        handles_sensitive_data=conversation.handles_sensitive_data,
        manifest={"fixed": fixed, "appended": []},
    )
    message.run = run
    message.save(update_fields=["run"])
    RunEvent.record(run, RunEventKind.STATUS, {"status": RunStatus.QUEUED}, actor=actor)
    RunEvent.record(run, RunEventKind.USER_MESSAGE, user_message_data(message, message.body, first=True), actor=actor)
    enqueue_run(run)
    return run


def start_next_request(conversation: Conversation) -> Run | None:
    """先行Runが終わった後に、待っている次の依頼を受け付けた順に始める（Runの状態を変えるtransactionの中で呼ぶ）。

    始められない依頼（既定のRuntime Profileがなくなった等）は取り消し、理由を会話のシステムのメッセージとして残して、
    次の依頼へ進む。投稿のときには受け付けを返しているので、黙って捨てない
    """
    if Run.objects.filter(conversation=conversation, status__in=ACTIVE_STATUSES).exists():
        return None
    instructions = (
        UserInstruction.objects.select_for_update(of=("self",))
        .filter(conversation=conversation, kind=InstructionKind.NEXT, state=InstructionState.RECEIVED)
        .select_related("message", "message__sender")
        .order_by("pk")
    )
    for instruction in instructions:
        try:
            run = start_run(conversation, instruction.message, instruction.message.sender)
        except CannotStart as exc:
            instruction.state = InstructionState.DISCARDED
            instruction.save(update_fields=["state", "updated"])
            ConversationMessage.objects.create(
                conversation=conversation,
                role=MessageRole.SYSTEM,
                body=_("次の依頼を始められませんでした：%(reason)s") % {"reason": exc},
            )
            continue
        instruction.state = InstructionState.STARTED
        instruction.run = run
        instruction.save(update_fields=["state", "run", "updated"])
        return run
    return None
