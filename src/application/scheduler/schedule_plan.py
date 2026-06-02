"""
日次パイプラインの時刻計画とステップ順序の単一の真実（pure / 副作用なし）。

設計の要点（「順番がおかしくならない」ことの保証）:
  - ユーザーが調整できるのは **各フェーズの時刻（時・分）と間隔だけ**。ステップの実行順序は
    CANONICAL_STEP_ORDER で固定され、ユーザー操作では決して入れ替わらない。
  - build_plan() は時刻設定からジョブ計画を生成するが、各ジョブ内のステップは
    必ず order_steps() で正準順に整列される。複数フェーズが同一時刻になった場合も、
    レース（競合）を避けて1ジョブにまとめ、正準順で逐次実行する。
  - validate_schedule() は範囲外・型不正をエラーに、運用上の注意を warning に分離。
  - clamp_schedule() は万一不正値が settings に残っていてもスケジューラが落ちない
    ように防御的に既定値へ丸める。

日次タイムライン（既定）:
  09:00 standup           — 前日レビュー＋アサイン妥当性確認＋スタンドアップ共有
  14:00 report_generate   — 日報テンプレ生成
        report_deliver    — 日報配信（生成の直後に限る）
  17:00 report_reminder   — 日報未提出者への催促
  17:30 wrap_up           — 当日総括＋リーダー確認ゲート起票
  （イベント駆動）final_analysis — リーダー確認後に発火（cron には載せない）
  30分間隔 alert_scan      — アラートスキャン（背景稼働）
"""

from __future__ import annotations

from dataclasses import dataclass

# --- 正準ステップ順序（不変） ------------------------------------------------
# 日次パイプラインの論理順序。インデックスが小さいほど先に実行される。
# final_analysis はリーダー確認ゲート駆動のため、ここ（cron 計画）には含めない。
CANONICAL_STEP_ORDER: tuple[str, ...] = (
    "standup",  # スタンドアップ（前日レビュー＋アサイン確認）
    "report_generate",  # 日報テンプレ生成
    "report_deliver",  # 日報配信（生成の直後に限る）
    "report_reminder",  # 日報未提出者への催促
    "wrap_up",  # 当日総括＋リーダー確認ゲート起票
    "alert_scan",  # アラートスキャン
)
_STEP_INDEX: dict[str, int] = {step: i for i, step in enumerate(CANONICAL_STEP_ORDER)}

# --- 既定値（こちらで設定。GUI で調整可） ------------------------------------
DEFAULT_STANDUP_HOUR, DEFAULT_STANDUP_MINUTE = 9, 0
DEFAULT_REPORT_HOUR, DEFAULT_REPORT_MINUTE = 14, 0
DEFAULT_REMINDER_HOUR, DEFAULT_REMINDER_MINUTE = 17, 0
DEFAULT_WRAP_UP_HOUR, DEFAULT_WRAP_UP_MINUTE = 17, 30
DEFAULT_SCAN_INTERVAL_MINUTES = 30

# --- 範囲 -------------------------------------------------------------------
HOUR_MIN, HOUR_MAX = 0, 23
MINUTE_MIN, MINUTE_MAX = 0, 59
INTERVAL_MIN, INTERVAL_MAX = 1, 1440


@dataclass(frozen=True)
class ScheduleConfig:
    """ユーザー調整可能なスケジュール設定（各フェーズの時・分＋スキャン間隔）。"""

    standup_hour: int = DEFAULT_STANDUP_HOUR
    standup_minute: int = DEFAULT_STANDUP_MINUTE
    report_hour: int = DEFAULT_REPORT_HOUR
    report_minute: int = DEFAULT_REPORT_MINUTE
    reminder_hour: int = DEFAULT_REMINDER_HOUR
    reminder_minute: int = DEFAULT_REMINDER_MINUTE
    wrap_up_hour: int = DEFAULT_WRAP_UP_HOUR
    wrap_up_minute: int = DEFAULT_WRAP_UP_MINUTE
    scan_interval_minutes: int = DEFAULT_SCAN_INTERVAL_MINUTES


