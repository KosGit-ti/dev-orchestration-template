"""Copilot レビュー API 検証用のテストファイル。

意図的にレビュー指摘が出るコードを含む。検証後に削除する。
"""

import os
import subprocess


def unsafe_execute(user_input: str) -> str:
    """ユーザー入力をそのまま実行する（危険）。"""
    result = subprocess.run(user_input, shell=True, capture_output=True, text=True)
    return result.stdout


def hardcoded_credentials() -> dict:
    """ハードコードされた認証情報（セキュリティ違反）。"""
    return {
        "api_key": "sk-1234567890abcdef1234567890abcdef1234567890abcdef",
        "password": "super_secret_password_123",
        "token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    }


def no_error_handling(path: str) -> str:
    """エラーハンドリングなし。"""
    with open(path) as f:
        data = f.read()
    return eval(data)


def unused_variables():
    """未使用変数が多い。"""
    x = 1
    y = 2
    z = 3
    a = "hello"
    b = [1, 2, 3]
    return None


def sql_injection(user_id: str) -> str:
    """SQL インジェクション脆弱性。"""
    query = f"SELECT * FROM users WHERE id = '{user_id}'"
    return query


def type_mismatch(value: int) -> str:
    """型の不整合。"""
    result: str = value + 10
    return result
