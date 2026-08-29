# 0x03 — Static analysis, and the local mirror

## Concept (read this once, ~7 minutes)

Tests tell you about the code you thought to exercise. Static analysis tells you
about all of it, in a second, without running anything. Three different tools
that people constantly confuse:

| Tool | Question it answers | Failure means |
|------|--------------------|----------------|
| **Formatter** (`ruff format`) | does it *look* like our code? | someone did not run it |
| **Linter** (`ruff check`) | is this a known bad pattern? | probably a real smell, occasionally a false positive |
| **Type checker** (`mypy`) | do the pieces fit together? | a real bug, or a missing annotation |

### Formatting is not a matter of opinion, it is a matter of diff noise

The value of a formatter has almost nothing to do with beauty. It is that a diff
contains only semantic change. A pull request where 200 lines moved because
somebody's editor reflowed them is a pull request nobody reviews properly.

So in CI, a formatter must **check**, never **fix**:

```yaml
- run: ruff format --check .        # exits 1 and shows what would change
```

A pipeline that reformats and commits back looks helpful and is a slow disaster:
it creates commits nobody wrote, it fights the developer's next push, and on a
protected branch it needs write access — which means every workflow in your repo
now has write access. Check in CI; fix locally.

### The gap that eats afternoons

```
   your machine                       CI
   ruff 0.6.9                         ruff 0.9.2
   "clean!"                           "17 errors"
```

This is the single most common frustration with linting, and it has one cause:
**two versions of the tool**. The fix is to pin the tool exactly, in both places,
and to have one source of truth for what "both places" means:

- `requirements.lock` pins the version CI installs.
- `.pre-commit-config.yaml` pins the version your machine runs.
- These two numbers must be **the same number**.

That is what task 2 checks, and it is not pedantry — a hook that disagrees with
the gate trains people to bypass the hook.

### pre-commit, briefly

`pre-commit` installs a git hook that runs your checks on staged files before
the commit exists:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

```bash
pip install pre-commit
pre-commit install          # the hook
pre-commit run --all-files  # what CI will see
```

Then run the same command in CI. Not "the same tools" — *the same command*. If
the hook and the gate can disagree, they eventually will, and the argument
always happens inside somebody's pull request at 18:00 on a Friday.

### Types, and the honest version of "we have mypy"

Non-strict mypy on a codebase without annotations passes by saying nothing: no
annotations means no types means nothing to contradict. `strict = true` is what
turns it into a gate, because it makes an unannotated function an error rather
than a shrug.

On an existing codebase you do not turn strict on globally on day one; you turn
it on per-package and expand. Here it is already on for `src/urlshort`, which is
small enough to keep clean.

### Findings belong on the diff

There is a protocol for this. When a step writes a line like

```
::error file=src/urlshort/store.py,line=42,col=5::Undefined name 'foo'
```

GitHub attaches that message to that line, in the Files-changed tab of the pull
request. Ruff emits it for you:

```yaml
- run: ruff check --output-format=github .
```

The difference in practice is enormous. A log a reviewer has to go find is
advice; a red squiggle on line 42 of the diff is feedback. Same information,
different distance.

### Run it in parallel — nothing about linting needs a test result

```
        ┌── lint ──────┐
push ───┼── typecheck ─┼──► build
        └── test ──────┘
```

Three jobs, three machines, one wall-clock cost. Chaining them (`lint` →
`test`) turns a 3-minute pipeline into a 7-minute one and buys you exactly
nothing, because the linter's opinion does not depend on whether the tests
passed. The checker verifies this by looking at when your jobs actually started
and finished, not by reading your YAML — a `needs:` edge is not the only way to
accidentally serialise a pipeline (a too-coarse concurrency group will do it
too, as you will see in `0x06`).

## You're done when you can answer these without looking

- Why must CI check formatting rather than fix it?
- Your machine says clean, CI says 17 errors, and neither is lying. What is
  wrong and where do you fix it?
- What does `strict = true` change about what mypy can tell you?
- What is the practical difference between a lint failure in a log and the same
  failure as an annotation?
- Two jobs have no `needs:` between them and still run one after another. Give
  two explanations.

## General requirements

- Jobs named **`lint`** and **`typecheck`** in `ci.yml`, neither depending on
  the other or on `test`.
- File: **`.pre-commit-config.yaml`**.
- Verify with `python checker.py 0x03`.

---

## Tasks

### 0. Lint and format, as a gate (mandatory)

Add a `lint` job that runs both:

```yaml
      - run: ruff check --output-format=github .
      - run: ruff format --check .
```

Get it green. If ruff complains about the course files, remember that
`pyproject.toml` already excludes `checker.py` and `ci_kit.py` — everything else
is yours to keep clean.

### 1. Types, strictly (mandatory)

Add a `typecheck` job that runs `mypy`. The configuration in `pyproject.toml`
already points it at `src/urlshort` with `strict = true`; do not weaken it.

If you hit an error you cannot resolve, resist `# type: ignore` without a
reason. `# type: ignore[arg-type]  # fastapi returns Any here` is a note to the
next person; a bare ignore is a hole with no name.

### 2. Make local and CI the same thing (mandatory)

**File:** `.pre-commit-config.yaml`

Add ruff and ruff-format hooks with an explicit `rev:`, install the hook
locally, and add a step to a workflow that runs:

```yaml
      - run: pip install pre-commit && pre-commit run --all-files
```

Then make the versions agree: the ruff version pinned in `.pre-commit-config.yaml`
must be the ruff version in `requirements.lock`. The checker compares them,
because this is exactly the drift that makes people disable hooks.

Prove the hook works: stage a badly formatted file and try to commit it.

### 3. Run the gates in parallel (mandatory)

`lint`, `typecheck` and `test` must not wait on each other. Push, then look at
the run's timing:

```bash
gh run view <id> --json jobs --jq '.jobs[] | {name, startedAt, completedAt}'
```

The checker asserts that `lint` and `test` genuinely overlapped in wall-clock
time. If they did not, something is serialising them.

### 4. Put the findings on the diff (mandatory)

You already added `--output-format=github`. Now see it work: on a branch,
introduce a lint error (an unused import will do), push, open a pull request,
and look at the **Files changed** tab. The finding should be sitting on the
line.

The checker requires a lint job that has failed at some point, with `::error`
lines in its log. Leave the branch red or fix it — either way, the history is
what it reads.

```bash
python checker.py 0x03
```
