import asyncio
import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from conversation.factories import ConversationFactory
from conversation.models import ConversationMessage, InstructionKind, InstructionState, UserInstruction
from run.factories import RunFactory
from run.models import ClarificationKind, ClarificationRequest, RunEvent, RunEventKind, RunStatus

from ..download import issue_file_token
from ..factories import ArtifactFactory
from ..models import Artifact, ArtifactKind, BlobObject, clean_filename

# Runを始めるには既定のRuntime Profileが要る
pytestmark = [pytest.mark.usefixtures("default_runtime"), pytest.mark.django_db]


@pytest.fixture(name="api_client")
def fixture_api_client(app_user):
    api_client = APIClient()
    api_client.force_authenticate(app_user)
    return api_client


@pytest.fixture(name="conversation")
def fixture_conversation(app_user):
    return ConversationFactory.create(owner=app_user)


def upload(api_client, conversation, name="請求書.pdf", content=b"%PDF-1.7", **extra):
    file = SimpleUploadedFile(name, content, content_type="text/html")
    return api_client.post(
        f"/api/app/v1/conversations/{conversation.uid}/artifacts/", {"file": file, **extra}, format="multipart"
    )


def send(api_client, conversation, body="", attachments=(), **extra):
    return api_client.post(
        f"/api/app/v1/conversations/{conversation.uid}/messages/",
        {"body": body, "attachments": list(attachments), **extra},
        format="json",
    )


def body_of(response) -> bytes:
    """ASGIで少しずつ返すための非同期のiteratorを読む"""

    async def collect():
        return b"".join([chunk async for chunk in response.streaming_content])

    return asyncio.run(collect())


