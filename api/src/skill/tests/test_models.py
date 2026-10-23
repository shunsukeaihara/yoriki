import pytest

from skill.errors import VersionConflict
from skill.factories import SkillDraftFactory, SkillFactory
from skill.models import SkillDraftAsset


class TestClosedDraft:
    @pytest.mark.django_db
    def test_changes_to_published_draft_conflict(self, app_user):
        # 更新の要求が対象を読んだ後に、別の要求がDraftを公開して閉じた
        draft = SkillDraftFactory.create(skill=SkillFactory.create(created_by=app_user))
        draft.skill.publish_draft(draft.uid, actor=app_user, expected_revision=draft.revision)
        with pytest.raises(VersionConflict):
            draft.revise(actor=app_user, expected_revision=draft.revision, instructions="遅れた更新")
        with pytest.raises(VersionConflict):
            draft.remove_asset(SkillDraftAsset(pk=1), actor=app_user, expected_revision=draft.revision)
