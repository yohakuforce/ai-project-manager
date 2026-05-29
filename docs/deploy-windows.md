# 社内 Windows PC デプロイ手順書（余白フォース AIマネージャー）

対象: 社内 Windows PC 1 台でのパイロット運用（6/1 開始想定）。
構成: **Context-Hub**（顧客データ取込・文脈提供 / port 8000）＋ **AI-Project-Manager**（5能力 / port 8001）。

> データ境界: 顧客機密は Context-Hub の取込先（社内 PC）に閉じる。AI-PM が REST 越しに受け取るのは
> 抽象化済みの構造化データのみ。生データを外部 LLM/SaaS に転記しない方針は両層で維持されている。

---

## 0. 検証ステータス（正直版）

| 項目 | 状態 |
|---|---|
| Context-Hub（SQLite）REST + MCP 読み取り | ✅ macOS でライブ検証済（実 issues/meeting/members が camelCase で返ること） |
| AI-PM 5能力ループ（Plan→Assign→Track→Alert→Overview）| ✅ ライブ Context-Hub に対して通し実行済（`scripts/demo_five_capabilities.py`）|
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

```powershell
git clone <Context-Hub private repo> ; cd Context-Hub
py -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -e .

copy .env.example .env   # 無ければ下記を .env に記載
#   CH_PROFILE=quickstart
#   APP_ENV=development
#   DEV_API_KEY=<社内用ランダムキー>      ← AI-PM の CONTEXT_HUB_API_KEY と一致させる
#   CH_SQLITE_DB=./data/context_hub.db

context-hub migrate                    # SQLite スキーマ作成（revision 001）
python scripts/seed_sample.py          # （任意）動作確認用のマスク済みサンプル投入

uvicorn context_hub.main:create_app --factory --host 127.0.0.1 --port 8000
#   stdio MCP で使う場合: python -m context_hub.mcp.server
```

動作確認:
```powershell
curl http://127.0.0.1:8000/health
curl -H "X-Api-Key: <DEV_API_KEY>" "http://127.0.0.1:8000/api/v1/projects/proj-001/issues?source=backlog"
```

> 実運用では quickstart の代わりに `CH_PROFILE=personal`（永続スケジューラ）も選択可。
> 本番グレードの取込・Postgres 化は `CH_PROFILE=production`（要 Postgres・別途検証）。

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
LLM_PROVIDER=claude
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

---

## 4. 結線確認（手動スモーク）

1. Context-Hub にサンプル投入済みであること（手順 2 の seed）。
2. AI-PM から Context-Hub の issues が取れること（`CONTEXT_HUB_USE_MOCK=false` で 500/接続エラーが出ないこと）。
3. 5能力の通し確認は `scripts/demo_five_capabilities.py` を参照（在 macOS 検証スクリプト。Windows でも PYTHONPATH=. で実行可）。

---

## 5. koya 依存（運用開始前に揃えるもの）

- **Slack ボットトークン**＋通知チャンネル（無ければ `local_file` で開始可）
- **データ源 API キー**（Slack / Backlog / Redmine / Gmail）— 取込実装は完了済、キーのみ
- **GitHub private（yohakuforce）** リポジトリと push 権限
- **情報システム / 法務のパイロット承認**
- **公開判断**: Context-Hub camelCase は v0.3.0 破壊的変更（PyPI 公開済のため要判断）/ core npm 再公開可否

---

## 付録: Docker を使わない AI-PM 起動（ローカル Postgres）

Docker を使わない場合は、ローカル Postgres を 5433 で立て、`DATABASE_URL` を実値にして:
```powershell
pip install -r requirements.txt
alembic upgrade head
uvicorn src.api.app:app --host 127.0.0.1 --port 8001
```
（AI-PM は SQLite 非対応のため Postgres は必須。）
