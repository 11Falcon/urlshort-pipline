#!/usr/bin/env python3
"""CI/CD course checker.

    python checker.py             # every module
    python checker.py 0x03        # one module
    python checker.py 0x03 2      # one task
    python checker.py doctor      # is my environment sane?

The checker never creates, edits or deletes anything. It reads your repository
on GitHub, your machine, and (for the deploy modules) your cluster, and asks
whether the state they are in is the state the task described.

Never edit this file.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.request

from ci_kit import (
    CheckFailed, ROOT, Skipped, api, artifacts_of, branch_protection, caches,
    check_runs, containers_running, current_branch, deployment_statuses,
    deployments, dig, docker_up, duration, env_variables, environment,
    environments, git, gh, gh_ready, have, head_sha, image_inspect, job,
    job_log, job_named, jobs, jobs_of, kget, kget_list, kubectl, load_yaml,
    need, need_cluster, need_docker, need_eq, need_file, need_in, need_job,
    need_k8s_obj, need_match, need_run, needs_of, package_tags, pulls,
    read_text, releases, repo, repo_slug, rulesets, run_log, run_scripts, runs,
    secrets_names, section, self_hosted_runners, sh, step_using, tags,
    tracked_files, triggers, uses_in, words, workflow, workflow_paths,
    workflow_text,
)

# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

MODULES: dict[str, str] = {
    "0x00": "The repository, the runner, the first green run",
    "0x01": "The build: pinned, cached, reproducible",
    "0x02": "The test gate",
    "0x03": "Static analysis, and the local mirror",
    "0x04": "Containers: build, tag, publish",
    "0x05": "Secrets, permissions, supply chain",
    "0x06": "Pipeline architecture: graph, reuse, concurrency",
    "0x07": "Environments, approvals, a real deploy",
    "0x08": "Release engineering",
    "0x09": "Deployment strategies and rollback",
    "0x0A": "The feedback loop: gates, protection, metrics",
    "0x0B": "Debugging drills",
    "0x0C": "Final project: the whole pipeline",
}

TASKS: dict[str, dict[int, tuple[str, callable]]] = {}

IMAGE = "urlshort"          # the GHCR package name
NS = "cicd"                 # the namespace the rolling deploy lands in


def task(module: str, idx: int, title: str):
    def deco(fn):
        TASKS.setdefault(module, {})[idx] = (title, fn)
        return fn
    return deco


# --------------------------------------------------------------------------
# small shared helpers
# --------------------------------------------------------------------------

def owner() -> str:
    return repo_slug().split("/")[0]


def image_ref(tag: str) -> str:
    return f"ghcr.io/{owner().lower()}/{IMAGE}:{tag}"


def newest_success(workflow_file: str = "ci.yml", branch: str = "main") -> dict:
    return need_run(workflow=workflow_file, conclusion="success", branch=branch,
                    what=f"a successful run of {workflow_file} on {branch}")


def find_failed_job(name_fragment: str, workflow_file: str = "ci.yml",
                    scan: int = 12) -> tuple[dict, dict] | None:
    """(run, job) for the newest failed job whose name contains the fragment."""
    seen = 0
    for r in runs(workflow=workflow_file):
        if r.get("conclusion") not in ("failure", "cancelled"):
            continue
        seen += 1
        if seen > scan:
            break
        for j in jobs_of(r["id"]):
            if (name_fragment.lower() in (j.get("name") or "").lower()
                    and j.get("conclusion") == "failure"):
                return r, j
    return None


def overlapped(a: dict, b: dict) -> bool:
    """Did two jobs run at the same time, at all?"""
    from ci_kit import ts
    a0, a1 = ts(a.get("started_at")), ts(a.get("completed_at"))
    b0, b1 = ts(b.get("started_at")), ts(b.get("completed_at"))
    return a0 < b1 and b0 < a1


def depths(wf: dict) -> dict[str, int]:
    """Longest-path depth of each job in the `needs` graph. Detects cycles."""
    js = jobs(wf)
    memo: dict[str, int] = {}

    def walk(name: str, seen: frozenset) -> int:
        if name in seen:
            raise CheckFailed(f"the needs: graph has a cycle through {name!r}")
        if name in memo:
            return memo[name]
        parents = [n for n in needs_of(js.get(name) or {}) if n in js]
        memo[name] = 0 if not parents else 1 + max(walk(p, seen | {name}) for p in parents)
        return memo[name]

    return {n: walk(n, frozenset()) for n in js}


def all_uses() -> list[tuple[str, str]]:
    """(file, uses-value) for every action referenced anywhere in the repo."""
    out: list[tuple[str, str]] = []
    for p in workflow_paths():
        rel = f".github/workflows/{p.name}"
        for u in uses_in(load_yaml(rel)):
            out.append((rel, u))
    actions_dir = ROOT / ".github" / "actions"
    if actions_dir.is_dir():
        for p in actions_dir.rglob("action.y*ml"):
            rel = str(p.relative_to(ROOT)).replace("\\", "/")
            for u in uses_in(load_yaml(rel)):
                out.append((rel, u))
    return out


def all_workflow_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in workflow_paths())


def http_get(url: str, timeout: int = 8) -> tuple[int, str]:
    """GET a local URL. Returns (status, body); status 0 means it never answered."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - localhost
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - connection refused, DNS, timeout: all "no answer"
        return 0, ""


def json_at(url: str, what: str) -> dict:
    status, body = http_get(url)
    need(status == 200, f"{what}: GET {url} returned {status or 'nothing at all'}",
         hint="is the container running? `docker ps`")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise CheckFailed(f"{what}: {url} did not return JSON", hint=body[:200]) from None


def container_named(fragment: str) -> dict | None:
    for c in containers_running():
        if fragment in (c.get("Names") or "") or fragment in (c.get("Image") or ""):
            return c
    return None


def pinned_violations() -> list[str]:
    bad = []
    for f, u in all_uses():
        if u.startswith("./") or u.startswith("docker://"):
            continue
        if not re.search(r"@[0-9a-f]{40}$", u.strip()):
            bad.append(f"{f}: {u}")
    return bad


# ==========================================================================
# 0x00 — the repository, the runner, the first green run
# ==========================================================================

@task("0x00", 0, "A public repo on GitHub, and this working tree pushes to it")
def _():
    r = repo()
    need_eq(r.get("visibility"), "public",
            "repository visibility (branch protection and Actions minutes are "
            "free on public repos; module 0x0A needs it)")
    need_eq(r.get("default_branch"), "main", "default branch")
    sha = head_sha()
    need(api(f"repos/{{repo}}/commits/{sha}") is not None,
         f"your HEAD commit {sha[:7]} is not on GitHub",
         hint="git push")
    need(not r.get("archived"), "the repository is archived")


@task("0x00", 1, "A workflow exists, and GitHub has registered it")
def _():
    wf = workflow("ci")
    need(wf.get("name"), "give the workflow a `name:` — it is what you will look for "
                         "in the Actions tab at 3am")
    t = triggers(wf)
    need("push" in t, "ci.yml does not trigger on push")
    need("pull_request" in t, "ci.yml does not trigger on pull_request — the gate "
                              "has to run before the merge, not after")
    branches = dig(t, "push.branches") or []
    need("main" in branches or not branches,
         f"push trigger listens to {branches}, not main")
    listed = api("repos/{repo}/actions/workflows")
    names = [w.get("path", "") for w in (listed or {}).get("workflows", [])]
    need(any(n.endswith("ci.yml") or n.endswith("ci.yaml") for n in names),
         "GitHub does not know about .github/workflows/ci.yml",
         hint="push it to main — a workflow is only registered once GitHub has "
              "seen it on the default branch")


@task("0x00", 2, "A green run on main")
def _():
    r = newest_success()
    need(r.get("head_branch") == "main", f"newest green run was on {r.get('head_branch')}")


@task("0x00", 3, "The run proves which commit it built")
def _():
    r = newest_success()
    text = workflow_text("ci")
    need_match(r"github\.sha", text,
               "ci.yml never mentions github.sha",
               hint="add a step that echoes the commit it is building")
    log = run_log(r["id"])
    need(log.strip(), "could not download the log for that run",
         hint=f"gh run view {r['id']} --log")
    sha = r.get("head_sha", "")
    need(sha[:7] in log,
         f"the log of run {r['id']} never prints its own commit {sha[:7]}",
         hint="echo \"building ${{ github.sha }}\" — you will want this every time "
              "you ask 'is that fix in this build?'")


@task("0x00", 4, "You have made it fail, and you wrote down what you saw")
def _():
    all_runs = runs(workflow="ci.yml")
    need(any(r.get("conclusion") == "failure" for r in all_runs),
         "no run of ci.yml has ever failed",
         hint="break something on a branch and push it — a pipeline you have never "
              "seen fail is a pipeline you do not know")
    finished = [r for r in all_runs if r.get("conclusion") and r.get("head_branch") == "main"]
    need(finished and finished[0]["conclusion"] == "success",
         "the newest finished run on main is not green — fix it before moving on")
    notes = read_text("0x00-first-run/notes.md")
    need(words(notes) >= 80, "notes.md is under 80 words; say what failed and how you "
                             "found out")
    need_in("log", notes, "notes.md (where did you read the failure?)")


# ==========================================================================
# 0x01 — the build
# ==========================================================================

