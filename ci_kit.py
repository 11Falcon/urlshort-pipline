"""Helpers used by checker.py.

Never edit this file, and never import it from your own workflows or scripts —
it exists so the checker can look at three kinds of real state:

    * your repository on GitHub   (runs, jobs, logs, artifacts, caches,
                                   environments, deployments, releases)
    * your machine                (git, docker, images, containers)
    * your cluster                (kubectl, for the deploy modules)

Nothing here creates or edits anything. It only reads.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GH = os.environ.get("GH", "gh")
DOCKER = os.environ.get("DOCKER", "docker")
KUBECTL = os.environ.get("KUBECTL", "kubectl")
GIT = os.environ.get("GIT", "git")


class CheckFailed(Exception):
    """A task did not pass. `hint` is printed underneath the failure."""

    def __init__(self, msg: str, hint: str | None = None):
        super().__init__(msg)
        self.hint = hint


class Skipped(Exception):
    """The task cannot be judged in this environment (and that is not your fault)."""


# --------------------------------------------------------------------------
# processes
# --------------------------------------------------------------------------

def sh(*args: str, timeout: int = 90, cwd: Path | None = None, stdin: str | None = None):
    """Run a command. Returns CompletedProcess; never raises on a non-zero exit."""
    try:
        return subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd or ROOT),
            input=stdin,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        raise CheckFailed(f"{args[0]} was not found on PATH",
                          hint="run `python checker.py doctor`") from None
    except subprocess.TimeoutExpired:
        raise CheckFailed(f"`{' '.join(args)}` timed out after {timeout}s") from None


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------

def git(*args: str, timeout: int = 60):
    return sh(GIT, *args, timeout=timeout)


def git_out(*args: str) -> str:
    p = git(*args)
    if p.returncode != 0:
        raise CheckFailed(f"`git {' '.join(args)}` failed",
                          hint=(p.stderr or p.stdout).strip()[:300])
    return p.stdout.strip()


@lru_cache(maxsize=1)
def repo_slug() -> str:
    """'owner/repo' taken from the origin remote of this working tree."""
    p = git("remote", "get-url", "origin")
    if p.returncode != 0:
        raise CheckFailed(
            "this folder has no git remote called 'origin'",
            hint="module 0x00: git init, then\n"
                 "      gh repo create <name> --public --source=. --remote=origin --push")
    url = p.stdout.strip()
    m = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?$", url)
    if not m:
        raise CheckFailed(f"origin does not look like a GitHub repo: {url}")
    return f"{m.group(1)}/{m.group(2)}"


def head_sha() -> str:
    return git_out("rev-parse", "HEAD")


def current_branch() -> str:
    return git_out("rev-parse", "--abbrev-ref", "HEAD")


def tracked_files() -> list[str]:
    return [f for f in git_out("ls-files").splitlines() if f]


def touched_in_history(pattern: str) -> bool:
    """Did any commit ever touch a path matching `pattern`?"""
    p = git("log", "--all", "--pretty=format:%H", "--", pattern)
    return bool(p.stdout.strip())


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------

_api_cache: dict[str, object] = {}


def gh(*args: str, timeout: int = 120):
    return sh(GH, *args, timeout=timeout)


@lru_cache(maxsize=1)
def gh_ready() -> bool:
    if not have(GH):
        return False
    return gh("auth", "status", timeout=45).returncode == 0


def need_gh():
    if not have(GH):
        raise CheckFailed(
            "the GitHub CLI (`gh`) is not installed",
            hint="winget install --id GitHub.cli   (then reopen your shell)")
    if not gh_ready():
        raise CheckFailed("`gh` is installed but not logged in", hint="gh auth login")


def api(path: str, *, raw: bool = False, paginate: bool = False,
        method: str = "GET", cache: bool = True, timeout: int = 120):
    """GET an API path. Returns parsed JSON (or text when raw=True), or None on 404.

    `path` may contain `{repo}`, which is filled in with owner/repo.
    """
    need_gh()
    path = path.format(repo=repo_slug())
    key = f"{method}:{path}:{raw}:{paginate}"
    if cache and key in _api_cache:
        return _api_cache[key]
    args = ["api", "-X", method, path]
    if paginate:
        args.append("--paginate")
    p = gh(*args, timeout=timeout)
    if p.returncode != 0:
        err = (p.stderr or "").strip()
        if "404" in err or "Not Found" in err:
            out = None
        elif "rate limit" in err.lower():
            raise CheckFailed("GitHub API rate limit reached",
                              hint="wait a minute, then run the checker again")
        elif "403" in err:
            raise CheckFailed(f"GitHub refused GET {path} (403)",
                              hint=err[:300] or "you may be missing an OAuth scope; try "
                                                "`gh auth refresh -s read:packages`")
        else:
            raise CheckFailed(f"GET {path} failed", hint=err[:300])
    elif raw:
        out = p.stdout
    else:
        try:
            out = json.loads(p.stdout)
        except json.JSONDecodeError:
            out = None
    if cache:
        _api_cache[key] = out
    return out


def repo() -> dict:
    r = api("repos/{repo}")
    if r is None:
        raise CheckFailed(f"GitHub has no repository {repo_slug()}",
                          hint="did you push it? `gh repo view --web` should open it")
    return r


def runs(*, workflow: str | None = None, branch: str | None = None,
         event: str | None = None, status: str | None = None,
         limit: int = 100) -> list[dict]:
    """Workflow runs, newest first. `workflow` is a file name like 'ci.yml'."""
    base = (f"repos/{{repo}}/actions/workflows/{workflow}/runs" if workflow
            else "repos/{repo}/actions/runs")
    q = [f"per_page={min(limit, 100)}"]
    if branch:
        q.append(f"branch={branch}")
    if event:
        q.append(f"event={event}")
    if status:
        q.append(f"status={status}")
    data = api(base + "?" + "&".join(q))
    if data is None:
        raise CheckFailed(
            f"GitHub does not know a workflow file called {workflow!r}",
            hint="push it to the default branch — GitHub only registers a workflow "
                 "once it has seen it there")
    return data.get("workflow_runs", []) or []


def latest_run(**kw) -> dict | None:
    got = runs(**kw)
    return got[0] if got else None


def need_run(*, workflow: str | None = None, conclusion: str | None = "success",
             branch: str | None = None, event: str | None = None,
             what: str | None = None) -> dict:
    """The newest run matching the filters, or a failure explaining what is missing."""
    got = [r for r in runs(workflow=workflow, branch=branch, event=event)
           if conclusion is None or r.get("conclusion") == conclusion]
    if not got:
        label = what or (f"a {conclusion or 'finished'} run of {workflow or 'any workflow'}"
                         + (f" on {branch}" if branch else "")
                         + (f" triggered by {event}" if event else ""))
        raise CheckFailed(f"could not find {label}", hint="gh run list --limit 20")
    return got[0]


def jobs_of(run_id: int) -> list[dict]:
    data = api(f"repos/{{repo}}/actions/runs/{run_id}/jobs?per_page=100")
    return (data or {}).get("jobs", []) or []


def job_named(run_id: int, name: str) -> dict | None:
    """A job whose name contains `name` (case-insensitive), matrix suffix and all."""
    for j in jobs_of(run_id):
        if name.lower() in (j.get("name") or "").lower():
            return j
    return None


def need_job(run_id: int, name: str) -> dict:
    j = job_named(run_id, name)
    if j is None:
        found = ", ".join(x.get("name", "?") for x in jobs_of(run_id)) or "none"
        raise CheckFailed(f"run {run_id} has no job matching {name!r}",
                          hint=f"jobs in that run: {found}")
    return j


def step_names(job_obj: dict) -> list[str]:
    return [s.get("name") or "" for s in (job_obj.get("steps") or [])]


def run_log(run_id: int) -> str:
    """The whole log of a run, as text. Cached — it is a big download."""
    key = f"log:{run_id}"
    if key in _api_cache:
        return str(_api_cache[key])
    p = gh("run", "view", str(run_id), "--log", timeout=300)
    text = p.stdout or ""
    if not text.strip():
        text = api(f"repos/{{repo}}/actions/runs/{run_id}/logs", raw=True, cache=False) or ""
    _api_cache[key] = text
    return text


def job_log(job_id: int) -> str:
    key = f"joblog:{job_id}"
    if key in _api_cache:
        return str(_api_cache[key])
    text = api(f"repos/{{repo}}/actions/jobs/{job_id}/logs", raw=True, cache=False) or ""
    _api_cache[key] = text
    return text


def artifacts_of(run_id: int) -> list[dict]:
    data = api(f"repos/{{repo}}/actions/runs/{run_id}/artifacts?per_page=100")
    return (data or {}).get("artifacts", []) or []


def caches() -> list[dict]:
    data = api("repos/{repo}/actions/caches?per_page=100")
    return (data or {}).get("actions_caches", []) or []


def secrets_names() -> list[str]:
    data = api("repos/{repo}/actions/secrets?per_page=100")
    return [s["name"] for s in (data or {}).get("secrets", []) or []]


def environments() -> list[dict]:
    data = api("repos/{repo}/environments")
    return (data or {}).get("environments", []) or []


def environment(name: str) -> dict | None:
    return api(f"repos/{{repo}}/environments/{name}")


def env_variables(name: str) -> list[dict]:
    data = api(f"repos/{{repo}}/environments/{name}/variables?per_page=100")
    return (data or {}).get("variables", []) or []


def deployments(environment_name: str | None = None) -> list[dict]:
    q = "?per_page=100" + (f"&environment={environment_name}" if environment_name else "")
    return api("repos/{repo}/deployments" + q) or []


def deployment_statuses(deployment_id: int) -> list[dict]:
    return api(f"repos/{{repo}}/deployments/{deployment_id}/statuses?per_page=100") or []


def releases() -> list[dict]:
    return api("repos/{repo}/releases?per_page=100") or []


def tags() -> list[dict]:
    return api("repos/{repo}/tags?per_page=100") or []


def pulls(state: str = "all") -> list[dict]:
    return api(f"repos/{{repo}}/pulls?state={state}&per_page=100") or []


def check_runs(sha: str) -> list[dict]:
    data = api(f"repos/{{repo}}/commits/{sha}/check-runs?per_page=100")
    return (data or {}).get("check_runs", []) or []


def branch_protection(branch: str = "main") -> dict | None:
    return api(f"repos/{{repo}}/branches/{branch}/protection")


def rulesets() -> list[dict]:
    return api("repos/{repo}/rulesets") or []


def self_hosted_runners() -> list[dict]:
    data = api("repos/{repo}/actions/runners?per_page=100")
    return (data or {}).get("runners", []) or []


def package_versions(package: str) -> list[dict]:
    """Versions of a GHCR container package owned by the repo owner."""
    owner = repo_slug().split("/")[0]
    for path in (f"users/{owner}/packages/container/{package}/versions",
                 f"orgs/{owner}/packages/container/{package}/versions"):
        try:
            data = api(path + "?per_page=100")
        except CheckFailed:
            continue
        if data:
            return data
    return []


def package_tags(package: str) -> dict[str, str]:
    """{tag: digest} for a GHCR package, read from the API."""
    out: dict[str, str] = {}
    for v in package_versions(package):
        digest = v.get("name", "")
        meta = ((v.get("metadata") or {}).get("container") or {})
        for tag in meta.get("tags", []) or []:
            out[tag] = digest
    return out


def ts(value: str | None) -> float:
    """An ISO-8601 GitHub timestamp as epoch seconds (0.0 if missing)."""
    if not value:
        return 0.0
    try:
        return time.mktime(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0.0


def duration(obj: dict) -> float:
    """Seconds between started_at/created_at and completed_at/updated_at."""
    start = ts(obj.get("started_at") or obj.get("created_at"))
    end = ts(obj.get("completed_at") or obj.get("updated_at"))
    return max(0.0, end - start)


# --------------------------------------------------------------------------
# YAML (workflows, compose files, configs)
# --------------------------------------------------------------------------

def _yaml():
    try:
        import yaml
    except ImportError:
        raise CheckFailed(
            "PyYAML is not installed — the checker needs it to read your workflows",
            hint="python -m pip install pyyaml") from None
    return yaml


def load_yaml(relpath: str):
    text = read_text(relpath)
    try:
        return _yaml().safe_load(text)
    except Exception as exc:  # noqa: BLE001 - any parse error belongs to the student
        raise CheckFailed(f"{relpath} is not valid YAML: {exc}") from None


def workflow_paths() -> list[Path]:
    d = ROOT / ".github" / "workflows"
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix in (".yml", ".yaml"))


def _workflow_path(name: str) -> Path:
    stem = name[:-4] if name.endswith(".yml") else (name[:-5] if name.endswith(".yaml") else name)
    for suffix in (".yml", ".yaml"):
        p = ROOT / ".github" / "workflows" / (stem + suffix)
        if p.exists():
            return p
    raise CheckFailed(f"missing workflow: .github/workflows/{stem}.yml",
                      hint="workflows only count when they live in .github/workflows/")


def workflow(name: str) -> dict:
    """Parse .github/workflows/<name>. Accepts 'ci' or 'ci.yml'."""
    p = _workflow_path(name)
    wf = load_yaml(str(p.relative_to(ROOT)).replace("\\", "/"))
    if not isinstance(wf, dict):
        raise CheckFailed(f"{p.name} does not parse to a mapping")
    return wf


def workflow_text(name: str) -> str:
    return _workflow_path(name).read_text(encoding="utf-8", errors="replace")


def triggers(wf: dict) -> dict:
    """The `on:` block. PyYAML reads a bare `on` as the boolean True — handle both."""
    for key in ("on", True, "On", "ON"):
        if key in wf:
            value = wf[key]
            if isinstance(value, str):
                return {value: None}
            if isinstance(value, list):
                return dict.fromkeys(value)
            if isinstance(value, dict):
                return value
    raise CheckFailed("the workflow has no `on:` block — nothing will ever trigger it")


def jobs(wf: dict) -> dict:
    js = wf.get("jobs")
    if not isinstance(js, dict) or not js:
        raise CheckFailed("the workflow has no `jobs:`")
    return js


def job(wf: dict, name: str) -> dict:
    js = jobs(wf)
    if name not in js:
        raise CheckFailed(f"no job called {name!r} — found: {', '.join(js)}")
    return js[name]


def needs_of(j: dict) -> list[str]:
    n = j.get("needs") or []
    return [n] if isinstance(n, str) else list(n)


def steps(j: dict) -> list[dict]:
    return [s for s in (j.get("steps") or []) if isinstance(s, dict)]


def uses_in(obj) -> list[str]:
    """Every `uses:` value anywhere inside a parsed workflow fragment."""
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "uses" and isinstance(v, str):
                found.append(v)
            else:
                found += uses_in(v)
    elif isinstance(obj, list):
        for item in obj:
            found += uses_in(item)
    return found


def run_scripts(obj) -> str:
    """Every `run:` script anywhere inside a fragment, concatenated."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "run" and isinstance(v, str):
                out.append(v)
            else:
                out.append(run_scripts(v))
    elif isinstance(obj, list):
        for item in obj:
            out.append(run_scripts(item))
    return "\n".join(x for x in out if x)


