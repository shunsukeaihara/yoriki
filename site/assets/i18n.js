/*
 * Minimal i18n for the landing page.
 * English is the source text in the HTML. Elements marked `data-i18n` are translated by looking up
 * their (whitespace-normalized) English text in the dictionary below. `data-i18n-attr="aria-label"`
 * translates an attribute instead. Unknown strings fall back to English.
 */
;(function () {
  const ja = {
    'Skip to content': '本文へ移動',
    Features: '機能',
    'How it works': '仕組み',
    Security: 'セキュリティ',
    Deploy: '配備',
    Roadmap: 'ロードマップ',
    Docs: 'ドキュメント',
    'Toggle theme': 'テーマを切り替える',

    // Hero
    'Open source · MIT · pre-alpha': 'オープンソース · MIT · pre-alpha',
    'AI agents for everyone at work.': '社内の誰もが使える、業務用AIエージェント。',
    'Governed by your servers, not by the prompt.': '権限を決めるのは、プロンプトではなくサーバー。',
    "Yoriki is a self-hosted platform that lets employees delegate real work to AI agents through chat. Skills, connectors and sandboxes stay under your administrators' control.":
      'Yorikiは、社員がチャットでAIエージェントに実際の業務を任せられる、セルフホストの基盤です。Skill・Connector・Sandboxは、管理者の管理のもとに置かれます。',
    'View on GitHub': 'GitHubで見る',
    'Read the design': '設計書を読む',

    // Mock UI
    'Invoice intake': '請求書の取込',
    running: '実行中',
    'Extract the line items from the attached invoices and produce a CSV for accounting.':
      '添付した請求書から明細を抜き出して、経理用のCSVを作ってください。',
    Plan: '計画',
    'Read 12 PDFs in the sandbox': 'Sandboxで12件のPDFを読む',
    "Normalize vendor names with the department's master CSV": '部門のマスターCSVで取引先名を名寄せする',
    'Cross-check totals against accounting-db (read-only)': 'accounting-db（読取り専用）と合計を照合する',
    'Write invoices_2026-10.csv': 'invoices_2026-10.csv を書き出す',
    'Add an instruction while it runs…': '実行中に追加の指示を出す…',
    Stop: '停止',
    Sandbox: 'Sandbox',
    Network: 'ネットワーク',
    none: 'なし',
    Runtime: 'ランタイム',
    Image: 'イメージ',
    Pinned: '固定した版',
    'read-only': '読取り専用',
    Model: 'モデル',

    // Origin
    "(与力) were the trusted officers of Edo-period Japan who carried out a magistrate's work. The name says what the product does: capable hands that act within the authority they are given.":
      '（与力）は、江戸時代に奉行の下で実務を担った役人のこと。与えられた権限の範囲で、確実に仕事をこなす手。この製品がすることを、そのまま名前にしました。',

    // Principle
    'The idea': '考え方',
    'Trust the boundary,': '信じるのは境界。',
    'not the model.': 'モデルではなく。',
    'Most agent products ask the model to behave. Yoriki does not. Who can use which Skill, which tables a Connector may read, and what a command can reach are decided by the server and the execution environment, and are enforced there.':
      '多くのエージェント製品は、モデルに「行儀よくふるまうこと」を求めます。Yorikiはそうしません。誰がどのSkillを使えるか、Connectorがどの表を読めるか、コマンドがどこまで届くかは、サーバーと実行環境が決め、そこで強制します。',
    'Employees get a chat where they state the goal and make the business calls. The agent chooses the method and does the work, inside a sandbox with no network, with only the files it was given. No approval prompt for every command. No prompt-level permission theatre.':
      '社員はチャットで目的を伝え、業務上の判断を下します。エージェントは方法を選び、ネットワークのないSandboxの中で、渡されたファイルだけを使って作業します。コマンドごとの承認はありません。プロンプトで権限を演出することもありません。',

    // Features
    'What you get': '提供するもの',
    'One platform for the whole company': '全社で使う、ひとつの基盤',
    'Conversations that do work': '仕事が進む会話',
    'Progress streams live over SSE. Interrupt, answer a confirmation, queue the next request, or stop. Switch model and effort per conversation. Close the tab and the run keeps going.':
      '進捗はSSEで逐次表示。途中で指示を足す、確認に答える、次の依頼を積む、停止する。会話ごとにモデルとeffortを切り替えられます。タブを閉じても実行は続きます。',
    'Skills, versioned': '版を持つSkill',
    'Procedures, rules and files (Markdown, CSV, PPTX) bundled per job. Draft, evaluate, publish, roll back. Scoped to a person, a department or the company. One kill switch stops a Skill everywhere.':
      '業務ごとの手順・判断基準・資料（Markdown、CSV、PPTX）をひとまとめに。Draft、評価、公開、切戻し。範囲は個人・部門・全社。緊急停止はすべてのRunに効きます。',
    'Connectors with limits': '上限つきのConnector',
    'Read-only access to PostgreSQL, MySQL and SQL Server. Expose only the tables and columns you choose, with row caps, as immutable versions. Pick one in chat with @.':
      'PostgreSQL・MySQL・SQL Serverを読取り専用で。公開する表と列、行数の上限を不変の版として持ちます。会話では @ で指定します。',
    'Tasks and triggers': 'タスクと起動',
    'Turn a recurring job into a template: prompt, pinned Skill version, input fields. Run it by hand or from a webhook, without a conversation. Results go where that task says.':
      '繰り返す業務はテンプレートに。プロンプト、固定したSkillの版、入力の項目を定義し、会話を介さず手動やWebhookから実行します。結果はタスクごとの通知先へ。',
    'One control plane': 'ひとつの管理画面',
    'LLM connections (OpenAI, Anthropic, Gemini, local), model profiles, runtime profiles, users, departments, SSO and usage in one admin UI. API keys are encrypted at rest and never leave it.':
      'LLMの接続（OpenAI・Anthropic・Gemini・ローカル）、モデル設定、Runtime Profile、ユーザー・部門、SSO、利用量を一元管理。API keyは暗号化して保存し、外へ出しません。',
    'Sandbox by default': '既定でSandbox',
    "Commands run in containers with no network, from images pinned by digest, under gVisor in production. Only this run's attachments and Skill files are mounted. Nothing else exists.":
      'コマンドはネットワークのないコンテナで動きます。イメージはdigestで固定、本番はgVisor。マウントされるのは、このRunの添付とSkillの資料だけです。',

    // How
    'From a request to a result': '依頼から結果まで',
    Ask: '依頼する',
    'An employee opens a conversation, attaches files, and picks a Skill with / and a Connector with @, or leaves the choice to the agent.':
      '社員が会話を開き、ファイルを添付し、/ でSkill、@ でConnectorを指定します。エージェントに任せることもできます。',
    'The Runner assembles context from the pinned Skill version and calls the LLM through the control plane, using the connection and model profile your admins configured.':
      'Runnerが固定したSkillの版から文脈を組み立て、管理者が設定した接続とモデル設定で、Control Planeを通してLLMを呼びます。',
    Execute: '実行する',
    'Commands run in an isolated sandbox. Progress streams back live. If the agent needs a decision, the run waits for the answer and resumes where it left off.':
      'コマンドは隔離されたSandboxで動き、進捗は逐次返ります。判断が必要になればRunは回答を待ち、そこから再開します。',
    Deliver: '届ける',
    'Artifacts are stored (local disk or S3), the full event history is kept, and the conversation stays open for the next request.':
      '成果物はローカルまたはS3に保存され、イベントの履歴はすべて残ります。会話はそのまま次の依頼を待ちます。',
    'Architecture: browser, nginx, Nuxt and Django API, PostgreSQL, worker, sandbox and LLM providers':
      '構成：ブラウザ、nginx、NuxtとDjango API、PostgreSQL、Worker、Sandbox、LLMプロバイダー',
    Employee: '社員',
    'browser · SSO': 'ブラウザ · SSO',
    'Control plane · your host': 'Control Plane · 自社のホスト',
    'employee & admin UI': '社員向け・管理画面',
    'REST · SSE': 'REST · SSE',
    'Django Q2 · Runner': 'Django Q2 · Runner',
    'source of truth': '実行状態の正本',
    'Sandbox container': 'Sandboxのコンテナ',
    'network: none · gVisor · digest-pinned': 'ネットワークなし · gVisor · digestで固定',
    'LLM providers': 'LLMプロバイダー',
    'Internal DBs': '社内DB',
    'read-only connectors': '読取り専用のConnector',
    'Only the control plane talks to LLM providers and internal databases. The sandbox has no network at all.':
      'LLMプロバイダーや社内DBと通信するのはControl Planeだけ。Sandboxにネットワークはありません。',

    // Security
    'Boundaries you can verify': '確かめられる境界',
    'The design doc lists acceptance tests for each boundary. These are not aspirations in a prompt; they are properties of the server, the database and the container runtime.':
      '設計書には、境界ごとの受入条件を書いています。プロンプトに書いた願望ではなく、サーバー・データベース・コンテナランタイムの性質です。',
    'No network from the sandbox': 'Sandboxからネットワークに出られない',
    'Shell and Python cannot reach the Internet, the office LAN or DNS. LLM calls, packages and connectors go through managed paths in the control plane.':
      'shellとPythonからInternet・社内LAN・DNSへ接続できません。LLMの呼出し、パッケージ、Connectorは、Control Planeの管理された経路を通ります。',
    "Only this run's files": 'このRunのファイルだけ',
    "Another run's data, arbitrary host files, and database or OAuth credentials are not reachable from inside the sandbox.":
      '別のRunのデータ、ホストの任意のファイル、DBやOAuthの認証情報には、Sandboxの中から届きません。',
    'Permissions enforced server-side': '権限はサーバー側で強制',
    'Permission classes, filtered querysets and sandbox limits decide what a user and their agent can do. Skills and Connectors a user may not use are invisible even as candidates.':
      'permission、QuerySetの絞り込み、Sandboxの制限が、利用者とそのエージェントにできることを決めます。権限のないSkill・Connectorは、候補としても見えません。',
    'Immutable versions and history': '不変の版と履歴',
    'Skill, Connector and model profile versions never change once published. Running work keeps its pinned version; messages, events and artifacts are retained.':
      'Skill・Connector・モデル設定の版は、公開した後は変わりません。実行中のRunは固定した版を使い続け、メッセージ・イベント・成果物は保存されます。',
    'Secrets stay in the control plane': 'SecretはControl Planeの中に',
    'LLM API keys are encrypted at rest and never sent to the runner, the sandbox, logs or the UI. Key rotation is a management command.':
      'LLMのAPI keyは暗号化して保存し、Runner・Sandbox・ログ・画面へは出しません。鍵の交換は管理コマンドで行います。',
    'Exactly one attempt at a time': '同時に動くAttemptはひとつ',
    'PostgreSQL is the source of truth for every run. Claims, leases and fencing stop duplicate execution even when a worker dies and a stale one comes back.':
      'すべてのRunの正本はPostgreSQL。claim・lease・fencingで、Workerが落ちても古いWorkerが戻ってきても、二重実行を防ぎます。',

    // Deploy
    'Your host. Your data. One compose file.': '自社のホストに。データは手元に。Composeひとつで。',
    'Yoriki ships as Docker images with a single-host Compose setup. Grow to multiple app servers, dedicated workers and a managed PostgreSQL when you need to. Publish through Cloudflare Tunnel without opening a port, or behind your own load balancer.':
      'YorikiはDockerイメージと、単一ホスト用のCompose構成で配布します。必要になれば、複数のアプリサーバー、専用のWorker、マネージドのPostgreSQLへ広げられます。ポートを開けずにCloudflare Tunnelで公開するか、自社のロードバランサーの裏に置けます。',
    'Sign-in with Microsoft 365 or Google Workspace (OIDC), or invited accounts where there is no SSO':
      'Microsoft 365・Google Workspace（OIDC）でログイン。SSOのない環境では招待制のアカウント',
    'Artifacts on local disk or S3-compatible storage': '成果物はローカルディスクまたはS3互換ストレージへ',
    'Sandbox images published on Docker Hub with provenance, pinned by digest':
      'SandboxのイメージはDocker Hubでprovenance付きで公開し、digestで固定',
    'Add worker hosts on the same private network as you scale': '同じ私設ネットワークにWorkerのホストを追加して拡張',
    '# Single host, production': '# 単一ホスト、本番',
    '# Publish through Cloudflare Tunnel instead of opening a port': '# ポートを開けずにCloudflare Tunnelで公開する',

    // Roadmap
    'Where it stands': 'いまの状況',
    'Yoriki is pre-alpha. The first stage of the design is being implemented in the open. Expect breaking changes, and read the status document before relying on anything.':
      'Yorikiはpre-alphaです。設計の第1段階を公開しながら実装しています。互換性のない変更があります。使う前に状況の文書を読んでください。',
    'Stage 1 · in progress': '第1段階 · 実装中',
    'Governed single-host execution': '管理された単一ホスト実行',
    'Conversations with live progress, versioned Skills with files, read-only database Connectors, task definitions, the harness with leases and resume, no-network sandboxes, SSO, admin UI, Compose deploy.':
      '逐次表示のある会話、資料を持つ版つきSkill、読取り専用のDB Connector、タスクの定義、leaseと再開を持つハーネス、ネットワークのないSandbox、SSO、管理画面、Composeでの配備。',
    'Not yet:': 'まだないもの：',
    'webhook triggers, notifications, Teams / Slack / Discord channels, the plugin registry, Python package additions, quotas.':
      'Webhookでの起動、通知、Teams / Slack / DiscordのChannel、Pluginの仕組み、Pythonパッケージの追加、クオータ。',
    'Stage 2': '第2段階',
    'Resume and failure recovery': '再開と障害復旧',
    'Checkpoints and workspace snapshots, waiting on input, outbox delivery, the reconciler, and the full set of failure drills: dead workers, stale leases, racing replies, publish conflicts.':
      'CheckpointとWorkspaceのsnapshot、入力待ち、Outboxによる配信、Reconciler、そして障害の検証一式：Workerの停止、失効したlease、競合する回答、公開の競合。',
    'Stage 3': '第3段階',
    'More worker hosts': 'Workerサーバーの追加',
    'Worker-only servers on the same private network, capability-based assignment, drain, and cleanup, sharing one PostgreSQL and one object store.':
      '同じ私設ネットワーク内のWorker専用サーバー、能力別の配分、drain、清掃。PostgreSQLとオブジェクトストレージは共有します。',

    // Final
    'Read the design. Run it. Tell us what breaks.': '設計を読んで、動かして、壊れたところを教えてください。',
    'Everything is in the repository: the design document, the conventions, the data model, and the acceptance criteria. Issues and pull requests are welcome.':
      '設計書、規約、データモデル、受入条件まで、すべてリポジトリにあります。IssueとPull Requestを歓迎します。',
    'Star on GitHub': 'GitHubでStar',
    'Current status': 'いまの状況',

    // Footer
    'Open-source AI agents for the workplace, governed by your servers.':
      '自社のサーバーが権限を決める、オープンソースの業務用AIエージェント。',
    Documentation: 'ドキュメント',
    Design: '設計',
    Authorization: '権限',
    'Agent harness': 'エージェントのハーネス',
    Deployment: '配備',
    Project: 'プロジェクト',
    Status: '状況',
    'MIT License': 'MITライセンス',
    'Released under the MIT License.': 'MITライセンスで公開しています。',
  }

  const dictionaries = { en: null, ja }
  const STORAGE_KEY = 'yoriki-lp-lang'
  const meta = {
    en: {
      title: 'Yoriki — AI agents for everyone at work, governed by your servers',
      description:
        "Yoriki is an open-source, self-hosted platform that lets employees delegate real work to AI agents through chat. Skills, connectors and sandboxes stay under your administrators' control.",
    },
    ja: {
      title: 'Yoriki — 社内の誰もが使える、業務用AIエージェント',
      description:
        'Yorikiは、社員がチャットでAIエージェントに業務を任せられる、オープンソースのセルフホスト基盤。Skill・Connector・Sandboxを管理者が一元管理し、権限はサーバーと実行環境で強制します。',
    },
  }

  const normalize = (s) => s.replace(/\s+/g, ' ').trim()

  // Remember the English source once, so switching back and forth is lossless.
  const nodes = Array.from(document.querySelectorAll('[data-i18n], [data-i18n-attr]')).map((el) => {
    const attr = el.getAttribute('data-i18n-attr')
    const source = attr ? el.getAttribute(attr) : el.textContent
    return { el, attr, source: normalize(source || '') }
  })

  function apply(lang) {
    const dict = dictionaries[lang]
    for (const { el, attr, source } of nodes) {
      const text = (dict && dict[source]) || source
      if (attr) el.setAttribute(attr, text)
      else el.textContent = text
    }
    document.documentElement.lang = lang
    document.title = meta[lang].title
    const desc = document.querySelector('meta[name="description"]')
    if (desc) desc.setAttribute('content', meta[lang].description)
    for (const btn of document.querySelectorAll('.lang__btn')) {
      btn.setAttribute('aria-pressed', String(btn.dataset.lang === lang))
    }
    try {
      localStorage.setItem(STORAGE_KEY, lang)
    } catch (_) {
      /* private mode etc. */
    }
  }

  function initialLang() {
    const fromQuery = new URLSearchParams(location.search).get('lang')
    if (fromQuery && dictionaries[fromQuery] !== undefined) return fromQuery
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      if (stored && dictionaries[stored] !== undefined) return stored
    } catch (_) {
      /* ignore */
    }
    return (navigator.language || '').toLowerCase().startsWith('ja') ? 'ja' : 'en'
  }

  for (const btn of document.querySelectorAll('.lang__btn')) {
    btn.addEventListener('click', () => apply(btn.dataset.lang))
  }
  apply(initialLang())

  // Expose for debugging / tests
  window.yorikiI18n = { apply, dictionaries, normalize, sources: nodes.map((n) => n.source) }
})()
