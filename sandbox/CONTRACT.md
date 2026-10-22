# Sandboxの契約

契約番号：**1**

Runner（`api/` のLauncherとTool）とSandboxイメージの取り決め。イメージはラベル `org.yoriki.sandbox-contract` に、この番号を記録する。Runnerは対応する契約番号のイメージだけを、Runtime Profileとして登録させる（deployment.md §2.1）。

- この文書を変えたら、契約番号と、イメージのラベルと、Runnerの対応番号を同じPull Requestで上げる（development.md §3.4）。
- 契約は `tests/` の契約テストで確かめる。テストは、ここに書いた制限の下でSandboxを起動する（`tests/sandbox.py`）。
- 補助コマンドが返す内容は、Sandboxの中で作られたものなので、権限や実行状態の正本にしない（design.md §7.1）。ファイルの中身の表示として扱う。

## 1 イメージ

| 項目 | 取り決め |
|---|---|
| ラベル | `org.yoriki.sandbox-contract`（この契約番号）、`org.yoriki.sandbox-profile`（`docs` 等） |
| 利用者 | `10001:10001`（イメージの `USER`）。名前は `sandbox` |
| PID 1 | `CMD` は補助コマンドの `init`。何もせず、親のいなくなったプロセスを回収する。SIGTERM・SIGINT・SIGHUPを無視するので、Sandboxの中のコマンドからは止められない。コマンドは呼出しごとに `docker exec` で始める（harness.md §8.1） |
| 補助コマンド | `/opt/yoriki/bin/yoriki-sandbox`（§4） |
| Python | `/usr/local/bin/python3`（mise.tomlの版）。頻出ライブラリは、全依存の版とSHA-256を固定したlockから、このPythonに入れてある（design.md §7.6）。補助コマンドもこれで動く |
| lock | `/opt/yoriki/requirements.lock`（イメージに入れたpackageの全依存の版とSHA-256）。追加するpackageの依存を解くときに使う |
| `/packages` | 空のディレクトリ。追加するpackageのbundle（wheelとlock）を、Launcherが読取り専用でmountする |
| 入っているもの | harness.md §8.5のGNUのツールとPython、`images/<profile>/requirements.in` のライブラリ、日本語のフォント（Noto CJK） |
| 入れないもの | 秘密情報、社内向けの設定、`sudo`。パッケージの追加（apt・ネットワークからのpip）はSandboxの中ではできない |

## 2 起動の制限

LauncherはSandboxを次の制限で起動する（design.md §7.2）。これより緩めない。資源の値はRuntime Profileで決め、表の値は契約テストで使う既定値。

| 項目 | 指定 |
|---|---|
| ネットワーク | `--network=none` |
| 利用者 | `--user=10001:10001` |
| rootfs | `--read-only` |
| 権限 | `--cap-drop=ALL`、`--security-opt=no-new-privileges:true`。標準のseccompを外さない |
| PID 1 | `--init` を付けない（イメージの `init` をPID 1にする。§4.2の `all` がPID 1を止めないため） |
| 止め方 | `docker rm -f`（SIGKILL）。`docker stop` のSIGTERMは `init` が無視する |
| 資源 | `--pids-limit=256`、`--memory=2g`、`--memory-swap=2g`（swapなし）、`--cpus=2` |
| `/tmp` | tmpfs（`size=256m,mode=1777`） |
| `/workspace/work` | 書ける・実行できる。tmpfs（`uid=10001,gid=10001,mode=0700,exec`）か、Workerの作業領域のbind mount。エージェントが作ったスクリプトを `./run.sh` で動かせるようにする（Pythonで任意のコードを動かせるので、実行を止めても守れるものはない） |
| `/workspace/output` | 書ける。tmpfs（`uid=10001,gid=10001,mode=0700`）か、Workerの作業領域のbind mount |
| `/workspace/env` | 書ける・実行できる（tmpfsなら `exec` を付ける）。最初は空で、`init-env` で薄い仮想環境を作る |
| `/packages` | 追加するpackageがあるときだけ、そのbundleを読取り専用のbind mountで |
| `/workspace/input`・`/workspace/master` | 読取り専用のbind mount |
| 渡さないもの | privileged、host network・PID、デバイス、Dockerのソケット、ホストの任意のディレクトリ |

- `/workspace/env` は、イメージのPythonの上に `--system-site-packages` で作る薄い仮想環境（数十KB）で、追加したpackageだけを持つ。追加したpackageは、イメージの版より先に読まれる。追加の量だけtmpfsのメモリを使う。
- `/workspace/work`・`/workspace/output`・`/workspace/env` の大きさは、tmpfsの `size` か、作業領域のquotaで制限する（design.md §7.2）。

## 3 コマンドの環境

Runnerは、呼出しごとに次の環境変数だけを渡す（harness.md §8.2）。イメージの環境変数は引き継がない。

