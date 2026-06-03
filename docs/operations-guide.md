# AI-Project-Manager 運用ガイド（ゼロから日次運用まで）

このページだけ読めば「何を・なぜ・どの順で設定し、どう動かすか」が分かることを目指した手順書です。アプリ起動中は `http://127.0.0.1:8001/guide` でも同じ内容を読めます。

---

## 0. 全体像（3分）

AI-Project-Manager（AI-PM）は受託・社内プロジェクトの **進行管理** を担うアプリです。

- **文脈（会議メモ・課題）** は別アプリ **Context-Hub** から取得します（無くてもモックで動きます）。
- **進行データ（タスク・日報・アラート・ゲート）** は AI-PM 自身が保持します（インメモリ or PostgreSQL）。
- **一日の流れ**：09:00 朝会 → 14:00 日報 → 17:00 催促 → 17:30 当日総括＋リーダー確認ゲート →（リーダー確認後）全体ステータス分析。

> まず動かすだけなら **外部トークンは一切不要** です（セクション2）。Slack や Context-Hub 連携は「やりたくなったら」足します。

---

## 1. 用意するもの

- **AI-PM 本体**（このリポジトリ）。Python 仮想環境（`.venv`）。
- **任意**：Context-Hub（実データ連携する場合）／Slack や Google スプレッドシート（通知する場合）／PostgreSQL（本番で永続化する場合）。

---

## 2. まず最小構成で起動する（外部トークン不要）

1. 依存をインストールする。

```
cd ~/Desktop/01_active/AI-Project-Manager
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

2. 起動する（インメモリ・モードなので DB も外部キーも不要）。

```
USE_DATABASE=false CONTEXT_HUB_USE_MOCK=true NOTIFICATION_CHANNEL=local_file \
  .venv/bin/python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8001
```

3. ブラウザで確認する。

- 設定画面：`http://127.0.0.1:8001/settings`
- この運用ガイド：`http://127.0.0.1:8001/guide`
- API ドキュメント：`http://127.0.0.1:8001/docs`

> 設定は `/settings` で編集でき、保存すると `.env` に書き込まれます。以降の環境変数はすべて `/settings` から設定できます。

---

## 3. API を叩くときの認証（重要）

AI-PM の業務エンドポイント（`/api/v1/...`）はすべて **`X-Api-Key` ヘッダー必須** です。値は設定の **`app_secret_key`**（既定 `dev-secret-change-in-production`）です。

```
curl -H "X-Api-Key: dev-secret-change-in-production" http://127.0.0.1:8001/api/v1/...
```

- 本番では `app_secret_key` を必ずランダム値に変えます（`openssl rand -hex 32`）。
- `/health` `/docs` `/settings` `/guide` は認証不要です。

---

## 4. Context-Hub と本物で繋ぐ（`context_hub_api_key` の取得方法）

**なぜ必要**：実際の会議メモ・課題からタスクを自動生成するためです。使わない間は `context_hub_use_mock=true` のままで構いません。

**`context_hub_api_key` はどこかから「貰う」ものではありません。開発時は「自分で好きな値を決めて、両者を一致させる」値です。**

手順：

1. Context-Hub を導入・初期化する。

```
pipx install yohakuforce-context-hub
context-hub init --profile quickstart
context-hub migrate
```

2. **API キーを自分で決める**（例：`dev-seed-key`）。それを環境変数 `DEV_API_KEY` にして Context-Hub を起動する。

```
DEV_API_KEY=dev-seed-key APP_ENV=development \
  uvicorn context_hub.main:create_app --factory --host 127.0.0.1 --port 8000
```

> Context-Hub は開発時（`APP_ENV=development`）、リクエストの `X-Api-Key` がこの `DEV_API_KEY` と一致すれば通します。つまり「決めた値」がそのまま API キーです。

3. AI-PM の `/settings` で次を設定する。

- `context_hub_use_mock` = `false`
- `context_hub_base_url` = `http://localhost:8000/api/v1`
- `context_hub_api_key` = `dev-seed-key`（手順2で決めた値と**同じ**）

4. `/settings` の「**Context-Hub 接続テスト**」ボタンで疎通を確認する。

**本番の場合**：`DEV_API_KEY` は本番（`APP_ENV=production`）では無効です。Context-Hub 側で発行したコンシューマ API キー（ハッシュ管理）を使います。発行方法は Context-Hub の運用ガイドを参照してください。

---

## 5. プロジェクトとメンバーを登録する（GUI）

ブラウザで **`http://127.0.0.1:8001/register`** を開きます（`/settings` 右上の「🗂 プロジェクト/メンバー登録」からも入れます）。

1. **プロジェクトを登録**：名前（必須）・顧客・ゴール・Context-Hub プロジェクトID（連携時のみ）を入力して登録。登録すると一覧に **プロジェクトUUID** が出るので控えます（以降の API の `project_id`）。
2. **メンバーを登録**：external_id（必須・重複不可。Slack のユーザーID＝DM 宛先に使う）・名前・役割を入力して登録。

登録内容はそのまま同じアプリ（スケジューラ・各 API）から利用できます。`USE_DATABASE=true` なら再起動後も残ります（`false` のインメモリはプロセス内のみ）。

> **CLI でまとめて投入したい場合**（任意）は seed スクリプトも使えます：
>
> ```
> USE_DATABASE=true .venv/bin/python scripts/seed_project.py \
>   --name "案件A" --member "user-a:山田" --member "user-b:鈴木" \
>   --context-hub-project proj-001
> ```

---

