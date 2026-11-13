class VersionConflict(Exception):
    """編集を始めた後に、他の人が版を作った・削除した"""


class CannotRun(Exception):
    """タスクのRunを始められない（停止中、使うものが使えなくなった、入力が合わない等）"""


class RunLimitReached(CannotRun):
    """タスク単位の受付頻度・同時実行数の上限に達している（design.md §9.3）。理由はタスクの中身を含まない"""
