# Sandboxのイメージの新案

Sandboxのイメージを見直す案。ワークスペースの置き方とコンテナのライフサイクルは [session-proposal.md](session-proposal.md) に書く。利用者と議論して決めた内容で、設計書へ反映する元にする。今の仕様は、設計書を直すまで design.md・harness.md・sandbox/CONTRACT.md を正とする。反映したら本書を消す。


---

## 1 目的

- 1つのイメージで、文書処理を一通り動かせるようにする。`SKILL.md` の形の外部のSkill（docx・pptx・xlsx・pdf等）に必要な道具も、ここに含める。
- 「リッチ」は、この「1つのイメージで一通り動く」ことを指す。道具ごとに機能を削らないという意味ではなく、各道具はSkillで使う最低限にする。
- 外部のSkillはイメージにもYorikiにも同梱しない。導入先が公開されているものを取り込み、取り込むかどうかの判断も導入先が行う（取り込みの仕様はいったん検討の外）。

## 2 イメージの構成

イメージは今の `yoriki-sandbox-docs` の1つのまま、次を足す。軽いイメージと文書用のイメージには分けない。今の大きさは582MB。足した後の大きさは、ビルドして測る。

| 種類 | 内容 | 今のイメージ |
|---|---|---|
| LibreOffice | ヘッドレスで、Writer・Calc・Impressの変換と計算に要るものだけ | なし |
| Node.js | 本体と、スキルが使うパッケージ（`docx`、`pptxgenjs`、`react`、`react-dom`、`react-icons`、`sharp`） | なし |
| 変換の道具 | pandoc、poppler-utils（`pdftoppm`） | なし |
| Python | `markitdown` を足す | ほかの `openpyxl`・`pandas`・`pypdf`・`pdfplumber`・`python-docx`・`python-pptx`・`pillow` 等はある |

パッケージの一覧は、公開されているスキルの説明を検索して拾ったもので、すべての依存を確かめたものではない。作るときに現物で確かめる。

- Sandboxにネットワークがないのは変えない。`npm install` はできないので、Node.jsのパッケージはPythonと同じく、版とhashを固定したlockからイメージに入れる。
- rootfsは読取り専用のままにする。

## 3 フォント

| 項目 | 内容 |
|---|---|
| 用途 | PDFへの変換は、デザインの確認に使う。きれいなPDFは利用者が手元で作る |
| 入れるもの | Noto Sans CJKとNoto Serif CJKの、RegularとBold（今のイメージと同じ `fonts-noto-cjk`。4ファイルで計93MB） |
| 入れないもの | アラビア文字、絵文字、タイ文字等のほかの文字 |

- **fontconfig**：
  - `sans` はNoto Sans CJK JP、`serif` はNoto Serif CJK JP、`monospace` はNoto Sans Mono CJK JP（Sansのファイルに入っているはずなので、確かめる）。
  - メイリオ、游ゴシック、MSゴシック、Arial、Calibri等はSansへ、MS明朝、Times New Roman等はSerifへ向ける。
  - 文書の言語の指定が中国語や韓国語なら、そちらの字形を優先する。
- **割り切ること**：元のフォントと文字の幅が違うので、改行やレイアウトはずれることがある。

## 4 今の文書との違い

採用するときに直すもの。

| 文書 | 直すこと |
|---|---|
| design.md §7.6 | 「文書の処理はLibreOfficeを使わない」の方針を改める。PPTX・Wordのプレビューが作れない前提も変わる |
| design.md §3.9 | PPTXの画像としてのプレビューは、「LibreOfficeを使わない方針のため後で決める」としている。作れるようにはなるが、今回は作らない |
| harness.md §8.5 | 入っているコマンドの一覧、フォント。代表的なコマンドの契約テストも足す |
| sandbox/CONTRACT.md §1 | 入っているもの |
| docs/status.md | Node.jsの後回しの記述、イメージの説明 |

## 5 確かめること

試していないので、作る前に確かめる。

- **gVisorでLibreOfficeが動くか**：公開されているスキルの説明には、素の `soffice` がそのサンドボックスでは止まるので専用のスクリプトを使う、とある。Yorikiのrunc・gVisorの両方で試す。
- **LibreOfficeの設定の置き場所**：`HOME` は `/workspace/work/.home` なので、そのままだと設定がsnapshotに入る。別の場所へ向けるかを決める。
- **資源の上限**：LibreOfficeとNode.jsで足りるメモリ・プロセス数。Runtime Profileの初期値を見直す。
- **信頼しない文書を開くこと**：LibreOfficeのマクロを無効にする設定。ネットワークがないことを前提にしてよいかも確かめる。
- **イメージの大きさとビルド**：multi-arch（arm64）のビルドと、Docker Hubへの配布の時間。
- **配布のライセンス**：イメージは公開のDocker Hubに置く前提で、今はMIT・BSD・Apache等を優先している。LibreOffice（MPL）、pandoc（GPL）、poppler-utils（GPL）、Noto（OFL）を入れて配布してよいかを確かめる。
