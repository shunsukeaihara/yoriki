import pytest
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile

from artifact.tests.test_api import body_of
from resources.models import ResourceVersion
from skill.factories import SkillDraftFactory, SkillFactory, SkillVersionFactory
from skill.models import Skill, SkillChange, SkillChangeKind, SkillDraft, SkillScope, SkillVersion
from user.factories import DepartmentFactory, DepartmentMembershipFactory
from user.models import DepartmentRole
from user.roles import sync_role_groups

SKILLS = "/api/app/v1/skills/"


def _url(skill: Skill, rest: str = "") -> str:
    return f"{SKILLS}{skill.uid}/{rest}"


def _upload(name: str, content: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type="text/plain")


def _skill_publisher(user):
    sync_role_groups()
    user.groups.add(Group.objects.get(name="Skill公開者"))
    return user


def _create(api_client, user, **data) -> dict:
    api_client.force_login(user)
    body = {"scope": SkillScope.PERSONAL, "name": "請求書の照合", "description": "請求書を照合する", **data}
    res = api_client.post(SKILLS, body, format="json")
    assert res.status_code == 201, res.json()
    return res.json()


def _draft(api_client, skill_id: str) -> dict:
    res = api_client.get(f"{SKILLS}{skill_id}/drafts/")
    assert res.status_code == 200, res.json()
    assert len(res.json()) == 1
    return res.json()[0]


def _publish(api_client, skill_id: str, draft: dict, **extra):
    return api_client.post(
        f"{SKILLS}{skill_id}/publish/", {"draft": draft["id"], "revision": draft["revision"], **extra}, format="json"
    )


class TestVisibility:
    @pytest.mark.django_db
    def test_scope_decides_who_sees(self, api_client, app_user, other_user):
        department = DepartmentFactory.create()
        DepartmentMembershipFactory.create(department=department, user=app_user)
        mine = SkillFactory.create(created_by=app_user)
        others = SkillFactory.create(created_by=other_user)
        in_department = SkillFactory.create(scope=SkillScope.DEPARTMENT, department=department, created_by=other_user)
        other_department = SkillFactory.create(
            scope=SkillScope.DEPARTMENT, department=DepartmentFactory.create(), created_by=other_user
        )
        company = SkillFactory.create(scope=SkillScope.COMPANY, created_by=other_user)
        for skill in (in_department, other_department, company):
            SkillVersionFactory.create(skill=skill)
        SkillFactory.create(created_by=app_user, deleted=True)

        api_client.force_login(app_user)
        ids = {s["id"] for s in api_client.get(SKILLS).json()["results"]}
        assert ids == {mine.uid, in_department.uid, company.uid}
        assert api_client.get(_url(others)).status_code == 404
        assert api_client.get(_url(other_department)).status_code == 404
        assert api_client.get(_url(other_department, "versions/")).status_code == 404

    @pytest.mark.django_db
    def test_unpublished_skill_is_only_for_editors(self, api_client, app_user, other_user):
        department = DepartmentFactory.create()
        membership = DepartmentMembershipFactory.create(department=department, user=app_user)
        in_department = SkillFactory.create(scope=SkillScope.DEPARTMENT, department=department, created_by=other_user)
        company = SkillFactory.create(scope=SkillScope.COMPANY, created_by=other_user)
        api_client.force_login(app_user)
        assert api_client.get(SKILLS).json()["results"] == []
        assert api_client.get(_url(company)).status_code == 404

        membership.role = DepartmentRole.EDITOR
        membership.save()
        _skill_publisher(app_user)
        results = api_client.get(SKILLS).json()["results"]
        assert {s["id"] for s in results} == {in_department.uid, company.uid}

        # 公開すれば、使うだけの人にも見える。Draftは見えない
        SkillVersionFactory.create(skill=company)
        SkillDraftFactory.create(skill=company)
        api_client.force_login(other_user)
        res = api_client.get(_url(company))
        assert res.json()["access"] == 1
        assert res.json()["draft"] is None

    @pytest.mark.django_db
    def test_deleted_department_hides_its_skills(self, api_client, app_user):
        department = DepartmentFactory.create()
        DepartmentMembershipFactory.create(department=department, user=app_user, role=DepartmentRole.PUBLISHER)
        skill = SkillFactory.create(scope=SkillScope.DEPARTMENT, department=department, created_by=app_user)
        department.deleted = True
        department.save()
        api_client.force_login(app_user)
        assert api_client.get(_url(skill)).status_code == 404

    @pytest.mark.django_db
    def test_requires_login(self, api_client):
        assert api_client.get(SKILLS).status_code == 401