@dataclass(frozen=True)
class ScheduleValidation:
    """検証結果。errors があれば保存をブロック、warnings は注意喚起のみ。"""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class ScheduledJob:
    """スケジューラに登録する1ジョブ。steps は必ず正準順に整列済み。"""

    job_id: str
    trigger: str  # "cron" | "interval"
    steps: tuple[str, ...]
    hour: int | None = None
    minute: int | None = None
    interval_minutes: int | None = None


# --- cron フェーズ定義 -------------------------------------------------------
# (フェーズキー, そのフェーズが内包するステップ列, hour 属性名, minute 属性名)
# 同一時刻のフェーズは build_plan で 1 ジョブに統合される。
_CRON_PHASES: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("standup", ("standup",), "standup_hour", "standup_minute"),
    ("report", ("report_generate", "report_deliver"), "report_hour", "report_minute"),
    ("reminder", ("report_reminder",), "reminder_hour", "reminder_minute"),
    ("wrap-up", ("wrap_up",), "wrap_up_hour", "wrap_up_minute"),
)


def order_steps(steps: tuple[str, ...]) -> tuple[str, ...]:
    """与えられたステップ群を正準順に整列する（未知ステップは末尾・安定）。"""
    return tuple(sorted(steps, key=lambda s: _STEP_INDEX.get(s, len(CANONICAL_STEP_ORDER))))


def _is_valid_hour(value: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and HOUR_MIN <= value <= HOUR_MAX


def _is_valid_minute(value: int) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and MINUTE_MIN <= value <= MINUTE_MAX
    )


def _is_valid_interval(value: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and INTERVAL_MIN <= value <= INTERVAL_MAX
    )


# フェーズキー → 日本語ラベル（検証メッセージ用）
_PHASE_LABELS: dict[str, str] = {
    "standup": "スタンドアップ",
    "report": "日報生成・配信",
    "reminder": "日報催促",
    "wrap-up": "当日総括",
}


def validate_schedule(config: ScheduleConfig) -> ScheduleValidation:
    """スケジュール設定を検証する。

    errors: 範囲外・型不正（保存をブロックすべき致命的問題）
    warnings: 動作はするが運用上知っておくべき点
    """
    errors: list[str] = []
    warnings: list[str] = []

    for key, _steps, hour_attr, minute_attr in _CRON_PHASES:
        label = _PHASE_LABELS.get(key, key)
        hour = getattr(config, hour_attr)
        minute = getattr(config, minute_attr)
        if not _is_valid_hour(hour):
            errors.append(
                f"{label}の時刻（時）は {HOUR_MIN}〜{HOUR_MAX} の整数で指定してください（現在: {hour!r}）。"
            )
        if not _is_valid_minute(minute):
            errors.append(
                f"{label}の時刻（分）は {MINUTE_MIN}〜{MINUTE_MAX} の整数で指定してください（現在: {minute!r}）。"
            )

    if not _is_valid_interval(config.scan_interval_minutes):
        errors.append(
            f"アラートスキャン間隔 (scan_interval_minutes) は "
            f"{INTERVAL_MIN}〜{INTERVAL_MAX} 分の整数で指定してください"
            f"（現在: {config.scan_interval_minutes!r}）。"
        )

    if not errors:
        # 同一時刻に複数フェーズが重なる場合は 1 ジョブに統合される旨を注意喚起。
        collisions = _collision_labels(config)
        for labels in collisions:
            warnings.append(
                f"{' と '.join(labels)} が同じ時刻です。順序は正準順（"
                "standup → report_generate → report_deliver → report_reminder → wrap_up）"
                "に 1 ジョブとしてまとめて自動実行され、入れ替わりは起きません。"
            )

    return ScheduleValidation(errors=tuple(errors), warnings=tuple(warnings))


