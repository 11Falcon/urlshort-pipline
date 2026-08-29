# 0x02 — The test gate

## Concept (read this once, ~8 minutes)

A test suite in CI is not there to tell you the code works. It is there to
**stop a change**. That is the only thing that separates a gate from a report,
and almost every failure mode in this module comes from a suite that quietly
stopped being able to stop anything.

Three ways that happens:

1. The failure is swallowed (`|| true`, `continue-on-error`) — drill 3.
2. The suite is too slow, so people stop waiting and merge anyway.
3. The suite is flaky, so a red result means "run it again" instead of "stop".

Note that only the first is a technical problem. The other two are social
problems that you fix with engineering.

### Speed is a correctness property

```
   0-30s      you keep looking at the screen
   30s-3m     you switch to another tab and come back
   3m-8m      you start another task; context is gone
   8m+        you merge on hope and read the result later, if at all
```

Past a few minutes a gate stops changing behaviour, and an unenforced gate is
just a slow way of finding out you were wrong. So splitting the suite is not
laziness, it is what keeps the gate real:

- **Pull-request gate**: everything fast and deterministic. Seconds.
- **Nightly / on-demand**: the slow, the expensive, the flaky-by-nature.

`tests/test_slow.py` in this repo is marked `slow` for exactly this reason. The
markers are declared in `pyproject.toml`, so `pytest -m "not slow"` and
`pytest -m slow` split the suite cleanly.

The trap: if the slow tests only ever run at 03:00 and nobody watches the
result, you have not split your suite, you have deleted half of it. Nightly runs
need to be dispatchable by hand and to shout when they fail.

### The matrix

```yaml
strategy:
  fail-fast: false
  matrix:
    python-version: ["3.11", "3.12"]
```

One job definition, N machines, in parallel. Two details people get wrong:

- **`fail-fast` defaults to `true`**, which cancels every other leg the moment
  one fails. That is right when the matrix is large and expensive; it is wrong
  when you need to know *whether the bug is version-specific*. "3.11 failed" and
  "3.11 failed but 3.12 passed" are completely different bug reports.
- The job name in the UI becomes `test (3.11)`. When you make a check
  **required** in module `0x0A`, you must name it exactly like that — which
  means changing your matrix silently breaks branch protection. Everyone learns
  this the hard way once.

### Coverage: a floor, not a target

Coverage measures which lines ran, not whether anything was asserted. It is
trivial to reach 95% with tests that assert nothing. So:

- Use it as a **ratchet**: a floor that fails the build when it drops.
  `--cov-fail-under=85` is a gate; a badge is a decoration.
- Do not chase 100%. The last 8% is usually error paths that cost more to
  simulate than they cost to get wrong.
- `branch = true` (already set in `pyproject.toml`) measures whether both sides
  of each `if` ran, which is a much better question than line coverage.

Coverage's real value in CI is directional: it tells you when a pull request
adds code without adding tests, at the moment it happens rather than a year
later.

### Keep the evidence, especially when it fails

The run that most needs its test report is the run that failed — and the naive
upload step never gets there, because a failed step ends the job:

```yaml
      - name: Publish results
        if: ${{ always() }}
        uses: actions/upload-artifact@v4
        with:
          name: junit-${{ matrix.python-version }}
          path: junit.xml
```

`if: always()` is the whole lesson. Also note the artifact **name must be
unique per matrix leg** — two legs uploading `junit` collide and the run fails
with a confusing error about an artifact that already exists.

### Flakes

A test that passes 95% of the time is worse than a test that fails always: it
teaches the team that red means nothing. When you find one, you have three
honest options — fix it, delete it, or quarantine it explicitly (mark it, run it
outside the gate, and give it a deadline). What is not an option is a rerun loop
that hides it. That converts a known flake into an unknown one.

The last task of this module asks for three green runs in a row on `main`, which
is the cheapest flake detector there is.

## You're done when you can answer these without looking

- Your suite takes 14 minutes. Name three things you would do before buying
  faster runners.
- Why is `fail-fast: false` usually right for a two-version matrix and usually
  wrong for a twelve-legged one?
- Coverage went from 86% to 84% in a pull request that added a feature. What
  actually happened, and is that a problem?
- The upload of your test report never runs on failing builds. Why?
- A test fails once a week in CI and never locally. Walk through what you do.

## General requirements

- Jobs named exactly **`test`** (and later `lint`, `typecheck`) — the checker
  looks for them by name, and so will branch protection in `0x0A`.
- Second workflow: **`.github/workflows/nightly.yml`**.
- Verify with `python checker.py 0x02`.

---

## Tasks

### 0. Prove the gate can close (mandatory)

On a branch, break an assertion in `tests/test_store.py` and push. Confirm that
the `test` job goes red and that the log shows the assertion failure, not some
unrelated setup error.

```bash
gh run view --log-failed
```

Then fix it. The checker looks through your failure history for a job named like
`test` that failed with a real test failure in it.

### 1. Make coverage a gate (mandatory)

Run the suite with coverage and a floor of at least 85:

```yaml
      - run: pytest -m "not slow" --cov=src/urlshort --cov-report=term --cov-fail-under=85
```

Watch what it prints. Then, out of curiosity, drop the floor to 100 once and see
what the failure looks like — that message is the one a colleague will hit.

### 2. Fan out across Python versions (mandatory)

Turn `test` into a matrix over at least `3.11` and `3.12`, with
`fail-fast: false`. After it runs, look at the Actions UI: you now have two
checks named `test (3.11)` and `test (3.12)`. Remember those names.

### 3. Keep the report, especially from failures (mandatory)

Produce a JUnit XML report and upload it with `if: ${{ always() }}` and a name
that includes the matrix value. Then make the suite fail again and confirm the
artifact is still there on the red run.

### 4. Split fast from complete (mandatory)

**File:** `.github/workflows/nightly.yml`

- `ci.yml` runs `pytest -m "not slow"`.
- `nightly.yml` runs the **whole** suite, on a `schedule:` (pick an hour) and on
  `workflow_dispatch:`.

Trigger it by hand once so it has a green run:

```bash
gh workflow run nightly.yml
gh run watch
```

The checker requires the nightly workflow to have both triggers, to *not* skip
the slow tests, and to have finished green at least once.

While you are here, think about who finds out when the nightly fails at 03:00.
There is no task for it, but the answer "nobody" is a common one and it is worth
noticing now.

### 5. Three green runs in a row on main (mandatory)

Push three commits (or re-run the workflow three times) and have all three of
the most recent finished runs on `main` conclude green.

If you cannot get three in a row, you have found a flake — and finding it is
worth more than anything else in this module. Common culprits in a suite like
this one: a test that depends on another test's leftover state (note the
`_clean_store` fixture in `tests/test_api.py` and ask yourself why it exists), a
timing assertion, or a matrix leg with a different dependency resolution.

```bash
python checker.py 0x02
```
