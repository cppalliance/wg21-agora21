# agora21

**agora21** turns ISO C++ committee (WG21) papers into **structured, Reddit-style discussion thread JSON** that matches a published schema. It is a **stateless processing engine**: you pass a paper id (or a mailing id for a batch), and it writes JSON under an output directory. It does not schedule work, track mailings over time, or serve HTTP.

## What it does

1. **Author pass** — an agent (Claude Code) drafts thread JSON guided by `prompts/reddit.md`.
2. **Validation** — output must satisfy `schemas/thread-v1.0.0.schema.json` (includes `producer_git_sha` for traceability).
3. **Reviewer pass** — if validation fails, a second agent (Cursor CLI) repairs the JSON.
4. **Git sandbox** — agent runs are wrapped so only changes under the repository `data/` tree are kept; generation assumes a normal git checkout.

Each finalized document includes **`producer_git_sha`** (the repository’s `git rev-parse HEAD` at run time) so results are traceable to a specific commit.

## Install

```bash
cd agora21
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and set API keys as needed.

If the package is not run from a source checkout, set **`AGORA21_REPO_ROOT`** to the directory that contains `pyproject.toml`.

## Usage

**One paper** — writes `<output-dir>/<paper>/thread.json` (default output dir: `<repo>/data`):

```bash
python -m agora21 eval P3642R4 --mailing-id 2026-02
python -m agora21 eval P3642R4 --mailing-id 2026-02 --output-dir ./data/
```

**Whole mailing** — fetches the paper list from open-std, processes up to `--max-cap` papers, writes `index.json` at the output root:

```bash
python -m agora21 run 2026-02 --max-cap 50 --max-processes 4
```

**Explicit paper batch** — skip the open-std fetch and process exactly the listed ids (e.g. queue-driven runs). `--max-cap` is ignored when this is set.

```bash
python -m agora21 run 2026-02 --papers-json '["p0001r0","p0002r0"]' --max-processes 4 --output-dir ./data/
```

Use `--dry-run` to emit minimal valid JSON without calling the agent (useful in CI).

### Parallel `run` and git

With `--max-processes` greater than 1, each paper runs in a **separate git worktree** so concurrent agent processes do not share one working tree (the per-agent git sandbox would otherwise race). Parallelism on a **single** checkout without worktrees would require **serial** agent runs or disabling the sandbox (`WG21_DISABLE_GIT_SANDBOX`). Submodule checkouts are often on **detached HEAD**; `git worktree add` from that state is still valid.

**Validate** artifacts:

```bash
python -m agora21 validate --output-dir ./data
```

## Layout

| Path | Role |
|------|------|
| `prompts/` | Moderation / thread instructions |
| `schemas/` | Thread JSON Schema (`thread-v1.0.0.schema.json`) |
| `data/` | Default output root (gitignored) |
| `temp/` | Optional Claude debug dumps |

## Development

```bash
pytest
ruff check src tests
```

## License

Distributed under the Boost Software License, Version 1.0.
See [LICENSE_1_0.txt](LICENSE_1_0.txt) or http://www.boost.org/LICENSE_1_0.txt

Official repository: https://github.com/cppalliance/agora21