class TestCreate:
    @pytest.mark.django_db
    def test_personal_skill_starts_with_a_draft(self, api_client, app_user):
        data = _create(api_client, app_user, instructions="1. 読む", tools=["read", "read", "grep"])
        assert data["scope"] == SkillScope.PERSONAL
        assert data["version"] == 0
        assert data["access"] == 3
        skill = Skill.objects.get(uid=data["id"])
        assert skill.owner == app_user
        draft = SkillDraft.objects.get(skill=skill)
        assert data["draft"] == draft.uid
        assert draft.tools == ["read", "grep"]
        assert draft.base_version is None

    @pytest.mark.django_db
    def test_same_name_in_same_scope(self, api_client, app_user, other_user):
        _create(api_client, app_user)
        api_client.force_login(app_user)
        res = api_client.post(SKILLS, {"scope": SkillScope.PERSONAL, "name": " 請求書の照合 "}, format="json")
        assert res.status_code == 400
        # 別の人の個人の範囲なら同じ名前でよい
        _create(api_client, other_user)

    @pytest.mark.django_db
    def test_department_needs_editor(self, api_client, app_user):
        department = DepartmentFactory.create()
        membership = DepartmentMembershipFactory.create(department=department, user=app_user)
        api_client.force_login(app_user)
        body = {"scope": SkillScope.DEPARTMENT, "department": department.uid, "name": "部門のSkill"}
        assert api_client.post(SKILLS, body, format="json").status_code == 403
        assert api_client.post(SKILLS, {**body, "department": None}, format="json").status_code == 400

        membership.role = DepartmentRole.EDITOR
        membership.save()
        res = api_client.post(SKILLS, body, format="json")
        assert res.status_code == 201, res.json()
        assert res.json()["department_info"]["id"] == department.uid
        assert res.json()["access"] == 2
        # 編集の役割ではToolを決められない（design.md §3.6）
        res = api_client.post(SKILLS, {**body, "name": "別のSkill", "tools": ["shell"]}, format="json")
        assert res.status_code == 403

    @pytest.mark.django_db
    def test_company_needs_permission(self, api_client, app_user):
        api_client.force_login(app_user)
        body = {"scope": SkillScope.COMPANY, "name": "全社のSkill", "tools": ["shell"]}
        assert api_client.post(SKILLS, body, format="json").status_code == 403
        _skill_publisher(app_user)
        assert api_client.post(SKILLS, body, format="json").status_code == 201

    @pytest.mark.django_db
    def test_invalid_schema(self, api_client, app_user):
        api_client.force_login(app_user)
        res = api_client.post(
            SKILLS,
            {"scope": SkillScope.PERSONAL, "name": "x", "output_schema": {"type": "no-such-type"}},
            format="json",
        )
        assert res.status_code == 400
        assert "output_schema" in res.json()

    @pytest.mark.django_db
    def test_result_schema_must_fit_every_provider(self, api_client, app_user):
        """業務結果のSchemaは submit_result の引数になる（harness.md §7.3）"""
        api_client.force_login(app_user)
        schema = {"type": "object", "properties": {"verdict": {"anyOf": [{"type": "string"}, {"type": "null"}]}}}
        res = api_client.post(
            SKILLS, {"scope": SkillScope.PERSONAL, "name": "x", "output_schema": schema}, format="json"
        )
        assert res.status_code == 400
        assert "anyOf" in res.json()["output_schema"][0]

    @pytest.mark.django_db
    def test_unknown_tool(self, api_client, app_user):
        api_client.force_login(app_user)
        res = api_client.post(SKILLS, {"scope": SkillScope.PERSONAL, "name": "x", "tools": ["rm_rf"]}, format="json")
        assert res.status_code == 400