def _collision_labels(config: ScheduleConfig) -> list[list[str]]:
    """同一(hour, minute)に重なるフェーズのラベル群を返す（重なりのみ）。"""
    by_time: dict[tuple[int, int], list[str]] = {}
    for key, _steps, hour_attr, minute_attr in _CRON_PHASES:
        slot = (getattr(config, hour_attr), getattr(config, minute_attr))
        by_time.setdefault(slot, []).append(_PHASE_LABELS.get(key, key))
    return [labels for labels in by_time.values() if len(labels) > 1]


def clamp_schedule(config: ScheduleConfig) -> ScheduleConfig:
    """不正値を既定値へ防御的に丸める（スケジューラが絶対に落ちないため）。"""
    return ScheduleConfig(
        standup_hour=config.standup_hour
        if _is_valid_hour(config.standup_hour)
        else DEFAULT_STANDUP_HOUR,
        standup_minute=(
            config.standup_minute
            if _is_valid_minute(config.standup_minute)
            else DEFAULT_STANDUP_MINUTE
        ),
        report_hour=config.report_hour
        if _is_valid_hour(config.report_hour)
        else DEFAULT_REPORT_HOUR,
        report_minute=(
            config.report_minute
            if _is_valid_minute(config.report_minute)
            else DEFAULT_REPORT_MINUTE
        ),
        reminder_hour=(
            config.reminder_hour if _is_valid_hour(config.reminder_hour) else DEFAULT_REMINDER_HOUR
        ),
        reminder_minute=(
            config.reminder_minute
            if _is_valid_minute(config.reminder_minute)
            else DEFAULT_REMINDER_MINUTE
        ),
        wrap_up_hour=(
            config.wrap_up_hour if _is_valid_hour(config.wrap_up_hour) else DEFAULT_WRAP_UP_HOUR
        ),
        wrap_up_minute=(
            config.wrap_up_minute
            if _is_valid_minute(config.wrap_up_minute)
            else DEFAULT_WRAP_UP_MINUTE
        ),
        scan_interval_minutes=(
            config.scan_interval_minutes
            if _is_valid_interval(config.scan_interval_minutes)
            else DEFAULT_SCAN_INTERVAL_MINUTES
        ),
    )


def build_plan(config: ScheduleConfig) -> tuple[ScheduledJob, ...]:
    """時刻設定からジョブ計画を生成する。

    - 各 cron フェーズ（standup / report / reminder / wrap-up）を時刻に割り付ける。
    - 同一時刻(hour, minute)のフェーズはレースを避けるため 1 つの cron ジョブにまとめ、
      正準順で実行する。
    - alert_scan は常に interval ジョブ。
    各ジョブの steps は order_steps() で正準順に整列されるため、ユーザーが
    どんな時刻を設定しても各ステップの順序は保証される。
    """
    cfg = clamp_schedule(config)
    jobs: list[ScheduledJob] = []

    # (hour, minute) → そのスロットに集まるフェーズ（定義順を保つ）
    slots: dict[tuple[int, int], list[tuple[str, tuple[str, ...]]]] = {}
    for key, steps, hour_attr, minute_attr in _CRON_PHASES:
        slot = (getattr(cfg, hour_attr), getattr(cfg, minute_attr))
        slots.setdefault(slot, []).append((key, steps))

    # スロットを時刻順（hour, minute 昇順）に登録して可読性を担保する。
    for (hour, minute), phases in sorted(slots.items()):
        all_steps: tuple[str, ...] = tuple(s for _key, steps in phases for s in steps)
        job_id = "daily-" + "-".join(key for key, _steps in phases)
        jobs.append(
            ScheduledJob(
                job_id=job_id,
                trigger="cron",
                steps=order_steps(all_steps),
                hour=hour,
                minute=minute,
            )
        )

    jobs.append(
        ScheduledJob(
            job_id="alert-scan",
            trigger="interval",
            steps=order_steps(("alert_scan",)),
            interval_minutes=cfg.scan_interval_minutes,
        )
    )

    return tuple(jobs)
