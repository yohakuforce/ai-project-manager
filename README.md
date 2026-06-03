# AI-Project-Manager（AIマネージャー）

**余白フォース / yohakuforce** スイートの進行管理層。[Context-Hub](https://pypi.org/project/yohakuforce-context-hub/) が集約したプロジェクト文脈をもとに、受託・社内プロジェクトの進行を 7 つの能力で回します。

> 社内運用アプリ（private）。Core / Context-Hub は OSS、本リポジトリは社内に閉じます。

---

## 7つの能力

| 能力 | 内容 |
|---|---|
| **Plan（計画）** | 会議メモや Backlog/Redmine の課題からタスクを自動生成し、プロジェクトに取り込む |
| **Assign（割当）** | メンバーの状況をふまえた担当案を作成。PM が確認・承認 |
| **Track（進捗追跡）** | 日報テンプレを生成・配信し、回答を集約・分析。ブロッカーを抽出。未提出者へ催促 |
| **Alert（アラート）** | タスク遅延・メンバー過負荷・日報未回答を検知して通知 |
| **Overview（経営サマリ）** | タスク状態・日報・アラートを集約した日次サマリとフェーズ進捗を生成 |
| **Standup（スタンドアップ）** | 前日の日報・出来事・アサインをレビューし、問題には DRAFT 入替案を添えてリーダーへ共有 |
| **WrapUp / Status（総括・確認ゲート）** | 当日総括とリーダー確認ゲート、確認後の全体ステータス分析＋未割当 DRAFT アサイン |

配信は **Slack / Google Sheets / ローカルファイル** のマルチチャネル（未設定時は安全側にフォールバック）。

## アーキテクチャ

- 自前の **PostgreSQL** にタスク / メンバー / 日報 / アラートを保持
- 文脈（会議・課題）は **Context-Hub の REST API**（camelCase 契約）から取得
- API: `:8001` / 設定GUI: `/settings`（localhost 専用）

```
Context-Hub (:8000, REST)  ──→  AI-Project-Manager (:8001)  ──→  Slack / Sheets / ローカル
        文脈・タスク                  7能力で進行管理                    配信
```

## 自動スケジューラ（時刻駆動）

内蔵スケジューラ（APScheduler）が設定時刻に日次パイプラインを自動実行します。アプリ起動時に立ち上がり、`scheduler_enabled=false` で無効化できます。

| フェーズ | 既定 | 設定キー（時・分） |
|---|---|---|
| スタンドアップ | 09:00 | `STANDUP_HOUR` / `STANDUP_MINUTE` |
| 日報生成 → 配信 | 14:00 | `REPORT_HOUR` / `REPORT_MINUTE` |
| 日報未提出の催促 | 17:00 | `REMINDER_HOUR` / `REMINDER_MINUTE` |
| 当日総括＋確認ゲート | 17:30 | `WRAP_UP_HOUR` / `WRAP_UP_MINUTE` |
| アラートスキャン | 30分間隔 | `ALERT_SCAN_INTERVAL_MINUTES` |
| 全体ステータス分析（final_analysis） | リーダー確認後（時刻駆動でない） | — |

時刻（時・分）は `/settings` GUI で自由に調整できます。**実行順序は時刻設定に関わらず常に固定**です（順序の単一の真実は `src/application/scheduler/schedule_plan.py` の `CANONICAL_STEP_ORDER`）。

### 一日の流れ

1. **09:00 スタンドアップ** — 前日の日報・出来事（Context-Hub の課題更新）・昨日のアサインをレビュー。過負荷・期日超過・ブロック等の問題タスクには別メンバーへの **DRAFT 入替案**を作り、スタンドアップとしてリーダーへ共有。
2. **14:00 日報生成 → 配信** — 当日のタスク状態から日報テンプレを生成し配信。
3. **17:00 催促** — 未提出メンバー本人へ DM、リーダーへ未提出者一覧。
4. **17:30 当日総括＋確認ゲート** —
   - 全員提出済みなら総括を生成し、注目タスクを添えてリーダーへ共有。同時に **「タスク状態は最新か」確認ゲート**を起票。
   - 未提出が残るなら **「未提出があるが総括するか」判断ゲート**を起票し、リーダーへ打診。
5. **リーダー確認後（final_analysis）** — リーダーが確認ゲートを `proceed` で解決すると、タスク状態・日報提出・アクティブアラートを集約した**全体ステータスレポート**を生成し、未割当タスクへ **DRAFT アサイン**を作成してリーダーへ報告。

### リーダー確認ゲート（人間判断のトリガー）

時刻では進められない「人間の確認・判断」を待つ仕組み。リーダーは API / GUI で解決する。

- `GET /api/v1/pipeline/{project_id}/gates` — PENDING ゲート一覧
- `POST /api/v1/pipeline/{project_id}/gates/{gate_id}/resolve` — `{"decision": "proceed" | "skip", "resolved_by": "..."}`
  - `WRAP_UP_DECISION` を `proceed` → 総括を生成し `TASK_STATE_CURRENT` ゲートを起票
  - `TASK_STATE_CURRENT` を `proceed` → final_analysis（全体ステータス＋未割当 DRAFT アサイン）を発火
- 手動再実行: `POST /api/v1/pipeline/standup` `/wrap-up` `/final-analysis`

> **永続化**: ゲートは `USE_DATABASE=true` で **PostgreSQL に永続化**されます（テーブル `leader_gates`、マイグレーション `0002`）。確認が翌日になってもプロセス再起動を跨いで PENDING ゲートが保持され、リーダーはいつでも解決できます。`USE_DATABASE=false`（dev / テスト）ではインメモリ保持です。

ユーザー操作でバグらないための制御:

- **順序固定**: 調整できるのは各フェーズの時刻（時・分）だけ。`standup → report_generate → report_deliver → report_reminder → wrap_up → alert_scan` の順は `order_steps()` が常に正準順へ整列するため入れ替わらない。
- **同時刻の安全化**: 複数フェーズが同じ時刻なら 1 ジョブにまとめ、レースを避けて正しい順に逐次実行。
- **多重起動の抑止**: `max_instances=1` + `coalesce=True` により、スキャン間隔を極端に短くしても滞留・多重起動しない。
- **不正値の自動丸め**: 範囲外の時刻/分/間隔は `clamp_schedule()` が既定値へ丸め、スケジューラは停止しない。
- **障害分離**: 1 プロジェクト / 1 ステップの失敗は記録のみで、他プロジェクトの進行を止めない。

> スケジューラの起動失敗は API 本体を巻き込みません（ログのみ・API は継続）。

## セットアップ

### 1. 環境変数（`.env`）

```env
DB_PASSWORD=<強いパスワード>
LLM_PROVIDER=claude-code   # Claude Code CLI（サブスク範囲・API 課金なし）。既定値
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
pytest -q   # 315 passed / 89% coverage
```

## スイート全体

Core / Context-Hub / AI Manager の三層がどう繋がるかは、公式ヘルプ（[yohakuforce.github.io/docs](https://yohakuforce.github.io/docs/)）を参照してください。AI マネージャーの実行モデル（一日の運用タイムライン・日報ライフサイクル・出力先別の挙動）も同サイトに記載があります。
