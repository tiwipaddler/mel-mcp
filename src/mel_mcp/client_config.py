"""Print the settings that connect an assistant to this server, with this computer's paths.

Run:  mel-mcp-config cursor
"""

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from mel_mcp.config import ROOT

NAME = "mel-evidence"

WHERE = {
    "claude-code": "Run this command. Then start Claude Code from a folder outside the project.",
    "codex": (
        "Run this command. Codex and the ChatGPT desktop app share this setting, "
        "so the server appears in both."
    ),
    "claude-desktop": (
        "Add this to ~/Library/Application Support/Claude/claude_desktop_config.json "
        "(Windows: %APPDATA%\\Claude\\claude_desktop_config.json), then restart Claude Desktop."
    ),
    "cursor": "Add this to ~/.cursor/mcp.json (all projects) or .cursor/mcp.json in one project.",
    "vscode": (
        "Add this to .vscode/mcp.json in the folder you open in VS Code, "
        "then use Copilot Chat in agent mode."
    ),
    "gemini": (
        "Add this to ~/.gemini/settings.json at the top level. If the file already has an "
        "mcpServers section, copy only the mel-evidence entry into it."
    ),
    "lm-studio": "In LM Studio, open the Program tab, click Edit mcp.json and add this.",
    "other": (
        "Any assistant that can start a local (stdio) MCP server: give it this command and "
        "environment variable. Most read this layout."
    ),
}
CLIENTS = tuple(WHERE)


def server_command() -> str:
    # The mel-mcp-server script sits next to the Python that runs this command (the venv).
    # abspath, not resolve(): resolving would follow the venv's link to the system Python.
    folder = Path(os.path.abspath(sys.executable)).parent
    name = "mel-mcp-server.exe" if sys.platform == "win32" else "mel-mcp-server"
    return str(folder / name)


def render(client: str) -> str:
    command, home = server_command(), str(ROOT)
    entry = {"command": command, "args": [], "env": {"MEL_HOME": home}}
    if client == "claude-code":
        return (
            f"claude mcp add --env MEL_HOME={shlex.quote(home)} --transport stdio "
            f"--scope user {NAME} -- {shlex.quote(command)}"
        )
    if client == "codex":
        return f"codex mcp add {NAME} --env MEL_HOME={shlex.quote(home)} -- {shlex.quote(command)}"
    if client == "vscode":
        return json.dumps({"servers": {NAME: {"type": "stdio", **entry}}}, indent=2)
    return json.dumps({"mcpServers": {NAME: entry}}, indent=2)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Print the settings that connect an assistant to this server."
    )
    parser.add_argument("client", choices=CLIENTS, help="The assistant you use")
    client = parser.parse_args(argv).client
    print(f"{WHERE[client]}\n\n{render(client)}")


if __name__ == "__main__":
    main()