## 6. タスクを起こす（Plan）

会議メモや課題からタスクを自動生成します（Context-Hub 連携時）。

```
# 会議メモから
curl -X POST http://127.0.0.1:8001/api/v1/plan/extract-from-meeting \
  -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"project_id":"<PROJECT_UUID>","meeting_id":"<会議ID>"}'

# 課題（Backlog/Redmine）から
curl -X POST http://127.0.0.1:8001/api/v1/plan/import-from-issues \
  -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"project_id":"<PROJECT_UUID>","source":"backlog","status_filter":"open"}'
```

---

## 7. 割当（Assign）

AI が担当案（DRAFT）を作り、リーダーが承認／却下します。

```
# 割当案を生成
curl -X POST http://127.0.0.1:8001/api/v1/assign/generate-drafts \
  -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"project_id":"<PROJECT_UUID>"}'

# 承認（却下は /reject）
curl -X POST http://127.0.0.1:8001/api/v1/assign/confirm \
  -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"project_id":"<PROJECT_UUID>","assignment_id":"<ID>","decided_by":"PM"}'
```

---

## 8. 日次運用（自動 ＋ 手動）

`scheduler_enabled=true`（既定）なら、設定時刻に自動で回ります。手動でも各フェーズを叩けます。

| フェーズ | 自動時刻 | 手動エンドポイント |
|---|---|---|
| 朝会（前日レビュー＋アサイン点検＋DRAFT入替案） | 09:00 | `POST /api/v1/pipeline/standup` |
| 日報 生成→配信 | 14:00 | `POST /api/v1/track/generate-templates` → `/track/deliver` |
| 日報の回答→分析 | 随時 | `POST /api/v1/track/submit-responses` → `/track/analyze` |
| 未提出の催促 | 17:00 | （自動。本人DM＋リーダー一覧） |
| 当日総括＋確認ゲート起票 | 17:30 | `POST /api/v1/pipeline/wrap-up` |
| 全体ステータス分析＋未割当DRAFTアサイン | リーダー確認後 | `POST /api/v1/pipeline/final-analysis` |

**リーダー確認ゲートの解決**（17:30 以降）：

```
# 未解決ゲートを一覧
curl -H "X-Api-Key: $KEY" http://127.0.0.1:8001/api/v1/pipeline/<PROJECT_UUID>/gates

# 解決（proceed で後続が発火 / skip で見送り）
curl -X POST http://127.0.0.1:8001/api/v1/pipeline/<PROJECT_UUID>/gates/<GATE_ID>/resolve \
  -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"decision":"proceed","resolved_by":"leader-1"}'
```

- `wrap_up_decision` を `proceed` → 総括を生成し `task_state_current` ゲートを起票。
- `task_state_current` を `proceed` → 全体ステータス分析と未割当 DRAFT アサインが発火。
- ゲートは PostgreSQL に永続化されるので、**翌日でも解決できます**（`use_database=true` のとき）。

---

## 9. 通知連携（任意）

- **Slack**：`/settings` の `slack_bot_token` の「なぜ必要？ 取得・設定の手順」を開くと、アプリ作成 → スコープ → トークン取得 → チャンネル招待までの手順があります。`notification_channel=slack` にして設定します。
- **Google スプレッドシート**：同様に `google_service_account_json` の手順（サービスアカウント鍵 → シート共有）を参照。`notification_channel=google_sheets`。
- 何も設定しなければ `local_file`（`./.ai-pm/notifications`）に書き出されるので、トークン無しでも動作確認できます。

---

## 10. 本番化のチェックリスト

- `app_env=production` / `log_level=INFO`
- `app_secret_key` と `jwt_secret` をランダム値に（`openssl rand -hex 32`）
- `use_database=true` ＋ `database_url`（compose の DB 設定と一致）→ `alembic upgrade head`
- `scheduler_timezone=Asia/Tokyo`
- 通知チャンネルを実値に（Slack 等）、Context-Hub は本番キーに

> Docker compose を使う場合：`docker compose up --build` で db(:5433) / app(:8001) / migrate が起動します。

---

## 11. トラブルシュート

- **401 / Invalid or missing X-Api-Key**：業務 API は `X-Api-Key: <app_secret_key>` が必要。
- **Context-Hub 接続テストが失敗**：Context-Hub が起動しているか、`base_url`（末尾 `/api/v1`）と `context_hub_api_key`（＝Context-Hub の `DEV_API_KEY`）が一致しているか確認。
- **通知が飛ばない**：`/settings` の「通知チャンネル テスト」で healthcheck。Slack はトークン＋Botのチャンネル招待が必要。
- **再起動したら未解決ゲートが消えた**：`use_database=false` だとインメモリ。`true` にして永続化。消えた場合は `POST /api/v1/pipeline/wrap-up` や `/final-analysis` で手動再実行。
- **スケジューラが動かない**：`scheduler_enabled=true` と `scheduler_timezone` を確認。順序は固定で変えられません。

---

## 12. 用語

- **リーダー確認ゲート**：時刻では進めない「人の判断待ち」。`wrap_up_decision`（未提出でも総括するか）と `task_state_current`（タスク状態は最新か）の2種。
- **final_analysis**：ゲート確認後に走る全体ステータス分析＋未割当 DRAFT アサイン。
- **DRAFT アサイン**：AI が作る割当の下書き。リーダーが承認して確定（AIは提案・人が承認）。
- **CANONICAL_STEP_ORDER**：日次フェーズの固定実行順。時刻を変えても順序は入れ替わりません。