@task("0x01", 0, "Dependencies are pinned in a lock file, and CI installs from it")
def _():
    lock = read_text("requirements.lock")
    lines = [ln.strip() for ln in lock.splitlines()
             if ln.strip() and not ln.strip().startswith(("#", "--hash", "-r", "-c"))]
    need(len(lines) >= 5,
         f"requirements.lock has {len(lines)} requirement lines — that is not a "
         "resolved dependency tree, it is a wish list",
         hint="python -m pip freeze > requirements.lock (inside a clean venv), or "
              "use pip-tools / uv pip compile")
    loose = [ln for ln in lines if "==" not in ln]
    need(not loose, f"unpinned lines in requirements.lock: {loose[:4]}",
         hint="every line needs ==, or the build is not the same build tomorrow")
    scripts = run_scripts(workflow("ci"))
    need_in("requirements.lock", scripts,
            "ci.yml never installs from requirements.lock")


@task("0x01", 1, "The cache is keyed on the lock file, and it actually hits")
def _():
    text = workflow_text("ci")
    need_match(r"hashFiles\(", text, "no hashFiles() anywhere in ci.yml",
               hint="a cache key that does not change with the lock file is a cache "
                    "that serves you yesterday's dependencies")
    need_match(r"hashFiles\([^)]*requirements\.lock", text,
               "the cache key does not hash requirements.lock")
    entries = caches()
    need(entries, "GitHub is storing no caches for this repo",
         hint="gh api repos/{owner}/{repo}/actions/caches")
    log = run_log(newest_success()["id"])
    need_match(r"cache restored|cache hit|restored from cache|cache is exact match",
               log, "no run has ever restored a cache",
               hint="the first run always misses. Run it twice — the second one is "
                    "the one that tells you whether your key is stable")


@task("0x01", 2, "The build produces a downloadable artifact")
def _():
    r = newest_success()
    arts = artifacts_of(r["id"])
    need(arts, f"run {r['id']} produced no artifacts")
    dist = [a for a in arts if re.search(r"dist|wheel|package", a.get("name", ""), re.I)]
    need(dist, f"no artifact named like a build output; found "
               f"{[a.get('name') for a in arts]}")
    need(dist[0].get("size_in_bytes", 0) > 1000,
         f"artifact {dist[0]['name']} is {dist[0].get('size_in_bytes')} bytes — empty?")
    need(not dist[0].get("expired"), "that artifact has expired; run the pipeline again")


@task("0x01", 3, "Built once, consumed downstream — not rebuilt")
def _():
    wf = workflow("ci")
    js = jobs(wf)
    producers = [n for n, j in js.items() if step_using(j, "upload-artifact")]
    need(producers, "no job uploads an artifact")
    consumers = [n for n, j in js.items() if step_using(j, "download-artifact")]
    need(consumers, "no job downloads the artifact — every job is rebuilding it")
    linked = [c for c in consumers if set(needs_of(js[c])) & set(producers)]
    need(linked, f"jobs {consumers} download an artifact but do not `needs:` "
                 f"the job that uploads it {producers} — that is a race, not a pipeline")
    for c in linked:
        body = run_scripts(js[c])
        need(not re.search(r"python\s+-m\s+build|pip\s+wheel|hatch\s+build", body),
             f"job {c} downloads the artifact and then builds it again anyway")


@task("0x01", 4, "The same commit builds byte-for-byte the same artifact")
def _():
    text = workflow_text("ci")
    need_in("SOURCE_DATE_EPOCH", text,
            "ci.yml never sets SOURCE_DATE_EPOCH",
            hint="zip timestamps are why two builds of one commit differ; hatchling "
                 "honours SOURCE_DATE_EPOCH")
    log = run_log(newest_success()["id"])
    hashes = re.findall(r"REPRODUCIBLE\s+([0-9a-f]{64})", log)
    need(len(hashes) >= 2,
         f"found {len(hashes)} `REPRODUCIBLE <sha256>` lines in the log, expected 2",
         hint="build twice in one job and print the sha256 of each wheel")
    need(len(set(hashes)) == 1,
         f"the two builds differ: {hashes[0][:16]}… vs {hashes[1][:16]}…",
         hint="something in the wheel carries the wall clock or a path")


@task("0x01", 5, "Every job has a timeout")
def _():
    missing = []
    for p in workflow_paths():
        wf = load_yaml(f".github/workflows/{p.name}")
        if not isinstance(wf, dict) or "jobs" not in wf:
            continue
        for name, j in (wf.get("jobs") or {}).items():
            if not isinstance(j, dict) or j.get("uses"):
                continue  # a job that calls a reusable workflow cannot set one
            t = j.get("timeout-minutes")
            if t is None:
                missing.append(f"{p.name}:{name}")
            elif isinstance(t, int) and t > 30:
                missing.append(f"{p.name}:{name} (={t}m, too generous)")
    need(not missing, f"jobs without a sane timeout-minutes: {missing[:6]}",
         hint="the default is 360 minutes. A hung job should cost you six minutes, "
              "not six hours of runner time")


# ==========================================================================
# 0x02 — the test gate
# ==========================================================================

@task("0x02", 0, "A failing test fails the pipeline")
def _():
    found = find_failed_job("test")
    need(found, "no run of ci.yml has ever failed inside a job named like `test`",
         hint="break an assertion on a branch and push. A gate you have not seen "
              "close is not a gate")
    r, j = found
    log = job_log(j["id"])
    need_match(r"assert|failed|error", log,
               "that job failed, but the log shows no test failure — did it fail for "
               "an unrelated reason (setup, install)?")


@task("0x02", 1, "Coverage is a gate, not a report")
def _():
    scripts = run_scripts(workflow("ci"))
    m = re.search(r"--cov-fail-under[= ](\d+)", scripts)
    cfg = read_text("pyproject.toml")
    fail_under = int(m.group(1)) if m else None
    if fail_under is None:
        m2 = re.search(r"fail_under\s*=\s*(\d+)", cfg)
        fail_under = int(m2.group(1)) if m2 else None
    need(fail_under is not None,
         "nothing enforces a coverage floor",
         hint="pytest --cov=src/urlshort --cov-fail-under=85")
    need(fail_under >= 85, f"the coverage floor is {fail_under}% — raise it to 85 or more")
    need_in("--cov", scripts, "ci.yml never measures coverage")
    log = run_log(newest_success()["id"])
    need_match(r"TOTAL\s+\d+|required test coverage|coverage:? *\d+%",
               log, "no coverage summary in the log of the newest green run")


@task("0x02", 2, "The suite runs on every Python you claim to support")
def _():
    wf = workflow("ci")
    j = job(wf, "test")
    matrix = dig(j, "strategy.matrix", {}) or {}
    values = [v for v in matrix.values() if isinstance(v, list)]
    need(values, "job `test` has no strategy.matrix")
    versions = [str(x) for v in values for x in v]
    need(sum(1 for x in versions if re.match(r"3\.\d", x)) >= 2,
         f"the matrix covers {versions} — use at least two Python versions")
    need_eq(dig(j, "strategy.fail-fast"), False,
            "strategy.fail-fast (set it to false: you want to know whether 3.11 AND "
            "3.12 broke, not just the first one)")
    r = newest_success()
    test_jobs = [x for x in jobs_of(r["id"]) if "test" in (x.get("name") or "").lower()]
    need(len(test_jobs) >= 2,
         f"run {r['id']} only had {len(test_jobs)} test job(s) — the matrix did not fan out")


@task("0x02", 3, "Test results survive a failing run")
def _():
    wf = workflow("ci")
    j = job(wf, "test")
    upload = step_using(j, "upload-artifact")
    need(upload, "job `test` never uploads its results")
    cond = str(upload.get("if", ""))
    need("always()" in cond,
         f"the upload step's `if:` is {cond!r} — without always() you only keep "
         "results for runs that did not need them")
    scripts = run_scripts(j)
    need_match(r"--junit-?xml|junit", scripts + json.dumps(upload),
               "nothing produces a machine-readable report",
               hint="pytest --junitxml=junit.xml")
    found = find_failed_job("test")
    if found:
        r, _ = found
        names = [a.get("name") for a in artifacts_of(r["id"])]
        need(names, f"the failed run {r['id']} kept no artifacts — that is exactly "
                    "the run whose results you needed")


@task("0x02", 4, "Fast on pull requests, complete every night")
def _():
    scripts = run_scripts(workflow("ci"))
    need_match(r"-m\s+[\"']?not slow", scripts,
               "the pull-request gate runs the slow tests too",
               hint='pytest -m "not slow"')
    wf = workflow("nightly")
    t = triggers(wf)
    need("schedule" in t, "nightly.yml has no schedule: trigger")
    need("workflow_dispatch" in t,
         "nightly.yml cannot be started by hand — add workflow_dispatch, because you "
         "will want to run it after a fix rather than wait until 2am")
    night = run_scripts(wf)
    need(not re.search(r"-m\s+[\"']?not slow", night),
         "the nightly run also skips the slow tests — then nothing ever runs them")
    ok = [r for r in runs(workflow="nightly.yml") if r.get("conclusion") == "success"]
    need(ok, "nightly.yml has never finished green",
         hint="gh workflow run nightly.yml")


@task("0x02", 5, "Green three times in a row on main")
def _():
    finished = [r for r in runs(workflow="ci.yml", branch="main") if r.get("conclusion")]
    need(len(finished) >= 3, f"only {len(finished)} finished runs on main so far")
    last3 = finished[:3]
    bad = [f"{r['id']}:{r['conclusion']}" for r in last3 if r["conclusion"] != "success"]
    need(not bad, f"the last three runs on main were not all green: {bad}",
         hint="a suite that passes two times in three is not a gate, it is a "
              "coin toss. Find the flake and pin it down")


# ==========================================================================
# 0x03 — static analysis
# ==========================================================================

@task("0x03", 0, "Lint is a gate, and formatting is not an opinion")
def _():
    scripts = run_scripts(workflow("ci"))
    need_in("ruff check", scripts, "ci.yml never runs `ruff check`")
    need_match(r"ruff format\s+--(check|diff)", scripts,
               "nothing checks formatting",
               hint="`ruff format --check .` fails instead of rewriting — that is "
                    "what you want in CI")
    r = newest_success()
    j = need_job(r["id"], "lint")
    need_eq(j.get("conclusion"), "success", "the lint job in the newest green run")


