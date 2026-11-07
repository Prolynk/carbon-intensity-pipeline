import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SAMPLE = ROOT / "data" / "sample" / "sample_intensity.json"


@pytest.fixture
def sample_payload() -> dict:
    """A real 12-period API response, with the last actual nulled out."""
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


@pytest.fixture
def sample_df(sample_payload):
    import transform

    return transform.transform([sample_payload])
