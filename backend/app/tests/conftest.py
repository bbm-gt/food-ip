from pathlib import Path

import pytest

from backend.scripts.make_sample_shots import make_sample_shots


@pytest.fixture(scope="session")
def sample_shots(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    return make_sample_shots(tmp_path_factory.mktemp("sample-shots"))