@task("0x03", 1, "Types are a gate too")
def _():
    scripts = run_scripts(workflow("ci"))
    need_in("mypy", scripts, "ci.yml never runs mypy")
    cfg = read_text("pyproject.toml")
    need_match(r"strict\s*=\s*true", cfg,
               "[tool.mypy] is not strict — non-strict mypy on an untyped codebase "
               "passes by saying nothing")
    r = newest_success()
    j = need_job(r["id"], "typecheck")
    need_eq(j.get("conclusion"), "success", "the typecheck job in the newest green run")


@task("0x03", 2, "What runs locally is what runs in CI")
def _():
    cfg = load_yaml(".pre-commit-config.yaml")
    repos = (cfg or {}).get("repos") or []
    ruff = next((r for r in repos if "ruff" in str(r.get("repo", ""))), None)
    need(ruff, ".pre-commit-config.yaml has no ruff hook")
    rev = str(ruff.get("rev", ""))
    need(rev, "the ruff hook has no pinned rev")
    version = re.sub(r"^v", "", rev)
    lock = read_text("requirements.lock")
    need(version in lock,
         f"pre-commit pins ruff {version}, which does not appear in requirements.lock",
         hint="two ruff versions means the hook and the gate disagree, and the "
              "argument always happens in a pull request")
    need_in("pre-commit run", all_workflow_text(),
            "no workflow runs `pre-commit run --all-files`",
            hint="run the same hooks in CI, or the hooks are a suggestion")


@task("0x03", 3, "Lint and test run at the same time, not in a queue")
def _():
    wf = workflow("ci")
    js = jobs(wf)
    need("lint" in js and "test" in js, "expected jobs named `lint` and `test`")
    need("test" not in needs_of(js["lint"]) and "lint" not in needs_of(js["test"]),
         "lint and test wait for each other — nothing about a linter needs a test "
         "result, or the other way round")
    r = newest_success()
    lint = need_job(r["id"], "lint")
    test = need_job(r["id"], "test")
    need(overlapped(lint, test),
         f"lint ran {duration(lint):.0f}s and test ran {duration(test):.0f}s, but "
         "they never overlapped in wall-clock time",
         hint="something is serialising them — a shared `needs:`, or a concurrency "
              "group that is too coarse")


@task("0x03", 4, "Findings land on the diff, not only in a log")
def _():
    scripts = run_scripts(workflow("ci"))
    need_match(r"--output-format[= ]github|reviewdog|problem-matcher", scripts,
               "ruff output is not formatted for GitHub annotations",
               hint="ruff check --output-format=github .")
    found = find_failed_job("lint")
    need(found, "the lint job has never failed, so you have never seen an annotation",
         hint="push a badly formatted line on a branch and look at the Files tab")
    _, j = found
    log = job_log(j["id"])
    need_match(r"::error|::warning", log,
               "that failed lint job emitted no ::error workflow command",
               hint="without the github output format, GitHub cannot place the "
                    "finding on the line that caused it")


# ==========================================================================
# 0x04 — containers
# ==========================================================================

@task("0x04", 0, "A Dockerfile you would let out of the building")
def _():
    df = read_text("Dockerfile")
    froms = re.findall(r"^\s*FROM\s+(\S+)(?:\s+AS\s+(\S+))?", df, re.I | re.M)
    need(len(froms) >= 2,
         f"{len(froms)} FROM line(s) — build tools and toolchains do not belong in "
         "the image you ship. Use a builder stage")
    need(any(alias for _, alias in froms), "no stage is named (`FROM … AS builder`)")
    unpinned = [img for img, _ in froms if "@sha256:" not in img]
    need(not unpinned,
         f"base images not pinned by digest: {unpinned}",
         hint="python:3.11-slim moves under you. "
              "docker buildx imagetools inspect python:3.11-slim --format "
              "'{{.Manifest.Digest}}'")
    need_match(r"^\s*USER\s+(?!root)\S+", df,
               "the final stage never drops root",
               hint="add a non-root USER after you copy the app in")
    leaked = re.findall(r"^\s*(?:ENV|ARG)\s+(\w*(?:TOKEN|PASSWORD|SECRET|KEY)\w*)\s*=\s*\S+",
                        df, re.I | re.M)
    need(not leaked, f"a credential-shaped default value is baked into the image: {leaked}")


@task("0x04", 1, "The Dockerfile is linted like everything else")
def _():
    text = all_workflow_text()
    need_match(r"hadolint", text, "no workflow lints the Dockerfile",
               hint="uses: hadolint/hadolint-action@<sha>")
    r = newest_success()
    j = job_named(r["id"], "hadolint") or job_named(r["id"], "docker") \
        or job_named(r["id"], "image") or job_named(r["id"], "lint")
    need(j and j.get("conclusion") == "success",
         "no green job in the newest run appears to have run hadolint")


@task("0x04", 2, "The image is published to GHCR by the pipeline")
def _():
    text = all_workflow_text()
    need_match(r"docker/build-push-action|docker\s+push|buildx\s+build.*--push",
               text, "no workflow pushes an image")
    need_match(r"packages:\s*write", text,
               "no job asks for `packages: write`",
               hint="GITHUB_TOKEN is read-only for packages by default — that is a "
                    "good default, so grant it in the one job that needs it")
    versions = package_tags(IMAGE)
    need(versions,
         f"no container package named {IMAGE!r} under {owner()}",
         hint=f"gh api users/{owner()}/packages/container/{IMAGE}/versions\n"
              "      (if this 403s: gh auth refresh -s read:packages)")


@task("0x04", 3, "Tags say what the image is")
def _():
    t = package_tags(IMAGE)
    sha_tags = [k for k in t if re.fullmatch(r"(sha-)?[0-9a-f]{7,40}", k)]
    need(sha_tags, f"no commit-identifying tag among {sorted(t)[:8]}",
         hint="uses: docker/metadata-action — type=sha gives you sha-<7>")
    moving = [k for k in t if k in ("latest", "main", "edge")]
    need(moving, "no moving tag (latest/main) — humans need one, machines must never "
                 "use it")
    probe = re.sub(r"^sha-", "", sha_tags[0])
    need(api(f"repos/{{repo}}/commits/{probe}") is not None,
         f"tag {sha_tags[0]!r} does not correspond to a commit in this repository")


@task("0x04", 4, "The layer cache survives between runs")
def _():
    text = all_workflow_text()
    need_match(r"cache-from:\s*type=(gha|registry)", text,
               "the image build has no cache-from",
               hint="cache-from: type=gha / cache-to: type=gha,mode=max")
    need_match(r"cache-to:\s*type=(gha|registry)", text, "the image build has no cache-to")
    r = newest_success()
    log = run_log(r["id"])
    need_match(r"\bCACHED\b|importing cache manifest|cache hit",
               log, "the newest build reused nothing from cache",
               hint="the first build fills the cache; the second is the one that "
                    "proves the key is right")


@task("0x04", 5, "The published image runs, and it is not enormous")
def _():
    need_docker()
    tags_now = package_tags(IMAGE)
    ref = image_ref(next((k for k in ("main", "latest") if k in tags_now),
                         sorted(tags_now)[0] if tags_now else "latest"))
    info = image_inspect(ref)
    need(info, f"{ref} is not on this machine", hint=f"docker pull {ref}")
    size_mb = (info.get("Size") or 0) / 1e6
    need(size_mb < 400, f"the image is {size_mb:.0f} MB — that is a lot of attack "
                        "surface to ship for one HTTP service")
    user = dig(info, "Config.User", "")
    need(user and user not in ("root", "0"), f"the image runs as {user or 'root'!r}")
    c = container_named("urlshort-local")
    need(c, "no running container named urlshort-local",
         hint=f"docker run -d --name urlshort-local -p 8000:8000 {ref}")
    body = json_at("http://127.0.0.1:8000/healthz", "the container")
    need_eq(body.get("status"), "ok", "/healthz from the published image")


# ==========================================================================
# 0x05 — secrets, permissions, supply chain
# ==========================================================================

@task("0x05", 0, "Least privilege is the default, and exceptions are local")
def _():
    weak = []
    for p in workflow_paths():
        wf = load_yaml(f".github/workflows/{p.name}")
        if not isinstance(wf, dict):
            continue
        top = wf.get("permissions")
        if top is None:
            weak.append(f"{p.name}: no top-level permissions:")
        elif top == "write-all" or (isinstance(top, dict)
                                    and any(v == "write" for v in top.values())):
            weak.append(f"{p.name}: top-level grants write")
    need(not weak, f"{weak[:4]}",
         hint="permissions: {contents: read} at the top, then widen inside the one "
              "job that needs it. The default token can write to your repo")
    per_job = any(isinstance(j, dict) and j.get("permissions")
                  for p in workflow_paths()
                  for j in (load_yaml(f".github/workflows/{p.name}") or {})
                  .get("jobs", {}).values())
    need(per_job, "no job declares its own permissions — the build/publish job needs "
                  "packages: write and should ask for it there")


@task("0x05", 1, "Every action is pinned to a commit you chose")
def _():
    bad = pinned_violations()
    need(not bad, f"actions referenced by a moving tag: {bad[:5]}",
         hint="a tag is a pointer someone else can move. Pin the SHA:\n"
              "      gh api repos/actions/checkout/commits/v4 --jq .sha")
    text = all_workflow_text()
    pins = re.findall(r"uses:\s*\S+@[0-9a-f]{40}(\s*#[^\n]*)?", text)
    need(pins and sum(1 for c in pins if c.strip()) >= max(1, len(pins) - 1),
         "pinned actions have no version comment",
         hint="uses: actions/checkout@11bd71… # v4.2.2 — otherwise nobody, you "
              "included, will ever dare upgrade them")


