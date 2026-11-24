"""The settings printed for each assistant must be valid and point at this project."""

import json
import shlex

import pytest

from mel_mcp import client_config
from mel_mcp.config import ROOT


@pytest.mark.parametrize("client", ["claude-desktop", "cursor", "gemini", "lm-studio", "other"])
def test_json_settings_use_the_shared_layout(client):
    entry = json.loads(client_config.render(client))["mcpServers"]["mel-evidence"]
    assert entry["command"].endswith(("mel-mcp-server", "mel-mcp-server.exe"))
    assert entry["env"] == {"MEL_HOME": str(ROOT)}


def test_vscode_settings_use_its_own_layout():
    entry = json.loads(client_config.render("vscode"))["servers"]["mel-evidence"]
    assert entry["type"] == "stdio" and entry["env"] == {"MEL_HOME": str(ROOT)}


@pytest.mark.parametrize("client", ["claude-code", "codex"])
def test_commands_keep_paths_with_spaces_together(client, monkeypatch):
    monkeypatch.setattr(client_config, "ROOT", "/Users/me/My Projects/mel-mcp")
    words = shlex.split(client_config.render(client))
    assert "MEL_HOME=/Users/me/My Projects/mel-mcp" in words
    assert words[words.index("--") + 1].endswith("mel-mcp-server")


def test_claude_code_options_come_before_the_name():
    words = shlex.split(client_config.render("claude-code"))
    # --env takes several values, so the name can't come straight after it.
    assert words[words.index("mel-evidence") - 1] == "user"


def test_main_says_where_the_settings_go(capsys):
    client_config.main(["cursor"])
    assert "~/.cursor/mcp.json" in capsys.readouterr().out
