from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from conversation.factories import ConversationFactory
from llm.factories import ModelProfileFactory

from .factories import ClarificationRequestFactory, RunFactory
from .models import (
    ClarificationKind,
    ClarificationStatus,
    ExecutionAttempt,
    Run,
    RunEndReason,
    RunEvent,
    RunEventKind,
    RunStatus,
)
from .tasks import claim_run, reconcile_runs


@pytest.fixture(name="api_client")
def fixture_api_client(app_user):
    api_client = APIClient()
    api_client.force_authenticate(app_user)
    return api_client


@pytest.fixture(name="run")
def fixture_run(app_user):
    return RunFactory.create(conversation=ConversationFactory.create(owner=app_user))


@pytest.mark.django_db
class TestCancel:
    def test_cancel_queued_run(self, api_client, run):
        res = api_client.post(f"/api/app/v1/runs/{run.uid}/cancel/", {"reason": "不要になった"}, format="json")
        assert res.status_code == 200, res.data
        assert res.data["status"] == RunStatus.CANCELLED
        assert res.data["end_reason"] == RunEndReason.CANCELLED

    def test_cancel_running_run_waits_for_the_worker(self, api_client, run):
        run.status = RunStatus.RUNNING
        run.save()
        res = api_client.post(f"/api/app/v1/runs/{run.uid}/cancel/", {}, format="json")
        assert res.data["status"] == RunStatus.CANCELLING

    def test_cannot_cancel_a_completed_run(self, api_client, run):
        run.status = RunStatus.COMPLETED
        run.save()
        res = api_client.post(f"/api/app/v1/runs/{run.uid}/cancel/", {}, format="json")
        assert res.status_code == 400

    def test_other_users_run_is_not_found(self, api_client, other_user):
        run = RunFactory.create(conversation=ConversationFactory.create(owner=other_user))
        assert api_client.post(f"/api/app/v1/runs/{run.uid}/cancel/", {}, format="json").status_code == 404
        assert api_client.get(f"/api/app/v1/runs/{run.uid}/events/").status_code == 404


@pytest.mark.django_db
class TestClarification:
    def url(self, clarification) -> str:
        return f"/api/app/v1/runs/{clarification.run.uid}/clarifications/{clarification.uid}/answer/"

    def test_answer_requeues_the_run(self, api_client, app_user):
        clarification = ClarificationRequestFactory.create(run__conversation=ConversationFactory.create(owner=app_user))
        res = api_client.post(self.url(clarification), {"answers": [{"selected": ["本社"]}]}, format="json")
        assert res.status_code == 200, res.data
        assert res.data["status"] == ClarificationStatus.ANSWERED
        run = Run.objects.get(pk=clarification.run_id)
        assert run.status == RunStatus.QUEUED
        result = RunEvent.objects.get(run=run, kind=RunEventKind.TOOL_RESULT)
        assert result.data["call_id"] == clarification.tool_call_id

    def test_answer_must_be_an_option(self, api_client, app_user):
        clarification = ClarificationRequestFactory.create(run__conversation=ConversationFactory.create(owner=app_user))
        res = api_client.post(self.url(clarification), {"answers": [{"selected": ["東京"]}]}, format="json")
        assert res.status_code == 400
        assert "answers" in res.data

    def test_answer_twice_is_rejected(self, api_client, app_user):
        clarification = ClarificationRequestFactory.create(run__conversation=ConversationFactory.create(owner=app_user))
        assert api_client.post(self.url(clarification), {"comment": "本社"}, format="json").status_code == 200
        assert api_client.post(self.url(clarification), {"comment": "本社"}, format="json").status_code == 400

    def test_model_switch_answer_switches_the_model(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user)
        run = RunFactory.create(conversation=conversation, status=RunStatus.WAITING_USER)
        other = ModelProfileFactory.create(version__supports_tools=True)
        from harness.recovery import request_model_switch  # noqa: PLC0415

        clarification = request_model_switch(run, "接続できません")
        assert clarification.kind == ClarificationKind.MODEL_SWITCH
        res = api_client.post(self.url(clarification), {"answers": [{"selected": [other.name]}]}, format="json")
        assert res.status_code == 200, res.data
        run.refresh_from_db()
        assert run.model_version == other.current_version
        assert run.status == RunStatus.QUEUED
        conversation.refresh_from_db()
        assert conversation.model_profile == other


