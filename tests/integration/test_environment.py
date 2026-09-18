"""Integration coverage for environment."""

import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import highway_drainage


def test_editable_package_resolves_to_src() -> None:
    expected = Path(__file__).resolve().parents[2] / "src" / "highway_drainage" / "__init__.py"
    assert highway_drainage.__file__ is not None
    assert Path(highway_drainage.__file__).resolve() == expected
    assert version("highway-drainage")


def test_engineering_modules_import_without_qt() -> None:
    # A fresh interpreter avoids Qt already imported by GUI test collection.
    script = """
import importlib
import importlib.abc
import pkgutil
import sys

class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PySide6', 'PyQt6', 'PyQt5', 'pytestqt'}:
            raise AssertionError(f'Engineering module imported {fullname}')

sys.meta_path.insert(0, NoQt())
for layer in ('domain', 'application', 'infrastructure'):
    package = importlib.import_module(f'highway_drainage.{layer}')
    for module in pkgutil.walk_packages(package.__path__, package.__name__ + '.'):
        importlib.import_module(module.name)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