@task("0x05", 2, "A secret scanner caught something, and you handled it properly")
def _():
    text = all_workflow_text()
    need_match(r"gitleaks|trufflehog|detect-secrets", text,
               "no workflow scans for secrets")
    wf_file = next((p.name for p in workflow_paths()
                    if re.search(r"gitleaks|trufflehog|detect-secrets",
                                 p.read_text(encoding="utf-8", errors="replace"), re.I)),
                   None)
    failed = [r for r in runs(workflow=wf_file) if r.get("conclusion") == "failure"]
    need(failed, f"{wf_file} has never failed — plant a fake credential on a branch "
                 "and watch it catch",
         hint='echo "AWS_KEY=AKIAIOSFODNN7EXAMPLE" > leak.txt, commit, push')
    live = []
    for f in tracked_files():
        p = ROOT / f
        if not p.is_file() or p.stat().st_size > 200_000:
            continue
        if re.search(r"AKIA[0-9A-Z]{16}", p.read_text(encoding="utf-8", errors="ignore")):
            live.append(f)
    need(not live, f"the fake key is still in your working tree: {live[:3]}",
         hint="and remember that removing it here does not remove it from the "
              "history — see rotation.md")
    notes = read_text("0x05-supply-chain/rotation.md")
    need(words(notes) >= 120, "rotation.md is too short")
    for term in ("history", "rotate"):
        need_in(term, notes, f"rotation.md never mentions {term!r}",
                hint="deleting the line does not remove it from the history — the "
                     "only real fix is to invalidate the credential")


@task("0x05", 3, "Dependencies are watched, not remembered")
def _():
    cfg = load_yaml(".github/dependabot.yml")
    ecos = {u.get("package-ecosystem") for u in (cfg or {}).get("updates", [])}
    for want in ("pip", "docker", "github-actions"):
        need(want in ecos, f"dependabot.yml does not watch {want!r} (found {sorted(ecos)})",
             hint="your actions are dependencies too — that is how a pinned SHA stops "
                  "being a burden")
    text = all_workflow_text()
    need_match(r"pip-audit|trivy|grype|safety", text,
               "nothing scans dependencies for known vulnerabilities")
    scheduled = any("schedule" in triggers(load_yaml(f".github/workflows/{p.name}"))
                    for p in workflow_paths()
                    if re.search(r"pip-audit|trivy|grype",
                                 p.read_text(encoding="utf-8", errors="replace"), re.I))
    need(scheduled, "the vulnerability scan only runs on push",
         hint="new CVEs land against code you did not change. Schedule it")


@task("0x05", 4, "Secrets are passed, never printed")
def _():
    names = secrets_names()
    need("SMOKE_TOKEN" in names,
         f"no repository secret called SMOKE_TOKEN (found {names or 'none'})",
         hint="gh secret set SMOKE_TOKEN")
    text = all_workflow_text()
    need_in("secrets.SMOKE_TOKEN", text, "no workflow uses the secret")
    echoed = re.findall(r"(?:echo|print|cat)[^\n]*\$\{\{\s*secrets\.", text)
    need(not echoed, f"a workflow prints a secret: {echoed[:2]}",
         hint="masking is a safety net, not a design")
    inline = [ln for ln in text.splitlines()
              if re.search(r"^\s*(run:|\s{4,})", ln) and "${{ secrets." in ln
              and "env" not in ln.lower()]
    need_match(r"\$\{\{\s*secrets\.SMOKE_TOKEN\s*\}\}", text, "secret reference")
    need(not re.search(r"pull_request_target", text),
         "a workflow uses pull_request_target",
         hint="it runs with your secrets against a fork's code. If you genuinely "
              "need it, you need a much longer conversation than this course")
    need(len(inline) <= 12, "consider passing secrets through `env:` rather than "
                            "interpolating them into shell lines")


@task("0x05", 5, "You can prove what went into the image")
def _():
    text = all_workflow_text()
    need_match(r"anchore/sbom-action|syft|cyclonedx|--sbom", text,
               "nothing generates an SBOM")
    need_match(r"attest-build-provenance|cosign|--provenance", text,
               "nothing attests where the image came from",
               hint="uses: actions/attest-build-provenance@<sha> — it needs "
                    "id-token: write and attestations: write")
    need_match(r"id-token:\s*write", text, "no job requests id-token: write")
    r = newest_success()
    arts = [a.get("name", "") for a in artifacts_of(r["id"])]
    need(any("sbom" in a.lower() for a in arts),
         f"the newest green run kept no SBOM artifact (has {arts})")


# ==========================================================================
# 0x06 — pipeline architecture
# ==========================================================================

@task("0x06", 0, "The pipeline is a graph, not a queue")
def _():
    wf = workflow("ci")
    js = jobs(wf)
    need(len(js) >= 4, f"ci.yml has {len(js)} jobs — a single job is a shell script "
                       "with extra steps")
    d = depths(wf)
    roots = [n for n, v in d.items() if v == 0]
    need(len(roots) >= 2,
         f"only {roots} start immediately — everything else is waiting on something. "
         "Lint, tests and the image build have no reason to queue behind each other")
    fan_in = [n for n in js if len(set(needs_of(js[n])) & set(js)) >= 2]
    need(fan_in, "no job waits on two others — nothing joins the branches back "
                 "together before you ship")


@task("0x06", 1, "A composite action removes the copy-paste")
def _():
    path = ".github/actions/setup/action.yml"
    if not (ROOT / path).exists():
        path = ".github/actions/setup/action.yaml"
    act = load_yaml(path)
    need_eq(dig(act, "runs.using"), "composite", "the action's runs.using")
    st = dig(act, "runs.steps", []) or []
    need(len(st) >= 2, "the composite action has fewer than two steps")
    need(all(("shell" in s or "uses" in s) for s in st),
         "every `run:` step of a composite action needs an explicit `shell:`")
    users = [n for n, j in jobs(workflow("ci")).items()
             if any("./.github/actions/setup" in u for u in uses_in(j))]
    need(len(users) >= 2,
         f"only {users} use the composite action — it exists to be used more than once")


@task("0x06", 2, "A reusable workflow, called with inputs")
def _():
    callee = next((p for p in workflow_paths()
                   if "workflow_call" in triggers(load_yaml(f".github/workflows/{p.name}"))),
                  None)
    need(callee, "no workflow declares `on: workflow_call`")
    wf = load_yaml(f".github/workflows/{callee.name}")
    need(dig(triggers(wf), "workflow_call.inputs"),
         f"{callee.name} takes no inputs — then it is a copy, not a function")
    callers = []
    for p in workflow_paths():
        if p.name == callee.name:
            continue
        other = load_yaml(f".github/workflows/{p.name}")
        for name, j in (other.get("jobs") or {}).items():
            if isinstance(j, dict) and callee.name in str(j.get("uses", "")):
                callers.append((p.name, name, j))
    need(callers, f"nothing calls {callee.name}")
    need(any(j.get("with") for _, _, j in callers),
         "the caller passes no `with:` — you built a function and called it with "
         "no arguments")
    r = newest_success()
    names = [j.get("name", "") for j in jobs_of(r["id"])]
    need(any(" / " in n for n in names),
         "no job in the newest green run came from a called workflow "
         f"(jobs were: {names[:6]})",
         hint="GitHub names them `caller-job / callee-job`")


@task("0x06", 3, "A superseded run gets cancelled instead of finishing")
def _():
    wf = workflow("ci")
    conc = wf.get("concurrency")
    need(conc, "ci.yml has no `concurrency:` block")
    group = conc.get("group") if isinstance(conc, dict) else str(conc)
    need("github.ref" in str(group) or "github.head_ref" in str(group),
         f"the concurrency group is {group!r} — key it on the branch, or one busy "
         "branch blocks everyone else")
    need(isinstance(conc, dict) and conc.get("cancel-in-progress") in (True, "true"),
         "cancel-in-progress is not set — the old run keeps burning minutes to tell "
         "you about a commit you have already replaced")
    cancelled = [r for r in runs(workflow="ci.yml") if r.get("conclusion") == "cancelled"]
    need(cancelled, "no run has ever been cancelled",
         hint="push twice within a minute and watch the first one stop")


@task("0x06", 4, "A documentation change does not run the whole pipeline")
def _():
    wf = workflow("ci")
    text = workflow_text("ci")
    filtered = ("paths" in str(triggers(wf)) or "paths-ignore" in str(triggers(wf))
                or "dorny/paths-filter" in text
                or re.search(r"if:\s*.*(changed|filter)", text, re.I))
    need(filtered, "nothing distinguishes a docs change from a code change",
         hint="`paths-ignore: ['**.md']` on the push trigger, or a paths-filter job "
              "that the heavy jobs check")
    skipped = None
    for r in runs(workflow="ci.yml")[:15]:
        for j in jobs_of(r["id"]):
            if j.get("conclusion") == "skipped":
                skipped = (r, j)
                break
        if skipped:
            break
    if skipped:
        return
    docs_only = []
    log = git("log", "--format=%H", "-n", "40").stdout.split()
    for sha in log:
        files = git("show", "--name-only", "--format=", sha).stdout.split()
        if files and all(f.endswith((".md", ".txt")) for f in files):
            docs_only.append(sha)
    need(docs_only, "no docs-only commit exists to prove the filter works",
         hint="edit a README, commit it alone, push")
    ran = [r for r in runs(workflow="ci.yml") if r.get("head_sha") in docs_only]
    need(not ran or any(j.get("conclusion") == "skipped"
                        for j in jobs_of(ran[0]["id"])),
         f"the docs-only commit {docs_only[0][:7]} ran the full pipeline anyway")


