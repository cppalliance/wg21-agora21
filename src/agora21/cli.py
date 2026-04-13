"""CLI: ``eval`` (one paper) and ``run`` (mailing batch) with optional ``validate``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from agora21.fetcher import fetch_papers_for_mailing
from agora21.loop import (
    author_reviewer_loop,
    generate_minimal_valid_thread,
    thread_json_path,
    write_thread_json,
)
from agora21.parallel_generate import papers_from_json_arg, run_parallel_eval
from agora21.credentials import load_repo_dotenv
from agora21.paths import repo_root
from agora21.revision import producer_git_sha
from agora21.validate import validate_thread_file, validation_error_message

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

INDEX_SCHEMA_VERSION = "agora21-index-v1"


def _default_output_dir(root: Path) -> Path:
    return root / "data"


def _mailing_year(mailing_id: str) -> str:
    return mailing_id.strip().split("-")[0]


def paper_ids_for_mailing(mailing_id: str) -> list[str]:
    papers = fetch_papers_for_mailing(_mailing_year(mailing_id), mailing_id)
    return [str(p["paper_id"]).lower() for p in papers]


def _eval_one(
    paper_ref: str,
    output_dir: Path,
    mailing_id: str,
    dry_run: bool,
) -> dict:
    root = repo_root()
    try:
        if dry_run:
            data = generate_minimal_valid_thread(paper_ref, mailing_id, root=root)
            write_thread_json(paper_ref, data, output_dir)
        else:
            data = author_reviewer_loop(
                paper_ref,
                mailing_id,
                root=root,
                output_base=output_dir,
            )
            write_thread_json(paper_ref, data, output_dir)
        return {"paper": paper_ref, "status": "ok"}
    except Exception as e:
        traceback.print_exc()
        return {"paper": paper_ref, "status": "error", "error": str(e)}


def cmd_eval(ns: argparse.Namespace) -> int:
    root = repo_root()
    output_dir = Path(ns.output_dir) if ns.output_dir else _default_output_dir(root)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paper_id = ns.paper.strip()
    mailing_id = ns.mailing_id
    if not mailing_id:
        logger.error("eval: --mailing-id is required")
        return 2
    r = _eval_one(paper_id, output_dir, mailing_id, ns.dry_run)
    if r["status"] != "ok":
        print(r.get("error", "failed"), file=sys.stderr)
        return 1
    print(thread_json_path(output_dir, paper_id))
    return 0


def _build_index(
    output_dir: Path,
    mailing_id: str,
    results: list[dict],
    *,
    root: Path,
) -> dict:
    succeeded = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] == "error"]
    index: dict = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "producer_git_sha": producer_git_sha(root=root),
        "mailing_id": mailing_id,
        "generated": datetime.now(timezone.utc).isoformat(),
        "total_papers": len(results),
        "succeeded": len(succeeded),
        "failed": len(failed),
    }
    if failed:
        index["failed_papers"] = [{"paper": r["paper"], "error": r.get("error", "")} for r in failed]
    return index


def cmd_run(ns: argparse.Namespace) -> int:
    root = repo_root()
    output_dir = Path(ns.output_dir) if ns.output_dir else _default_output_dir(root)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    mailing_id = ns.mailing_id.strip()
    max_cap = ns.max_cap
    max_processes = ns.max_processes

    if ns.papers_json is not None:
        try:
            paper_ids = [p.strip().lower() for p in papers_from_json_arg(ns.papers_json) if p and str(p).strip()]
        except (ValueError, json.JSONDecodeError) as e:
            logger.error("run: %s", e)
            return 2
        if not paper_ids:
            logger.error("run: --papers-json resolved to an empty list")
            return 1
        logger.info("Using explicit paper list (%d papers) for mailing %s", len(paper_ids), mailing_id)
    else:
        logger.info("Fetching paper list for mailing %s...", mailing_id)
        paper_ids = paper_ids_for_mailing(mailing_id)
        if not paper_ids:
            logger.error("No papers found for mailing %s", mailing_id)
            return 1
        if max_cap > 0:
            paper_ids = paper_ids[:max_cap]

    results: list[dict] = []

    if max_processes <= 1:
        for paper_id in paper_ids:
            r = _eval_one(paper_id, output_dir, mailing_id, ns.dry_run)
            results.append(r)
            status = "OK" if r["status"] == "ok" else "FAILED"
            print(f"\n  [{status}] {paper_id}")
    else:
        pmap = run_parallel_eval(
            root,
            paper_ids,
            mailing_id=mailing_id,
            output_dir=output_dir,
            max_workers=max_processes,
            dry_run=ns.dry_run,
        )
        for paper_id in paper_ids:
            pid = paper_id.lower()
            ok, err = pmap.get(pid, (False, "missing result"))
            results.append(
                {
                    "paper": pid,
                    "status": "ok" if ok else "error",
                    **({} if ok else {"error": err or ""}),
                }
            )
            status = "OK" if ok else "FAILED"
            print(f"\n  [{status}] {pid}")

    index = _build_index(output_dir, mailing_id, results, root=root)
    index_path = output_dir / "index.json"
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    succeeded = index["succeeded"]
    failed = index["failed"]
    total = index["total_papers"]
    print(f"\n{'=' * 60}")
    print(f"Mailing {mailing_id} complete: {succeeded}/{total} succeeded, {failed} failed")
    print(f"Index: {index_path}")
    print(f"{'=' * 60}")

    return 0 if failed == 0 else 1


def cmd_validate(ns: argparse.Namespace) -> int:
    root = repo_root()
    base = Path(ns.output_dir) if ns.output_dir else _default_output_dir(root)
    base = base.resolve()
    err = 0
    if not base.is_dir():
        print("FAIL", base, "not a directory", file=sys.stderr)
        return 1
    for sub in sorted(base.iterdir()):
        if not sub.is_dir():
            continue
        tp = sub / "thread.json"
        if not tp.is_file():
            continue
        try:
            validate_thread_file(tp)
            print("OK", tp)
        except Exception as e:
            print("FAIL", tp, validation_error_message(e))
            err = 1
    return err


def main(argv: list[str] | None = None) -> None:
    load_repo_dotenv()
    p = argparse.ArgumentParser(prog="agora21")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("eval", help="Generate thread JSON for one paper")
    e.add_argument("paper", metavar="paper_id", help="WG21 paper id (e.g. P3642R4)")
    e.add_argument(
        "--output-dir",
        default=None,
        help="Output root (default: <repo>/data); writes <paper>/thread.json",
    )
    e.add_argument("--mailing-id", required=True, dest="mailing_id", help="Mailing YYYY-MM")
    e.add_argument("--dry-run", action="store_true", help="Write minimal valid JSON without LLM")
    e.set_defaults(func=cmd_eval)

    r = sub.add_parser("run", help="Generate threads for all papers in a mailing")
    r.add_argument("mailing_id", help="e.g. 2026-02")
    r.add_argument(
        "--output-dir",
        default=None,
        help="Output root (default: <repo>/data); writes per-paper dirs + index.json",
    )
    r.add_argument("--max-cap", type=int, default=0, help="Max papers (0 = all); ignored when --papers-json is set")
    r.add_argument("--max-processes", type=int, default=1, help="Parallel workers (default 1; uses git worktrees)")
    r.add_argument(
        "--papers-json",
        default=None,
        metavar="JSON",
        help='JSON array of paper ids, e.g. \'["p0001r0","p0002r0"]\' (skips open-std fetch; use for queue-driven batches)',
    )
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("validate", help="Validate <output-dir>/*/thread.json")
    v.add_argument(
        "--output-dir",
        default=None,
        help="Directory tree to scan (default: <repo>/data)",
    )
    v.set_defaults(func=cmd_validate)

    ns = p.parse_args(argv)
    try:
        sys.exit(ns.func(ns))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
