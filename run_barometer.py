"""The Barometer — one tick, with every network tap explicitly enabled."""
import argparse
import os
from pathlib import Path
import time

from barometer.adapters import HNAdapter, RedditAdapter, XAdapter
from barometer.canary import CanaryRunner
from barometer.cli import tick
from barometer.public import write_run_status
from barometer.store import Store
from barometer.submissions import SubmissionStore


def openai_canary(text):
    """Return output-token logprobs. The SDK reads OPENAI_API_KEY from env."""
    from openai import OpenAI

    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-5.5-2026-04-23",
        messages=[{"role": "user", "content": f"Repeat exactly: {text}"}],
        logprobs=True,
        top_logprobs=1,
        max_completion_tokens=len(text.split()) + 20,
        reasoning_effort="none",
    )
    logprobs = [token.logprob for token in response.choices[0].logprobs.content]
    fingerprint = getattr(response, "system_fingerprint", None)
    return logprobs, fingerprint


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--observe-hn",
        action="store_true",
        help=("run the isolated HN trial: separate database, aggregate public "
              "snapshot, 30-day private raw retention"),
    )
    parser.add_argument(
        "--observe-x",
        action="store_true",
        help=("add the capped X trial to the isolated observation profile; "
              "may be combined with --observe-hn"),
    )
    parser.add_argument(
        "--observe-reddit",
        action="store_true",
        help=("add authenticated, zero-persistence Reddit observation; "
              "may be combined with other observation sources"),
    )
    parser.add_argument(
        "--hn",
        action="store_true",
        help="enable the Hacker News tap",
    )
    parser.add_argument(
        "--reddit",
        action="store_true",
        help=("enable authenticated ephemeral Reddit ingestion (uses "
              "REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT)"),
    )
    parser.add_argument(
        "--x",
        action="store_true",
        help="enable capped X recent-search sampling (uses X_BEARER_TOKEN)",
    )
    parser.add_argument(
        "--x-daily-read-limit",
        type=int,
        default=60,
        help="hard UTC-day X read allowance (default: 60; minimum: 10)",
    )
    parser.add_argument(
        "--openai-canary",
        action="store_true",
        help="enable the once-daily OpenAI canary (uses OPENAI_API_KEY)",
    )
    parser.add_argument("--db", help="SQLite path (profile default if omitted)")
    parser.add_argument("--out-dir", help="dashboard directory")
    parser.add_argument("--public-snapshot", help="aggregate-only JSON path")
    parser.add_argument("--public-history", help="aggregate daily history path")
    parser.add_argument("--status-file", help="last-run health JSON path")
    parser.add_argument(
        "--submission-db",
        help=("moderated user-report database; approved structured reports "
              "are included without their free-text descriptions"),
    )
    parser.add_argument("--retention-days", type=int,
                        help="delete private raw reports older than this")
    args = parser.parse_args(argv)
    if args.observe_hn:
        args.hn = True
    if args.observe_x:
        args.x = True
    if args.observe_reddit:
        args.reddit = True
    observation_profile = (
        args.observe_hn or args.observe_x or args.observe_reddit)
    if observation_profile and args.openai_canary:
        parser.error("observation profiles cannot use a canary")
    if args.x_daily_read_limit < 10:
        parser.error("--x-daily-read-limit must be at least 10")
    if args.x and not os.environ.get("X_BEARER_TOKEN"):
        parser.error("--x requires X_BEARER_TOKEN in the process environment")
    if args.reddit:
        required = (
            "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            parser.error(
                "--reddit requires these process environment variables: "
                + ", ".join(missing))
    return args


def main(argv=None) -> None:
    args = parse_args(argv)
    observation_profile = (
        args.observe_hn or args.observe_x or args.observe_reddit)

    db_path = args.db or (
        "observation/private/barometer.db" if observation_profile
        else "barometer.db"
    )
    out_dir = args.out_dir or (
        "observation/public" if observation_profile else "."
    )
    public_snapshot = args.public_snapshot
    if observation_profile and public_snapshot is None:
        public_snapshot = str(Path(out_dir) / "summary.json")
    public_history = args.public_history
    if observation_profile and public_history is None:
        public_history = str(Path(out_dir) / "history.json")
    status_file = args.status_file
    if observation_profile and status_file is None:
        status_file = str(Path(out_dir) / "status.json")
    retention_days = args.retention_days
    if observation_profile and retention_days is None:
        retention_days = 30
    submission_db = args.submission_db
    if submission_db is None:
        default_submission_db = Path("observation/private/user_reports.db")
        if observation_profile or default_submission_db.exists():
            submission_db = str(default_submission_db)

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    try:
        with Store(db_path) as store:
            adapters = []
            if args.hn:
                adapters.append(HNAdapter())
            if args.reddit:
                adapters.append(RedditAdapter(
                    ["ClaudeAI", "OpenAI", "ChatGPT", "GeminiAI", "Bard"],
                    client_id=os.environ["REDDIT_CLIENT_ID"],
                    client_secret=os.environ["REDDIT_CLIENT_SECRET"],
                    user_agent=os.environ["REDDIT_USER_AGENT"],
                ))
            if args.x:
                adapters.append(XAdapter(
                    store,
                    bearer_token=os.environ["X_BEARER_TOKEN"],
                    daily_read_limit=args.x_daily_read_limit,
                ))
            runner = None
            if args.openai_canary:
                runner = CanaryRunner(store, providers={"gpt": openai_canary})
            # Offline mode is an archive render, so do not discard retained reports
            # merely because the live detector's rolling window has moved on.
            window_days = 21 if adapters or runner else 36500
            approved_user_reports = []
            if submission_db is not None:
                Path(submission_db).parent.mkdir(parents=True, exist_ok=True)
                with SubmissionStore(submission_db) as submission_store:
                    approved_user_reports = submission_store.approved_complaints(
                        since=time.time() - window_days * 86400,
                    )
            report = tick(
                store,
                adapters,
                runner,
                out_dir=out_dir,
                window_days=window_days,
                retention_days=retention_days,
                public_snapshot=public_snapshot,
                public_history=public_history,
                approved_user_reports=approved_user_reports,
            )
    except Exception as exc:
        if status_file is not None:
            write_run_status(
                status_file,
                "error",
                started_at,
                time.time(),
                error=f"{type(exc).__name__}: {exc}",
            )
        raise
    if status_file is not None:
        run_status = (
            "degraded"
            if report.get("tap_errors") or report.get("canary_errors")
            else "ok"
        )
        write_run_status(
            status_file,
            run_status,
            started_at,
            time.time(),
            report=report,
        )
    print(report)


if __name__ == "__main__":
    main()
