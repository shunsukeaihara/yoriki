import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from connector.factories import ConnectorFactory
from connector.models import ConnectorScope
from llm.factories import LLMConnectionFactory, ModelProfileFactory
from llm.models import ProviderKind
from run.factories import ClarificationRequestFactory, RunFactory
from run.models import ClarificationStatus, Run, RunEvent, RunEventKind, RunKind, RunStatus
from skill.factories import SkillFactory, SkillVersionFactory
from skill.models import SkillRequirement, SkillScope
from user.factories import DepartmentFactory, DepartmentMembershipFactory

from .factories import ConversationFactory
from .models import Conversation, InstructionKind, UserInstruction
from .serializers import ConversationSerializer

# Runを始めるには既定のRuntime Profileが要る
pytestmark = pytest.mark.usefixtures("default_runtime")


@pytest.fixture(name="api_client")
def fixture_api_client(app_user):
    api_client = APIClient()
    api_client.force_authenticate(app_user)
    return api_client


@pytest.fixture(name="local_profile")
def fixture_local_profile(db):
    connection = LLMConnectionFactory.create(kind=ProviderKind.LOCAL, base_url="http://llm.internal/v1", api_key="")
    return ModelProfileFactory.create(version__connection=connection, version__supports_tools=True)


@pytest.mark.django_db
class TestConversation:
    def test_create_uses_the_default_model(self, api_client):
        default = ModelProfileFactory.create()
        default.make_default()
        res = api_client.post("/api/app/v1/conversations/", {}, format="json")
        assert res.status_code == 201, res.data
        assert res.data["model_profile"] == default.uid
        assert res.data["model_profile_info"]["is_default"]

    def test_sensitive_conversation_requires_a_local_model(self, api_client, local_profile):
        cloud = ModelProfileFactory.create()
        res = api_client.post(
            "/api/app/v1/conversations/", {"model_profile": cloud.uid, "handles_sensitive_data": True}, format="json"
        )
        assert res.status_code == 400
        assert "model_profile" in res.data
        res = api_client.post(
            "/api/app/v1/conversations/",
            {"model_profile": local_profile.uid, "handles_sensitive_data": True},
            format="json",
        )
        assert res.status_code == 201, res.data

    def test_effort_must_be_accepted_by_the_model(self, api_client):
        profile = ModelProfileFactory.create(
            version__supports_reasoning=True, version__effort_values=["low", "high"], version__default_effort="low"
        )
        res = api_client.post(
            "/api/app/v1/conversations/", {"model_profile": profile.uid, "effort": "max"}, format="json"
        )
        assert res.status_code == 400
        assert "effort" in res.data
        res = api_client.post(
            "/api/app/v1/conversations/", {"model_profile": profile.uid, "effort": "high"}, format="json"
        )
        assert res.status_code == 201, res.data

    def test_other_users_conversation_is_not_found(self, api_client, other_user):
        conversation = ConversationFactory.create(owner=other_user)
        assert api_client.get(f"/api/app/v1/conversations/{conversation.uid}/").status_code == 404
        res = api_client.post(f"/api/app/v1/conversations/{conversation.uid}/messages/", {"body": "x"}, format="json")
        assert res.status_code == 404

    def test_switching_the_model_during_a_run_is_recorded(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        run = RunFactory.create(conversation=conversation, status=RunStatus.RUNNING)
        other = ModelProfileFactory.create(version__supports_tools=True)
        res = api_client.patch(
            f"/api/app/v1/conversations/{conversation.uid}/", {"model_profile": other.uid}, format="json"
        )
        assert res.status_code == 200, res.data
        run.refresh_from_db()
        assert run.model_version == other.current_version
        event = RunEvent.objects.get(run=run, kind=RunEventKind.MODEL_SWITCHED)
        assert event.data["trigger"] == "user"
        assert run.manifest["appended"][0]["kind"] == "model"

    def test_deleting_a_conversation_cancels_its_active_run(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        run = RunFactory.create(conversation=conversation, status=RunStatus.QUEUED)
        assert api_client.delete(f"/api/app/v1/conversations/{conversation.uid}/").status_code == 204
        run.refresh_from_db()
        assert run.status == RunStatus.CANCELLED

    def test_conversation_can_become_confidential_but_not_back(self, api_client, app_user, local_profile):
        conversation = ConversationFactory.create(owner=app_user)
        url = f"/api/app/v1/conversations/{conversation.uid}/"
        # 機密にするにはローカルLLMのモデルが要る
        res = api_client.patch(url, {"handles_sensitive_data": True}, format="json")
        assert res.status_code == 400
        assert "model_profile" in res.data
        run = RunFactory.create(conversation=conversation, status=RunStatus.RUNNING)
        res = api_client.patch(url, {"handles_sensitive_data": True, "model_profile": local_profile.uid}, format="json")
        assert res.status_code == 200, res.data
        run.refresh_from_db()
        assert run.handles_sensitive_data
        assert run.model_version == local_profile.current_version
        res = api_client.patch(url, {"handles_sensitive_data": False}, format="json")
        assert res.status_code == 400
        assert "handles_sensitive_data" in res.data
        assert Conversation.objects.get(pk=conversation.pk).handles_sensitive_data

    def test_making_it_confidential_checks_the_model_saved_by_another_update(self, app_user, local_profile):
        conversation = ConversationFactory.create(owner=app_user, model_profile=local_profile)
        serializer = ConversationSerializer(
            conversation, data={"handles_sensitive_data": True}, partial=True, context={"request": None}
        )
        assert serializer.is_valid(), serializer.errors
        # 検証の後に、別の更新がクラウドのモデルに変えた
        cloud = ModelProfileFactory.create(version__supports_tools=True)
        Conversation.objects.filter(pk=conversation.pk).update(model_profile=cloud)
        with pytest.raises(ValidationError):
            serializer.save()
        assert not Conversation.objects.get(pk=conversation.pk).handles_sensitive_data

    def test_stale_update_does_not_write_back_the_old_model(self, app_user, local_profile):
        cloud = ModelProfileFactory.create(version__supports_tools=True)
        conversation = ConversationFactory.create(owner=app_user, model_profile=cloud)
        serializer = ConversationSerializer(
            conversation, data={"title": "新しい題名"}, partial=True, context={"request": None}
        )
        assert serializer.is_valid(), serializer.errors
        # 検証の後に、別の更新がローカルLLMに切り替えて機密にした
        Conversation.objects.filter(pk=conversation.pk).update(handles_sensitive_data=True, model_profile=local_profile)
        serializer.save()
        conversation.refresh_from_db()
        assert conversation.title == "新しい題名"
        assert conversation.handles_sensitive_data
        assert conversation.model_profile == local_profile

    def test_conversation_made_confidential_during_the_update_is_not_reverted(self, app_user, local_profile):
        conversation = ConversationFactory.create(owner=app_user, model_profile=local_profile)
        cloud = ModelProfileFactory.create(version__supports_tools=True)
        serializer = ConversationSerializer(
            conversation, data={"model_profile": cloud.uid}, partial=True, context={"request": None}
        )
        assert serializer.is_valid(), serializer.errors
        # 検証の後に、別の更新が会話を機密にした
        Conversation.objects.filter(pk=conversation.pk).update(handles_sensitive_data=True)
        with pytest.raises(ValidationError):
            serializer.save()
        conversation.refresh_from_db()
        assert conversation.handles_sensitive_data
        assert conversation.model_profile == local_profile


@pytest.mark.django_db
class TestMessage:
    def url(self, conversation) -> str:
        return f"/api/app/v1/conversations/{conversation.uid}/messages/"

    def test_first_message_starts_a_run(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user, title="")
        res = api_client.post(self.url(conversation), {"body": "請求書を確認して"}, format="json")
        assert res.status_code == 201, res.data
        run = Run.objects.get(conversation=conversation)
        assert res.data["run"] == run.uid
        assert run.status == RunStatus.QUEUED
        assert run.manifest["fixed"]["model_version"] == run.model_version_id
        assert run.dispatches.count() == 1
        conversation.refresh_from_db()
        assert conversation.title == "請求書を確認して"

    def test_message_during_a_run_becomes_an_instruction(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        run = RunFactory.create(conversation=conversation, status=RunStatus.RUNNING)
        res = api_client.post(self.url(conversation), {"body": "支店は別にして"}, format="json")
        assert res.status_code == 201, res.data
        instruction = UserInstruction.objects.get(conversation=conversation)
        assert instruction.kind == InstructionKind.CURRENT
        assert instruction.run == run
        assert res.data["instruction"]["kind"] == InstructionKind.CURRENT
        assert Run.objects.filter(conversation=conversation).count() == 1

    def test_next_mode_queues_a_request(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        RunFactory.create(conversation=conversation, status=RunStatus.RUNNING)
        res = api_client.post(self.url(conversation), {"body": "次はこれ", "mode": "next"}, format="json")
        assert res.status_code == 201, res.data
        assert UserInstruction.objects.get(conversation=conversation).kind == InstructionKind.NEXT

    def test_message_while_waiting_answers_the_question(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        run = RunFactory.create(conversation=conversation, status=RunStatus.WAITING_USER)
        clarification = ClarificationRequestFactory.create(run=run)
        res = api_client.post(self.url(conversation), {"body": "本社です"}, format="json")
        assert res.status_code == 201, res.data
        clarification.refresh_from_db()
        assert clarification.status == ClarificationStatus.ANSWERED
        run.refresh_from_db()
        assert run.status == RunStatus.QUEUED

    def test_resent_message_is_accepted_once(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        body = {"body": "請求書を確認して", "client_message_id": "m-1"}
        first = api_client.post(self.url(conversation), body, format="json")
        second = api_client.post(self.url(conversation), body, format="json")
        assert first.data["id"] == second.data["id"]
        assert Run.objects.filter(conversation=conversation).count() == 1

    def test_blank_body_is_rejected(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        res = api_client.post(self.url(conversation), {"body": "  "}, format="json")
        assert res.status_code == 400

    def test_inactive_model_cannot_start(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        conversation.model_profile.is_active = False
        conversation.model_profile.save()
        res = api_client.post(self.url(conversation), {"body": "依頼"}, format="json")
        assert res.status_code == 400
        assert "non_field_errors" in res.data

    def test_message_with_a_skill_starts_a_run_with_it(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        version = SkillVersionFactory.create(skill__created_by=app_user, skill__name="請求書の照合")
        res = api_client.post(self.url(conversation), {"body": "この3件", "skills": [version.skill.uid]}, format="json")
        assert res.status_code == 201, res.data
        assert res.data["skills_info"] == [{"id": version.skill.uid, "name": "請求書の照合"}]
        run = Run.objects.get(conversation=conversation)
        assert [s["version"] for s in run.manifest["fixed"]["skills"]] == [version.pk]
        # 履歴には送ったときの名前を出す（後でSkillの名前を変えても変わらない）
        version.skill.name = "新しい名前"
        version.skill.save()
        listed = api_client.get(self.url(conversation))
        assert listed.data["results"][0]["skills_info"] == res.data["skills_info"]

    def test_message_with_a_connector_starts_a_run_with_it(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        connector = ConnectorFactory.create(name="販売管理")
        res = api_client.post(self.url(conversation), {"body": "取引先", "connectors": [connector.uid]}, format="json")
        assert res.status_code == 201, res.data
        assert res.data["connectors_info"] == [{"id": connector.uid, "name": "販売管理"}]
        run = Run.objects.get(conversation=conversation)
        assert run.manifest["fixed"]["connectors"] == [
            {
                "connector": connector.uid,
                "name": "販売管理",
                "version": connector.current_version_id,
            }
        ]

    def test_connector_the_user_cannot_use_is_rejected(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        others = ConnectorFactory.create(scope=ConnectorScope.DEPARTMENT, department=DepartmentFactory.create())
        system = ConnectorFactory.create(scope=ConnectorScope.SYSTEM)
        for connector in (others, system):
            res = api_client.post(
                self.url(conversation), {"body": "依頼", "connectors": [connector.uid]}, format="json"
            )
            assert res.status_code == 400
            assert "connectors" in res.data
        assert not Run.objects.filter(conversation=conversation).exists()

    def test_skill_the_user_cannot_use_is_rejected(self, api_client, app_user, other_user):
        conversation = ConversationFactory.create(owner=app_user)
        others = SkillVersionFactory.create(skill__created_by=other_user)
        unpublished = SkillFactory.create(created_by=app_user)
        for skill in (others.skill, unpublished):
            res = api_client.post(self.url(conversation), {"body": "依頼", "skills": [skill.uid]}, format="json")
            assert res.status_code == 400
            assert "skills" in res.data
        assert not Run.objects.filter(conversation=conversation).exists()

    def test_edit_skill_message_starts_a_skill_edit_run(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        skill = SkillVersionFactory.create(skill__created_by=app_user).skill
        res = api_client.post(self.url(conversation), {"body": "説明を直して", "edit_skill": skill.uid}, format="json")
        assert res.status_code == 201, res.data
        assert res.data["skill_edit"] == {"id": skill.uid, "name": skill.name}
        run = Run.objects.get(conversation=conversation)
        assert run.kind == RunKind.SKILL_EDIT
        assert run.manifest["fixed"]["skill_edit"] == {"skill": skill.uid, "name": skill.name}

    def test_create_skill_message_and_edit_during_a_run_is_the_next_request(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        RunFactory.create(conversation=conversation, status=RunStatus.RUNNING)
        res = api_client.post(self.url(conversation), {"body": "Skillにして", "create_skill": True}, format="json")
        assert res.status_code == 201, res.data
        assert res.data["skill_edit"] == {"id": "", "name": ""}
        assert UserInstruction.objects.get(conversation=conversation).kind == InstructionKind.NEXT

    def test_edit_skill_needs_edit_access_and_a_body(self, api_client, app_user, other_user):
        conversation = ConversationFactory.create(owner=app_user)
        department = DepartmentFactory.create()
        DepartmentMembershipFactory.create(department=department, user=app_user)
        shared = SkillVersionFactory.create(
            skill__created_by=other_user, skill__scope=SkillScope.DEPARTMENT, skill__department=department
        ).skill
        mine = SkillVersionFactory.create(skill__created_by=app_user).skill
        cases = [
            ({"body": "直して", "edit_skill": shared.uid}, "edit_skill"),
            ({"body": "", "edit_skill": mine.uid}, "body"),
            ({"body": "直して", "edit_skill": mine.uid, "skills": [mine.uid]}, "skills"),
            ({"body": "直して", "edit_skill": mine.uid, "create_skill": True}, "create_skill"),
        ]
        for data, field in cases:
            res = api_client.post(self.url(conversation), data, format="json")
            assert res.status_code == 400
            assert field in res.data
        assert not Run.objects.filter(conversation=conversation).exists()

    def test_model_without_the_capabilities_of_the_skills_in_use_is_rejected(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        model_version = conversation.model_profile.current_version
        assert model_version is not None
        model_version.supports_images = True
        model_version.save()
        version = SkillVersionFactory.create(skill__created_by=app_user, requires=[SkillRequirement.IMAGE_INPUT])
        api_client.post(self.url(conversation), {"body": "依頼", "skills": [version.skill.uid]}, format="json")
        other = ModelProfileFactory.create(version__supports_tools=True)
        res = api_client.patch(
            f"/api/app/v1/conversations/{conversation.uid}/", {"model_profile": other.uid}, format="json"
        )
        assert res.status_code == 400
        assert "model_profile" in res.data