class TestDraftAndPublish:
    @pytest.mark.django_db
    def test_edit_and_publish(self, api_client, app_user):
        skill_id = _create(api_client, app_user)["id"]
        draft = _draft(api_client, skill_id)
        url = f"{SKILLS}{skill_id}/drafts/{draft['id']}/"

        res = api_client.patch(
            url, {"revision": draft["revision"], "instructions": "1. 読む\r\n2. 照合する  "}, format="json"
        )
        assert res.status_code == 200, res.json()
        assert res.json()["instructions"] == "1. 読む\n2. 照合する"
        assert res.json()["revision"] == draft["revision"] + 1
        assert api_client.patch(url, {"instructions": "revisionなし"}, format="json").status_code == 400
        # 編集を始めた時点のrevisionが古ければ409
        stale = api_client.patch(url, {"revision": draft["revision"], "instructions": "古い"}, format="json")
        assert stale.status_code == 409

        draft = _draft(api_client, skill_id)
        res = _publish(api_client, skill_id, draft, change_reason="最初の版", operation_key="op-1")
        assert res.status_code == 201, res.json()
        assert res.json()["number"] == 1
        assert res.json()["instructions"] == "1. 読む\n2. 照合する"

        skill = Skill.objects.get(uid=skill_id)
        assert skill.current_version is not None
        assert skill.current_version.number == 1
        assert not SkillDraft.objects.filter(skill=skill).exists()
        change = SkillChange.objects.get(skill=skill)
        assert change.kind == SkillChangeKind.PUBLISH
        assert change.previous_version is None
        assert "instructions" in change.summary["fields"]
        # 最初の版でも、空のままの項目は変わった項目にしない
        assert "requires" not in change.summary["fields"]
        assert "input_schema" not in change.summary["fields"]

        # 再送しても版を重ねて作らない（Draftは閉じていても、同じ結果を返す）
        again = _publish(api_client, skill_id, draft, operation_key="op-1")
        assert again.status_code == 201
        assert again.json()["id"] == res.json()["id"]
        assert SkillVersion.objects.filter(skill=skill).count() == 1
        # operation keyのない再送は、閉じたDraftなので409
        assert _publish(api_client, skill_id, draft).status_code == 409
        # 公開のkeyを、別のDraftの公開や切戻しに使い回せない（別のDraftが閉じた後でも）
        draft = api_client.post(f"{SKILLS}{skill_id}/drafts/", {}, format="json").json()
        assert _publish(api_client, skill_id, draft, operation_key="op-1").status_code == 409
        assert _publish(api_client, skill_id, draft, operation_key="op-2").status_code == 201
        assert _publish(api_client, skill_id, draft, operation_key="op-1").status_code == 409
        rollback = {"number": 1, "expected_version": 2, "operation_key": "op-1"}
        assert api_client.post(f"{SKILLS}{skill_id}/rollback/", rollback, format="json").status_code == 409

    @pytest.mark.django_db
    def test_cannot_publish_without_instructions(self, api_client, app_user):
        skill_id = _create(api_client, app_user)["id"]
        res = _publish(api_client, skill_id, _draft(api_client, skill_id))
        assert res.status_code == 400
        assert not SkillVersion.objects.exists()

    @pytest.mark.django_db
    def test_one_draft_per_skill(self, api_client, app_user):
        skill_id = _create(api_client, app_user)["id"]
        assert api_client.post(f"{SKILLS}{skill_id}/drafts/", {}, format="json").status_code == 409

    @pytest.mark.django_db
    def test_publish_conflicts_with_newer_version(self, api_client, app_user):
        version = SkillVersionFactory.create(skill=SkillFactory.create(created_by=app_user))
        skill = version.skill
        api_client.force_login(app_user)
        res = api_client.post(_url(skill, "drafts/"), {}, format="json")
        assert res.status_code == 201, res.json()
        draft = res.json()
        assert draft["base_version"] == 1
        assert draft["instructions"] == version.instructions
        # Draftを開いた後に、別の版が公開された
        SkillVersionFactory.create(skill=skill)
        assert _publish(api_client, skill.uid, draft).status_code == 409

    @pytest.mark.django_db
    def test_open_draft_from_old_version(self, api_client, app_user):
        skill = SkillFactory.create(created_by=app_user)
        SkillVersionFactory.create(skill=skill, instructions="第1版")
        SkillVersionFactory.create(skill=skill, instructions="第2版")
        api_client.force_login(app_user)
        res = api_client.post(_url(skill, "drafts/"), {"base_version": 1}, format="json")
        assert res.status_code == 201, res.json()
        assert res.json()["instructions"] == "第1版"
        # 元にしたのは第1版でも、公開するときに確かめるのは開いたときの公開中の版
        assert res.json()["base_version"] == 2
        assert _publish(api_client, skill.uid, res.json()).status_code == 201
        assert api_client.post(_url(skill, "drafts/"), {"base_version": 9}, format="json").status_code == 400

    @pytest.mark.django_db
    def test_discard_draft(self, api_client, app_user):
        skill_id = _create(api_client, app_user)["id"]
        draft = _draft(api_client, skill_id)
        url = f"{SKILLS}{skill_id}/drafts/{draft['id']}/"
        api_client.patch(url, {"revision": draft["revision"], "instructions": "他の人の変更"}, format="json")
        # 古い画面からの破棄で、その後の変更を黙って消さない
        assert api_client.delete(f"{url}?revision={draft['revision']}").status_code == 409
        assert api_client.delete(f"{url}?revision={draft['revision'] + 1}").status_code == 204
        assert api_client.get(f"{SKILLS}{skill_id}/drafts/").json() == []


