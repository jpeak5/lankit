"""
Shared fixtures for the offline unit suite (Layer 1 of the test harness; strategy doc lives in lankit-workshop).

Everything here runs without hardware, network, or a live router. Config
fixtures are loaded by explicit Path — never from cwd or LANKIT_CONFIG.
"""
from pathlib import Path

import pytest
import yaml

from lankit.core.config import load

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def minimal_yml() -> Path:
    """Path to the canonical 3-segment valid fixture."""
    return FIXTURES_DIR / "network-minimal.yml"


@pytest.fixture
def no_pihole_yml() -> Path:
    """Path to the valid fixture with no Pi-hole host (dns_ip fallback)."""
    return FIXTURES_DIR / "network-no-pihole.yml"


@pytest.fixture
def cfg(minimal_yml):
    """Loaded Config from the minimal fixture."""
    return load(minimal_yml)


@pytest.fixture
def mutated(tmp_path):
    """Return a helper that copies a fixture YAML, applies a mutation to the
    raw dict, writes the result to tmp_path, and returns the new Path.

    Used to produce invalid configs from a known-valid base.
    """
    def _mutate(base_path: Path, mutate_fn) -> Path:
        raw = yaml.safe_load(base_path.read_text())
        mutate_fn(raw)
        out = tmp_path / "network.yml"
        out.write_text(yaml.safe_dump(raw, sort_keys=False))
        return out

    return _mutate
