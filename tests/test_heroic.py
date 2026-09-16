import json

import pytest

from conftest import make_game
from linuxport import heroic, proton


@pytest.fixture
def heroic_game(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / ".config" / "heroic"
    install = home / "Games" / "Example"
    install.mkdir(parents=True)
    (root / "legendaryConfig" / "legendary").mkdir(parents=True)
    (root / "GamesConfig").mkdir()
    game_id = "example-id"
    (root / "legendaryConfig" / "legendary" / "installed.json").write_text(
        json.dumps({game_id: {"app_name": game_id, "title": "Example", "install_path": str(install)}}),
        encoding="utf8")
    script = home / "tools" / "proton"
    script.parent.mkdir()
    script.write_text("", encoding="utf8")
    config = root / "GamesConfig" / f"{game_id}.json"
    config.write_text(json.dumps({game_id: {"wineVersion": {"bin": str(script)},
                                             "enviromentOptions": [{"key": "KEEP", "value": "yes"}]}}),
                      encoding="utf8")
    monkeypatch.setattr(heroic, "_roots", lambda: [root])
    game = make_game(install, "example.exe", source="Heroic")
    return game, config


def test_heroic_environment_merge_is_idempotent_and_keeps_existing(heroic_game, monkeypatch):
    game, config = heroic_game
    monkeypatch.setattr(heroic, "running", lambda: False)
    backup = heroic.set_environment(game, {"PROTON_FORCE_NVAPI": "1", "KEEP": "yes"})
    assert backup and backup.is_file()
    data = json.loads(config.read_text())
    values = {x["key"]: x["value"] for x in data["example-id"]["enviromentOptions"]}
    assert values == {"KEEP": "yes", "PROTON_FORCE_NVAPI": "1"}
    assert heroic.set_environment(game, {"PROTON_FORCE_NVAPI": "1"}) is None


def test_heroic_refuses_changes_while_running(heroic_game, monkeypatch):
    game, _ = heroic_game
    monkeypatch.setattr(heroic, "running", lambda: True)
    with pytest.raises(RuntimeError, match="Heroic is running"):
        heroic.set_environment(game, {"PROTON_FORCE_NVAPI": "1"})


def test_environment_tokens_parse_quoted_override():
    line = 'PROTON_FORCE_NVAPI=1 WINEDLLOVERRIDES="dxgi=n,b;d3d12=n,b" %command%'
    assert proton._environment_tokens(line) == {
        "PROTON_FORCE_NVAPI": "1", "WINEDLLOVERRIDES": "dxgi=n,b;d3d12=n,b"}
