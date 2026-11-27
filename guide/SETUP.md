# Setting up MEL Evidence

Installing, connecting an assistant and fixing problems. For loading your own reports, indicators and surveys, see [Your data](YOUR_DATA.md).

## Before you start

You need:

- **Python 3.11 or 3.12.** Not 3.13: PyTorch, which the search model needs, doesn't support it on every machine. Check with `python3.11 --version`.
- **Git**, to download the project.
- **An MCP client:** [Claude Code](https://docs.claude.com/en/docs/claude-code) (recommended) or Claude Desktop.
- **Node.js** (optional), only for the MCP Inspector test in [Check that it works](#check-that-it-works).
- **About 2 GB of disk space** for the Python packages and the search model.

Tested on macOS (Intel) and Linux. Windows hasn't been tested yet: see [Windows](#windows-not-yet-tested).

You can load any of three kinds of evidence, and you don't need all of them:

| Evidence | What you provide | What the assistant can do with it |
|---|---|---|
| **Reports** | PDF or OpenDocument (.odt) files, plus a spreadsheet describing each one | Search them by meaning and quote them with page citations |
| **Indicator tracker** | A CSV of targets and actuals, or public IATI data | Compare targets with actuals and flag doubtful figures |
| **Survey data** | Cleaned Stata (.dta), CSV or Parquet files, one per survey round | Calculate totals, averages and shares by group, with small groups hidden |

> **Privacy.** The server never shows individual survey answers, but it does show report text and indicator figures. Only load files you are allowed to share with the AI service you connect.

## Install on macOS or Linux

From a terminal on macOS or Linux:

```bash
git clone https://github.com/tiwipaddler/mel-mcp.git
cd mel-mcp
./setup.sh
```

`setup.sh` finds Python 3.11 or 3.12, creates a private Python environment in a folder called `venv`, installs the packages, creates your settings file (`.env`), runs the tests and downloads the search model (about 400 MB). If anything is missing, it stops and says what to install. It is safe to run again, and it never replaces your `.env` or your data. Add `--starter` to also load the Kenya starter pack.

After setup, **in every new terminal window**, activate the environment before using any `mel-*` command:

```bash
cd mel-mcp
source venv/bin/activate
```

Your prompt shows `(venv)` when it is active. The rest of this guide assumes you are in the `mel-mcp` folder with the environment active; check with `pwd`, which should end in `/mel-mcp`.

On Windows, see [Windows](#windows-not-yet-tested) below.

<details>
<summary>Installing by hand instead</summary>

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt     # the exact versions this was tested with
pip install -e . --no-deps          # adds the mel-* commands
cp .env.example .env
pytest -q
```

</details>

### Settings (`.env`)

Everything has a working default. The settings you are most likely to change:

| Setting | Default | Change it when |
|---|---|---|
| `DOCS_DIR` | `docs` | Your reports live somewhere else, such as a shared or cloud drive |
| `MANIFEST_FILE` | `docs/manifest.csv` | Your report list lives somewhere else |
| `DATA_DIR` | `data` | Your indicator and survey files live somewhere else |
| `IATI_API_KEY` | empty | You want to fetch public IATI data (free key, see [Your data](YOUR_DATA.md#reports-and-indicators-from-iati-public-data)) |
| `MIN_CELL_SIZE` | `10` | Your rules require a different minimum group size for survey results |

Paths can be relative to the project folder or absolute. **Put paths that contain spaces in double quotes**:

```
DOCS_DIR="/Users/me/Google Drive/My Drive/mel-docs"
MANIFEST_FILE="/Users/me/Google Drive/My Drive/mel-docs/manifest.csv"
```

Never put real API keys in `.env.example`, which is shared on GitHub. `.env` is not shared.

## Windows (not yet tested)

Nobody has run MEL Evidence on Windows yet, so treat these steps as a starting point and [open an issue](https://github.com/tiwipaddler/mel-mcp/issues) with whatever goes wrong. There are two routes.

### Route 1: WSL (recommended)

WSL (Windows Subsystem for Linux) runs Linux inside Windows, so the macOS and Linux steps above work unchanged.

1. Install WSL: open PowerShell as administrator, run `wsl --install`, and restart when asked. This installs Ubuntu.
2. Open **Ubuntu** from the Start menu, then install Python and Git:

   ```bash
   sudo apt update
   sudo apt install -y git python3 python3-venv
   ```

   Ubuntu 24.04 comes with Python 3.12, which works. Check with `python3 --version`.
3. Follow [Install on macOS or Linux](#install-on-macos-or-linux) inside Ubuntu.
4. Connect an assistant:
   - **Claude Code inside Ubuntu** works as on Linux: `mel-mcp-config claude-code`.
   - **Claude Desktop on Windows** can't run a Linux path directly. Start the server through `wsl` instead, in `%APPDATA%\Claude\claude_desktop_config.json`, replacing `YOU` with your Ubuntu user name:

     ```json
     {
       "mcpServers": {
         "mel-evidence": {
           "command": "wsl",
           "args": ["-e", "/home/YOU/mel-mcp/venv/bin/mel-mcp-server"]
         }
       }
     }
     ```

     The server finds its project folder from where it is installed, so it needs no `MEL_HOME` here.

### Route 2: Windows itself (PowerShell)

`setup.sh` doesn't run on Windows, so the steps are done by hand. Install [Python 3.11 or 3.12](https://www.python.org/downloads/windows/) (tick **Add python.exe to PATH**) and [Git](https://git-scm.com/download/win), then in PowerShell:

```powershell
git clone https://github.com/tiwipaddler/mel-mcp.git
cd mel-mcp
py -3.11 -m venv venv
venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
copy .env.example .env
python -m pytest -q
mel-starter load
```

If PowerShell refuses to run `Activate.ps1`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then try again.

On Windows the environment keeps its programs in `venv\Scripts\`, not `venv/bin/`, so read `venv/bin/...` in this guide as `venv\Scripts\...`. `mel-mcp-config claude-desktop` prints settings with the right Windows path.

To add an API key, open `.env` in Notepad and paste it after the `=`.

## Check that it works

1. **Run the tests** (about 20 seconds, no network or API keys needed):

   ```bash
   pytest -q
   ```

   Every test should pass.

2. **Search from the terminal**, to check your reports were indexed:

   ```bash
   mel-search "reasons for delays" --country Kenya --year-from 2020
   ```

   You should see passages with their title, page, phase and year.

3. **Optional: try the tools by hand** in the MCP Inspector, a web page that lists the server's tools and lets you call them (needs Node.js):

   ```bash
   npx @modelcontextprotocol/inspector venv/bin/mel-mcp-server
   ```

   Click **Connect**, open **Tools**, and try `list_datasets` or `find_indicators` with `country` set to `Kenya`.

## Connect an assistant

The server works with any assistant that can start a local MCP server on your computer (a "stdio" server). Each assistant keeps its settings in a different place, so the project includes a command that prints the right settings for yours, with your paths already filled in:

```bash
mel-mcp-config cursor
```

It prints where the settings go, then the settings to copy. Use the name for your assistant:

| Assistant | Name to use | What you do with the output |
|---|---|---|
| Claude Code | `claude-code` | Run the printed command |
| Codex CLI and the ChatGPT desktop app (they share settings) | `codex` | Run the printed command |
| Claude Desktop | `claude-desktop` | Paste into its config file, then restart it |
| Cursor | `cursor` | Paste into `~/.cursor/mcp.json` |
| VS Code (Copilot Chat, agent mode) | `vscode` | Paste into `.vscode/mcp.json` in the folder you open |
| Gemini CLI | `gemini` | Paste into `~/.gemini/settings.json` |
| LM Studio (models running on your computer) | `lm-studio` | Paste into its `mcp.json` (Program tab, Edit mcp.json) |
| Anything else | `other` | Give your assistant the command and `MEL_HOME` setting shown |

If a settings file already lists other servers, add the `mel-evidence` entry next to them rather than replacing the file. After changing settings, restart the assistant or start a new session.

**Start coding assistants from a separate folder.** Claude Code, Codex, Gemini CLI, Cursor and VS Code can read the files in the folder they are opened in. Open them in an empty folder, not in `mel-mcp`, so they can only reach your data through the server's tools, which apply the privacy and data-quality rules:

```bash
mkdir -p ~/mel-chat
cd ~/mel-chat
claude          # or codex, gemini; or open this folder in Cursor or VS Code
```

**Check it's connected.** In Claude Code, Codex and Gemini CLI, type `/mcp` to see the server and its tools. In the other apps, look for `mel-evidence` in the tools or MCP list. If it shows as failed, see [Troubleshooting](#troubleshooting).

**Which model to use.** The answers depend on the model following several steps and long tool descriptions. It was built and tested with Claude. Other strong tool-using models should work. Small local models often skip steps or run out of context; in LM Studio, choose a model that supports tool use and give it a long context (at least 16,000 tokens).

**Web chat apps can't use it yet.** claude.ai, ChatGPT on the web and similar apps can only connect to servers hosted on the internet, and this server runs on your computer. Use one of the apps above.

## Troubleshooting

| Problem | Fix |
|---|---|
| `pip install` fails on `torch` | Use Python 3.11 or 3.12: `python3.11 -m venv venv`. On Intel Macs, keep `torch==2.2.2` and `numpy==1.26.4` from `requirements.txt` |
| `command not found: mel-ingest` | Activate the environment: `source venv/bin/activate` (from the project folder) |
| `manifest.csv is missing column(s)` | The manifest needs at least `file`, `title`, `study_phase` and `source_type` |
| `study_phase '...' is not one of ...` | Use one of the listed values exactly, e.g. `endline`, not `Endline report` |
| A folder path with spaces isn't found | Put it in double quotes in `.env` |
| `IATI API error 401` or `403` | Check `IATI_API_KEY` in `.env` and that your IATI subscription is active |
| `Too many open files` when running tests (macOS) | Run `ulimit -n 1024` in that terminal, then `pytest -q` again |
| A report never comes up in searches | It may be a scanned PDF with no text, or missing from the manifest. Check with `mel-search` and its title |
| The assistant can't see new reports or fixes | Start a new assistant session (see [Keeping it up to date](YOUR_DATA.md#keeping-it-up-to-date)) |
| The assistant shows the server as failed | Run `mel-mcp-config <assistant>` again and check your settings match it exactly, including the full path to `venv/bin/mel-mcp-server`. Check that `pytest -q` passes |
| The assistant never uses the tools | Name them in your question ("Using the mel-evidence tools, ..."). With a local model, choose one that supports tool use |
| A prompt got only part of your question | Claude Code splits prompt arguments at spaces. Type single words (`Kenya 2020`) and give the question when asked |