def step_using(j: dict, action: str) -> dict | None:
    for s in steps(j):
        if action.lower() in (s.get("uses") or "").lower():
            return s
    return None


# --------------------------------------------------------------------------
# docker
# --------------------------------------------------------------------------

def docker(*args: str, timeout: int = 180):
    return sh(DOCKER, *args, timeout=timeout)


def docker_up() -> bool:
    return have(DOCKER) and docker("info", timeout=60).returncode == 0


def need_docker():
    if not docker_up():
        raise Skipped("Docker is not running — start Docker Desktop and re-run")


def image_inspect(ref: str) -> dict | None:
    p = docker("image", "inspect", ref, timeout=90)
    if p.returncode != 0:
        return None
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None
    return data[0] if data else None


def remote_digest(ref: str) -> str | None:
    """The digest of a published image, without pulling it."""
    p = docker("buildx", "imagetools", "inspect", ref,
               "--format", "{{.Manifest.Digest}}", timeout=120)
    if p.returncode == 0:
        m = re.search(r"sha256:[0-9a-f]{64}", p.stdout)
        if m:
            return m.group(0)
    p = docker("manifest", "inspect", "--verbose", ref, timeout=120)
    if p.returncode != 0:
        return None
    m = re.search(r'"digest"\s*:\s*"(sha256:[0-9a-f]{64})"', p.stdout)
    return m.group(1) if m else None


