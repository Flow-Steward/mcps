"""Contract for LlamaParse's regional API-key catalog choices."""

import json
from pathlib import Path

import yaml


def test_regional_api_key_choices_require_secret_full_bearer_header():
    folder = Path(__file__).resolve().parents[1] / "servers" / "llamaparse"
    catalog = yaml.safe_load((folder / "catalog.yaml").read_text())
    server = json.loads((folder / "server.json").read_text())
    choices = {remote["id"]: remote for remote in catalog["remotes"]}
    assert set(choices) == {"north-america-api-key", "europe-api-key"}
    records = {remote["url"]: remote for remote in server["remotes"]}
    assert len(records) == 2
    for remote in choices.values():
        assert remote["auth"]["mode"] == "headers"
        assert "Bearer" in remote["field_labels"]["Authorization"]
        header, = records[remote["url"]]["headers"]
        assert header["name"] == "Authorization"
        assert header["isRequired"] is True
        assert header["isSecret"] is True
        assert "Bearer" in header["description"]
    assert "getUploadUrl" in catalog["preview_note"]
    assert "uploadFileByUrl" in catalog["preview_note"]
