"""代表的なコマンドが、実際の制限の下で動くことを確かめる（harness.md §8.5）。一覧は commands.toml"""

import tomllib
from pathlib import Path

import pytest

COMMANDS = tomllib.loads((Path(__file__).parent / "commands.toml").read_text(encoding="utf-8"))["command"]

SETUP = r"""
mkdir -p c '日本語のフォルダ'
seq 1 100 > c/a.txt
printf '番号,名前,点数\n1,山田,30\n2,佐藤,45\n' > c/名簿.csv
cp /workspace/input/請求書一覧.csv c/sjis.csv
printf '{"items": [{"name": "りんご", "price": 100}, {"name": "みかん", "price": 150}]}' > c/data.json
"""


@pytest.fixture(scope="module", autouse=True)
def setup(sandbox):
    sandbox.sh(SETUP)


@pytest.mark.parametrize("case", COMMANDS, ids=[c["run"].splitlines()[0][:60] for c in COMMANDS])
def test_command(sandbox, case):
    result = sandbox.exec(case["run"])
    assert result.exit_code == case.get("exit", 0), result.output
    if "output" in case:
        assert case["output"] in result.output, result.output
