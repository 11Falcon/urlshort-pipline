# 0x00 — The repo, the runner, the first green run

## Concept (read this once, ~7 minutes)

Continuous integration is not a tool. It is a bet: that **small changes,
verified constantly, are cheaper than big changes verified rarely**. Everything
in this course — the caching, the matrices, the digests, the approvals — exists
to make that bet keep paying off as the change rate goes up.

The mechanism is embarrassingly simple:

```
   you push        GitHub sees an event        a machine appears
   ─────────►  ─────────────────────────►  ────────────────────────►
                 (push, pull_request,          fresh VM, 2 cores,
                  schedule, tag, manual)       nothing of yours on it
                                                       │
                                                       ▼
                                            it clones your repo,
                                            runs your steps in order,
                                            reports pass or fail,
                                            and is destroyed
```

That last line is the important one. **The runner is thrown away.** Nothing you
install persists, nothing you cached is there unless you explicitly restored it,
and nothing about "it works on my machine" transfers. This is the feature, not
the cost: a fresh machine is the only honest test of whether your build
instructions are complete.

### The four nouns

```
workflow            one file in .github/workflows/, one `on:` block
  └── job           runs on its own machine; jobs are parallel by default
        └── step    runs in order, on that machine; a `run:` or a `uses:`
              └── action   someone else's step, pulled from a repo
```

A job is a machine. Two jobs share nothing — not a filesystem, not an installed
package, not a variable — unless you pass it explicitly. This trips up everyone
once: you `pip install` in one job and the next job cannot find it. That is not
a bug, it is the boundary doing its job.

### Triggers, and the one that catches everybody

`on:` decides when the workflow exists at all:

```yaml
on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:        # a button in the UI
  schedule:
    - cron: "0 3 * * *"
```

Two rules worth memorising now, because they cost people entire afternoons:

1. **A workflow is registered only once GitHub has seen it on the default
   branch.** You can add `ci.yml` on a branch and open a pull request and it
   will run — but `gh workflow list` and the "run workflow" button will not know
   it exists until it lands on `main`.
2. `pull_request` runs against a **merge commit** of your branch and the base,
   not against your branch. So CI can pass on your branch and fail after merge
   if `main` moved. That is `pull_request` doing you a favour.

Note also that `on` is a YAML landmine: unquoted `on` parses as the boolean
`true` in YAML 1.1. GitHub handles it, but your own tooling might not. (The
checker handles both, because it had to.)

### What "green" actually means

A step fails when its process exits non-zero. That is the entire contract. A job
fails when a step fails. A run fails when a job fails. Every gate you build in
this course is ultimately one command returning 1 instead of 0 — which is why
`|| true` is the most destructive two-word sequence in delivery engineering, and
why module `0x0B` devotes a whole drill to it.

### Why you should break it today

A pipeline you have never seen fail is not a safety net; it is decoration you
have not tested. The first thing you should do after your first green run is
make it red on purpose and read the failure the way you will read it in six
months: from the Actions tab, at speed, looking for the one line that matters.

The last task of this module makes you do exactly that.

## You're done when you can answer these without looking

- You added `ci.yml` on a branch. Why does `gh workflow run ci.yml` say it does
  not exist?
- Job A installs a package. Job B cannot import it. What happened?
- Your workflow passes on your branch and fails on `main` five minutes later.
  Name two mechanisms that produce this.
- What exactly makes a step "fail"?
- Where would you look first for a run that produced no output at all?

## General requirements

- A **public** GitHub repository whose default branch is `main`, with this
  folder pushed to it.
- Workflow file: **`.github/workflows/ci.yml`**.
- Verify with `python checker.py 0x00`.

---

## Tasks

### 0. Put this on GitHub (mandatory)

From this folder:

```bash
git init -b main
git add .
git commit -m "chore: import the course skeleton"
gh repo create urlshort-pipeline --public --source=. --remote=origin --push
```

Public matters: Actions minutes are free on public repositories, and so is
branch protection, which module `0x0A` needs. The checker verifies the repo
exists, is public, defaults to `main`, and that your local `HEAD` is on it.

Confirm the whole picture with:

```bash
python checker.py doctor
```

### 1. The smallest workflow that is still worth having (mandatory)

**File:** `.github/workflows/ci.yml`

Give it a `name:`, and make it trigger on pushes to `main` **and** on pull
requests. One job is fine for now — install Python, install the dependencies,
run the tests:

```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements-dev.txt && pip install -e .
      - run: pytest -m "not slow"
```

Push it to `main`. Then check that GitHub has actually registered it:

```bash
gh workflow list
gh run list --limit 5
```

If `gh workflow list` is empty, re-read the first rule in the concept section.

### 2. Get it green (mandatory)

Watch the run happen from the terminal — you will use this constantly:

```bash
gh run watch
gh run view --log
```

The checker wants the newest run on `main` to have concluded `success`.

### 3. Make the run say what it built (mandatory)

Add a step that prints the commit under test, for example:

```yaml
      - name: Context
        run: |
          echo "building ${{ github.sha }} on ${{ github.ref }}"
          echo "runner: ${{ runner.os }}"
```

This looks like a triviality. It is not. The single most common question asked
of a pipeline is *"is my fix in this build?"*, and the fastest answer is a log
line with a commit SHA in it. The checker downloads the log of your newest green
run and looks for that run's own commit in it.

### 4. Break it on purpose, and write down what you saw (mandatory)

**File:** `0x00-first-run/notes.md`

On a branch, break something real — an assertion in `tests/test_store.py`, or
the install command — and push it. Watch it go red. Then:

1. Find the failure from `gh run list` alone, without opening a browser.
2. Get to the failing line with `gh run view <id> --log-failed`.
3. Fix it, push, and let `main` go green again.

Write at least 80 words in `notes.md`: what you broke, what the failure looked
like from the outside, which command showed you the actual error, and how long
the whole loop took. That last number is the thing this entire course is trying
to shrink.

The checker wants: at least one failed run in the history, the newest finished
run on `main` green, and your notes.

```bash
python checker.py 0x00
```