@task("0x06", 5, "Jobs hand values to each other, and the run explains itself")
def _():
    wf = workflow("ci")
    js = jobs(wf)
    producers = {n: j for n, j in js.items() if j.get("outputs")}
    need(producers, "no job declares `outputs:`",
         hint="a version, a digest, a tag: compute it once and pass it down")
    text = workflow_text("ci")
    need_match(r"needs\.[\w-]+\.outputs\.", text,
               "no job reads another job's outputs")
    need_match(r"\$GITHUB_OUTPUT", text,
               "nothing writes to $GITHUB_OUTPUT",
               hint='echo "digest=$D" >> "$GITHUB_OUTPUT"')
    need_match(r"\$GITHUB_STEP_SUMMARY", text,
               "no step writes a job summary",
               hint="the summary is the one page a colleague will read; put the "
                    "version, the digest and the coverage number in it")


# ==========================================================================
# 0x07 — environments, approvals, a real deploy
# ==========================================================================

@task("0x07", 0, "A self-hosted runner on your machine, and a job that ran on it")
def _():
    rs = self_hosted_runners()
    need(rs, "this repository has no self-hosted runner registered",
         hint="Settings > Actions > Runners > New self-hosted runner, then\n"
              "      ./config.cmd --labels local   and   ./run.cmd")
    labelled = [r for r in rs
                if any((lb.get("name") or "").lower() == "local"
                       for lb in r.get("labels", []) or [])]
    need(labelled, f"no runner carries the label `local` "
                   f"(labels seen: {[l.get('name') for r in rs for l in r.get('labels', [])]})")
    names = {r.get("name") for r in rs}
    hit = None
    for r in runs(workflow="deploy.yml")[:12]:
        for j in jobs_of(r["id"]):
            if j.get("runner_name") in names and j.get("conclusion") == "success":
                hit = j
                break
        if hit:
            break
    need(hit, "no successful deploy job has run on your self-hosted runner",
         hint="runs-on: [self-hosted, local]")


@task("0x07", 1, "Two environments, and staging only accepts main")
def _():
    have_envs = {e.get("name") for e in environments()}
    for want in ("staging", "production"):
        need(want in have_envs, f"no `{want}` environment (found {sorted(have_envs)})",
             hint="Settings > Environments — or `gh api -X PUT "
                  "repos/{owner}/{repo}/environments/staging`")
    st = environment("staging") or {}
    policy = st.get("deployment_branch_policy")
    rules = [r.get("type") for r in st.get("protection_rules", []) or []]
    need(policy or rules,
         "the staging environment has no protection at all — any branch can deploy "
         "to it, including one from a fork")


@task("0x07", 2, "Production waits for a human")
def _():
    prod = environment("production") or {}
    rules = {r.get("type") for r in prod.get("protection_rules", []) or []}
    need("required_reviewers" in rules,
         f"production has no required reviewer (rules: {sorted(rules) or 'none'})",
         hint="this is the only gate in the whole pipeline that is allowed to be "
              "a person. Everything else should be a check")
    text = all_workflow_text()
    need_match(r"environment:\s*\n?\s*(name:\s*)?production|environment:\s*production",
               text, "no job targets the production environment",
               hint="the approval only happens if the job declares `environment:`")


@task("0x07", 3, "A deployment is recorded, with a URL")
def _():
    ds = deployments("staging")
    need(ds, "GitHub has recorded no deployment to staging",
         hint="a job with `environment:` creates one automatically")
    latest = ds[0]
    states = [s.get("state") for s in deployment_statuses(latest["id"])]
    need("success" in states,
         f"the newest staging deployment ended in {states or ['no status at all']}")
    wf = workflow("deploy")
    envs = [j.get("environment") for j in jobs(wf).values() if isinstance(j, dict)]
    need(any(isinstance(e, dict) and e.get("url") for e in envs),
         "the deploy job's `environment:` has no url:",
         hint="environment:\n        name: staging\n        url: http://localhost:8081")


@task("0x07", 4, "Staging is actually running the commit it says it is")
def _():
    need_docker()
    c = container_named("urlshort-staging")
    need(c, "no running container named urlshort-staging",
         hint="your deploy job should (re)start it on the self-hosted runner")
    body = json_at("http://127.0.0.1:8081/version", "staging")
    need_eq(body.get("environment"), "staging", "/version environment")
    sha = str(body.get("git_sha", ""))
    need(len(sha) >= 7 and sha != "unknown",
         f"staging reports git_sha={sha!r} — a deploy you cannot identify is a "
         "deploy you cannot roll back")
    need(api(f"repos/{{repo}}/commits/{sha}") is not None,
         f"staging claims to run commit {sha[:12]}, which is not in this repository")
    ds = deployments("staging")
    need(any(d.get("sha", "").startswith(sha[:7]) or sha.startswith(d.get("sha", "")[:7])
             for d in ds[:5]),
         f"the running commit {sha[:7]} matches none of the last GitHub deployments "
         f"{[d.get('sha', '')[:7] for d in ds[:5]]}")


@task("0x07", 5, "Configuration comes from the environment, not the image")
def _():
    values = {}
    for env in ("staging", "production"):
        values[env] = {v["name"]: v.get("value") for v in env_variables(env)}
        need("APP_ENV" in values[env],
             f"the {env} environment has no APP_ENV variable",
             hint=f"gh variable set APP_ENV --env {env} --body {env}")
    need(values["staging"]["APP_ENV"] != values["production"]["APP_ENV"],
         "APP_ENV is the same in both environments — then it configures nothing")
    text = all_workflow_text()
    need_match(r"\$\{\{\s*vars\.APP_ENV\s*\}\}", text,
               "no workflow reads vars.APP_ENV")
    need(not re.search(r"APP_ENV[=:]\s*[\"']?staging", read_text("Dockerfile"), re.I),
         "the Dockerfile hardcodes an environment — then the image is not the same "
         "artifact everywhere, and staging stops predicting production")


# ==========================================================================
# 0x08 — release engineering
# ==========================================================================

def newest_version_tag() -> str:
    vs = [t["name"] for t in tags() if re.fullmatch(r"v\d+\.\d+\.\d+", t.get("name", ""))]
    need(vs, "no semantic version tag (vMAJOR.MINOR.PATCH) exists",
         hint="git tag -a v1.0.0 -m 'first release' && git push origin v1.0.0")
    return sorted(vs, key=lambda v: [int(x) for x in v[1:].split(".")])[-1]


@task("0x08", 0, "A semantic version, tagged, and matching the code")
def _():
    tag = newest_version_tag()
    version = tag[1:]
    cfg = read_text("pyproject.toml")
    m = need_match(r'^version\s*=\s*"([^"]+)"', cfg, "version in pyproject.toml")
    need_eq(m.group(1), version,
            "pyproject.toml version vs the newest tag "
            "(the artifact must not disagree with the tag that produced it)")
    need(re.search(r"\d+\.\d+\.\d+", version), "version is not semver-shaped")


@task("0x08", 1, "The tag is what triggers the release")
def _():
    wf = workflow("release")
    t = triggers(wf)
    tag_patterns = dig(t, "push.tags") or []
    need(tag_patterns, "release.yml does not trigger on tag pushes",
         hint="on:\n  push:\n    tags: ['v*.*.*']")
    ok = [r for r in runs(workflow="release.yml") if r.get("conclusion") == "success"]
    need(ok, "release.yml has never finished green")
    need(any(str(r.get("head_branch", "")).startswith("v") for r in ok),
         f"no green release run came from a tag "
         f"(refs seen: {[r.get('head_branch') for r in ok[:4]]})")


@task("0x08", 2, "A GitHub Release with real notes and real files")
def _():
    rel = releases()
    need(rel, "no GitHub Release exists")
    latest = rel[0]
    need(not latest.get("draft"), "the newest release is still a draft")
    need(words(latest.get("body") or "") >= 30,
         "the release notes are under 30 words — 'bug fixes and improvements' is "
         "not a release note")
    assets = latest.get("assets") or []
    need(assets, "the release has no attached files",
         hint="attach the wheel you already built; a release nobody can download is "
              "a git tag with ceremony")
    need(all(a.get("size", 0) > 0 for a in assets), "an asset is zero bytes")


@task("0x08", 3, "A changelog a human wrote or a human would recognise")
def _():
    ch = read_text("CHANGELOG.md")
    tag = newest_version_tag()
    need_in(tag[1:], ch, f"CHANGELOG.md never mentions {tag}")
    need(words(ch) >= 100, "CHANGELOG.md is under 100 words")
    heads = re.findall(r"^###?\s*(added|changed|fixed|removed|security)\b", ch, re.I | re.M)
    need(len(set(h.lower() for h in heads)) >= 2,
         "the changelog has no categories (Added / Changed / Fixed)")
    need(re.search(r"#\d+|[0-9a-f]{7,40}", ch),
         "no entry points at a PR or a commit — a changelog you cannot trace back is "
         "a story, not a record")


@task("0x08", 4, "Release promotes a digest; it does not rebuild")
def _():
    text = workflow_text("release")
    need(not re.search(r"docker/build-push-action|docker\s+build", text),
         "the release workflow builds the image again",
         hint="rebuilding means the bits you tested are not the bits you tag. "
              "Retag the digest: docker buildx imagetools create -t <new> <old>")
    need_match(r"imagetools create|crane copy|regctl image copy|skopeo copy", text,
               "nothing in release.yml promotes an existing image")
    t = package_tags(IMAGE)
    tag = newest_version_tag()
    need(tag in t, f"the registry has no {tag} tag (has {sorted(t)[:8]})")
    tagged_sha = next((x.get("commit", {}).get("sha") for x in tags()
                       if x.get("name") == tag), None)
    candidates = [k for k in t if tagged_sha and re.sub(r"^sha-", "", k) ==
                  tagged_sha[:len(re.sub(r"^sha-", "", k))] and k != tag]
    need(candidates,
         f"no commit-tagged image matches the commit behind {tag}",
         hint="the release should promote the image built from that very commit")
    need(t[tag] == t[candidates[0]],
         f"{tag} points at {t[tag][:19]}… but {candidates[0]} points at "
         f"{t[candidates[0]][:19]}… — those are two different images")