| 変数 | 値 |
|---|---|
| `HOME` | `/workspace/work/.home` |
| `PATH` | `/workspace/env/bin:/opt/yoriki/bin:/usr/local/bin:/usr/bin:/bin` |
| `LANG` | `C.UTF-8` |
| `TZ` | 導入先の設定（例：`Asia/Tokyo`） |
| `PAGER`・`GIT_PAGER` | `cat` |
| `TERM`・`NO_COLOR` | `dumb`・`1` |
| `PYTHONUNBUFFERED` | `1` |
| `MPLBACKEND`・`MPLCONFIGDIR` | `Agg`・`/workspace/work/.cache/matplotlib` |
| `XDG_CACHE_HOME`・`PIP_CACHE_DIR` | `/workspace/work/.cache`・`/workspace/work/.cache/pip` |

## 4 補助コマンド `yoriki-sandbox`

`docker exec -i <container> /opt/yoriki/bin/yoriki-sandbox <サブコマンド>` で呼ぶ（harness.md §8.4）。

- 引数は標準入力のJSONのオブジェクトで渡す（shellの解釈を通さない）。
- 結果は標準出力に1つのJSONのオブジェクトで返す。成功は `{"ok": true, ...}`、モデルに返して直させるエラーは `{"ok": false, "error": {"code": "...", "message": "...", "detail": {...}}}`。どちらも終了コードは0。0以外の終了コードは補助コマンド自身の不具合（標準エラーに詳細）で、Runの障害として扱う。
- `message` はモデル向けの英語で、次にどうすればよいかを書く。
- パスは絶対パス。symlinkを辿った先が `/workspace` の中（`write`・`edit` は `/workspace/work`・`/workspace/output` の中）でなければ断る。Runnerも同じことを確かめる。

### 4.1 `exec`

コマンドを独立したプロセスグループで実行する（harness.md §8.3）。

- 入力：`command`（bashで実行する文字列）、`cwd`（既定 `/workspace/work`）、`env`（§3の変数。これだけを渡す）。
- 標準入力のJSONを読んだ後、コマンドの標準入力は `/dev/null` にする。
- コマンドの標準出力と標準エラーは、どちらもこのプロセスの標準出力へ流す（1つの出力として順に読む）。
- 補助コマンド自身の知らせは、標準エラーへ1行ずつJSONで書く：始まり `{"event": "started", "pgid": N}`、終わり `{"event": "exited", "exit_code": N, "signal": N|null}`。
- 終了コードは、コマンドの終了コード（signalで終わったら `128 + signal`）。始める前に断った場合（`cwd` が外にある等）は、標準出力にエラーのJSONを書いて `125` で終わる。
- 終了と終了コードの正本は、LauncherがDockerのexecの状態（inspect）から読む。
- 補助コマンドのプロセスは、SIGINT・SIGTERM・SIGHUPを無視して、コマンドの終わりを待つ。止めるときは `kill` でプロセスグループへ送る。

### 4.2 `kill`

- 入力：`pgid`（`exec` の `started` の値）か `all: true`、`signal`（`INT`・`TERM`・`KILL`。既定 `TERM`）。
- `pgid`：そのプロセスグループへ送る。ジョブを止めるのに使う。コマンドが `setsid` 等で新しいグループを作ると、それには届かない。
- `all`：PID 1（`init`）と呼んだプロセス自身を除く、すべてのプロセスへ送る。Attemptを終える・Checkpointを確定する前に `KILL` で送り、Sandboxの中で動き続けるものがないようにする（harness.md §8.3）。
- 結果：`{"ok": true, "killed": true|false}`（`false` は送る先がなかった）。

### 4.3 `read`

形式に合わせて読む（harness.md §7.8）。形式はファイルの拡張子で決める。

- 入力：`path`、`offset`・`limit`（行。既定1・2000、`limit` は2000まで）、`pages`（PDF。`"1-5"`、`"3,7"`。既定は先頭10ページ）、`sheet`・`range`（Excel。`range` は `A1:F50`）、`images`（真なら画像を返す。使うモデルが画像の入力に対応するときだけ真にする）。
- 共通の結果：`kind`（`text`・`csv`・`pdf`・`xlsx`・`docx`・`pptx`・`image`・`binary`）、`path`、`size`、`sha256`（読んだ内容の版。`write`・`edit` の `expected_sha256` に使う）、`text`（モデルに見せる本文）。
- 行で区切る形式（`text`・`csv`・`docx`・`pptx`）：`text` は行番号つき（`     12│本文`。1行2000文字で切る）。`total_lines`、`first_line`、`last_line`、続きがあれば `next_offset`。
- `text`・`csv`：`encoding`（`utf-8`・`cp932` 等）、`bom`、`newline`（`lf`・`crlf`、両方があれば `mixed`）。`csv` は `csv`（`delimiter`、`columns`、`rows`）。
- `pdf`：`total_pages`、`pages`（読んだページ）、続きがあれば `next_pages`。テキストのないページは `pages_without_text`、`images` が真ならその画像（5ページまで）。
- `xlsx`：`sheets`（`name`・`dimensions`・`rows`・`columns`）。`sheet` を指定すると、`text` にセルの番地つきの値（数式のセルは `⟨=式⟩` を添える）。値のあるセルを4000まで出す。範囲（既定はシートの使われた範囲）が5万セルを超えるときは、読む前に断る（`too_large`）。
- `image`：`format`・`width`・`height`。`images` が真なら画像。
- `images` の要素：`mime`（`image/png`）、`width`、`height`、`data`（base64）。長辺は1568pxまでに縮小する。
- 20MBより大きいファイルは読まない（`too_large`）。

