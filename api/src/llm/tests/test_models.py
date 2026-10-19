import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.test import override_settings

from llm.errors import VersionConflict
from llm.factories import LLMConnectionFactory, ModelProfileFactory
from llm.models import LLMConnection


class TestApiKeyField:
    @pytest.mark.django_db
    def test_plaintext_is_never_exposed_by_accident(self):
        connection = LLMConnectionFactory.create()
        connection = LLMConnection.objects.get(pk=connection.pk)
        assert str(connection.api_key) == "****abcd"
        assert "sk-test" not in repr(connection.api_key)
        assert connection.api_key.reveal() == "sk-test-0123456789abcd"

    @pytest.mark.django_db
    def test_rotate_with_new_key(self, settings):
        connection = LLMConnectionFactory.create()
        old_key = settings.YORIKI_SECRET_ENCRYPTION_KEYS[0]
        new_key = Fernet.generate_key().decode()

        with override_settings(YORIKI_SECRET_ENCRYPTION_KEYS=[new_key, old_key]):
            call_command("rotate_secret_encryption")
        connection.refresh_from_db()
        # 古い鍵を外しても復号できる
        with override_settings(YORIKI_SECRET_ENCRYPTION_KEYS=[new_key]):
            assert connection.api_key.reveal() == "sk-test-0123456789abcd"

    @pytest.mark.django_db
    def test_short_key_has_no_hint(self):
        connection = LLMConnectionFactory.create(api_key="short")
        assert connection.api_key.hint == ""
        assert connection.api_key.reveal() == "short"


class TestRevise:
    @pytest.mark.django_db
    def test_conflict_keeps_current_version(self):
        profile = ModelProfileFactory.create()
        assert profile.current_version is not None
        with pytest.raises(VersionConflict):
            profile.revise(
                actor=profile.created_by,
                expected_version=0,
                connection=profile.current_version.connection,
                model_name="x",
                max_input_tokens=1,
                max_output_tokens=1,
            )
        profile.refresh_from_db()
        assert profile.version_number == 1
        assert profile.versions.count() == 1
