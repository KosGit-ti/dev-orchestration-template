"""スモークテスト: パッケージのインポートと基本動作を検証する。

CI 品質ゲートが正しく機能していることを確認するための最小限のテスト。
各パッケージが正しくインポートでき、公開 API が __all__ と一致することを検証する。
"""

from __future__ import annotations

import importlib
import importlib.resources
import types

import pytest


class TestSamplePackageImport:
    """sample パッケージのインポートスモークテスト。"""

    def test_import_sample(self) -> None:
        """sample パッケージをインポートできること。"""
        import sample

        assert isinstance(sample, types.ModuleType)

    def test_sample_has_all(self) -> None:
        """sample パッケージに __all__ が定義されていること。"""
        import sample

        assert hasattr(sample, "__all__")
        assert len(sample.__all__) > 0

    def test_sample_exports_available(self) -> None:
        """sample.__all__ に列挙されたシンボルがすべてアクセス可能であること。"""
        import sample

        for name in sample.__all__:
            assert hasattr(sample, name), f"{name} が sample パッケージに見つからない"

    def test_example_entity_creation(self) -> None:
        """ExampleEntity を生成できること。"""
        from sample.example_module import ExampleEntity

        entity = ExampleEntity(name="smoke", value=1.0)
        assert entity.name == "smoke"
        assert entity.value == 1.0

    def test_process_function(self) -> None:
        """process 関数が正しく動作すること。"""
        from sample.example_module import ExampleEntity, process

        entity = ExampleEntity(name="smoke", value=2.0)
        result = process(entity, 3.0)
        assert result == pytest.approx(6.0)


class TestObservabilityPackageImport:
    """observability パッケージのインポートスモークテスト。"""

    def test_import_observability(self) -> None:
        """observability パッケージをインポートできること。"""
        import observability

        assert isinstance(observability, types.ModuleType)

    def test_observability_has_all(self) -> None:
        """observability パッケージに __all__ が定義されていること。"""
        import observability

        assert hasattr(observability, "__all__")
        assert len(observability.__all__) > 0

    def test_observability_exports_available(self) -> None:
        """observability.__all__ に列挙されたシンボルがすべてアクセス可能であること。"""
        import observability

        for name in observability.__all__:
            assert hasattr(observability, name), f"{name} が observability パッケージに見つからない"


class TestPyTypedMarkers:
    """PEP 561 py.typed マーカーファイルの存在を検証する。"""

    @pytest.mark.parametrize("package_name", ["sample", "observability"])
    def test_py_typed_exists(self, package_name: str) -> None:
        """各パッケージに py.typed マーカーファイルが含まれていること。"""
        pkg = importlib.import_module(package_name)
        assert pkg.__file__ is not None
        pkg_dir = importlib.resources.files(package_name)
        py_typed = pkg_dir / "py.typed"
        assert py_typed.is_file(), f"{package_name}/py.typed が見つからない"
