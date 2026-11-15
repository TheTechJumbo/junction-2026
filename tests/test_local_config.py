import os

import pytest

from scripts.run_local import load_private_env


def test_private_values_are_literal_and_explicit_environment_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("EXISTING", "keep")
    monkeypatch.delenv("CONFIG_TEST_SECRET", raising=False)
    path = tmp_path / "private.env"
    path.write_text('# local\nCONFIG_TEST_SECRET="$(do-not-execute) $HOME"\nEXISTING=replace\n')
    load_private_env(path)
    try:
        assert os.environ["CONFIG_TEST_SECRET"] == "$(do-not-execute) $HOME"
        assert os.environ["EXISTING"] == "keep"
    finally:
        os.environ.pop("CONFIG_TEST_SECRET", None)


def test_bad_assignment_never_reports_secret(tmp_path):
    path = tmp_path / "private.env"
    path.write_text("INVALID KEY=private-value")
    with pytest.raises(ValueError) as error:
        load_private_env(path)
    assert "private-value" not in str(error.value)
