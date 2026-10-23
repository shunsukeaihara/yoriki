"""文書処理の所要時間を、コンテナの実行環境ごとに比べる（design.md §7.2の5、gVisorの評価）。

契約テストと同じ制限のSandboxを実行環境ごとに起動し、同じ作業を `yoriki-sandbox exec` で動かして、
ホストから見た時間（docker execの往復を含む）の中央値を出す。

    cd sandbox && uv run python -m benchmarks.docs --runtime runc --runtime runsc
"""

import argparse
import json
import statistics
import subprocess
import time
import uuid
from pathlib import Path

from tests.conftest import IMAGE
from tests.sandbox import RUN_OPTIONS, Sandbox

# 日本語の文字の入った20ページのPDFを作る（matplotlib）
MAKE_PDF = r"""python - <<'PY'
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
plt.rcParams["font.family"] = "Noto Sans CJK JP"
with PdfPages("report.pdf") as pdf:
    for page in range(20):
        fig = plt.figure(figsize=(8.27, 11.69))
        for line in range(40):
            text = f"請求書 {page:02d}-{line:02d} 株式会社テスト 金額 {page * 1000 + line} 円"
            fig.text(0.08, 0.95 - line * 0.022, text)
        pdf.savefig(fig)
        plt.close(fig)
PY
"""

# 見本のファイル。作る時間は測らない
SETUP = (
    "set -e\nmkdir -p /workspace/work/b && cd /workspace/work/b\n"
    + MAKE_PDF
    + r"""seq 1 2000000 | awk '{print $1 % 9973, "行", $1}' > big.txt
for d in $(seq 50); do mkdir -p many/$d; for f in $(seq 100); do echo "file $d $f 請求" > many/$d/$f.txt; done; done
"""
)

# (名前, コマンド)。/workspace/work/b で実行する
CASES = [
    ("コマンドの往復（true）", "true"),
    ("Pythonの起動", "python -c pass"),
    ("pandasの読込み", "python -c 'import pandas'"),
    ("PDFの生成（matplotlib、20ページ）", MAKE_PDF),
    (
        "PDFの文字の抽出（pdfplumber、20ページ）",
        "python -c \"import pdfplumber; pdf = pdfplumber.open('report.pdf'); "
        "print(sum(len(p.extract_text() or '') for p in pdf.pages))\"",
    ),
    (
        "PDFの画像化（pypdfium2、150dpi、20ページ）",
        "python -c \"import pypdfium2 as p; d = p.PdfDocument('report.pdf'); "
        "[d[i].render(scale=150/72).to_pil().save(f'/tmp/p{i}.png') for i in range(len(d))]\"",
    ),
    (
        "PDFの分割と結合（pypdf）",
        "python -c \"from pypdf import PdfReader, PdfWriter; r = PdfReader('report.pdf'); w = PdfWriter(); "
        "[w.add_page(p) for _ in range(5) for p in r.pages]; w.write('/tmp/merged.pdf')\"",
    ),
    (
        "Excelの書出しと読込み（openpyxl・pandas、2万行）",
        'python -c "import pandas as pd, numpy as np; '
        "df = pd.DataFrame(np.random.rand(20000, 10), columns=list('abcdefghij')); "
        "df.to_excel('/tmp/a.xlsx', index=False); print(len(pd.read_excel('/tmp/a.xlsx')))\"",
    ),
    (
        "Word・PowerPointの生成（python-docx・python-pptx）",
        "python -c \"import docx, pptx; d = docx.Document(); [d.add_paragraph(f'段落 {i}') for i in range(500)]; "
        "t = d.add_table(rows=100, cols=5); d.save('/tmp/a.docx'); p = pptx.Presentation(); "
        "[setattr(p.slides.add_slide(p.slide_layouts[1]).shapes.title, 'text', f'スライド {i}') for i in range(50)]; "
        "p.save('/tmp/a.pptx')\"",
    ),
    (
        "pandasの集計（100万行）",
        "python -c \"import pandas as pd, numpy as np; df = pd.DataFrame({'k': np.random.randint(0, 1000, 1_000_000), "
        "'v': np.random.rand(1_000_000)}); print(df.groupby('k').v.agg(['sum', 'mean']).shape)\"",
    ),
    (
        "名寄せ（rapidfuzz、2000×2000）",
        "python -c \"from rapidfuzz import process, fuzz; a = [f'株式会社テスト{i}' for i in range(2000)]; "
        'print(process.cdist(a, a, scorer=fuzz.ratio).shape)"',
    ),
    ("並べ替え（sort、200万行）", "sort -n big.txt | tail -1"),
    ("文字列の検索（rg、5000ファイル）", "rg -c 請求 many | wc -l"),
    ("ファイルの一覧（find、5000ファイル）", "find many -type f | wc -l"),
    ("小さいファイルの作成（1000個）", "mkdir -p /tmp/s && for i in $(seq 1000); do echo $i > /tmp/s/$i; done"),
    ("tarでのまとめ（Workspaceの取り出しに相当）", "tar -cf - -C /workspace/work b | wc -c"),
]


def start(runtime: str) -> tuple[Sandbox, float]:
    """添付（/workspace/input）はmountしない"""
    box = Sandbox(IMAGE, f"yoriki-sandbox-bench-{runtime}-{uuid.uuid4().hex[:8]}", Path())
    options = [*RUN_OPTIONS, f"--runtime={runtime}"]
    began = time.perf_counter()
    subprocess.run(["docker", "run", "-d", "--name", box.name, *options, IMAGE], check=True, capture_output=True)
    assert box.helper("init-env", {})["ok"]
    return box, time.perf_counter() - began


def measure(box: Sandbox, command: str, repeat: int) -> list[float]:
    times = []
    for _ in range(repeat):
        began = time.perf_counter()
        result = box.exec(command, cwd="/workspace/work/b")
        times.append(time.perf_counter() - began)
        assert result.exit_code == 0, f"{command!r} exited {result.exit_code}:\n{result.output}"
    return times


def measure_helper(box: Sandbox, repeat: int) -> list[float]:
    times = []
    for _ in range(repeat + 1):
        began = time.perf_counter()
        box.helper("stat", {"path": "/workspace/work/b/report.pdf"})
        times.append(time.perf_counter() - began)
    return times[1:]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", action="append", required=True, help="比べる実行環境（先頭を基準にする）")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--json", type=Path, help="結果をJSONで書き出す")
    args = parser.parse_args()

    results: dict[str, dict[str, float]] = {}
    for runtime in args.runtime:
        starts = []
        for _ in range(args.repeat):
            box, elapsed = start(runtime)
            starts.append(elapsed)
            box.stop()
        box, _ = start(runtime)
        try:
            box.sh(SETUP)
            row = {"Sandboxの起動（init-envまで）": statistics.median(starts)}
            row["補助コマンドの往復（stat）"] = statistics.median(measure_helper(box, args.repeat))
            for name, command in CASES:
                box.exec(command, cwd="/workspace/work/b")  # 1回目はキャッシュを温める
                row[name] = statistics.median(measure(box, command, args.repeat))
            results[runtime] = row
        finally:
            box.stop()

    base = args.runtime[0]
    header = ["作業", *(f"{r}（秒）" for r in args.runtime), *(f"{r}/{base}" for r in args.runtime[1:])]
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for name in results[base]:
        values = [results[r][name] for r in args.runtime]
        ratios = [f"{v / values[0]:.2f}" for v in values[1:]]
        print("| " + " | ".join([name, *(f"{v:.3f}" for v in values), *ratios]) + " |")
    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