def containers_running() -> list[dict]:
    p = docker("ps", "--format", "{{json .}}", timeout=60)
    out = []
    for line in (p.stdout or "").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


# --------------------------------------------------------------------------
# kubernetes (deploy modules only)
# --------------------------------------------------------------------------

def kubectl(*args: str, timeout: int = 60):
    return sh(KUBECTL, *args, timeout=timeout)


def cluster_up() -> bool:
    return have(KUBECTL) and kubectl("version", "-o", "json", timeout=30).returncode == 0


def need_cluster():
    if not cluster_up():
        raise Skipped("no Kubernetes cluster reachable — enable Docker Desktop's "
                      "Kubernetes or `kind create cluster`, then re-run")


def kget(kind: str, name: str, ns: str | None = None):
    args = ["get", kind, name, "-o", "json"]
    if ns:
        args += ["-n", ns]
    p = kubectl(*args)
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return None


def kget_list(kind: str, ns: str | None = None, selector: str | None = None) -> list:
    args = ["get", kind, "-o", "json"]
    if ns:
        args += ["-n", ns]
    if selector:
        args += ["-l", selector]
    p = kubectl(*args)
    if p.returncode != 0:
        return []
    try:
        return (json.loads(p.stdout) or {}).get("items", []) or []
    except json.JSONDecodeError:
        return []


