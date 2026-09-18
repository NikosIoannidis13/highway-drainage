"""Publish completed catchment artifacts; roll back failed replacements."""

import json
import re
from pathlib import Path
from uuid import uuid4


def publish_results(stage: Path, output: Path, overwrite: bool, source_dem: Path) -> None:
    output = output.resolve()
    if not output.exists():
        stage.rename(output)
        return
    if not overwrite:
        raise ValueError("Existing results are never overwritten without confirmation.")
    if not output.is_dir():
        raise ValueError("The catchment output path must be a directory.")
    incoming = {p.name for p in stage.iterdir()}
    old_masks: set[str] = set()
    manifest = output / "manifest.json"
    if manifest.is_file():
        try:
            previous = json.loads(manifest.read_text(encoding="utf-8"))
            for catchment in previous["result"]["catchments"]:
                name = Path(catchment.get("mask") or "").name
                if re.fullmatch(r"catchment_\d{5,}\.tif", name):
                    old_masks.add(name)
        except (ValueError, KeyError, TypeError, AttributeError):
            # An unrelated/unreadable manifest cannot establish ownership of old masks.
            pass
    names = incoming | old_masks
    for name in names:
        target = output / name
        if target.resolve() == source_dem.resolve():
            raise ValueError("Output would overwrite the input DEM; choose another directory.")
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError(f"Cannot replace a directory or link used as a result file: {target}")

    # This is a unique, checked sibling directory. Only files moved here by this
    # operation are removed; no recursive deletion of the user's output directory.
    backup = output.with_name(f".{output.name}.backup-{uuid4().hex}")
    backup.mkdir()
    saved: list[str] = []
    published: list[str] = []
    try:
        for name in sorted(names):
            target = output / name
            if target.exists():
                target.rename(backup / name)
                saved.append(name)
        for name in sorted(incoming):
            (stage / name).rename(output / name)
            published.append(name)
    except OSError:
        try:
            for name in published:
                (output / name).unlink()
            for name in saved:
                (backup / name).rename(output / name)
            backup.rmdir()
        except OSError as rollback_error:
            raise OSError(
                f"Could not restore all previous results. Recovery files are preserved at {backup}"
            ) from rollback_error
        raise
    # Publishing succeeded. If cleanup is blocked, retain the recovery files rather
    # than reporting that the completed calculation failed.
    try:
        for name in saved:
            (backup / name).unlink()
        backup.rmdir()
    except OSError:
        pass