class TestUpload:
    def test_upload_and_send(self, api_client, conversation):
        res = upload(api_client, conversation)
        assert res.status_code == 201, res.data
        assert res.data["kind"] == ArtifactKind.ATTACHMENT
        assert res.data["name"] == "請求書.pdf"
        # 形式はファイル名から決め、ブラウザが送ったものは使わない
        assert res.data["content_type"] == "application/pdf"
        assert res.data["size"] == 8
        assert res.data["path"] == ""
        artifact_id = res.data["id"]

        # 送るまでは一覧に出ない
        listed = api_client.get(f"/api/app/v1/conversations/{conversation.uid}/artifacts/")
        assert listed.data["results"] == []

        res = send(api_client, conversation, "取り込んで", [artifact_id])
        assert res.status_code == 201, res.data
        assert res.data["attachments"] == [artifact_id]
        assert res.data["attachments_info"][0]["path"] == "請求書.pdf"
        listed = api_client.get(f"/api/app/v1/conversations/{conversation.uid}/artifacts/")
        assert [a["id"] for a in listed.data["results"]] == [artifact_id]

        # Runの最初の発言に、添付の一覧を残す
        event = RunEvent.objects.get(kind=RunEventKind.USER_MESSAGE)
        assert event.data["attachments"] == [
            {
                "id": artifact_id,
                "name": "請求書.pdf",
                "path": "/workspace/input/請求書.pdf",
                "size": 8,
                "content_type": "application/pdf",
            }
        ]

    def test_next_request_is_told_about_earlier_attachments(self, api_client, conversation):
        """同じ会話の次の依頼でも、前の依頼の添付は /workspace/input にある（design.md §7.1）"""
        first = upload(api_client, conversation).data["id"]
        assert send(api_client, conversation, "1", [first]).status_code == 201
        run = conversation.runs.get()
        run.set_status(RunStatus.RUNNING)
        run.set_status(RunStatus.COMPLETED)
        second = upload(api_client, conversation, name="追加.csv").data["id"]
        assert send(api_client, conversation, "2", [second]).status_code == 201
        event = RunEvent.objects.filter(kind=RunEventKind.USER_MESSAGE).latest("pk")
        assert [a["id"] for a in event.data["attachments"]] == [second]
        assert [a["path"] for a in event.data["earlier_attachments"]] == ["/workspace/input/請求書.pdf"]

    def test_attachments_of_requests_not_started_are_not_inputs_yet(self, api_client, conversation):
        """実行中に送った次の依頼・追加指示の添付は、その依頼を取り込むまで /workspace/input に置かない"""
        first = upload(api_client, conversation).data["id"]
        assert send(api_client, conversation, "1", [first]).status_code == 201
        run = conversation.runs.get()
        run.set_status(RunStatus.RUNNING)
        later = upload(api_client, conversation, name="次.csv").data["id"]
        current = upload(api_client, conversation, name="追加.csv").data["id"]
        assert send(api_client, conversation, "2", [later], mode="next").status_code == 201
        assert send(api_client, conversation, "3", [current]).status_code == 201

        def inputs():
            return set(Artifact.objects.sent_inputs(conversation.pk).values_list("uid", flat=True))

        assert inputs() == {first}
        UserInstruction.objects.filter(kind=InstructionKind.CURRENT).update(state=InstructionState.APPLIED)
        assert inputs() == {first, current}
        run.set_status(RunStatus.COMPLETED)  # 次の依頼が始まる
        assert inputs() == {first, current, later}

    def test_files_only_message(self, api_client, app_user):
        conversation = ConversationFactory.create(owner=app_user, title="")
        artifact_id = upload(api_client, conversation).data["id"]
        res = send(api_client, conversation, "", [artifact_id])
        assert res.status_code == 201, res.data
        conversation.refresh_from_db()
        assert conversation.title == "請求書.pdf"
        assert send(api_client, conversation, "").status_code == 400

    def test_same_names_do_not_replace_earlier_attachments(self, api_client, conversation):
        first = upload(api_client, conversation).data["id"]
        second = upload(api_client, conversation).data["id"]
        third = upload(api_client, conversation).data["id"]
        assert send(api_client, conversation, "1", [first]).status_code == 201
        res = send(api_client, conversation, "2", [second, third], mode="next")
        assert res.status_code == 201, res.data
        paths = [a["path"] for a in res.data["attachments_info"]]
        assert paths == ["請求書 (2).pdf", "請求書 (3).pdf"]

    def test_cannot_send_an_attachment_twice(self, api_client, conversation):
        artifact_id = upload(api_client, conversation).data["id"]
        first = send(api_client, conversation, "1", [artifact_id], client_message_id="m1")
        assert first.status_code == 201
        # 応答を受け取れずに送り直したものは、前のメッセージを返す
        retry = send(api_client, conversation, "1", [artifact_id], client_message_id="m1")
        assert retry.status_code == 201
        assert retry.data["id"] == first.data["id"]
        # 別のメッセージには付けられない
        res = send(api_client, conversation, "2", [artifact_id], mode="next", client_message_id="m2")
        assert res.status_code == 400
        assert "non_field_errors" in res.data
        assert ConversationMessage.objects.count() == 1

    def test_cannot_send_others_or_other_conversations_files(self, api_client, conversation, app_user, other_user):
        others = ArtifactFactory.create(conversation=ConversationFactory.create(owner=other_user))
        elsewhere = ArtifactFactory.create(conversation=ConversationFactory.create(owner=app_user))
        for artifact in (others, elsewhere):
            res = send(api_client, conversation, "x", [artifact.uid])
            assert res.status_code == 400
            assert "attachments" in res.data

    def test_size_limit(self, api_client, conversation, settings):
        settings.YORIKI_UPLOAD_MAX_MB = 1
        res = upload(api_client, conversation, content=b"x" * (1024 * 1024 + 1))
        assert res.status_code == 400
        assert "file" in res.data
        assert not Artifact.objects.exists()

    def test_number_of_attachments_is_limited(self, api_client, conversation, settings):
        settings.YORIKI_ATTACHMENTS_PER_MESSAGE = 1
        ids = [upload(api_client, conversation).data["id"] for _i in range(2)]
        res = send(api_client, conversation, "x", ids)
        assert res.status_code == 400
        assert "attachments" in res.data

    def test_cannot_upload_to_others_conversation(self, api_client, other_user):
        res = upload(api_client, ConversationFactory.create(owner=other_user))
        assert res.status_code == 404

    def test_answer_with_attachments(self, api_client, conversation):
        run = RunFactory.create(conversation=conversation, status=RunStatus.WAITING_USER)
        ClarificationRequest.objects.create(
            run=run, kind=ClarificationKind.QUESTION, tool_call_id="c1", questions=[{"question": "どれ？"}]
        )
        artifact_id = upload(api_client, conversation).data["id"]
        res = send(api_client, conversation, "これです", [artifact_id])
        assert res.status_code == 201, res.data
        result = RunEvent.objects.get(run=run, kind=RunEventKind.TOOL_RESULT)
        assert "/workspace/input/請求書.pdf" in result.data["content"][0]["text"]


