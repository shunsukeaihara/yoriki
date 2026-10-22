# Sandbox

エージェントのコマンドを動かすSandboxのイメージ（design.md §7、harness.md §8）。Runnerとの取り決めは [CONTRACT.md](CONTRACT.md)。

```
sandbox/
├── CONTRACT.md              # Runnerとイメージの取り決め（契約番号つき）
├── helper/                  # Sandboxの中の補助コマンド yoriki-sandbox（すべてのイメージで共通）
├── images/<profile>/        # 用途別のイメージ（今は docs だけ）
│   ├── Dockerfile
│   ├── requirements.in      # 入れるPythonのライブラリ
│   └── requirements.lock    # 全依存の版とSHA-256（requirements.in から作る。イメージのPythonに入れる）
└── tests/                   # 契約テスト（ホストで動かし、制限付きのSandboxを起動して確かめる）
```

## ビルドと契約テスト

ビルドのcontextは `sandbox/`（共通の `helper/` を使うため）。

```bash
docker build -f sandbox/images/docs/Dockerfile -t yoriki-sandbox-docs:dev sandbox
cd sandbox && uv sync && uv run pytest
```

- 契約テストは `YORIKI_SANDBOX_TEST_IMAGE`（既定 `yoriki-sandbox-docs:dev`）のイメージを使う。
- 本体で手元のイメージを使うときは、`YORIKI_SANDBOX_IMAGE_OVERRIDE=yoriki-sandbox-docs:dev` を設定する（deployment.md の開発時の手順）。

## ライブラリを変える

`requirements.in` を変えたら、lockを作り直す。公開から2週間以内の版は使わない（`--exclude-newer` に2週間前の日付を指定する）。

```bash
cd sandbox/images/docs
uv pip compile requirements.in --universal --python-version 3.13 --generate-hashes \
  --exclude-newer <2週間前の日付> --no-header -o requirements.lock
```

- ライセンスは、MIT・BSD・Apache等のものを優先する。AGPL・GPL・LGPL等のものは、入れる前に相談する（design.md §7.6。例：文字コードの判定は、LGPLの chardet ではなく MIT の charset-normalizer を使う）。
- ライブラリはイメージのPythonに入る。イメージは大きくなるが、Runごとの `/workspace/env` は大きくならない（CONTRACT.md §2）。

## コマンドを足す

エージェントがSkillで使ったコマンドは、`tests/commands.toml` に足して、制限の下で動くことを確かめる（harness.md §8.5）。

## 契約を変える

`CONTRACT.md` を変えたら、契約番号、`helper/yoriki_sandbox/__init__.py` の `CONTRACT`、イメージのラベル `org.yoriki.sandbox-contract`、Runnerの対応番号を同じPull Requestで上げる（development.md §3.4）。