class TestDepartmentRoles:
    @pytest.fixture(name="department_skill")
    def fixture_department_skill(self, other_user) -> Skill:
        department = DepartmentFactory.create()
        skill = SkillFactory.create(scope=SkillScope.DEPARTMENT, department=department, created_by=other_user)
        SkillVersionFactory.create(skill=skill, created_by=other_user)
        return skill

    def _join(self, skill: Skill, user, role: DepartmentRole):
        DepartmentMembershipFactory.create(department=skill.department, user=user, role=role)

    @pytest.mark.django_db
    def test_member_can_only_use(self, api_client, app_user, department_skill):
        self._join(department_skill, app_user, DepartmentRole.MEMBER)
        api_client.force_login(app_user)
        assert api_client.get(_url(department_skill)).json()["access"] == 1
        assert api_client.get(_url(department_skill, "versions/1/")).status_code == 200
        assert api_client.get(_url(department_skill, "drafts/")).status_code == 403
        assert api_client.post(_url(department_skill, "drafts/"), {}, format="json").status_code == 403
        assert api_client.post(_url(department_skill, "suspend/"), {}, format="json").status_code == 403

    @pytest.mark.django_db
    def test_editor_edits_but_cannot_publish_or_change_tools(self, api_client, app_user, department_skill):
        self._join(department_skill, app_user, DepartmentRole.EDITOR)
        api_client.force_login(app_user)
        draft = api_client.post(_url(department_skill, "drafts/"), {}, format="json").json()
        url = _url(department_skill, f"drafts/{draft['id']}/")
        res = api_client.patch(url, {"revision": draft["revision"], "instructions": "直した"}, format="json")
        assert res.status_code == 200
        res = api_client.patch(url, {"revision": res.json()["revision"], "tools": ["shell"]}, format="json")
        assert res.status_code == 403
        # 同じToolを送るだけなら変更ではない
        same = api_client.patch(url, {"revision": draft["revision"] + 1, "tools": []}, format="json")
        assert same.status_code == 200
        assert _publish(api_client, department_skill.uid, same.json()).status_code == 403
        assert api_client.patch(_url(department_skill), {"name": "改名"}, format="json").status_code == 403
        assert api_client.delete(_url(department_skill)).status_code == 403

    @pytest.mark.django_db
    def test_publisher_publishes(self, api_client, app_user, department_skill):
        self._join(department_skill, app_user, DepartmentRole.PUBLISHER)
        api_client.force_login(app_user)
        draft = api_client.post(_url(department_skill, "drafts/"), {}, format="json").json()
        res = api_client.patch(
            _url(department_skill, f"drafts/{draft['id']}/"),
            {"revision": draft["revision"], "tools": ["read"]},
            format="json",
        )
        assert res.status_code == 200
        assert _publish(api_client, department_skill.uid, res.json()).status_code == 201
        department_skill.refresh_from_db()
        assert department_skill.current_version.tools == ["read"]

    @pytest.mark.django_db
    def test_company_editor(self, api_client, app_user, other_user):
        skill = SkillFactory.create(scope=SkillScope.COMPANY, created_by=_skill_publisher(other_user))
        SkillVersionFactory.create(skill=skill)
        group = Group.objects.create(name="編集者")
        group.permissions.add(
            *Group.objects.get(name="Skill公開者").permissions.filter(codename="can_edit_company_skills")
        )
        app_user.groups.add(group)
        api_client.force_login(app_user)
        assert api_client.get(_url(skill)).json()["access"] == 2
        draft = api_client.post(_url(skill, "drafts/"), {}, format="json").json()
        assert _publish(api_client, skill.uid, draft).status_code == 403
        # 公開の権限を持つ人が、残された変更案を公開する
        api_client.force_login(other_user)
        assert _publish(api_client, skill.uid, draft).status_code == 201


