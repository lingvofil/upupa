from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _pinned_versions(path: Path) -> dict[str, str]:
    versions = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        versions[name.lower()] = version
    return versions


def test_shared_dependency_versions_match_production():
    production = _pinned_versions(ROOT / "requirements.txt")
    testing = _pinned_versions(ROOT / "requirements-test.txt")

    mismatches = {
        name: (production[name], testing[name])
        for name in production.keys() & testing.keys()
        if production[name] != testing[name]
    }

    assert not mismatches, (
        "Shared dependencies must use the same versions in production and tests: "
        f"{mismatches}"
    )
