class VersionConflict(Exception):
    """編集を始めた後に、Draftか公開中の版が変わった"""


class DraftExists(Exception):
    """Skillには既にDraftがある（DraftはSkillごとに1つ）"""


class InvalidDraft(Exception):
    """公開できない内容のDraft。メッセージは利用者に見せる"""