@task("0x08", 5, "The image can tell you what it is without being asked nicely")
def _():
    need_docker()
    tag = newest_version_tag()
    ref = image_ref(tag)
    info = image_inspect(ref)
    need(info, f"{ref} is not on this machine", hint=f"docker pull {ref}")
    labels = dig(info, "Config.Labels", {}) or {}
    version = labels.get("org.opencontainers.image.version", "")
    revision = labels.get("org.opencontainers.image.revision", "")
    need(version, "the image has no org.opencontainers.image.version label",
         hint="docker/metadata-action writes the OCI labels for you")
    need(tag[1:] in version or version == tag,
         f"the image's version label is {version!r}, not {tag}")
    need(len(revision) >= 7 and api(f"repos/{{repo}}/commits/{revision}") is not None,
         f"the revision label {revision!r} is not a commit in this repository")


# ==========================================================================
# 0x09 — deployment strategies and rollback
# ==========================================================================

def ready_replicas(ns: str, name: str) -> int:
    d = kget("deployment", name, ns)
    return int(dig(d, "status.readyReplicas", 0) or 0) if d else 0


@task("0x09", 0, "The cluster runs a digest the pipeline chose")
def _():
    need_cluster()
    d = need_k8s_obj(kget("deployment", "api", NS), "deployment", "api", NS)
    ready = dig(d, "status.readyReplicas", 0)
    want = dig(d, "spec.replicas", 1)
    need(ready == want, f"deployment/api has {ready}/{want} ready replicas",
         hint=f"kubectl -n {NS} rollout status deploy/api")
    image = dig(d, "spec.template.spec.containers.0.image", "")
    need_in(IMAGE, image, "the deployed image")
    need("@sha256:" in image,
         f"the deployment references {image} by tag, not by digest",
         hint="tags move. Deploy the digest and the cluster runs exactly the bits "
              "that passed the pipeline")
    ann = dig(d, "spec.template.metadata.annotations", {}) or {}
    ann.update(dig(d, "metadata.annotations", {}) or {})
    joined = json.dumps(ann)
    need_match(r"git[-_]?sha", joined, "no git-sha annotation on the deployment")
    need_match(r"run[-_]?(url|id)", joined, "no link back to the run that deployed it",
               hint="at 3am you want the deployment itself to tell you which run "
                    "put it there")


@task("0x09", 1, "A smoke test stands between the deploy and 'done'")
def _():
    text = workflow_text("deploy")
    need_match(r"rollout status", text, "the deploy never waits for the rollout")
    smoke = re.search(r"smoke", text, re.I)
    need(smoke, "no step called anything like `smoke`")
    need_match(r"/healthz|/version", text, "the smoke test hits no endpoint")
    found = None
    for r in runs(workflow="deploy.yml")[:15]:
        if r.get("conclusion") != "failure":
            continue
        for j in jobs_of(r["id"]):
            for s in j.get("steps") or []:
                if "smoke" in (s.get("name") or "").lower() and s.get("conclusion") == "failure":
                    found = (r, s)
                    break
            if found:
                break
        if found:
            break
    need(found, "no deploy has ever failed at the smoke test",
         hint="deploy a deliberately broken image once. A gate you have not seen "
              "close does not exist")


@task("0x09", 2, "A failed deploy rolls itself back")
def _():
    need_cluster()
    text = workflow_text("deploy")
    need_match(r"if:\s*(\$\{\{\s*)?failure\(\)", text,
               "no step runs `if: failure()`")
    need_match(r"rollout undo|kubectl apply.*previous|helm rollback", text,
               "nothing rolls back on failure")
    d = need_k8s_obj(kget("deployment", "api", NS), "deployment", "api", NS)
    rev = int((dig(d, "metadata.annotations", {}) or {})
              .get("deployment.kubernetes.io/revision", 0))
    need(rev >= 3, f"deployment/api is at revision {rev} — deploy, break it, and let "
                   "the rollback happen; that is at least three")
    conds = {c.get("type"): c.get("status") for c in dig(d, "status.conditions", []) or []}
    need(conds.get("Available") == "True",
         "deployment/api is not Available — you rolled back into another broken state")
    good = [dep for dep in deployments("staging")
            if "success" in [s.get("state") for s in deployment_statuses(dep["id"])]]
    need(good, "no successful deployment record to compare the rolled-back state to")


@task("0x09", 3, "Blue and green, and a switch you can throw")
def _():
    need_cluster()
    ns = "cicd-bg"
    for colour in ("blue", "green"):
        d = need_k8s_obj(kget("deployment", f"api-{colour}", ns),
                         "deployment", f"api-{colour}", ns)
        need(dig(d, "status.readyReplicas", 0) >= 1,
             f"api-{colour} has no ready replicas — in blue/green the idle side is "
             "warm, not absent. That is the whole point")
    svc = need_k8s_obj(kget("service", "api", ns), "service", "api", ns)
    sel = dig(svc, "spec.selector", {}) or {}
    live = sel.get("colour") or sel.get("color")
    need(live in ("blue", "green"),
         f"service/api selects {sel} — the switch is the selector, so it has to name "
         "a colour")
    blue = dig(kget("deployment", "api-blue", ns), "spec.template.spec.containers.0.image", "")
    green = dig(kget("deployment", "api-green", ns), "spec.template.spec.containers.0.image", "")
    need(blue != green, "both sides run the same image — nothing is being tested")
    notes = read_text("0x09-deploy/bluegreen.md")
    need(words(notes) >= 80, "bluegreen.md is under 80 words")
    need_in("selector", notes, "bluegreen.md (what exactly does the cutover change?)")


@task("0x09", 4, "A canary takes a slice of the traffic, not all of it")
def _():
    need_cluster()
    ns = "cicd-canary"
    stable = ready_replicas(ns, "api-stable")
    canary = ready_replicas(ns, "api-canary")
    need(stable >= 4, f"api-stable has {stable} ready replicas, expected 4 or more")
    need(canary >= 1, f"api-canary has {canary} ready replicas, expected at least 1")
    share = canary / (stable + canary)
    need(0.05 <= share <= 0.30,
         f"the canary is {share:.0%} of the fleet — aim for 5-25%, which is enough "
         "traffic to see errors and few enough users to survive them")
    svc = need_k8s_obj(kget("service", "api", ns), "service", "api", ns)
    sel = dig(svc, "spec.selector", {}) or {}
    need("colour" not in sel and "color" not in sel and "track" not in sel,
         f"service/api selects {sel} — a canary needs the service to match BOTH "
         "deployments, or it receives nothing")
    pods = kget_list("pods", ns, "app=api")
    imgs = {dig(p, "spec.containers.0.image", "") for p in pods}
    need(len(imgs) >= 2, f"every pod behind the service runs the same image: {imgs}")


@task("0x09", 5, "Every deploy is auditable, and you have rolled one back by hand")
def _():
    need_cluster()
    d = need_k8s_obj(kget("deployment", "api", NS), "deployment", "api", NS)
    ann = json.dumps(dig(d, "metadata.annotations", {}) or {})
    for want, why in (("deployed-by", "who or what triggered it"),
                      ("git-sha", "which commit"),
                      ("run-url", "which pipeline run")):
        need_match(want.replace("-", "[-_]?"), ann,
                   f"no {want} annotation ({why})")
    notes = read_text("0x09-deploy/rollback.md")
    need(words(notes) >= 120, "rollback.md is under 120 words")
    for term in ("rollout undo", "digest"):
        need_in(term, notes, f"rollback.md never mentions {term!r}")
    need_match(r"\b\d+\s*(s|sec|second|min|minute)", notes,
               "rollback.md does not say how long the rollback took",
               hint="time it. That number is your MTTR floor, and it is the only "
                    "one your users feel")


# ==========================================================================
# 0x0A — the feedback loop
# ==========================================================================

def protection_summary() -> tuple[bool, bool, list[str]]:
    """(requires_pr, blocks_force_push, required_check_names) from either mechanism."""
    prot = branch_protection("main")
    if prot:
        checks = dig(prot, "required_status_checks.contexts", []) or []
        checks += [c.get("context") for c in
                   dig(prot, "required_status_checks.checks", []) or []]
        return (bool(prot.get("required_pull_request_reviews") is not None
                     or dig(prot, "required_pull_request_reviews.required_approving_review_count")
                     is not None),
                not dig(prot, "allow_force_pushes.enabled", False),
                [c for c in checks if c])
    pr_rule, no_force, checks = False, False, []
    for rs in rulesets():
        detail = api(f"repos/{{repo}}/rulesets/{rs['id']}") or {}
        if detail.get("target") not in (None, "branch"):
            continue
        for rule in detail.get("rules", []) or []:
            if rule.get("type") == "pull_request":
                pr_rule = True
            if rule.get("type") == "non_fast_forward":
                no_force = True
            if rule.get("type") == "required_status_checks":
                checks += [c.get("context") for c in
                           dig(rule, "parameters.required_status_checks", []) or []]
    return pr_rule, no_force, [c for c in checks if c]


