"""Copilot レビュー API 検証用のテストファイル。

意図的にレビュー指摘が出るコードを含む。検証後に削除する。
"""

import subprocess


def unsafe_execute(user_input: str) -> str:
    """ユーザー入力をそのまま実行する（修正: shell=False にしたが引数が文字列のまま）。"""
    result = subprocess.run(user_input, shell=False,
                            capture_output=True, text=True)
    return result.stdout


def hardcoded_credentials() -> dict:
    """認証情報を環境変数から取得するべきだが、まだハードコード。"""
    return {
        "api_key": "sk-1234567890abcdef1234567890abcdef1234567890abcdef",
        "password": "still_hardcoded_oops",
        "token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    }


def no_error_handling(path: str) -> str:
    """エラーハンドリングを追加したが eval は残存。"""
    try:
        with open(path) as f:
            data = f.read()
        return eval(data)
    except FileNotFoundError:
        return ""


def unused_variables() -> int:
    """未使用変数を一部修正したが問題は残存。"""
    x = 1
    y = 2
    z = 3
    unused_a = "hello"
    return x + y + z


def sql_injection(user_id: str) -> str:
    """SQL インジェクション脆弱性（未修正）。"""
    query = f"SELECT * FROM users WHERE id = '{user_id}'"
    return query


def type_mismatch(value: int) -> str:
    """型の不整合（未修正）。"""
    result: str = value + 10
    return result
