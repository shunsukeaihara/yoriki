class InvalidTransition(Exception):
    pass


class AlreadyAnswered(Exception):
    pass


class LeaseLost(Exception):
    """このAttemptは実行の権利を失った（lease切れ、別のAttemptが始まった、取消済み）。何も書かずに終える"""
