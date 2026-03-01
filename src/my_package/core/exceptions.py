"""ドメイン例外定義。

すべてのドメイン例外は ``DomainError`` を基底とし、
呼び出し側で一括ハンドリングできるようにする。
"""


class DomainError(Exception):
    """ドメイン例外の基底クラス。"""


class ValidationError(DomainError):
    """入力データのバリデーション失敗。"""


class ConfigError(DomainError):
    """設定の読み込みまたは検証エラー。"""


class ConstraintViolationError(DomainError):
    """制約違反。

    Attributes:
        constraint_id: 違反した制約の ID（例: "C-001"）。
        detail: 違反の詳細説明。
    """

    def __init__(self, constraint_id: str, detail: str) -> None:
        """制約違反例外を生成する。

        Args:
            constraint_id: 制約 ID。
            detail: 違反の詳細。
        """
        self.constraint_id = constraint_id
        self.detail = detail
        super().__init__(f"[{constraint_id}] {detail}")