### 4.4 `write`

テキストのファイルを作る・置き換える。

- 入力：`path`、`content`、`expected_sha256`（既存のファイルを置き換えるときは、`read` 等で得た版。新しく作るときは省く）。
- 既存のファイルは、`expected_sha256` が今の内容と一致するときだけ置き換える（読んでから書く。harness.md §7.8）。encoding・改行・BOMは元のファイルに合わせる（改行がまじったファイルは、送られた内容の改行のまま）。新しいファイルはUTF-8・LFで作り、親のディレクトリも作る。
- 同じディレクトリの一時ファイルに書いてから置き換える。置き換える直前に版を確かめ直し、変わっていれば `changed`。親のディレクトリはsymlinkを辿らずに開き、置き換える先が通常のファイルでなければ断る。ファイルのモードは保つ。
- 版の確かめ直しと置き換えの間に、動いているジョブが同じファイルを変える可能性は残る。Runnerは、書込みと同じファイルを変えるジョブを並行させないよう、モデルに案内する。
- 結果：`created`、`sha256`（書いた後の版）、`lines`、`encoding`。

### 4.5 `edit`

テキストのファイルの一部を置き換える。

- 入力：`path`、`old_string`、`new_string`、`replace_all`（既定false）、`expected_sha256`（必須）。
- 一致は、完全一致 → 行末の空白の違いを無視、の2段まで。1か所に決まらなければ `multiple_matches`（`detail.lines` に一致した行番号）。
- 改行（CRLF・LF）がまじったファイルは、改行を保てないので断る（`mixed_newlines`）。
- 書き方は `write` と同じ（一時ファイルから置き換え、直前に版を確かめ直す）。
- 結果：`replacements`、`sha256`、`snippet`（変えた箇所の前後3行。行番号つき）。

### 4.6 `glob`・`grep`・`stat`

- `glob`：入力 `pattern`（`path` からの相対。`**` を使える。`..` は断る）、`path`（既定 `/workspace`）。パターンの途中のsymlinkを辿って `/workspace` の外に出たものは、結果に入れない。結果 `entries`（`path`・`type`・`size`。更新の新しい順に100件まで）、`total`、`truncated`。
- `grep`：入力 `pattern`（正規表現。ripgrep）、`path`、`include`（`*.py` 等）、`output`（`files`・`lines`・`count`。既定 `files`）。結果 `total_matches`、`total_files`、`files`（`path`・`matches`）か `lines`（`path`・`line`・`text`）。250件まで、`truncated`。
- `stat`：入力 `path`。結果 `exists`、`type`（`file`・`directory`・`other`）、`size`、`mtime`、ファイルなら `sha256`・`kind`。

### 4.7 `init`・`init-env`・`version`

- `init`：コンテナのPID 1（§1）。標準入力・出力は使わない。
- `init-env`：空の `/workspace/env` に、イメージのPythonの上の薄い仮想環境（`--system-site-packages`）を作る。`pip` には、入れる操作（`install`・`download`・`wheel`・`uninstall`）を断って `install_python_packages` へ案内する形を置く（Sandboxにはネットワークがないため。権限の境界ではない）。追加のpackageは、Runnerが `/workspace/env/bin/python -m pip install --no-index ...` で `/packages` から入れる。すでにあれば何もしない。Launcherが、Sandboxを起動した直後に呼ぶ。結果 `created`、`seconds`。
- `version`：結果 `contract`（この契約番号）、`python`。

### 4.8 エラーの `code`

| code | 意味 |
|---|---|
| `invalid_argument` | 引数の誤り |
| `not_found` | ファイル・シートがない |
| `not_a_file`・`not_a_directory` | 種類が違う |
| `outside_workspace` | `/workspace` の外 |
| `read_only` | 書けない場所（`input`・`master` 等） |
| `not_read` | 既存のファイルを、読まずに置き換えようとした |
| `changed` | 読んだ後に内容が変わった |
| `no_match`・`multiple_matches` | `edit` の `old_string` が見つからない・1か所に決まらない |
| `not_text`・`unencodable` | テキストでない・元のencodingで書けない文字がある |
| `mixed_newlines` | 改行がまじっていて、`edit` で保てない |
| `unreadable` | 壊れたファイル等で読めない |
| `too_large` | 大きすぎる |
| `timeout` | 時間がかかりすぎた（`grep`） |
