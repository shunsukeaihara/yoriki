class VersionConflict(Exception):
    """編集を始めた後に、別の版ができている"""


class RegistryError(Exception):
    """レジストリからイメージを確かめられなかった。message は利用者に見せる文（翻訳済み）"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