@task("0x0A", 0, "main cannot be pushed to, only merged into")
def _():
    pr_rule, no_force, checks = protection_summary()
    need(pr_rule or checks,
         "main has neither branch protection nor a ruleset",
         hint="Settings > Branches (or Rules > Rulesets). Everything you built so "
              "far is advisory until this exists")
    need(pr_rule, "nothing requires a pull request to change main")
    need(checks, "no status check is required to merge",
         hint="required checks are named after the JOB, not the workflow: `lint`, "
              "`test (3.11)`, …")
    need(any("test" in c.lower() for c in checks),
         f"required checks are {checks} — the test job is not among them")
    need(no_force, "force pushes to main are still allowed")


@task("0x0A", 1, "A pull request was blocked by a check, and then merged")
def _():
    merged = [p for p in pulls("closed") if p.get("merged_at")]
    need(merged, "no pull request has ever been merged — you are still pushing to main")
    for p in merged[:8]:
        cs = check_runs(p["head"]["sha"])
        names = {c.get("name") for c in cs if c.get("conclusion") == "failure"}
        good = {c.get("name") for c in cs if c.get("conclusion") == "success"}
        if names and (names & good):
            return
    raise CheckFailed(
        "no merged pull request shows a check that failed and then passed on the "
        "same head commit",
        hint="that is the loop this whole course is about: push, get told no, fix, "
             "get told yes, merge. Do it once on purpose")


@task("0x0A", 2, "Merge hygiene is configured, not remembered")
def _():
    r = repo()
    need(r.get("allow_squash_merge"), "squash merging is disabled")
    need(not r.get("allow_merge_commit") or not r.get("allow_rebase_merge"),
         "all three merge strategies are enabled — pick one and let the button "
         "enforce it")
    need(r.get("delete_branch_on_merge"),
         "merged branches are not deleted automatically")
    need_file(".github/pull_request_template.md")
    owners = next((p for p in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
                   if (ROOT / p).exists()), None)
    need(owners, "no CODEOWNERS file")
    need_match(r"^\s*\S+\s+@\S+", read_text(owners), "CODEOWNERS assigns no owner")


@task("0x0A", 3, "The gate is fast enough that people wait for it")
def _():
    prs = [r for r in runs(workflow="ci.yml", event="pull_request")
           if r.get("conclusion") == "success"]
    sample = prs if len(prs) >= 5 else [
        r for r in runs(workflow="ci.yml") if r.get("conclusion") == "success"]
    need(len(sample) >= 5, f"only {len(sample)} green runs to measure — keep going")
    med = statistics.median(duration(r) for r in sample[:20])
    need(med > 0, "could not measure run durations")
    need(med <= 480,
         f"median green run takes {med / 60:.1f} minutes",
         hint="past about eight minutes people stop waiting for the check and start "
              "context-switching, and the gate quietly becomes a formality. Cache, "
              "parallelise, or move the slow part to the nightly run")


@task("0x0A", 4, "You measured your own delivery")
def _():
    path = "0x0A-feedback/metrics.py"
    src = read_text(path)
    need_match(r"gh\b|api|requests|urllib", src,
               "metrics.py does not appear to query GitHub at all")
    need(not re.search(r"return\s+(0\.\d+|\d+)\s*#\s*hard", src), "no hardcoding")
    p = sh(sys.executable, path, timeout=300)
    need(p.returncode == 0, f"`python {path}` exited {p.returncode}",
         hint=(p.stderr or p.stdout)[-400:])
    out = p.stdout.lower()
    for label, pattern in (
        ("deployment frequency", r"deploy(ment)? frequency"),
        ("lead time", r"lead time"),
        ("change failure rate", r"change failure rate"),
        ("time to restore", r"(time to restore|mttr|restore)"),
    ):
        need_match(pattern, out, f"the output never reports {label}")
    need(len(re.findall(r"\d+(\.\d+)?", out)) >= 4,
         "the output has fewer than four numbers in it",
         hint="four metrics, four numbers, computed from your own run history")


# ==========================================================================
# 0x0B — debugging drills
# ==========================================================================

def drill(n: int) -> tuple[dict, str, str]:
    """(parsed workflow, its text, the diagnosis section) for drill n."""
    wf = workflow(f"drill-{n}")
    text = workflow_text(f"drill-{n}")
    diag = section(read_text("0x0B-drills/diagnosis.md"), n)
    need(words(diag) >= 40,
         f"diagnosis section {n} is under 40 words — name the cause, not the fix")
    return wf, text, diag


def drill_green(n: int) -> dict:
    return need_run(workflow=f"drill-{n}.yml", conclusion="success",
                    what=f"a green run of drill-{n}.yml")


@task("0x0B", 1, "Drill 1: the cache that never hit")
def _():
    wf, text, diag = drill(1)
    need(not re.search(r"key:.*github\.(sha|run_id|run_number)", text),
         "the cache key still changes on every commit",
         hint="a key that is unique per run can only ever miss; it stores a copy "
              "every time and reads none of them")
    need_match(r"hashFiles\(", text, "the key does not hash anything")
    runs_ok = [r for r in runs(workflow="drill-1.yml") if r.get("conclusion") == "success"]
    need(len(runs_ok) >= 2, "run it twice — the second run is the evidence")
    need_match(r"cache restored|cache hit|restored from cache", run_log(runs_ok[0]["id"]),
               "the newest run still restored nothing")
    need_in("key", diag, "diagnosis 1")


@task("0x0B", 2, "Drill 2: the artifact that was not there yet")
def _():
    wf, text, diag = drill(2)
    js = jobs(wf)
    consumers = [n for n, j in js.items() if step_using(j, "download-artifact")]
    producers = [n for n, j in js.items() if step_using(j, "upload-artifact")]
    need(consumers and producers, "the drill lost one of its two jobs")
    for c in consumers:
        need(set(needs_of(js[c])) & set(producers),
             f"job {c} still does not `needs:` {producers}")
    drill_green(2)
    need_in("needs", diag, "diagnosis 2")


@task("0x0B", 3, "Drill 3: the green pipeline that tested nothing")
def _():
    wf, text, diag = drill(3)
    need(not re.search(r"\|\|\s*true|;\s*exit\s+0|continue-on-error:\s*true", text),
         "the failure is still being swallowed",
         hint="`pytest || true` makes the step green whatever happens. So does "
              "continue-on-error. Both turn a gate into decoration")
    need(any(r.get("conclusion") == "failure" for r in runs(workflow="drill-3.yml")),
         "drill-3 has never failed — with the swallow removed, a broken test must "
         "turn it red. Prove it once")
    drill_green(3)
    need_match(r"exit|status|swallow|\|\|", diag, "diagnosis 3 (why was it green?)")


@task("0x0B", 4, "Drill 4: the workflow that never ran")
def _():
    wf, text, diag = drill(4)
    t = triggers(wf)
    branches = dig(t, "push.branches") or []
    need(not branches or "main" in branches,
         f"the push trigger still listens to {branches}")
    got = runs(workflow="drill-4.yml")
    need(got, "drill-4.yml has still never produced a single run")
    need(got[0].get("conclusion") == "success", "its newest run is not green")
    need_match(r"branch|trigger|path|filter", diag, "diagnosis 4")


@task("0x0B", 5, "Drill 5: two deploys at once")
def _():
    wf, text, diag = drill(5)
    conc = wf.get("concurrency")
    need(conc, "drill-5 still has no concurrency block")
    group = str(conc.get("group") if isinstance(conc, dict) else conc)
    need("github.ref" in group or "github.workflow" in group,
         f"the group is {group!r} — it has to be stable across the runs you want "
         "to serialise")
    need(isinstance(conc, dict) and str(conc.get("cancel-in-progress")).lower() in
         ("true", "false"),
         "decide explicitly: cancel the older run, or queue behind it")
    got = runs(workflow="drill-5.yml")
    need(any(r.get("conclusion") == "cancelled" for r in got)
         or any(r.get("status") == "queued" for r in got),
         "no run of drill-5 was ever cancelled or queued behind another",
         hint="push twice in quick succession")
    need_match(r"concurren|race|simultan|at the same time", diag, "diagnosis 5")


@task("0x0B", 6, "Drill 6: the workflow that handed secrets to a stranger")
def _():
    wf, text, diag = drill(6)
    need(not re.search(r"pull_request_target", text),
         "drill-6 still triggers on pull_request_target",
         hint="pull_request_target runs with repository secrets and write access, "
              "in the context of code you have not reviewed")
    need(not re.search(r"permissions:\s*write-all", text), "write-all is still there")
    perms = wf.get("permissions")
    need(isinstance(perms, dict) and perms.get("contents") == "read",
         f"top-level permissions are {perms!r}, expected contents: read")
    need(not re.search(r"run:[^\n]*\$\{\{\s*secrets\.", text),
         "a secret is still interpolated straight into a shell line")
    drill_green(6)
    need_match(r"fork|untrusted|pull_request_target|secret", diag, "diagnosis 6")


# ==========================================================================
# 0x0C — final project
# ==========================================================================

@task("0x0C", 0, "One pipeline, all the gates, on every pull request")
def _():
    wf = workflow("ci")
    js = jobs(wf)
    for want in ("lint", "typecheck", "test", "build"):
        need(any(want in n for n in js), f"no job named like `{want}` (found {list(js)})")
    need("pull_request" in triggers(wf), "the pipeline does not run on pull requests")
    pr_runs = [r for r in runs(workflow="ci.yml", event="pull_request")
               if r.get("conclusion") == "success"]
    need(pr_runs, "no green pull-request run")
    names = {j.get("name", "").split(" (")[0] for j in jobs_of(pr_runs[0]["id"])}
    need(len(names) >= 4, f"that run only had jobs {names}")


