/*
 * Minimal i18n for the landing page.
 * English is the source text in the HTML. Elements marked `data-i18n` are translated by looking up
 * their (whitespace-normalized) English text in the dictionary below. `data-i18n-attr="aria-label"`
 * translates an attribute instead. Unknown strings fall back to English.
 */
;(function () {
  const ja = {
    'Skip to content': '本文へ移動',
    'Why Yoriki': 'なぜYoriki',
    'For employees': '社員の方へ',
    Skills: 'Skill',
    Data: 'データ',
    Automation: '自動実行',
    'For IT': '管理者の方へ',
    'Toggle theme': 'テーマを切り替える',

    // Hero
    'AI agents your whole company': 'AIエージェントを、',
    'can use with confidence.': '全社で安心して使うための基盤。',
    'Using AI agents at work is no longer optional. But leaks, damaged systems, attacks on outsiders and runaway agents are real fears. Yoriki lets the company decide what data an agent may touch, where its commands run and where results go, and enforces that in software. Human judgement still matters. The agent cannot step over the line on its own.':
      '業務の効率化に、AIエージェントはもう欠かせない。でも、情報漏洩、システムやデータの破壊、社外への攻撃、暴走が怖い。Yorikiは、エージェントが触れるデータ、コマンドが動く場所、結果の届け先を会社が決め、ソフトウェアがそのとおりに守ります。人の判断に頼る部分はなくなりませんが、エージェントが自分で境界を越えることはありません。',
    'View on GitHub': 'GitHubで見る',
    'How the fears are answered': '不安への答えを見る',
    'Runs on your servers': '自社のサーバーで動く',
    'Cloud or local LLMs': 'クラウドでも社内のLLMでも',
    'Sandboxed commands': 'コマンドはサンドボックスで',

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
    Pinned: '固定した版',
    'read-only': '読取り専用',
    Model: 'モデル',

    // Fears
    'Four fears.': '4つの不安に、',
    'Four boundaries, enforced in software.': 'ソフトウェアが守る4つの境界で答える。',
    "These are the questions that stop a rollout. Each one is answered by a boundary that Yoriki's server and execution environment enforce, not by a rule in a prompt that the model may or may not follow.":
      '導入を止めるのは、この4つの問いです。それぞれに、Yorikiのサーバーと実行環境が強制する境界で答えます。モデルが守るかどうか分からない、プロンプトの中の決まりではありません。',
    '"Will the agent leak our data?"': '「エージェントが情報を漏らさないか」',
    'It only sees what administrators let it see.': '管理者が見せると決めたものしか見えません。',
    'Which data the agent can read is decided by administrators, table by table and column by column. Work that involves personal or confidential information is marked as such and runs only on LLMs you host. Nothing typed into a prompt can widen that.':
      'エージェントが読めるデータは、管理者が表と列の単位で決めます。個人情報や機密情報を扱う業務にはその印を付け、自社でホストするLLMでだけ動かします。プロンプトに何を書いても、この範囲は広がりません。',
    'Access scopes set in software, not in instructions': 'アクセスの範囲は指示文ではなく設定で決める',
    'Confidential work stays on local models': '機密の業務は社内のモデルにとどまる',
    'API keys and credentials never reach the agent': 'API keyや資格情報はエージェントに渡らない',
    '"Will people reach things that aren\'t theirs?"': '「本人の権限を超えて、情報や機能に触れないか」',
    'Department and permissions decide what each person, and their agent, can see and do.':
      '所属と権限が、本人とそのエージェントに見えるもの・できることを決めます。',
    'Skills, data sources and tasks have an owner and a scope: a person, a department or the whole company. What a user may see and do is checked on the server from their department and permissions, and the agent working for them gets exactly those rights, never more.':
      'Skill、データソース、タスクには持ち主と範囲があります。個人、部門、全社。誰に何が見え、何ができるかは、その人の所属と権限からサーバーが判定し、その人のために働くエージェントには、まったく同じ権限だけが与えられます。それ以上はありません。',
    'Scope by person, department or company': '範囲は個人・部門・全社',
    'Checked on the server, not in a prompt': '判定するのはプロンプトではなくサーバー',
    "The agent inherits the user's rights, never more": 'エージェントの権限は本人と同じで、それ以上にはならない',
    '"Will it break a system, or attack someone outside?"':
      '「システムを壊したり、社外を攻撃する踏み台になったりしないか」',
    'Commands run in a sandbox with nothing to break and no network.':
      '壊せるものがなく、ネットワークもないサンドボックスで、コマンドが動きます。',
    "Administrators restrict what the agent may do to databases and cloud storage at the software level. Commands run in a disposable sandbox that holds only that job's files, with no Internet, no office LAN and no DNS. The host, your systems and the outside world are out of reach. The only outbound traffic is what Yoriki itself makes through managed paths.":
      'DBやクラウドストレージに対してエージェントができることは、管理者がソフトウェアのレベルで制限します。コマンドは使い捨てのサンドボックスで動き、中にあるのはその仕事のファイルだけ。インターネットも、社内LANも、DNSもありません。ホスト、社内のシステム、社外には届かず、外へ出る通信はYoriki自身が管理された経路で行うものだけです。',
    'Access to DBs and storage limited by configuration': 'DBやストレージへのアクセスは設定で制限',
    "A fresh, isolated sandbox for every run, on gVisor, with only this job's files":
      '実行のたびに新しい、gVisor上の隔離されたサンドボックス。中はその仕事のファイルだけ',
    'No network from the sandbox; results go only to registered destinations':
      'サンドボックスからネットワークに出られず、結果が届くのは登録した通知先だけ',
    '"What if it runs wild?"': '「暴走しないか」',
    'People set the goal. The agent cannot grow its own powers.':
      '目的は人が決め、エージェントは自分の権限を増やせません。',
    'People decide the purpose and the business calls; the agent chooses the method within its limits. Any run can be stopped at once. Every tool call is checked on the server, the agent cannot grant itself permissions, and every step is recorded and cannot be rewritten.':
      '目的と業務上の判断は人が決め、エージェントは許された範囲で方法を選びます。実行はいつでもすぐ止められます。Toolの呼出しはすべてサーバーで確かめられ、エージェントは自分に権限を与えられず、すべての手順が記録され、書き換えられません。',
    'Stop any run, any time': 'どの実行も、いつでも止められる',
    'Permissions checked on every call, server-side': '権限は呼出しのたびにサーバー側で確認',
    'Immutable history of what was done': '何をしたかの履歴は書き換えられない',
    'What people type in remains their judgement. Yoriki supports it: a confidential flag that keeps sensitive work on local models, a check when a conversation starts, and data sources that are limited before anyone asks a question.':
      '何を入力するかは、人の判断のままです。Yorikiはそれを支えます。機密の業務を社内のモデルにとどめる印、会話を始めるときの確認、そして質問される前から制限されたデータソースです。',

    // For employees
    'And still, people just get work done.': 'それでも、社員は普通に仕事を頼めます。',
    'The boundaries sit underneath. On top, employees get a chat where they hand over real work: documents, spreadsheets, PDFs, data from internal systems.':
      '境界は下に敷かれています。その上で社員は、文書、表計算、PDF、社内システムのデータといった実際の仕事を、チャットで任せられます。',
    'Ask in plain language, attach files': '言葉で頼んで、ファイルを添付する',
    'Describe the job, attach what is needed, and pick a Skill or a data source if you want to. Or let the agent choose from what you are allowed to use.':
      '仕事を説明し、必要なものを添付し、使いたければSkillやデータソースを指定します。指定しなければ、エージェントが使える範囲から選びます。',
    'Watch it plan and work': '計画と作業を見守る',
    'The agent shows its plan and its progress as it goes. Add an instruction midway, answer a question, or queue the next request.':
      'エージェントは計画と進捗を見せながら進めます。途中で指示を足す、質問に答える、次の依頼を積んでおくことができます。',
    'No approval for every command': 'コマンドごとの承認はいらない',
    'Because the sandbox makes commands safe, nobody has to click "allow" for each step. Stop is always one click away.':
      'サンドボックスがコマンドを安全にしているので、手順ごとに「許可」を押す必要はありません。停止はいつでも1クリックです。',
    'Close the tab, come back later': 'タブを閉じて、あとで戻る',
    'Runs continue on the server. Results and files wait in the conversation, and the conversation stays open for the next job.':
      '実行はサーバーで続きます。結果とファイルは会話の中で待っていて、会話はそのまま次の仕事に使えます。',

    // Skills
    'Turn know-how into Skills': '業務の知識をSkillにして、',
    'the whole company can use.': '全社で使う。',
    'A Skill bundles how a job is done: the procedure, the rules of judgement, and the files it needs, such as a Markdown guideline, a CSV master list or a PowerPoint template. Share it with a person, a department or the whole company.':
      'Skillは、仕事のやり方をひとまとめにしたものです。手順、判断の基準、そして必要な資料。Markdownのガイドライン、CSVのマスター、PowerPointのテンプレートなど。個人、部門、全社の単位で共有できます。',
    'Shared, with a clear owner': '共有され、持ち主がはっきりしている',
    'Personal, department or company-wide. Who can use a Skill and who can change it is decided by its scope, not by whoever happens to find it.':
      '個人、部門、全社。誰が使え、誰が直せるかはSkillの範囲で決まります。たまたま見つけた人が変えることはできません。',
    'Maintained from the conversation': '会話の中で育てる',
    '"Make this procedure a Skill." "From now on, handle it this way." The agent drafts the change; people with the right to publish make it live. Files can be replaced the same way.':
      '「この手順をSkillにして」「今後はこの基準で処理して」。エージェントが変更の下書きを作り、公開の権限を持つ人がそれを有効にします。資料の差し替えも同じやり方でできます。',
    'Versioned, reversible, stoppable': '版があり、戻せて、止められる',
    'Every published Skill is an immutable version. Running work keeps the version it started with. Roll back in one step, or stop a Skill everywhere with one switch.':
      '公開したSkillは不変の版になります。実行中の仕事は始めたときの版を使い続けます。ひとつ前に戻すのも、すべての場所で止めるのも、ひとつの操作です。',

    // Automation
    'Routine work, started anywhere,': '定型業務を、どこからでも起動し、',
    'delivered anywhere.': 'どこへでも届ける。',
    'Put a routine job into a task: the instructions, the Skill version to use, and the inputs it takes. Then start it from a trigger instead of a conversation, and have the result show up where the team already looks.':
      '定型業務はタスクにまとめます。指示、使うSkillの版、受け取る入力。あとは会話の代わりにトリガーから起動し、結果はチームがいつも見ている場所へ届けます。',
    Triggers: 'トリガー',
    'from the systems you already run': 'いつも使っているシステムから',
    Chat: 'チャット',
    'an employee starts the task from a conversation': '社員が会話からタスクを起動する',
    Schedule: 'スケジュール',
    'on a timer': '決まった時刻に',
    'Manual run': '手動実行',
    'from the task list or the API': 'タスクの一覧かAPIから',
    'The task runs': 'タスクが動く',
    'Same safe sandbox as a conversation': '会話と同じ安全なサンドボックスで',
    'Fixed Skill version, so results stay consistent': 'Skillの版を固定し、結果がぶれない',
    'Inputs picked out of the incoming payload': '届いたpayloadから入力を取り出す',
    'Pauses for a human decision, then resumes': '人の判断が必要なら止まり、回答で再開する',
    Destinations: '通知先',
    'In-app': '画面内',
    'result and files in Yoriki': 'Yorikiの画面に結果とファイル',
    'hand the result to any other system': '結果を他のシステムへ渡す',
    'questions, answers and the report in one thread': '質問・回答・報告を1つのスレッドに',
    'When an automated run needs a human decision, it asks in the channel you chose. Anyone in that thread can answer, and the same run continues. Results go only to destinations an administrator registered, and confidential results only to destinations allowed to receive them.':
      '自動実行の途中で人の判断が必要になると、指定したチャネルで質問します。スレッドの誰でも答えられ、同じ実行が続きます。結果が届くのは管理者が登録した通知先だけで、機密の結果は、受け取ってよいとした通知先にだけ届きます。',

    // Plugins
    Plugins: 'プラグイン',
    'Data sources, triggers and destinations': 'データソース、トリガー、通知先は、',
    'are plugins.': 'すべてプラグイン。',
    'Everything that connects Yoriki to the outside is a plugin with the same contract: installed by the platform administrator as a reviewed package, configured with explicit limits, and checked on every call. Nothing loads code from a prompt or the network.':
      'Yorikiと外をつなぐものは、すべて同じ契約のプラグインです。基盤管理者がレビュー済みのパッケージとして入れ、明示的な制限を設定し、呼出しのたびにサーバーが確かめます。プロンプトやネットワークからコードを読み込むことはありません。',
    Connectors: 'Connector',
    'Database products, cloud storage such as SharePoint and Google Drive, and MCP servers.':
      'DB製品、SharePointやGoogle Driveなどのクラウドストレージ、MCPサーバー。',
    'Expose only the tables, columns, folders and operations you choose, with caps on rows and runtime':
      '公開するのは選んだ表・列・フォルダ・操作だけ。件数と実行時間に上限',
    'Several views on the same connection: one for accounting, another for sales':
      '同じ接続の上に、経理向け・営業向けなど複数の範囲を作れる',
    'Which departments may use each source is part of its configuration':
      'どの部門が使えるかは、データソースの設定の一部',
    'Sources holding personal data carry a confidential flag and are used only with local LLMs':
      '個人情報を持つデータソースには機密の印を付け、社内のLLMとだけ使う',
    'Employees point at a source with @ in the chat; the agent cannot see outside the slice':
      '社員はチャットで @ を付けて指定する。エージェントは範囲の外を見られない',
    'Webhook, chat, schedule, manual run.': 'Webhook、チャット、スケジュール、手動実行。',
    'Each trigger has its own credential and can be suspended on its own': 'トリガーごとに鍵があり、単独で止められる',
    'Incoming payloads are handed to the agent as files, never as instructions':
      '届いたpayloadは指示ではなくファイルとしてエージェントに渡す',
    'Duplicate events are dropped by their event ID': '重複したイベントはイベントIDで除く',
    "A trigger starts only the task it is attached to, with that task's permissions":
      'トリガーが起動できるのは結び付けたタスクだけで、そのタスクの権限で動く',
    'In-app, webhook and Zapier, Teams, Slack and Discord.': '画面内、WebhookとZapier、Teams・Slack・Discord。',
    'Results go only to destinations an administrator registered': '結果が届くのは管理者が登録した通知先だけ',
    'Confidential results go only to destinations allowed to receive them':
      '機密の結果は、受け取ってよいとした通知先にだけ届く',
    'Delivery is retried and recorded separately from the run itself': '配信は実行とは別に再試行し、記録する',
    'Channel destinations carry questions, answers and the report in one thread':
      'チャネルの通知先では、質問・回答・報告が1つのスレッドにまとまる',

    // For IT
    'Your identity provider. Your choice of LLM. One place to manage it.':
      '自社の認証基盤で。LLMは選べる。管理はひとつの画面で。',
    'Yoriki runs in an environment you control. Nothing is sent to a vendor you did not configure yourself, and everything an agent did is there to review.':
      'Yorikiは、自社が管理する環境で動きます。自分で設定していない外部のサービスへは何も送られず、エージェントがしたことはすべて後から確かめられます。',
    'Sign-in': 'ログイン',
    'Microsoft 365 or Google Workspace (OIDC). Invited accounts with passwords where there is no SSO.':
      'Microsoft 365・Google Workspace（OIDC）。SSOのない環境では、招待制のパスワードのアカウント。',
    'OpenAI, Anthropic, Gemini, or a local server such as vLLM or Ollama. Keys are encrypted at rest and never reach the agent, the sandbox or the logs.':
      'OpenAI・Anthropic・Gemini、またはvLLMやOllamaなどの社内サーバー。API keyは暗号化して保存し、エージェント、サンドボックス、ログには出しません。',
    'Containers on gVisor with no network. Images published on Docker Hub.':
      'gVisor上の、ネットワークのないコンテナ。イメージはDocker Hubで公開。',
    Files: 'ファイル',
    'Attachments and results on local disk or S3-compatible storage.':
      '添付と結果はローカルディスクか、S3互換のストレージへ。',
    Control: '管理',
    'Users, departments and permissions; usage per user; history of every run. One admin UI.':
      'ユーザー・部門・権限、ユーザーごとの利用量、すべての実行の履歴。ひとつの管理画面で。',

    // Final / footer
    'Give everyone an agent.': '全員にエージェントを。',
    'Keep the boundaries yours.': '境界は会社の手に。',
    'Yoriki is open source under the MIT license. Run it on your own infrastructure, connect your identity provider and your data, and let your company start delegating work.':
      'YorikiはMITライセンスのオープンソースです。自社の環境で動かし、認証基盤とデータをつなぎ、会社として仕事を任せ始めてください。',
    'Get Yoriki on GitHub': 'GitHubでYorikiを入手',
    'Read the docs': 'ドキュメントを読む',
    "Named after the yoriki (与力), the officers of Edo-period Japan who carried out a magistrate's work within the authority they were given.":
      '名前は与力から。江戸時代に奉行の下で、与えられた権限の範囲で実務を担った役人です。',
    Project: 'プロジェクト',
    'MIT License': 'MITライセンス',
    'Released under the MIT License.': 'MITライセンスで公開しています。',
  }

  const dictionaries = { en: null, ja }
  const STORAGE_KEY = 'yoriki-lp-lang'
  const meta = {
    en: {
      title: 'Yoriki — AI agents your whole company can use with confidence',
      description:
        'Yoriki is an open-source, self-hosted platform for using AI agents at work. The company decides what data an agent may touch, where its commands run and where results go, and Yoriki enforces it in software.',
    },
    ja: {
      title: 'Yoriki — AIエージェントを、全社で安心して使うための基盤',
      description:
        'Yorikiは、業務でAIエージェントを使うための、オープンソースのセルフホスト基盤。エージェントが触れるデータ、コマンドが動く場所、結果の届け先を会社が決め、ソフトウェアがそのとおりに守ります。',
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
