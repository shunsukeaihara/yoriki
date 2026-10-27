"""会話・タスクでSkillを使う（harness.md §10.1、design.md §3.2）。

エージェントに見せるSkillの一覧と、入力欄の `/` で指定したSkillは、Run開始時に決めてManifestに固定する
（Toolの一覧と `skill` の引数のenumをRunの中で変えないため）。エージェントが途中で読み込んだSkillは、その時点の
公開中の版へ解決してManifestへ追記する。資料はSandboxの /workspace/master/skills/<Skill>/<key>/ に読取り専用で置く。
"""

import re
from typing import Any

from django.db.models import Case, IntegerField, Value, When
from django.utils.translation import gettext as _

from connector.models import Operation
from run.models import Run
from skill import connectors as skill_connectors
from skill.models import (
    Skill,
    SkillAction,
    SkillAssetBinding,
    SkillEvaluation,
    SkillEvaluationAsset,
    SkillScope,
    SkillVersion,
)

# Runが使うSkillの中身。公開した版か、Draftの評価に写したDraftの中身（design.md §3.7.1）
SkillContentSource = SkillVersion | SkillEvaluation

MASTER_DIR = "/workspace/master"
# 一覧に使う文脈の割合と、説明を短くする段階（文字数。日本語は1文字を1 tokenとして数える）
CATALOG_CONTEXT_RATIO = 0.02
DESCRIPTION_LIMITS = (300, 120, 40)
# 入力欄の `/` で1つのメッセージに指定できるSkillの数
MAX_EXPLICIT_SKILLS = 3


class SkillUnavailable(Exception):
    pass


def skill_dir(skill_uid: str) -> str:
    return f"{MASTER_DIR}/skills/{skill_uid}"


def asset_path(skill_uid: str, key: str, name: str) -> str:
    """資料のSandboxの中のパス。keyとファイル名はサーバーが検証した値なので、パスの外へは出ない（design.md §7.1）"""
    return f"{skill_dir(skill_uid)}/{key}/{name}"


def draft_asset_path(skill_uid: str, revision: int, key: str, name: str) -> str:
    """Skillの編集のRunで、Draftの資料を置くSandboxの中のパス（harness.md §10.9）"""
    return f"{MASTER_DIR}/drafts/{skill_uid}/r{revision}/{key}/{name}"


def scope_label(skill: Skill) -> str:
    if skill.scope == SkillScope.DEPARTMENT and skill.department is not None:
        return f"{skill.get_scope_display()} {skill.department.name}"
    return str(skill.get_scope_display())