class TestAssets:
    @pytest.mark.django_db
    def test_upload_replace_and_publish(self, api_client, app_user):
        skill_id = _create(api_client, app_user, instructions="1. 読む")["id"]
        draft = _draft(api_client, skill_id)
        assets = f"{SKILLS}{skill_id}/drafts/{draft['id']}/assets/"

        res = api_client.post(
            assets,
            {
                "key": "vendor-master",
                "role": 2,
                "description": "取引先",
                "file": _upload("vendors.csv", b"code,name\n"),
                "revision": draft["revision"],
            },
            format="multipart",
        )
        assert res.status_code == 201, res.json()
        assert res.json()["file_info"]["name"] == "vendors.csv"
        assert res.json()["file_info"]["number"] == 1
        assert res.json()["draft_revision"] == draft["revision"] + 1
        first_file = res.json()["file_info"]["id"]

        # 同じkeyは足せない。keyの形も確かめる
        revision = res.json()["draft_revision"]
        dup = api_client.post(
            assets,
            {"key": "vendor-master", "role": 2, "file": _upload("a.csv", b"x"), "revision": revision},
            format="multipart",
        )
        assert dup.status_code == 400
        bad = api_client.post(
            assets,
            {"key": "../etc", "role": 2, "file": _upload("a.csv", b"x"), "revision": revision},
            format="multipart",
        )
        assert bad.status_code == 400

        draft = _draft(api_client, skill_id)
        assert _publish(api_client, skill_id, draft).status_code == 201

        # 次のDraftで置き換えても、公開済みの第1版は前の原本を使い続ける
        draft = api_client.post(f"{SKILLS}{skill_id}/drafts/", {}, format="json").json()
        assert [a["key"] for a in draft["assets"]] == ["vendor-master"]
        asset_url = f"{SKILLS}{skill_id}/drafts/{draft['id']}/assets/vendor-master/"
        assert api_client.patch(asset_url, {"description": "revisionなし"}, format="multipart").status_code == 400
        replace = {"file": _upload("vendors-v2.csv", b"code,name\n1,A\n"), "revision": draft["revision"]}
        res = api_client.patch(asset_url, replace, format="multipart")
        assert res.status_code == 200, res.json()
        # 同じ資料を、同じ時点のDraftから別のファイルで置き換えようとすると409（先の置換を黙って上書きしない）
        other = {"file": _upload("other.csv", b"x"), "revision": draft["revision"]}
        assert api_client.patch(asset_url, other, format="multipart").status_code == 409
        assert res.json()["description"] == "取引先"
        assert res.json()["file_info"]["number"] == 2
        assert res.json()["file_info"]["resource"] == draft["assets"][0]["file_info"]["resource"]

        draft = _draft(api_client, skill_id)
        version = _publish(api_client, skill_id, draft).json()
        assert version["number"] == 2
        assert version["assets"][0]["file"]["name"] == "vendors-v2.csv"
        old = api_client.get(f"{SKILLS}{skill_id}/versions/1/").json()
        assert old["assets"][0]["file"]["id"] == first_file
        change = SkillChange.objects.filter(skill__uid=skill_id).latest("pk")
        assert change.summary["assets_replaced"] == ["vendor-master"]

        res = api_client.get(f"{SKILLS}{skill_id}/files/{first_file}/download/")
        assert res.status_code == 200
        assert body_of(res) == b"code,name\n"

    @pytest.mark.django_db
    def test_remove_asset(self, api_client, app_user):
        skill_id = _create(api_client, app_user)["id"]
        draft = _draft(api_client, skill_id)
        assets = f"{SKILLS}{skill_id}/drafts/{draft['id']}/assets/"
        api_client.post(
            assets,
            {"key": "rules", "role": 1, "file": _upload("rules.md", b"# rules"), "revision": draft["revision"]},
            format="multipart",
        )
        assert api_client.delete(f"{assets}rules/").status_code == 400
        assert api_client.delete(f"{assets}rules/?revision={draft['revision']}").status_code == 409
        assert api_client.delete(f"{assets}rules/?revision={draft['revision'] + 1}").status_code == 204
        assert api_client.get(assets).json() == []
        assert _draft(api_client, skill_id)["revision"] == draft["revision"] + 2

    @pytest.mark.django_db
    def test_draft_files_are_hidden_from_users(self, api_client, app_user, other_user):
        skill = SkillFactory.create(scope=SkillScope.COMPANY, created_by=_skill_publisher(other_user))
        SkillVersionFactory.create(skill=skill)
        api_client.force_login(other_user)
        draft = api_client.post(_url(skill, "drafts/"), {}, format="json").json()
        res = api_client.post(
            _url(skill, f"drafts/{draft['id']}/assets/"),
            {"key": "secret", "role": 1, "file": _upload("draft.md", b"draft"), "revision": draft["revision"]},
            format="multipart",
        )
        file_id = res.json()["file_info"]["id"]
        assert api_client.get(_url(skill, f"files/{file_id}/")).status_code == 200

        api_client.force_login(app_user)
        assert api_client.get(_url(skill, f"files/{file_id}/download/")).status_code == 404
        assert api_client.get(_url(skill, f"drafts/{draft['id']}/assets/")).status_code == 403
        # 別のSkillのURLからは、どの原本も取れない
        mine = SkillFactory.create(created_by=app_user)
        assert api_client.get(_url(mine, f"files/{file_id}/")).status_code == 404
        assert ResourceVersion.objects.filter(uid=file_id).exists()


