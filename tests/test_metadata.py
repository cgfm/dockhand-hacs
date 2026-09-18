"""Repository metadata and translation consistency tests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "dockhand"
EXPECTED_REPOSITORY = "https://github.com/cgfm/dockhand-hacs"


def _json(path: Path) -> dict[str, Any]:
    """Load a repository JSON object."""
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    assert isinstance(value, dict)
    return value


def _leaf_paths(value: Any, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    """Return every scalar translation path."""
    if isinstance(value, dict):
        paths: set[tuple[str, ...]] = set()
        for key, child in value.items():
            paths.update(_leaf_paths(child, (*prefix, key)))
        return paths
    return {prefix}


def test_manifest_and_hacs_metadata_are_release_consistent() -> None:
    """Version, repository URLs, dependency policy, and minimum HA agree."""
    manifest = _json(INTEGRATION / "manifest.json")
    hacs = _json(ROOT / "hacs.json")

    assert manifest["version"] == "1.4.0"
    assert manifest["documentation"] == EXPECTED_REPOSITORY
    assert manifest["issue_tracker"] == f"{EXPECTED_REPOSITORY}/issues"
    assert manifest["requirements"] == []
    assert hacs["homeassistant"] == "2026.8.0"
    assert "ha-dockhand" not in repr((manifest, hacs))


def test_all_translation_files_have_the_complete_english_schema() -> None:
    """Every shipped locale has all flow, exception, and entity keys."""
    strings = _json(INTEGRATION / "strings.json")
    english = _json(INTEGRATION / "translations" / "en.json")
    expected_paths = _leaf_paths(english)
    assert _leaf_paths(strings) == expected_paths

    for path in sorted((INTEGRATION / "translations").glob("*.json")):
        translation = _json(path)
        assert _leaf_paths(translation) == expected_paths, path.name
        assert not re.search(r"https?://", repr(translation)), path.name

    assert not re.search(r"https?://", repr(strings))


def test_yaml_and_json_metadata_parse() -> None:
    """Every shipped metadata document is syntactically valid."""
    for path in sorted(ROOT.rglob("*.json")):
        _json(path)
    with (INTEGRATION / "services.yaml").open(encoding="utf-8") as file:
        yaml.safe_load(file)
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        with path.open(encoding="utf-8") as file:
            assert isinstance(yaml.safe_load(file), dict), path.name


def test_sections_card_grid_defaults_match_rendered_sizes() -> None:
    """Custom cards advertise grid sizes that contain their rendered content."""
    source = (INTEGRATION / "frontend" / "dockhand-cards.js").read_text(
        encoding="utf-8"
    )
    overview_source, container_source = source.split("class DockhandContainerCard", 1)
    overview_source = overview_source.split("class DockhandOverviewCard", 1)[1]
    container_source, stack_source = container_source.split(
        "class DockhandStackCard", 1
    )
    logs_source = (INTEGRATION / "frontend" / "dockhand-logs-card.js").read_text(
        encoding="utf-8"
    )

    assert 'return { columns: 12, rows: "auto", min_columns: 12 };' in overview_source
    assert (
        "return { columns: 12, rows: 6, min_columns: 12, min_rows: 6 };"
        in container_source
    )
    assert (
        "return { columns: 12, rows: 3, min_columns: 12, min_rows: 3 };" in stack_source
    )
    assert "columns: 12," in logs_source
    assert "rows: this._open ? 6 : 2," in logs_source
    assert "min_columns: 12," in logs_source
    assert "min_rows: 2," in logs_source
    for card_source in (container_source, stack_source):
        assert ":host { height: 100%; }" in card_source
        assert "ha-card { height: 100%; }" in card_source