def _one_line(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_catalog(user, model_version, *, sensitive: bool, connectors: set[str]) -> dict[str, Any]:
    """エージェントに見せる一覧：利用者が使え、使うモデルが必要な能力を持ち、宣言したConnectorがこのRunの一覧
    （`connectors`）にあり、機密の制限に合う（機密のSkillは機密のRunだけ）Skillの名前と説明（harness.md §10.1）。

    一覧の量は文脈の2%までにし、超えたら説明を短くして、それでも入らない分は数だけを示す。同じ名前のSkillが
    範囲（個人・部門・全社）の違いで重なれば、範囲を添えて分ける（`skill` の引数のenumにするため）
    """
    skills = (
        Skill.objects.usable_by(user)
        .select_related("current_version", "department")
        .annotate(
            # 本人のものを先に（同じ名前なら、より身近な範囲のものを先に見せる）
            order=Case(
                When(scope=SkillScope.PERSONAL, then=Value(0)),
                When(scope=SkillScope.DEPARTMENT, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        )
        .order_by("order", "name", "pk")
    )
    runnable = [s for s in skills if s.current_version and not s.current_version.unsupported_by(model_version)]
    declared = skill_connectors.load(s.current_version for s in runnable)
    usable = [
        s
        for s in runnable
        if not skill_connectors.unusable_reason(
            s.current_version, user, sensitive=sensitive, available=connectors, connectors=declared
        )
    ]
    counts: dict[str, int] = {}
    for skill in usable:
        counts[skill.name] = counts.get(skill.name, 0) + 1
    entries = []
    taken: set[str] = set()
    for skill in usable:
        name = skill.name if counts[skill.name] == 1 else f"{skill.name} ({scope_label(skill)})"
        # 範囲を添えても別のSkillの名前と重なれば（「X (個人)」という名前のSkill等）、番号を足す
        base, number = name, 2
        while name in taken:
            name, number = f"{base} ({number})", number + 1
        taken.add(name)
        version = skill.current_version
        entry = {"skill": skill.uid, "name": name, "description": version.description if version else ""}
        if version is not None and version.connectors:
            # Connectorの一覧に、宣言したConnectorを先に並べるため（restrict_catalog()）
            entry["connectors"] = skill_connectors.declared_uids(version)
        entries.append(entry)
    budget = int(model_version.max_input_tokens * CATALOG_CONTEXT_RATIO)
    for limit in DESCRIPTION_LIMITS:
        shortened = [{**e, "description": _one_line(e["description"], limit)} for e in entries]
        if sum(len(e["name"]) + len(e["description"]) + 30 for e in shortened) <= budget:
            return {"skills": shortened, "omitted": 0}
    listed: list[dict[str, Any]] = []
    used = 0
    for entry in shortened:
        used += len(entry["name"]) + len(entry["description"]) + 30
        if used > budget:
            break
        listed.append(entry)
    return {"skills": listed, "omitted": len(entries) - len(listed)}


def catalog_connectors(catalog: dict[str, Any] | None) -> list[dict[str, Any]]:
    """一覧のSkillが宣言したConnector。Connectorの一覧から数の上限で外れないように、先に並べる"""
    return [{"connector": uid} for e in (catalog or {}).get("skills", []) for uid in e.get("connectors", [])]


def restrict_catalog(catalog: dict[str, Any], listed: set[str]) -> dict[str, Any]:
    """宣言したConnectorがRunのConnectorの一覧にないSkillを外す（Connectorが多すぎて一覧に載らなかったとき）"""
    kept = [e for e in catalog["skills"] if all(uid in listed for uid in e.get("connectors", []))]
    return {"skills": kept, "omitted": catalog.get("omitted", 0) + len(catalog["skills"]) - len(kept)}


def catalog_text(catalog: dict[str, Any]) -> str:
    """`<runtime_context>` の中の `<skills>`"""
    lines = ["  <skills>"]
    for entry in catalog["skills"]:
        lines.append(f'    <skill name="{_escape(entry["name"])}">{_escape(entry["description"])}</skill>')
    if catalog.get("omitted"):
        lines.append(f"    ({catalog['omitted']} more skills are not listed)")
    lines.append("  </skills>")
    return "\n".join(lines)


def resolve_explicit(skills: list[Skill], user, model_version, *, sensitive: bool) -> list[dict[str, Any]]:
    """入力欄の `/` で指定したSkillを公開中の版へ解決する（design.md §3.2）。使えなければ SkillUnavailable。

    返すのはManifestに固定する内容と、最初の発言に入れる本文。業務結果のSchema（`submit_result` の引数）は
    1つのRunで1つだけにする
    """
    if len(skills) > MAX_EXPLICIT_SKILLS:
        raise SkillUnavailable(_("指定できるSkillは%(limit)s個までです") % {"limit": MAX_EXPLICIT_SKILLS})
    resolved = []
    for skill in skills:
        if reason := skill.unusable_reason(user, model_version, sensitive=sensitive):
            raise SkillUnavailable(_("%(name)s：%(reason)s") % {"name": skill.name, "reason": reason})
        assert skill.current_version_id is not None  # unusable_reason() が確かめた
        version = SkillVersion.objects.prefetch_related("assets__resource_version").get(pk=skill.current_version_id)
        resolved.append((skill, version))
    if sum(1 for _skill, version in resolved if version.output_schema) > 1:
        raise SkillUnavailable(_("業務結果の形を決めているSkillは、1つの依頼に1つだけ指定できます"))
    return [manifest_entry(skill, version) for skill, version in resolved]


def declared_connectors(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`/` で指定したSkillが宣言したConnector。Runの一覧から数の上限で外れないように、先に並べる"""
    versions = SkillVersion.objects.filter(pk__in=[e["version"] for e in entries if e.get("version")])
    return [{"connector": uid} for v in versions for uid in skill_connectors.declared_uids(v)]


def manifest_entry(skill: Skill, version: SkillVersion, name: str = "") -> dict[str, Any]:
    return {"skill": skill.uid, "name": name or skill.name, "version": version.pk, "number": version.number}


def evaluation_entry(evaluation: SkillEvaluation) -> dict[str, Any]:
    """Draftの評価のRunで、評価に写したDraftの中身を指すManifestの項目（版はない）"""
    skill = evaluation.skill
    return {"skill": skill.uid, "name": skill.name, "version": None, "number": None, "evaluation": evaluation.pk}


def content_key(entry: dict[str, Any]) -> str:
    """Manifestの項目が指す中身を区別するkey（同じ中身の資料を置き直さないために使う）"""
    if entry.get("evaluation"):
        return f"evaluation:{entry['evaluation']}"
    return f"version:{entry['version']}"


def load_contents(entries: list[dict[str, Any]]) -> dict[str, SkillContentSource]:
    """Manifestの項目が指す中身（content_key() ごと。資料の版も読む）。消えたものは含まない"""
    versions = (
        SkillVersion.objects.filter(pk__in=[e["version"] for e in entries if e.get("version")])
        .select_related("skill")
        .prefetch_related("assets__resource_version__blob")
    )
    evaluations = (
        SkillEvaluation.objects.filter(pk__in=[e["evaluation"] for e in entries if e.get("evaluation")])
        .select_related("skill")
        .prefetch_related("assets__resource_version__blob")
    )
    contents: dict[str, SkillContentSource] = {f"version:{v.pk}": v for v in versions}
    contents |= {f"evaluation:{e.pk}": e for e in evaluations}
    return contents


def loaded_entries(run: Run) -> list[dict[str, Any]]:
    """Runが使うSkill（`/` で指定して固定したものと、エージェントが読み込んで追記したもの）。同じSkillは最後のもの"""
    manifest = run.manifest or {}
    entries = list((manifest.get("fixed") or {}).get("skills", []))
    entries += [e for e in manifest.get("appended", []) if e.get("kind") == "skill"]
    latest: dict[str, dict[str, Any]] = {}
    for entry in entries:
        latest[entry["skill"]] = entry
    return list(latest.values())


def explicit_versions(run: Run) -> list[SkillContentSource]:
    """`/` で指定して固定したSkillの版（Draftの評価のRunでは、評価に写したDraftの中身）。Runの中では変わらない"""
    entries = list(((run.manifest or {}).get("fixed") or {}).get("skills", []))
    contents = load_contents(entries)
    return [contents[key] for e in entries if (key := content_key(e)) in contents]


def loaded_skills(run: Run) -> list[tuple[dict[str, Any], Skill | None, SkillContentSource | None]]:
    """Runが使うSkill（Manifestの記録、今のSkill、固定した版）。Skillが消えていればNone"""
    entries = loaded_entries(run)
    skills = {
        s.uid: s
        for s in Skill.objects.filter(uid__in=[e["skill"] for e in entries]).select_related(
            "department", "current_version"
        )
    }
    contents = load_contents(entries)
    return [(e, skills.get(e["skill"]), contents.get(content_key(e))) for e in entries]


def revoked_skill(run: Run) -> str:
    """Runが使うSkillのうち、停止・削除されたか、利用者が使えなくなったか（宣言したConnectorを使えなくなったものも）、
    機密でないRunで機密のSkillになった（宣言したConnectorに機密のフラグが付いた）ものがあれば、その理由。過去の版でも、
    実行時には拒否する（design.md §3.2・§4冒頭・§4.9.1）"""
    for entry, skill, version in loaded_skills(run):
        if skill is None or skill.deleted:
            reason = _("削除されました")
        elif skill.suspended:
            reason = _("停止中です")
        elif not skill.can(run.owner, SkillAction.VIEW):
            reason = _("使う権限がありません")
        elif version is not None and (
            problem := skill_connectors.unusable_reason(version, run.owner, sensitive=run.handles_sensitive_data)
        ):
            reason = problem
        else:
            continue
        return _("Skill「%(name)s」を使えなくなったので、作業を止めました（%(reason)s）。") % {
            "name": entry["name"],
            "reason": reason,
        }
    return ""


def unsupported_skills(run: Run, model_version) -> list[str]:
    """Runが使うSkillのうち、モデル設定の版が必要な能力を持たないものの名前（モデルの切り替えの条件。design.md §4.8）"""
    return [
        entry["name"]
        for entry, _skill, version in loaded_skills(run)
        if version is not None and version.unsupported_by(model_version)
    ]


def render(skill: Skill, version: SkillContentSource, *, name: str = "", result_tool: bool = True) -> str:
    """Skillの本文と資料の場所（harness.md §10.1）。`skill` の結果と、`/` で指定したときの最初の発言に入れる。

    `result_tool` は、このRunに submit_result があるか。途中で読み込んだSkillにはない（Toolの一覧はRun開始時に
    決める）ので、業務結果は最後の応答に書かせる
    """
    lines = [f'<skill name="{_escape(name or skill.name)}" version="{version.number}">']
    lines.append(f"<instructions>\n{version.instructions.strip()}\n</instructions>")
    assets: list[SkillAssetBinding | SkillEvaluationAsset] = sorted(version.assets.all(), key=lambda a: a.key)
    if assets:
        lines.append("<assets>")
        for asset in assets:
            description = f" — {_one_line(asset.description, 200)}" if asset.description else ""
            role = asset.get_role_display()
            path = asset_path(skill.uid, asset.key, asset.resource_version.name)
            lines.append(f"  {path} ({role}, version {asset.resource_version.number}){description}")
        lines.append("</assets>")
    if version.tools:
        lines.append(f"<tools>{', '.join(version.tools)}</tools>")
    else:
        lines.append("<tools>none (this skill does not run commands)</tools>")
    if version.connectors:
        lines.append(connectors_text(version))
    if version.output_schema:
        import json  # noqa: PLC0415  業務結果のSchemaがあるときだけ使う

        schema = json.dumps(version.output_schema, ensure_ascii=False)
        lines.append(f"<output_schema>\n{schema}\n</output_schema>")
        if not result_tool:
            lines.append(
                "<note>submit_result is not available in this work. Put the result in this form in your final "
                "reply instead.</note>"
            )
    lines.append("</skill>")
    return "\n".join(lines)


def connectors_text(version: SkillContentSource) -> str:
    """Skillが宣言したConnector（名前、操作のTool、表）。名前はこのRunのConnectorの一覧と同じもの"""
    connectors = skill_connectors.load([version])
    lines = ["<connectors>"]
    for declaration in version.connectors:
        connector = connectors.get(declaration["connector"])
        if connector is None:
            continue
        operations = ", ".join(declaration["operations"]) or ", ".join(Operation.values)
        tables = ", ".join(declaration.get("tables", [])) or "any published table"
        lines.append(f"  {_escape(connector.name)} — {operations} (tables: {_escape(tables)})")
    lines.append("</connectors>")
    return "\n".join(lines)


def explicit_text(entries: list[dict[str, Any]]) -> str:
    """`/` で指定したSkillの本文。最初の発言に添える（モデルに `skill` を呼ばせない）"""
    contents = load_contents(entries)
    blocks = [render(contents[content_key(e)].skill, contents[content_key(e)]) for e in entries]
    head = "The user chose these skills for this request. Follow them:"
    return head + "\n" + "\n\n".join(blocks)


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
