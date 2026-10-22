import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from .sandbox import Sandbox

# 確かめるイメージ。CIはビルドしたイメージを、手元では `docker build ... -t yoriki-sandbox-docs:dev` したものを使う
IMAGE = os.environ.get("YORIKI_SANDBOX_TEST_IMAGE", "yoriki-sandbox-docs:dev")


def _input_files(root: Path) -> None:
    """/workspace/input に置く添付（読取り専用でmountする）"""
    (root / "請求書一覧.csv").write_bytes(
        "番号,取引先,金額\r\n1,株式会社テスト,1000\r\n2,髙橋商店,2500\r\n".encode("cp932")
    )
    (root / "メモ.txt").write_text("一行目\n二行目\n三行目\n", encoding="utf-8")
    (root / "data.bin").write_bytes(bytes(range(256)) * 4)


@pytest.fixture(scope="session")
def sandbox(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Sandbox]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False).returncode != 0:
        pytest.fail(
            f"Image {IMAGE} is not built. Run: docker build -f sandbox/images/docs/Dockerfile -t {IMAGE} sandbox"
        )
    input_dir = tmp_path_factory.mktemp("input")
    _input_files(input_dir)
    input_dir.chmod(0o755)
    for path in input_dir.iterdir():
        path.chmod(0o644)
    box = Sandbox(IMAGE, f"yoriki-sandbox-test-{uuid.uuid4().hex[:8]}", input_dir)
    box.start()
    try:
        yield box
    finally:
        box.stop()
