# AI-Project-Manager（AIマネージャー）

**余白フォース / yohakuforce** スイートの進行管理層。[Context-Hub](https://pypi.org/project/yohakuforce-context-hub/) が集約したプロジェクト文脈をもとに、受託・社内プロジェクトの進行を5つの能力で回します。

> 社内運用アプリ（private）。Core / Context-Hub は OSS、本リポジトリは社内に閉じます。

---

## 5つの能力

| 能力 | 内容 |
|---|---|
| **Plan（計画）** | 会議メモや Backlog/Redmine の課題からタスクを自動生成し、プロジェクトに取り込む |
| **Assign（割当）** | メンバーの状況をふまえた担当案を作成。PM が確認・承認 |
| **Track（進捗追跡）** | 日報テンプレを生成・配信し、回答を集約・分析。ブロッカーを抽出 |
| **Alert（アラート）** | タスク遅延・メンバー過負荷・日報未回答を検知して通知 |
| **Overview（経営サマリ）** | タスク状態・日報・アラートを集約した日次サマリとフェーズ進捗を生成 |

配信は **Slack / Google Sheets / ローカルファイル** のマルチチャネル（未設定時は安全側にフォールバック）。

## アーキテクチャ

- 自前の **PostgreSQL** にタスク / メンバー / 日報 / アラートを保持
- 文脈（会議・課題）は **Context-Hub の REST API**（camelCase 契約）から取得
- API: `:8001` / 設定GUI: `/settings`（localhost 専用）

```
Context-Hub (:8000, REST)  ──→  AI-Project-Manager (:8001)  ──→  Slack / Sheets / ローカル
        文脈・タスク                  5能力で進行管理                    配信
```

## セットアップ

### 1. 環境変数（`.env`）

```env
DB_PASSWORD=<強いパスワード>
LLM_PROVIDER=claude
CONTEXT_HUB_BASE_URL=http://localhost:8000/api/v1
CONTEXT_HUB_API_KEY=<Context-Hub の DEV_API_KEY と同値>
CONTEXT_HUB_USE_MOCK=false
NOTIFICATION_CHANNEL=local_file   # Slack トークン未取得時の安全策
```

### 2. 起動（Docker / PostgreSQL）

```bash
docker compose up --build
# db(:5433) / app(:8001) / migrate(alembic upgrade head) が起動
```

### 3. ローカル起動（Docker を使わない場合）

```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn src.api.app:app --host 127.0.0.1 --port 8001
```

> AI-PM は PostgreSQL 専用です（SQLite 非対応）。

詳しい社内 Windows PC 向け手順は [`docs/deploy-windows.md`](docs/deploy-windows.md) を参照。

## 会議 → タスク自動生成

Context-Hub に会議メモを登録すると取込時に自動でタスクが抽出されます。AI-PM はそれを取り込みます。

```python
plan.extract_tasks_from_meeting(project_id, meeting_id)
# → Context-Hub の get_meeting を叩き、抽出済みタスクを Project に生成
```

## テスト

```bash
pytest -q   # 251 passed / 88% coverage
```

## スイート全体

Core / Context-Hub / AI Manager の三層がどう繋がるかは、スイートドキュメント（`yohakuforce-suite-docs.html`）を参照してください。
