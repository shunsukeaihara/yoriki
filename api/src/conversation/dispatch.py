"""会話のメッセージの受付と、Runの開始（design.md §5.1、§5.5）"""

from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from artifact.models import Artifact, ArtifactKind
from connector.models import Connector
from harness.connectors import DATASOURCE_TOOL_NAMES, ConnectorUnavailable
from harness.connectors import build_catalog as build_connector_catalog
from harness.connectors import explicit_text as explicit_connectors_text
from harness.connectors import resolve_explicit as resolve_explicit_connectors
from harness.connectors import usable_uids as usable_connector_uids
from harness.skills import (
    SkillUnavailable,
    build_catalog,
    catalog_connectors,
    declared_connectors,
    explicit_text,
    resolve_explicit,
    restrict_catalog,
)
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


class MessageMode:
    AUTO = "auto"  # 実行中なら今の作業への追加指示、確認待ちなら回答、それ以外は新しい依頼
    NEXT = "next"  # 実行中なら、終わった後に始める次の依頼


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
) -> dict[str, object]:
    """利用者の発言のRunEventの内容。添付は /workspace/input の場所とあわせて残す（harness/context.py が読む）。

    Runの最初の発言（first）には、同じ会話でそれまでに送った添付も添える。/workspace/input には会話の添付が
    すべてあり、作業ファイルも前の依頼のCheckpointから引き継ぐので、前の依頼のファイルを使う依頼に答えられるように。
    `skills` は `/` で指定してRun開始時に解決したSkill（Manifestの内容）。本文も添え、モデルに `skill` を呼ばせない。
    `connectors` は `@` で指定したConnector。公開している表の説明を添える（harness.md §10.2）。
    `connector_catalog` はRunに固定したConnectorの一覧で、Skillの本文のConnectorの名前に使う
    """
    data: dict[str, object] = {"text": text, "message": message.uid}
    if skills:
        data["skills"] = skills
        data["skills_text"] = explicit_text(skills, connector_catalog)
    if connectors:
        data["connectors"] = connectors
        data["connectors_text"] = explicit_connectors_text(connectors)
    if attachments := describe_attachments(message):
        data["attachments"] = attachments
    if first:
        earlier = (
            Artifact.objects.sent_inputs(message.conversation_id)
            .exclude(messages=message)
            .select_related("blob")
            .order_by("-pk")[:EARLIER_ATTACHMENTS_LIMIT]
        )
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
    mode: str,
    actor,
    attachments: list[Artifact] | None = None,
    skills: list[Skill] | None = None,
    connectors: list[Connector] | None = None,
    skill_edit: dict[str, str] | None = None,
):
    """利用者のメッセージを保存し、新しいRun・追加指示・確認への回答・次の依頼のどれかとして受け付ける。

    Skill・Connectorを指定したメッセージは、Skillの版と使えるConnectorの一覧をRun開始時に決めるので、実行中なら
    次の依頼にする。Skillの編集の依頼（`skill_edit`。{"id", "name"}、新しいSkillならidが空）も、別のRunになるので
    同じく次の依頼にする
    """
    conversation = Conversation.objects.select_for_update().get(pk=conversation.pk)
    if client_message_id:
        sent = ConversationMessage.objects.filter(
            conversation=conversation, client_message_id=client_message_id
        ).first()
        if sent is not None:
            return sent  # 再送。二重に受け付けない
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
        Artifact.attach(conversation, unsent)
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
    separate = bool(skills) or bool(connectors) or bool(skill_edit)  # 別のRunとして始める依頼
    question = ClarificationRequest.objects.filter(
        run=run, status=ClarificationStatus.OPEN, kind__in=(ClarificationKind.QUESTION, ClarificationKind.SKILL_EDIT)
    ).first()
    if mode == MessageMode.AUTO and question is not None and run.status == RunStatus.WAITING_USER and not separate:
        # 確認待ちのときのメッセージは、その確認への自由記述の回答にする
        question.accept_answer(
            [{"selected": [], "text": ""} for _q in question.questions],
            body,
            actor,
            attachments=describe_attachments(message),
        )
        return message
    kind = (
        InstructionKind.NEXT
        if mode == MessageMode.NEXT or run.status == RunStatus.CANCELLING or separate
        else InstructionKind.CURRENT
    )
    UserInstruction.objects.create(
        conversation=conversation, message=message, run=run if kind == InstructionKind.CURRENT else None, kind=kind
    )
    return message


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
    if connectors and skills:
        tools = SkillVersion.objects.filter(pk__in=[s["version"] for s in skills]).values_list("tools", flat=True)
        if not any(name in DATASOURCE_TOOL_NAMES for names in tools for name in names):
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
    # Skillの編集の依頼は、対象のSkillを固定した編集のRunにする（harness.md §10.9）。それ以外では、`/` で指定した
    # Skillをその時点の公開中の版へ解決して固定し、指定がなければ、エージェントが選べるSkillの一覧を固定する
    # （Toolの一覧と `skill` の引数をRunの中で変えない。harness.md §7.5・§10.1）
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
    # Skillの一覧には、宣言したConnectorをこのRunで使えるものだけを出す（design.md §4.9.1）
    catalog = (
        build_catalog(
            conversation.owner,
            version,
            sensitive=sensitive,
            connectors=usable_connector_uids(conversation.owner, sensitive=sensitive),
        )
        if not skills and skill_edit is None and version.supports_tools
        else None
    )
    # エージェントが使えるConnectorの一覧も固定する（Toolの引数をRunの中で変えない。harness.md §10.2）。
    # 使えるかは操作のたびに確かめ直す。`@` で指定したものと、Skillが宣言したものを先に並べる
    connectors = resolve_connectors(message, conversation.owner, version, skills, sensitive=sensitive)
    connector_catalog = (
        build_connector_catalog(
            conversation.owner,
            sensitive=sensitive,
            explicit=[*connectors, *declared_connectors(skills), *catalog_connectors(catalog)],
        )
        if version.supports_tools
        else None
    )
    if catalog is not None:
        catalog = restrict_catalog(catalog, {e["connector"] for e in (connector_catalog or {}).get("connectors", [])})
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
        # 同じ会話の前の依頼。作業ファイルはその最後のCheckpointから引き継ぐ（design.md §7.9）
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
