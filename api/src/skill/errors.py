class VersionConflict(Exception):
    """編集を始めた後に、Draftか公開中の版が変わった"""


class DraftExists(Exception):
    """Skillには既にDraftがある（DraftはSkillごとに1つ）"""


class InvalidDraft(Exception):
    """公開できない内容のDraft。メッセージは利用者に見せる"""


class EvaluationRequired(InvalidDraft):
    """必須の評価ケースが、今のDraftで通過していない（design.md §3.7.1）。`cases` は足りないケースの名前"""

    def __init__(self, message: str, cases: list[str]):
        super().__init__(message)
        self.cases = cases


class CannotEvaluate(Exception):
    """評価を始められない。メッセージは利用者に見せる"""