def need_k8s_obj(obj, kind: str, name: str, ns: str | None = None):
    if obj is None:
        where = f" in namespace {ns}" if ns else ""
        raise CheckFailed(f"no {kind} named '{name}'{where}",
                          hint=f"kubectl get {kind} {name}" + (f" -n {ns}" if ns else ""))
    return obj


# --------------------------------------------------------------------------
# navigation and assertions
# --------------------------------------------------------------------------

def dig(obj, path: str, default=None):
    """dig(run, "head_commit.message") -> value or default. Ints index lists."""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return default
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return default
        elif isinstance(cur, dict):
            if part not in cur:
                return default
            cur = cur[part]
        else:
            return default
    return default if cur is None else cur


def need(cond, msg: str, hint: str | None = None):
    if not cond:
        raise CheckFailed(msg, hint)
    return cond


def need_eq(actual, expected, what: str):
    if actual != expected:
        raise CheckFailed(f"{what}: expected {expected!r}, got {actual!r}")


def need_in(needle: str, haystack: str, what: str, hint: str | None = None):
    if needle.lower() not in (haystack or "").lower():
        raise CheckFailed(f"{what}: could not find {needle!r}",
                          hint=hint or (haystack or "")[:300])


def need_match(pattern: str, text: str, what: str, hint: str | None = None):
    m = re.search(pattern, text or "", re.I | re.M)
    if not m:
        raise CheckFailed(f"{what}: nothing matched /{pattern}/", hint=hint)
    return m


def need_file(relpath: str) -> Path:
    p = ROOT / relpath
    if not p.exists():
        raise CheckFailed(f"missing file: {relpath}", hint=f"create {p}")
    return p


def read_text(relpath: str) -> str:
    return need_file(relpath).read_text(encoding="utf-8", errors="replace")


def section(text: str, number: int) -> str:
    """Pull `## <number>` ... up to the next `##` out of a markdown file."""
    m = re.search(rf"^##\s*{number}\b(.*?)(?=^##\s|\Z)", text, re.S | re.M)
    if not m:
        raise CheckFailed(f"no `## {number}` section found in that file")
    return m.group(1)


def words(text: str) -> int:
    return len((text or "").split())
