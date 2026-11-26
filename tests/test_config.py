"""Where settings put things when MEL_HOME is relative."""

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
FOLDERS = ("DOCS_DIR", "MANIFEST_FILE", "DATA_DIR", "CHROMA_DIR", "REPORTS_DIR", "AUDIT_LOG")


def test_a_relative_mel_home_keeps_the_manifest_in_the_docs_folder(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in FOLDERS}
    env.update(MEL_HOME="workspace", PYTHONPATH=str(SRC))
    out = subprocess.run(
        [sys.executable, "-c", "from mel_mcp.config import settings as s; print(s.manifest_file)"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    expected = (tmp_path / "workspace" / "docs" / "manifest.csv").resolve()
    assert Path(out.stdout.strip()) == expected