@pytest.mark.django_db
class TestList:
    def test_filter_by_conversation(self, api_client, run, app_user):
        RunFactory.create(conversation=ConversationFactory.create(owner=app_user))
        res = api_client.get("/api/app/v1/runs/", {"conversation": run.conversation.uid})
        assert [r["id"] for r in res.data["results"]] == [run.uid]


@pytest.mark.django_db
class TestEvents:
    def test_events_are_listed_in_order(self, api_client, run):
        run.set_status(RunStatus.RUNNING)
        RunEvent.record(run, RunEventKind.ASSISTANT_MESSAGE, {"text": "確認します", "tool_calls": []})
        res = api_client.get(f"/api/app/v1/runs/{run.uid}/events/")
        assert res.status_code == 200
        types = [e["type"] for e in res.data["results"]]
        assert types == ["run.status", "run.message"]
        assert res.data["results"][1]["text"] == "確認します"
        assert "provider_state" not in res.data["results"][1]


@pytest.mark.django_db
class TestClaim:
    def test_claim_once(self, run):
        assert claim_run(run.pk) is not None
        assert claim_run(run.pk) is None  # 再配送されても重複して実行しない

    def test_reconcile_retries_an_expired_attempt(self, run):
        attempt = claim_run(run.pk)
        assert attempt is not None
        ExecutionAttempt.objects.filter(pk=attempt.pk).update(lease_expires=timezone.now() - timedelta(seconds=1))
        reconcile_runs()
        run.refresh_from_db()
        assert run.status == RunStatus.QUEUED
        assert ExecutionAttempt.objects.get(pk=attempt.pk).failure == "lease_expired"

    def test_reconcile_gives_up_after_repeated_failures(self, run):
        for _ in range(3):
            attempt = claim_run(run.pk)
            assert attempt is not None
            ExecutionAttempt.objects.filter(pk=attempt.pk).update(lease_expires=timezone.now() - timedelta(seconds=1))
            reconcile_runs()
        run.refresh_from_db()
        assert run.status == RunStatus.FAILED
        assert run.end_reason == RunEndReason.ATTEMPTS

    def test_reconcile_finishes_a_cancelling_run(self, run):
        attempt = claim_run(run.pk)
        assert attempt is not None
        run.set_status(RunStatus.CANCELLING)
        ExecutionAttempt.objects.filter(pk=attempt.pk).update(lease_expires=timezone.now() - timedelta(seconds=1))
        reconcile_runs()
        run.refresh_from_db()
        assert run.status == RunStatus.CANCELLED

    def test_expired_question_fails_the_run(self, app_user):
        clarification = ClarificationRequestFactory.create(
            run__conversation=ConversationFactory.create(owner=app_user), expires=timezone.now() - timedelta(seconds=1)
        )
        reconcile_runs()
        run = Run.objects.get(pk=clarification.run_id)
        assert run.status == RunStatus.FAILED
        assert run.end_reason == RunEndReason.EXPIRED

    def test_claim_respects_the_runs_per_qcluster_limit(self, run, settings):
        settings.YORIKI_RUNS_PER_QCLUSTER = 1
        other = RunFactory.create(conversation=ConversationFactory.create(owner=run.owner))
        assert claim_run(run.pk) is not None
        assert claim_run(other.pk) is None
        assert Run.objects.get(pk=other.pk).status == RunStatus.QUEUED

    def test_reconcile_redispatches_a_waiting_run(self, run):
        from .dispatch import enqueue_run  # noqa: PLC0415

        enqueue_run(run)
        old = timezone.now() - timedelta(minutes=5)
        run.dispatches.update(dispatched=old, created=old)
        Run.objects.filter(pk=run.pk).update(updated=old)
        reconcile_runs()
        assert run.dispatches.count() == 2

    def test_invalid_transition_is_rejected(self, run):
        from .errors import InvalidTransition  # noqa: PLC0415

        with pytest.raises(InvalidTransition):
            run.set_status(RunStatus.COMPLETED)