class TestRollbackAndSuspend:
    @pytest.mark.django_db
    def test_rollback_makes_new_version(self, api_client, app_user):
        skill = SkillFactory.create(created_by=app_user)
        SkillVersionFactory.create(skill=skill, instructions="第1版", tools=["read"])
        SkillVersionFactory.create(skill=skill, instructions="第2版")
        api_client.force_login(app_user)

        res = api_client.post(
            _url(skill, "rollback/"), {"number": 1, "expected_version": 2, "operation_key": "rb"}, format="json"
        )
        assert res.status_code == 201, res.json()
        assert res.json()["number"] == 3
        assert res.json()["instructions"] == "第1版"
        assert res.json()["tools"] == ["read"]
        assert res.json()["rolled_back_from"] == 1
        again = api_client.post(
            _url(skill, "rollback/"), {"number": 1, "expected_version": 2, "operation_key": "rb"}, format="json"
        )
        assert again.json()["number"] == 3
        assert SkillVersion.objects.filter(skill=skill).count() == 3

        # 同じkeyを別の操作に使うと、前の結果を返さずに409
        reused = api_client.post(
            _url(skill, "rollback/"), {"number": 2, "expected_version": 3, "operation_key": "rb"}, format="json"
        )
        assert reused.status_code == 409
        stale = api_client.post(_url(skill, "rollback/"), {"number": 2, "expected_version": 2}, format="json")
        assert stale.status_code == 409
        current = api_client.post(_url(skill, "rollback/"), {"number": 3, "expected_version": 3}, format="json")
        assert current.status_code == 400
        history = api_client.get(_url(skill, "changes/")).json()["results"]
        assert [c["kind"] for c in history] == [SkillChangeKind.ROLLBACK]

    @pytest.mark.django_db
    def test_suspend_and_resume(self, api_client, app_user):
        skill = SkillVersionFactory.create(skill=SkillFactory.create(created_by=app_user)).skill
        api_client.force_login(app_user)
        res = api_client.post(_url(skill, "suspend/"), {"reason": "誤った手順"}, format="json")
        assert res.status_code == 200
        assert res.json()["suspended"] is True
        api_client.post(_url(skill, "suspend/"), {}, format="json")
        api_client.post(_url(skill, "resume/"), {}, format="json")
        kinds = list(SkillChange.objects.filter(skill=skill).order_by("pk").values_list("kind", flat=True))
        assert kinds == [SkillChangeKind.SUSPEND, SkillChangeKind.RESUME]

    @pytest.mark.django_db
    def test_delete(self, api_client, app_user):
        skill = SkillFactory.create(created_by=app_user)
        SkillDraftFactory.create(skill=skill)
        api_client.force_login(app_user)
        assert api_client.delete(_url(skill)).status_code == 204
        skill.refresh_from_db()
        assert skill.deleted
        assert api_client.get(_url(skill)).status_code == 404