@task("0x0C", 1, "Built once; the thing deployed is the thing that was tested")
def _():
    need_cluster()
    d = need_k8s_obj(kget("deployment", "api", NS), "deployment", "api", NS)
    image = dig(d, "spec.template.spec.containers.0.image", "")
    m = need_match(r"@(sha256:[0-9a-f]{64})", image, "the deployed image digest")
    digest = m.group(1)
    published = set(package_tags(IMAGE).values())
    need(digest in published,
         f"the cluster runs {digest[:19]}…, which is not a digest GitHub published "
         "for this package",
         hint="deploy the digest your build job emitted, not a tag you resolved "
              "again later")
    ann = json.dumps(dig(d, "metadata.annotations", {}) or {})
    m2 = re.search(r"[0-9a-f]{40}", ann)
    need(m2, "the deployment does not record the commit it came from")
    need(api(f"repos/{{repo}}/commits/{m2.group(0)}") is not None,
         "that commit is not in this repository")


@task("0x0C", 2, "The supply chain holds end to end")
def _():
    need(not pinned_violations(), "some actions are still floating on tags")
    text = all_workflow_text()
    for pattern, what in (
        (r"gitleaks|trufflehog", "secret scanning"),
        (r"pip-audit|trivy|grype", "dependency scanning"),
        (r"hadolint", "Dockerfile linting"),
        (r"sbom|syft|cyclonedx", "an SBOM"),
        (r"attest-build-provenance|cosign", "provenance"),
    ):
        need_match(pattern, text, f"the final pipeline lost {what}")
    df = read_text("Dockerfile")
    need("@sha256:" in df, "the Dockerfile stopped pinning its base image")


@task("0x0C", 3, "Merging to main deploys to staging, by itself")
def _():
    ds = deployments("staging")
    need(ds, "no staging deployments")
    latest = ds[0]
    need("success" in [s.get("state") for s in deployment_statuses(latest["id"])],
         "the newest staging deployment did not succeed")
    main_sha = (api("repos/{repo}/commits/main") or {}).get("sha", "")
    need(latest.get("sha") == main_sha,
         f"staging runs {latest.get('sha', '')[:7]} but main is at {main_sha[:7]} — "
         "either the deploy did not fire, or somebody deployed by hand")


@task("0x0C", 4, "A release goes out, and production waits for a person")
def _():
    tag = newest_version_tag()
    need(re.match(r"v1\.[1-9]|v[2-9]", tag),
         f"the newest tag is {tag} — cut a second release now that the pipeline is "
         "complete, so you exercise the whole path once")
    rel = releases()
    need(rel and rel[0].get("tag_name") == tag,
         f"no published release for {tag}")
    prod = deployments("production")
    need(prod, "nothing has ever been deployed to production")
    states = [s.get("state") for s in deployment_statuses(prod[0]["id"])]
    need("success" in states, f"the newest production deployment is {states}")
    rules = {r.get("type") for r in (environment("production") or {})
             .get("protection_rules", []) or []}
    need("required_reviewers" in rules, "production lost its human gate")
    notes = read_text("0x0C-final/RUNBOOK.md")
    need_in("approv", notes, "RUNBOOK.md (who approves a production deploy?)")


@task("0x0C", 5, "Production deploys are survivable")
def _():
    text = workflow_text("deploy")
    need_match(r"if:\s*(\$\{\{\s*)?failure\(\)", text, "no failure path")
    need_match(r"rollout undo|helm rollback", text, "no rollback")
    need_match(r"smoke", text, "no smoke test")
    need_cluster()
    canary = ready_replicas("cicd-canary", "api-canary")
    bg = ready_replicas("cicd-bg", "api-blue") + ready_replicas("cicd-bg", "api-green")
    need(canary >= 1 or bg >= 2,
         "neither the canary nor the blue/green setup is still standing — the final "
         "state should be a cluster you could actually ship to")


@task("0x0C", 6, "A runbook someone else could follow at 3am")
def _():
    txt = read_text("0x0C-final/RUNBOOK.md").lower()
    for word in ("rollback", "digest", "smoke", "staging", "production", "log"):
        need(word in txt, f"RUNBOOK.md never mentions {word!r}")
    need(words(txt) >= 300, "the runbook is too thin to be useful on a bad night")
    need_match(r"```|^\s{4}\S|`\S+`", read_text("0x0C-final/RUNBOOK.md"),
               "the runbook contains no literal commands",
               hint="write the command someone will paste, not a description of it")


@task("0x0C", 7, "You know how well your own pipeline delivers")
def _():
    rep = read_text("0x0C-final/report.md")
    need(words(rep) >= 250, "report.md is under 250 words")
    for term in ("lead time", "failure rate"):
        need_in(term, rep, f"report.md never mentions {term!r}")
    need(len(re.findall(r"\d+(\.\d+)?\s*(m|min|minute|h|hour|%|s\b)", rep, re.I)) >= 3,
         "the report has no measured numbers in it",
         hint="run your 0x0A script and quote what it said")
    need_match(r"^\s*(-|\*|\d\.)\s+.*(improv|reduc|cut|speed|fix)", rep,
               "the report names no improvement you actually made")


# ==========================================================================
# doctor
# ==========================================================================

def doctor() -> int:
    print("environment check\n" + "-" * 62)

    def line(label, value):
        print(f"  {label:<24}: {value}")

    line("python", sys.version.split()[0])
    try:
        import yaml  # noqa: F401
        line("pyyaml", "ok")
    except ImportError:
        line("pyyaml", "MISSING  ->  python -m pip install pyyaml")
    line("git", "ok" if have("git") else "MISSING")
    line("gh (GitHub CLI)", "ok" if have("gh") else "MISSING  ->  winget install GitHub.cli")
    if have("gh"):
        line("gh auth", "ok" if gh_ready() else "NOT LOGGED IN  ->  gh auth login")
        p = gh("auth", "status")
        m = re.search(r"[Tt]oken scopes:\s*(.+)", (p.stdout or "") + (p.stderr or ""))
        line("gh scopes", (m.group(1).strip() if m else "unknown"))
        missing = [s for s in ("read:packages", "workflow")
                   if m and s not in m.group(1)]
        if missing:
            line("", f"add {','.join(missing)}  ->  "
                     f"gh auth refresh -s {','.join(missing)}")
    if (ROOT / ".git").exists():
        try:
            line("repo", repo_slug())
            r = repo()
            line("visibility", r.get("visibility"))
            line("default branch", r.get("default_branch"))
            line("branch (local)", current_branch())
        except CheckFailed as e:
            line("repo", f"{e}")
    else:
        line("repo", "this folder is not a git repository yet (module 0x00)")
    line("docker", "ok" if docker_up() else "not running (needed from 0x04)")
    line("kubectl/cluster",
         "ok" if have("kubectl") and kubectl("version", "-o", "json").returncode == 0
         else "no cluster (needed for 0x09)")
    wfs = [p.name for p in workflow_paths()]
    line("workflows", ", ".join(wfs) if wfs else "none yet")
    if (ROOT / ".git").exists() and gh_ready():
        try:
            rs = self_hosted_runners()
            line("self-hosted runners",
                 ", ".join(f"{r['name']}({r.get('status')})" for r in rs) or
                 "none (needed from 0x07)")
        except CheckFailed:
            pass
    return 0


# ==========================================================================
# runner
# ==========================================================================

GREEN, RED, YELLOW, GREY, BOLD, OFF = (
    ("\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[1m", "\033[0m")
    if os.environ.get("NO_COLOR") is None else ("",) * 6)


def run_module(mod: str, only: int | None) -> tuple[int, int, int]:
    tasks = TASKS.get(mod, {})
    if only is not None and only not in tasks:
        print(f"{RED}no task {only} in module {mod}{OFF}")
        return 0, 1, 0
    items = sorted(tasks.items()) if only is None else [(only, tasks[only])]
    print(f"\n{BOLD}{mod} — {MODULES.get(mod, '')}{OFF}")
    ok = bad = skip = 0
    for idx, (title, fn) in items:
        try:
            fn()
        except Skipped as e:
            skip += 1
            print(f"  {YELLOW}~{OFF} {idx}. {title}")
            print(f"      {GREY}{e}{OFF}")
        except CheckFailed as e:
            bad += 1
            print(f"  {RED}x{OFF} {idx}. {title}")
            print(f"      {RED}{e}{OFF}")
            if e.hint:
                for ln in str(e.hint).splitlines():
                    print(f"      {GREY}{ln}{OFF}")
        except Exception as e:  # noqa: BLE001 - a broken check must not stop the run
            bad += 1
            print(f"  {RED}x{OFF} {idx}. {title}")
            print(f"      {RED}checker error: {type(e).__name__}: {e}{OFF}")
        else:
            ok += 1
            print(f"  {GREEN}v{OFF} {idx}. {title}")
    return ok, bad, skip


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("doctor", "--doctor"):
        return doctor()
    mods = list(MODULES)
    only = None
    if argv:
        want = argv[0].lower().removeprefix("0x")
        want = "0x" + want.rjust(2, "0").upper()
        if want not in MODULES:
            print(f"unknown module {argv[0]!r}. known: {', '.join(MODULES)}")
            return 2
        mods = [want]
        if len(argv) > 1:
            only = int(argv[1], 0)
    if not (ROOT / ".git").exists():
        print(f"{RED}This folder is not a git repository yet.{OFF} "
              "Start with module 0x00.")
        return 1
    if not gh_ready():
        print(f"{RED}The GitHub CLI is not logged in.{OFF} "
              "Run `python checker.py doctor`.")
        return 1
    ok = bad = skip = 0
    for m in mods:
        if m not in TASKS:
            continue
        a, b, c = run_module(m, only)
        ok, bad, skip = ok + a, bad + b, skip + c
    total = ok + bad + skip
    colour = GREEN if bad == 0 else RED
    print(f"\n{colour}{ok}/{total} passed{OFF}"
          + (f", {bad} failed" if bad else "")
          + (f", {skip} skipped" if skip else ""))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

