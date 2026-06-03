# 社内 Windows PC デプロイ手順書（余白フォース AIマネージャー）

対象: 社内 Windows PC 1 台でのパイロット運用（6/1 開始想定）。
構成: **Context-Hub**（顧客データ取込・文脈提供 / port 8000）＋ **AI-Project-Manager**（7能力 / port 8001）。

> データ境界: 顧客機密は Context-Hub の取込先（社内 PC）に閉じる。AI-PM が REST 越しに受け取るのは
> 抽象化済みの構造化データのみ。生データを外部 LLM/SaaS に転記しない方針は両層で維持されている。

---

## 0. 検証ステータス（正直版）

| 項目 | 状態 |
|---|---|
| Context-Hub（SQLite）REST + MCP 読み取り | ✅ macOS でライブ検証済（実 issues/meeting/members が camelCase で返ること） |
| AI-PM 中核5能力ループ（Plan→Assign→Track→Alert→Overview）| ✅ ライブ Context-Hub に対して通し実行済（`scripts/demo_five_capabilities.py`）。残る Standup / WrapUp を加えた全7能力はスケジューラ経由で稼働 |
| AI-PM Postgres + docker compose（Windows）| ⚠️ **未検証** — 実 Windows + Docker Desktop での `compose up` は現地で要確認 |
| Slack 配信 | ⚠️ トークン未取得。`local_file` / `google_sheets` で代替起動可 |

AI-PM の永続化は **Postgres 専用**（SQLite 非対応）。Context-Hub はパイロットでは **SQLite** で十分。

---

## 1. 事前準備（Windows PC）

- WSL2 + Docker Desktop for Windows（AI-PM 用）
- Python 3.12+（Context-Hub をローカルプロセスで動かす場合）
- Git
- Docker Desktop の File sharing で対象ドライブを共有しておく（compose の bind mount 用）

---

## 2. Context-Hub の起動（SQLite / Docker 不要）

Context-Hub は PyPI 公開済み。pipx で入れるのが最短です。

```powershell
pipx install yohakuforce-context-hub          # CLI: context-hub
#   意味（ベクトル）検索を使うなら（BGE-M3, 初回約2.3GB DL）:
#   pipx install "yohakuforce-context-hub[embedding]"

cd <作業用フォルダ>                            # ここに .env と data/ が作られる
context-hub init --profile quickstart         # .env 生成 + DEV_API_KEY を自動発行（画面に表示）
context-hub migrate                           # SQLite スキーマ作成（revision 001）

# ↑ init が表示する「Admin GUI key (DEV_API_KEY)」を控える。
#   AI-PM の CONTEXT_HUB_API_KEY にこの値を設定する（両者一致が必須）。
#   後から確認: PowerShell> Select-String DEV_API_KEY .env

context-hub serve --host 127.0.0.1 --port 8000
#   ※ 必ず `context-hub serve` で起動すること（.env を読み込み DEV_API_KEY を有効化する）。
#   stdio MCP で使う場合: context-hub serve --mcp-only
```

動作確認:
```powershell
curl http://127.0.0.1:8000/health
#   設定/取込は GUI が楽: ブラウザで http://127.0.0.1:8000/admin → 上の DEV_API_KEY を貼る
#   → Sources タブで「プロジェクト作成」、各ソース（Slack/Backlog/Redmine/Gmail）を設定
```

> 実運用では quickstart の代わりに `--profile personal`（永続スケジューラ + ローカル埋め込み）も選択可。
> 本番グレードの取込・Postgres 化は `--profile production`（要 Postgres・別途検証）。
> OSS 版のソースから動かしたい場合は GitHub `yohakuforce/context-hub` を clone して `pip install -e .`。

---

## 3. AI-Project-Manager の起動（Postgres + docker compose）

```powershell
git clone <AI-Project-Manager private repo> ; cd AI-Project-Manager
copy .env.example .env
```

`.env` の最低限の設定:
```
DB_NAME=ai_project_manager
DB_USER=postgres
DB_PASSWORD=<強いパスワード>           # compose が必須要求
LLM_PROVIDER=claude-code               # Claude Code CLI（サブスク範囲・API 課金なし）。既定値
CONTEXT_HUB_BASE_URL=http://host.docker.internal:8000/api/v1   # コンテナ→ホストのCH
CONTEXT_HUB_API_KEY=<Context-Hub の DEV_API_KEY と同値>
CONTEXT_HUB_USE_MOCK=false             # 実 Context-Hub に接続
NOTIFICATION_CHANNEL=local_file        # Slack トークン未取得時の安全策
```

