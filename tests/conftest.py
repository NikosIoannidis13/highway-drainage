"""Classify suites by directory; never create a QApplication for core tests."""

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        for category in ("unit", "integration", "gui"):
            if category in item.path.relative_to(item.config.rootpath / "tests").parts:
                item.add_marker(getattr(pytest.mark, category))
                break