class TestDownload:
    def test_download_streams_the_file_as_an_attachment(self, api_client, conversation):
        artifact_id = upload(api_client, conversation, name="結果.html", content=b"<script>x</script>").data["id"]
        send(api_client, conversation, "x", [artifact_id])
        res = api_client.get(f"/api/app/v1/conversations/{conversation.uid}/artifacts/{artifact_id}/download/")
        assert res.status_code == 200
        assert body_of(res) == b"<script>x</script>"
        assert res["Content-Disposition"] == "attachment; filename*=utf-8''%E7%B5%90%E6%9E%9C.html"
        assert res["X-Content-Type-Options"] == "nosniff"
        assert res["Content-Security-Policy"].startswith("sandbox")
        assert res["Content-Length"] == "18"

    def test_download_through_nginx(self, api_client, conversation, settings):
        settings.YORIKI_STORAGE["BACKENDS"]["test-main"]["OPTIONS"]["accel_redirect"] = "/_protected/"
        artifact_id = upload(api_client, conversation).data["id"]
        send(api_client, conversation, "x", [artifact_id])
        res = api_client.get(f"/api/app/v1/conversations/{conversation.uid}/artifacts/{artifact_id}/download/")
        assert res.status_code == 200
        key = Artifact.objects.get(uid=artifact_id).blob.location().key
        assert res["X-Accel-Redirect"] == f"/_protected/{key}"
        assert res.content == b""

    def test_others_files_are_not_found(self, api_client, other_user):
        conversation = ConversationFactory.create(owner=other_user)
        others = ArtifactFactory.create(conversation=conversation, path="a.txt")
        res = api_client.get(f"/api/app/v1/conversations/{conversation.uid}/artifacts/{others.uid}/download/")
        assert res.status_code == 404

    def test_outputs_are_listed(self, api_client, conversation):
        run = RunFactory.create(conversation=conversation)
        output = Artifact.create_output(run, "集計/結果.xlsx", BlobObject.put(io.BytesIO(b"xlsx")))
        res = api_client.get(f"/api/app/v1/conversations/{conversation.uid}/artifacts/", {"kind": ArtifactKind.OUTPUT})
        assert [(a["id"], a["name"], a["path"], a["run"]) for a in res.data["results"]] == [
            (output.uid, "結果.xlsx", "集計/結果.xlsx", run.uid)
        ]
        assert output.content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class TestFileToken:
    def test_token_gives_the_file_without_login(self, conversation):
        artifact = ArtifactFactory.create(conversation=conversation, content=b"result")
        token = issue_file_token(artifact, timedelta(hours=1))
        res = APIClient().get(f"/api/files/{token}/")
        assert res.status_code == 200
        assert body_of(res) == b"result"

    def test_expired_tampered_or_deleted(self, conversation):
        artifact = ArtifactFactory.create(conversation=conversation)
        expired = issue_file_token(artifact, timedelta(seconds=-1))
        assert APIClient().get(f"/api/files/{expired}/").status_code == 404
        token = issue_file_token(artifact, timedelta(hours=1))
        assert APIClient().get(f"/api/files/{token[:-2]}xx/").status_code == 404
        Artifact.objects.filter(pk=artifact.pk).update(deleted=True)
        assert APIClient().get(f"/api/files/{token}/").status_code == 404


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\a\\請求書.pdf", "請求書.pdf"),
        (".bashrc", "bashrc"),
        ("a\x00b<c>.txt", "a_b_c_.txt"),
        ("", "file"),
        ("..", "file"),
        ("a" * 300 + ".xlsx", "a" * 195 + ".xlsx"),
    ],
)
def test_clean_filename(name, expected):
    assert clean_filename(name) == expected