class TestOtherUsersSkill:
    @pytest.mark.django_db
    def test_cannot_touch_personal_skill_of_others(self, api_client, app_user, other_user):
        skill = SkillVersionFactory.create(skill=SkillFactory.create(created_by=other_user)).skill
        api_client.force_login(app_user)
        for res in (
            api_client.post(_url(skill, "drafts/"), {}, format="json"),
            api_client.post(_url(skill, "suspend/"), {}, format="json"),
            api_client.post(_url(skill, "rollback/"), {"number": 1, "expected_version": 1}, format="json"),
            api_client.get(_url(skill, "changes/")),
        ):
            assert res.status_code == 404


class TestAssetValidation:
    def _add(self, api_client, skill_id: str, draft: dict, name: str, content: bytes) -> dict:
        res = api_client.post(
            f"{SKILLS}{skill_id}/drafts/{draft['id']}/assets/",
            {"key": "vendors", "role": 2, "file": _upload(name, content), "revision": draft["revision"]},
            format="multipart",
        )
        assert res.status_code == 201, res.json()
        return res.json()

    @pytest.mark.django_db
    def test_checks_block_publishing_until_fixed(self, api_client, app_user):
        skill_id = _create(api_client, app_user, instructions="1. 読む")["id"]
        draft = _draft(api_client, skill_id)
        asset = self._add(api_client, skill_id, draft, "vendors.csv", b"code,name\n001,A\n001,B\n")
        assert asset["validation"]["format"] == "csv"
        assert asset["validation"]["errors"] == []
        assert asset["validation"]["info"]["rows"] == 2

        # キーの列を決めると検証し直し、重複が誤りになる。誤りがあれば公開できない
        url = f"{SKILLS}{skill_id}/drafts/{draft['id']}/assets/vendors/checks/"
        res = api_client.put(
            url, {"revision": asset["draft_revision"], "checks": {"key_columns": ["code"]}}, format="json"
        )
        assert res.status_code == 200, res.json()
        assert res.json()["checks"]["key_columns"] == ["code"]
        assert res.json()["validation"]["errors"] == ["キーが重複しています：001"]
        draft = _draft(api_client, skill_id)
        res = _publish(api_client, skill_id, draft)
        assert res.status_code == 400
        assert "資料「vendors」に検証の誤りがあります" in res.json()["non_field_errors"][0]

        # 直したファイルで置き換えれば公開でき、版に条件と検証の結果が残る
        patch = {"file": _upload("vendors.csv", b"code,name\n001,A\n002,B\n"), "revision": draft["revision"]}
        res = api_client.patch(f"{SKILLS}{skill_id}/drafts/{draft['id']}/assets/vendors/", patch, format="multipart")
        assert res.json()["validation"]["errors"] == []
        version = _publish(api_client, skill_id, _draft(api_client, skill_id)).json()
        assert version["assets"][0]["checks"]["key_columns"] == ["code"]
        assert version["assets"][0]["validation"]["errors"] == []

        # 次の版での置換は、公開中の版との差分を行ごとに示し、変更の記録に数を残す
        draft = api_client.post(f"{SKILLS}{skill_id}/drafts/", {}, format="json").json()
        assert draft["assets"][0]["validation"]["diff"] is None
        patch = {"file": _upload("vendors.csv", b"code,name\n001,A\n002,Bee\n003,C\n"), "revision": draft["revision"]}
        res = api_client.patch(f"{SKILLS}{skill_id}/drafts/{draft['id']}/assets/vendors/", patch, format="multipart")
        diff = res.json()["validation"]["diff"]
        assert (diff["rows_added"], diff["rows_changed"]) == (1, 1)
        assert diff["rows"][0]["key"] == "003"
        _publish(api_client, skill_id, _draft(api_client, skill_id))
        change = api_client.get(f"{SKILLS}{skill_id}/changes/").json()["results"][0]
        assert change["summary"]["assets_replaced"] == ["vendors"]
        assert change["asset_diffs"] == [{"key": "vendors", "rows_added": 1, "rows_removed": 0, "rows_changed": 1}]

        # 過去の版から開いたDraftは、公開中の版との差分を持つ
        draft = api_client.post(f"{SKILLS}{skill_id}/drafts/", {"base_version": 1}, format="json").json()
        diff = draft["assets"][0]["validation"]["diff"]
        assert (diff["rows_removed"], diff["rows_changed"]) == (1, 1)
        _publish(api_client, skill_id, draft)
        change = api_client.get(f"{SKILLS}{skill_id}/changes/").json()["results"][0]
        assert change["asset_diffs"][0]["rows_removed"] == 1

    @pytest.mark.django_db
    def test_editor_cannot_change_checks(self, api_client, app_user, other_user):
        department = DepartmentFactory.create()
        DepartmentMembershipFactory.create(department=department, user=app_user, role=DepartmentRole.EDITOR)
        skill = SkillFactory.create(scope=SkillScope.DEPARTMENT, department=department, created_by=other_user)
        draft = SkillDraftFactory.create(skill=skill, created_by=other_user, updated_by=other_user)
        api_client.force_login(app_user)
        draft_body = _draft(api_client, skill.uid)
        asset = self._add(api_client, skill.uid, draft_body, "vendors.csv", b"code\n1\n")
        url = _url(skill, f"drafts/{draft.uid}/assets/vendors/checks/")
        body = {"revision": asset["draft_revision"], "checks": {"required_columns": []}}
        # 条件を変えない送信は通し、変える送信は公開の権限がなければ403
        assert api_client.put(url, body, format="json").status_code == 200
        body = {"revision": asset["draft_revision"] + 1, "checks": {"required_columns": ["name"]}}
        assert api_client.put(url, body, format="json").status_code == 403

    @pytest.mark.django_db
    def test_same_file_keeps_the_version_and_unchanged_errors_do_not_block(self, api_client, app_user):
        skill_id = _create(api_client, app_user, instructions="1. 読む")["id"]
        draft = _draft(api_client, skill_id)
        content = b"code\n001\n001\n"
        asset = self._add(api_client, skill_id, draft, "vendors.csv", content)
        url = f"{SKILLS}{skill_id}/drafts/{draft['id']}/assets/vendors/"
        res = api_client.patch(
            url, {"file": _upload("vendors.csv", content), "revision": asset["draft_revision"]}, format="multipart"
        )
        assert res.json()["file_info"]["number"] == 1
        _publish(api_client, skill_id, _draft(api_client, skill_id))
        # 公開した後に条件を厳しくした版を作る（検証する前に公開した資料と同じ状況）
        skill = Skill.objects.get(uid=skill_id)
        assert skill.current_version is not None
        skill.current_version.assets.update(checks={"key_columns": ["code"]}, validation={})
        draft = api_client.post(f"{SKILLS}{skill_id}/drafts/", {}, format="json").json()
        # 開くときに検証し、誤りは見せるが、原本も条件も変えていなければ公開は止めない
        assert draft["assets"][0]["validation"]["errors"] == ["キーが重複しています：001"]
        res = api_client.patch(
            f"{SKILLS}{skill_id}/drafts/{draft['id']}/",
            {"description": "直した", "revision": draft["revision"]},
            format="json",
        )
        assert res.status_code == 200
        assert _publish(api_client, skill_id, _draft(api_client, skill_id)).status_code == 201

    @pytest.mark.django_db
    def test_changed_files_without_validation_are_checked_when_publishing(self, api_client, app_user):
        skill_id = _create(api_client, app_user, instructions="1. 読む")["id"]
        draft = _draft(api_client, skill_id)
        self._add(api_client, skill_id, draft, "vendors.csv", b"code\n001\n001\n")
        # 検証の仕組みを入れる前に置き換えた資料（検証の結果がない）
        SkillDraft.objects.get(uid=draft["id"]).assets.update(checks={"key_columns": ["code"]}, validation={})
        res = _publish(api_client, skill_id, _draft(api_client, skill_id))
        assert res.status_code == 400
        assert "キーが重複しています" in res.json()["non_field_errors"][0]
