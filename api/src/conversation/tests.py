import pytest
from rest_framework.test import APIClient

from llm.factories import LLMConnectionFactory, ModelProfileFactory
from llm.models import ProviderKind
from run.factories import ClarificationRequestFactory, RunFactory
from run.models import ClarificationStatus, Run, RunEvent, RunEventKind, RunStatus
from skill.factories import SkillFactory, SkillVersionFactory
from skill.models import SkillRequirement

from .factories import ConversationFactory
from .models import Conversation, InstructionKind, UserInstruction

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

    def test_sensitivity_cannot_be_changed_later(self, api_client, app_user, local_profile):
        conversation = ConversationFactory.create(
            owner=app_user, model_profile=local_profile, handles_sensitive_data=True
        )
        res = api_client.patch(
            f"/api/app/v1/conversations/{conversation.uid}/", {"handles_sensitive_data": False}, format="json"
        )
        assert res.status_code == 200
        assert Conversation.objects.get(pk=conversation.pk).handles_sensitive_data


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
        listed = api_client.get(self.url(conversation))
        assert listed.data["results"][0]["skills_info"] == res.data["skills_info"]

    def test_skill_the_user_cannot_use_is_rejected(self, api_client, app_user, other_user):
        conversation = ConversationFactory.create(owner=app_user)
        others = SkillVersionFactory.create(skill__created_by=other_user)
        unpublished = SkillFactory.create(created_by=app_user)
        for skill in (others.skill, unpublished):
            res = api_client.post(self.url(conversation), {"body": "依頼", "skills": [skill.uid]}, format="json")
            assert res.status_code == 400
            assert "skills" in res.data
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