起動:
```powershell
docker compose up --build
#   db(5433) / app(8001) / migrate(alembic upgrade head 一回) が立ち上がる
```

動作確認: `http://localhost:8001/health` / 設定GUI `http://localhost:8001/settings`（localhost専用・auth除外）。

### 3 つの GUI（ブラウザだけで一通り回せる）

**設定 `/settings`** — 接続先・LLMプロバイダ・配信時刻などを全項目編集。各項目に「なぜ必要 / 取得手順」付き。秘匿値はマスク表示。

![AI-PM 設定GUI](images/ai-pm-settings.png)

**登録 `/register`** — プロジェクトとメンバーを作成・一覧・削除。

![AI-PM 登録GUI](images/ai-pm-register.png)

**運用ガイド `/guide`** — ゼロ→日次運用までの手順をブラウザ内で確認。

![AI-PM 運用ガイド](images/ai-pm-guide.png)

---

## 4. 結線確認（手動スモーク）

1. Context-Hub にサンプル投入済みであること（手順 2 の seed）。
2. AI-PM から Context-Hub の issues が取れること（`CONTEXT_HUB_USE_MOCK=false` で 500/接続エラーが出ないこと）。
3. 中核5能力の通し確認は `scripts/demo_five_capabilities.py` を参照（在 macOS 検証スクリプト。Windows でも PYTHONPATH=. で実行可）。Standup / WrapUp を含む全7能力はスケジューラ（`SCHEDULER_ENABLED=true`）で稼働。

---

## 5. 運用開始前に揃えるもの

導入担当者が現地で必要になる外部資格情報のチェックリストです。社内固有の承認・
公開判断など運用判断事項は [`internal/pilot-readiness.md`](internal/pilot-readiness.md)
に分離しています（公開リポにする際はこの章のみ持ち出せば足ります）。

- **Slack ボットトークン**＋通知チャンネル（無ければ `local_file` で開始可）
- **データ源 API キー**（Slack / Backlog / Redmine / Gmail）— 取込実装は完了済、キーのみ
- **GitHub リポジトリ**と push 権限

---

## 会議→タスク自動生成（必須機能）

会議メモを Context-Hub に登録すると、**取込時に1回だけ on-prem LLM がタスクを抽出して永続化**します
（再読込でも結果が変わらない = 取りこぼし防止）。抽出は Context-Hub 側（社内PC・`LLM_PROVIDER`）で
行われ、生トランスクリプトは外部 API に出ません。

```powershell
# 会議メモを登録（source=meeting なら自動でタスク抽出される）
curl -X POST "http://127.0.0.1:8000/api/v1/documents" -H "X-Api-Key: <KEY>" -H "Content-Type: application/json" `
  -d '{"projectId":"proj-001","sourceType":"meeting","externalId":"mtg-1","title":"定例","text":"...議事録..."}'

# AI-PM が会議からタスク生成: plan.extract_tasks_from_meeting(projectId, 上で返った documentId)
```

`LLM_PROVIDER=claude-code`（または `ollama`）で実抽出。`mock` では抽出されない（決定性のCI用）。

## Slack スクレイピング → Context-Hub 投入

Slack は API ではなく**既存の web スクレイピング**で取得し、その結果を push します（Backlog/Redmine は API 取得済み）。

```powershell
curl -X POST "http://127.0.0.1:8000/api/v1/projects/proj-001/ingest/slack" -H "X-Api-Key: <KEY>" -H "Content-Type: application/json" `
  -d '{"messages":[{"ts":"1716800000.001","text":"...","user":"U1","userName":"メンバーA","permalink":"https://..."}]}'
# → {ingested, updated, skipped, documentIds}。ts をキーに冪等 upsert（再投入は updated）。
```

スクレイパ側は `messages[]`（`ts` 必須、`text` 必須）の JSON を組み立てて上記へ POST するだけ。

## 付録: Docker を使わない AI-PM 起動（ローカル Postgres）

Docker を使わない場合は、ローカル Postgres を 5433 で立て、`DATABASE_URL` を実値にして:
```powershell
pip install -r requirements.txt
alembic upgrade head
uvicorn src.api.app:app --host 127.0.0.1 --port 8001
```
（AI-PM は SQLite 非対応のため Postgres は必須。）
