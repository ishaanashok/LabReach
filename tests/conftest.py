import json
import stat
import sys
from datetime import date

import pytest

from labreach.compose import templates as tpl
from labreach.config import REPO_ROOT, load_settings
from labreach.db import connect
from labreach.profile import load_profile


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def stop_file(tmp_path):
    return tmp_path / "STOP"


@pytest.fixture(scope="session")
def profile():
    return load_profile(REPO_ROOT / "data" / "student_profile.yaml")


@pytest.fixture(scope="session")
def settings():
    loaded = load_settings()
    loaded["student"].setdefault("born", "2010-07-01")   # CI has no private contact file
    return loaded


@pytest.fixture(scope="session")
def templates():
    return tpl.load_templates()


@pytest.fixture
def today():
    return date(2026, 10, 13)


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """A stand-in `claude` executable. Queue responses with fake.queue([...]); inspect fake.calls()."""
    script = tmp_path / "claude"
    queue_file, log_file = tmp_path / "queue.json", tmp_path / "calls.jsonl"
    queue_file.write_text("[]")
    script.write_text(f"""#!{sys.executable}
import json, os, sys
queue_file, log_file = {str(queue_file)!r}, {str(log_file)!r}
prompt = sys.stdin.read()
with open(log_file, "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "prompt": prompt, "has_api_key": "ANTHROPIC_API_KEY" in os.environ}}) + "\\n")
queue = json.load(open(queue_file))
resp = queue.pop(0) if queue else {{"stdout": "{{}}", "returncode": 0}}
json.dump(queue, open(queue_file, "w"))
sys.stdout.write(resp["stdout"]); sys.stderr.write(resp.get("stderr", ""))
sys.exit(resp.get("returncode", 0))
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("LABREACH_CLAUDE_BIN", str(script))

    class Fake:
        bin = str(script)

        @staticmethod
        def queue(items):
            queue_file.write_text(json.dumps(items))

        @staticmethod
        def calls():
            return [json.loads(line) for line in log_file.read_text().splitlines()] if log_file.exists() else []

        @staticmethod
        def ok(structured=None, result=""):
            return {"stdout": json.dumps({"type": "result", "is_error": False, "result": result,
                                          **({"structured_output": structured} if structured is not None else {})})}

    return Fake
