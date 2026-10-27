"""Skillの範囲ごとの規則（authorization.md §5.1）。

判定はsuperuserではなく、必要なpermissionと所属だけを付けたユーザーで試す（authorization.md §7.2）
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from skill.factories import SkillFactory, SkillVersionFactory
from skill.models import Skill, SkillAction, SkillScope
from user.factories import DepartmentFactory, DepartmentMembershipFactory, UserFactory, grant
from user.models import User
from utils.permissions import AppPermission as P

SKILLS = "/api/app/v1/skills/"


@pytest.fixture(name="world")
def fixture_world(db) -> dict:
    """部門2つと、範囲・状態の違うSkill"""
    sales = DepartmentFactory.create(name="営業部")
    accounting = DepartmentFactory.create(name="経理部")
    closed = DepartmentFactory.create(name="旧部門")
    owner = UserFactory.create()

    def skill(name: str, scope: SkillScope, *, department=None, published=True, **fields) -> Skill:
        created = SkillFactory.create(name=name, scope=scope, department=department, created_by=owner, **fields)
        if published:
            SkillVersionFactory.create(skill=created)
        return Skill.objects.get(pk=created.pk)

    skills = {
        "personal": skill("個人", SkillScope.PERSONAL),
        "sales": skill("営業", SkillScope.DEPARTMENT, department=sales),
        "sales_draft": skill("営業の未公開", SkillScope.DEPARTMENT, department=sales, published=False),
        "sales_suspended": skill("営業の停止", SkillScope.DEPARTMENT, department=sales, suspended=True),
        "accounting": skill("経理", SkillScope.DEPARTMENT, department=accounting),
        "closed": skill("旧部門", SkillScope.DEPARTMENT, department=closed),
        "company": skill("全社", SkillScope.COMPANY),
        "company_draft": skill("全社の未公開", SkillScope.COMPANY, published=False),
        "system": skill("システム", SkillScope.SYSTEM),
    }
    closed.deleted = True
    closed.save()
    return {"sales": sales, "accounting": accounting, "owner": owner, "skills": skills}


def _member(department, *perms) -> User:
    user = UserFactory.create()
    DepartmentMembershipFactory.create(department=department, user=user)
    return grant(user, *perms)


def _users(world) -> dict[str, User]:
    sales = world["sales"]
    return {
        "owner": world["owner"],
        "member": _member(sales),
        "outsider": UserFactory.create(),
        "editor": _member(sales, P.EDIT_DEPARTMENT_SKILLS),
        "publisher": _member(sales, P.PUBLISH_DEPARTMENT_SKILLS),
        # permissionがあっても、所属していない部門のSkillには効かない
        "outside_editor": grant(UserFactory.create(), P.EDIT_DEPARTMENT_SKILLS),
        # すべての部門を使えるだけでは、編集は広がらない
        "auditor": _member(sales, P.USE_ALL_DEPARTMENTS, P.EDIT_DEPARTMENT_SKILLS),
        "all_manager": grant(UserFactory.create(), P.MANAGE_ALL_DEPARTMENTS, P.EDIT_DEPARTMENT_SKILLS),
        "company_publisher": grant(UserFactory.create(), P.EDIT_COMPANY_SKILLS, P.PUBLISH_COMPANY_SKILLS),
        "system_admin": grant(UserFactory.create(), P.EDIT_SYSTEM_SKILLS, P.PUBLISH_SYSTEM_SKILLS),
        "platform_admin": UserFactory.create(is_superuser=True),
        "inactive": UserFactory.create(is_active=False),
    }


# 利用者ごとに、見える・使える・編集できる・公開できるSkill
EXPECTED: dict[str, dict[SkillAction, set[str]]] = {
    "owner": {
        SkillAction.VIEW: {"personal", "company"},
        SkillAction.USE: {"personal", "company"},
        SkillAction.EDIT: {"personal"},
        SkillAction.PUBLISH: {"personal"},
    },
    "member": {
        SkillAction.VIEW: {"sales", "sales_suspended", "company"},
        SkillAction.USE: {"sales", "company"},
        SkillAction.EDIT: set(),
        SkillAction.PUBLISH: set(),
    },
    "outsider": {
        SkillAction.VIEW: {"company"},
        SkillAction.USE: {"company"},
        SkillAction.EDIT: set(),
        SkillAction.PUBLISH: set(),
    },
    "editor": {
        SkillAction.VIEW: {"sales", "sales_draft", "sales_suspended", "company"},
        SkillAction.USE: {"sales", "company"},
        SkillAction.EDIT: {"sales", "sales_draft", "sales_suspended"},
        SkillAction.PUBLISH: set(),
    },
    "publisher": {
        SkillAction.VIEW: {"sales", "sales_draft", "sales_suspended", "company"},
        SkillAction.USE: {"sales", "company"},
        SkillAction.EDIT: {"sales", "sales_draft", "sales_suspended"},
        SkillAction.PUBLISH: {"sales", "sales_draft", "sales_suspended"},
    },
    "outside_editor": {
        SkillAction.VIEW: {"company"},
        SkillAction.USE: {"company"},
        SkillAction.EDIT: set(),
        SkillAction.PUBLISH: set(),
    },
    "auditor": {
        SkillAction.VIEW: {"sales", "sales_draft", "sales_suspended", "accounting", "company"},
        SkillAction.USE: {"sales", "accounting", "company"},
        SkillAction.EDIT: {"sales", "sales_draft", "sales_suspended"},
        SkillAction.PUBLISH: set(),
    },
    "all_manager": {
        SkillAction.VIEW: {"sales", "sales_draft", "sales_suspended", "accounting", "company"},
        SkillAction.USE: {"sales", "accounting", "company"},
        SkillAction.EDIT: {"sales", "sales_draft", "sales_suspended", "accounting"},
        SkillAction.PUBLISH: set(),
    },
    "company_publisher": {
        SkillAction.VIEW: {"company", "company_draft"},
        SkillAction.USE: {"company"},
        SkillAction.EDIT: {"company", "company_draft"},
        SkillAction.PUBLISH: {"company", "company_draft"},
    },
    "system_admin": {
        SkillAction.VIEW: {"company", "system"},
        SkillAction.USE: {"company"},
        SkillAction.EDIT: {"system"},
        SkillAction.PUBLISH: {"system"},
    },
    # 基盤管理者もpermissionで判定するので、個人のものは見えず、システムのものは会話で使わない
    "platform_admin": {
        SkillAction.VIEW: {
            "sales",
            "sales_draft",
            "sales_suspended",
            "accounting",
            "company",
            "company_draft",
            "system",
        },
        SkillAction.USE: {"sales", "accounting", "company"},
        SkillAction.EDIT: {
            "sales",
            "sales_draft",
            "sales_suspended",
            "accounting",
            "company",
            "company_draft",
            "system",
        },
        SkillAction.PUBLISH: {
            "sales",
            "sales_draft",
            "sales_suspended",
            "accounting",
            "company",
            "company_draft",
            "system",
        },
    },
    "inactive": {action: set() for action in SkillAction},
}


class TestScopeRules:
    @pytest.mark.django_db
    def test_queryset_and_can_agree(self, world):
        """一覧の絞り込み（QuerySet）と1件の判定（can）が、操作ごとに同じ結果になる（authorization.md §7.2）"""
        names = {skill.pk: name for name, skill in world["skills"].items()}
        querysets = {
            SkillAction.VIEW: Skill.objects.visible_to,
            SkillAction.USE: Skill.objects.usable_by,
            SkillAction.EDIT: Skill.objects.editable_by,
        }
        for label, user in _users(world).items():
            for action, expected in EXPECTED[label].items():
                by_can = {name for name, skill in world["skills"].items() if skill.can(user, action)}
                assert by_can == expected, (label, action)
                if action in querysets:
                    by_query = {names[pk] for pk in querysets[action](user).values_list("pk", flat=True)}
                    assert by_query == expected, (label, action)

    @pytest.mark.django_db
    def test_unsaved_skill_decides_creation(self, world):
        """作る前の判定は、保存していないSkillで行う（SkillCreateSerializer）"""
        users = _users(world)
        new = Skill(scope=SkillScope.DEPARTMENT, department=world["accounting"])
        assert not new.can(users["editor"], SkillAction.EDIT)
        assert new.can(users["all_manager"], SkillAction.EDIT)
        system = Skill(scope=SkillScope.SYSTEM)
        assert system.can(users["system_admin"], SkillAction.PUBLISH)
        assert not system.can(users["company_publisher"], SkillAction.EDIT)


class TestAPI:
    @pytest.mark.django_db
    def test_list_hides_system_skills_unless_asked(self, api_client, world):
        admin = _users(world)["system_admin"]
        api_client.force_login(admin)
        names = {s["name"] for s in api_client.get(SKILLS).json()["results"]}
        assert names == {"全社"}
        res = api_client.get(SKILLS, {"scope": SkillScope.SYSTEM})
        assert [s["name"] for s in res.json()["results"]] == ["システム"]
        assert api_client.get(f"{SKILLS}{world['skills']['system'].uid}/").json()["can_publish"] is True
        # システムのSkillの権限がなければ、指定しても見えない
        api_client.force_login(UserFactory.create())
        assert api_client.get(SKILLS, {"scope": SkillScope.SYSTEM}).json()["results"] == []

    @pytest.mark.django_db
    def test_create_system_skill(self, api_client, world):
        body = {"scope": SkillScope.SYSTEM, "name": "部門横断の照合", "tools": ["read"]}
        api_client.force_login(_users(world)["company_publisher"])
        assert api_client.post(SKILLS, body, format="json").status_code == 403
        api_client.force_login(grant(UserFactory.create(), P.EDIT_SYSTEM_SKILLS))
        # 編集の権限ではToolを決められない
        assert api_client.post(SKILLS, body, format="json").status_code == 403
        api_client.force_login(_users(world)["system_admin"])
        res = api_client.post(SKILLS, body, format="json")
        assert res.status_code == 201, res.json()
        assert res.json()["scope"] == SkillScope.SYSTEM

    @pytest.mark.django_db
    def test_editable_filter(self, api_client, world):
        api_client.force_login(_users(world)["editor"])
        names = {s["name"] for s in api_client.get(SKILLS, {"editable": "true"}).json()["results"]}
        assert names == {"営業", "営業の未公開", "営業の停止"}
        rows = {s["name"]: (s["can_edit"], s["can_publish"]) for s in api_client.get(SKILLS).json()["results"]}
        assert rows == {
            "営業": (True, False),
            "営業の未公開": (True, False),
            "営業の停止": (True, False),
            "全社": (False, False),
        }

    @pytest.mark.django_db
    def test_list_query_count_does_not_grow(self, api_client, world):
        """一覧の件数が増えても、クエリの数は増えない（行ごとに判定のクエリを出さない）"""
        user = _users(world)["publisher"]
        api_client.force_login(user)

        def count() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert api_client.get(SKILLS).status_code == 200
            return len(queries)

        before = count()
        for i in range(5):
            SkillVersionFactory.create(
                skill=SkillFactory.create(name=f"追加{i}", scope=SkillScope.DEPARTMENT, department=world["sales"])
            )
        assert count() == before
