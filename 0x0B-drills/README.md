# 0x0B — Debugging drills

## Concept (read this once, ~5 minutes)

Six broken pipelines. They are in `0x0B-drills/broken/`, and each one is a
failure that is common, cheap to cause, and expensive to find. Four of them
produce **no error message at all** — they are wrong in a way that looks fine,
which is what makes them worth practising.

### How to debug a pipeline, in order

Most people start by reading the YAML. That is the fourth step, not the first.

1. **Did it run?** `gh run list --workflow drill-4.yml`. If nothing is there,
   the problem is the trigger, or the file is not on the default branch, or the
   path filter excluded your change. No amount of staring at the job will help.
2. **Which job, which step?** `gh run view <id>` shows the shape.
   `gh run view <id> --log-failed` goes straight to the failing step's output.
3. **What did the machine see?** Not what you meant — what it got. Echo the
   variable. Print the resolved image reference. `env | sort`. Most workflow
   bugs are a value being empty, or a string being a different string.
4. **Only now**, read the YAML — and read it as the runner does: contexts are
   substituted *before* the shell runs, jobs share nothing, and a step's success
   is only its exit code.

Two tools worth having:

```bash
gh run view <id> --log            # everything
gh run rerun <id> --failed        # re-run only what failed
gh run rerun <id> --debug         # step debug logging
```

`--debug` (or setting the `ACTIONS_STEP_DEBUG` secret to `true`) turns on the
internal logging of the actions themselves, which is how you find out what a
cache key resolved to or why an artifact was not found.

### The important habit

Write down the diagnosis before the fix. If you cannot say *what was wrong* in
one sentence, you do not have a fix, you have a change that made the symptom go
away — and those come back with interest.

## General requirements

- Copy each drill from `0x0B-drills/broken/` into `.github/workflows/`, keeping
  the name (`drill-1.yml`, …).
- **Pin the actions as you copy them.** The rule from `0x05` applies to every
  workflow in this repository, so the checker's pinning task will fail while a
  drill uses `@v4`. Drill 6 will also turn `0x05` task 4 red for as long as it
  is unfixed — it uses `pull_request_target`, which is precisely the thing that
  task forbids. That is not a bug in the checker; it is the point.
- Diagnoses go in **`0x0B-drills/diagnosis.md`**, one `## N` section per drill,
  at least 40 words each. Say what was wrong and how you proved it — the fix is
  in the file already.
- Verify with `python checker.py 0x0B`.

Do not delete a drill to make it pass. If a drill genuinely cannot run in your
setup, say so in the diagnosis and move on; the checker will stay red on that
one, and an honest red beats a fake green.

---

## Tasks

### 1. The cache that never hit (mandatory)

**Symptom.** Every run reinstalls everything. The cache step is green every
time. The "Post" step dutifully saves an entry after each run.

Make the cache actually restore, then run it twice — the second run is the
evidence. The checker wants a restore in the newest run's log, and it wants your
diagnosis to name the thing that was wrong.

```bash
gh api repos/:owner/:repo/actions/caches --jq '.actions_caches[] | {key, size_in_bytes}'
```

Look at that list before you fix anything. It tells the whole story.

### 2. The artifact that was not there yet (mandatory)

**Symptom.** `Unable to find any artifacts for the associated workflow run` —
usually. Occasionally the run passes, which should worry you more than the
failures do.

Ask yourself what decides the order of two jobs, and what an intermittent
failure implies about the current answer.

### 3. The green pipeline that tested nothing (mandatory)

**Symptom.** None. It has been green for three months and the team trusts it.

Read it as the runner does: which of these steps *can* fail? Fix it so a broken
test turns the run red, then prove it by breaking one — the checker requires a
failed run of `drill-3.yml` in the history as well as a green one now.

This drill is the reason `continue-on-error` is on this course's list of things
you must justify in a comment when you use.

### 4. The workflow that never ran (mandatory)

**Symptom.** A colleague added it last week. The Actions tab has never shown a
single run. No errors anywhere.

There are three separate reasons in this file. Find all of them — the checker
requires the workflow to have produced at least one run, and that run to be
green.

### 5. Two deploys at once (mandatory)

**Symptom.** When two commits land close together, the *older* commit sometimes
ends up live. Both runs are green and both logs look perfect.

Reproduce it first: push twice within about twenty seconds and watch what
happens to `live.txt`. Then fix it, and decide deliberately whether the older
run should be cancelled or should queue — and say which you chose, and why, in
the diagnosis.

The checker wants evidence: a run of `drill-5.yml` that was cancelled or queued
behind another.

### 6. The workflow that handed secrets to a stranger (mandatory)

**Symptom.** None. It works perfectly. It also means anyone who opens a pull
request can read your repository secrets and write to your repository.

Fix the trigger, the permissions, and the way the secret reaches the command.
The result must still do something useful on pull requests — a check that runs a
contributor's tests is a reasonable thing to want; the question is what it is
allowed to hold while it does that.

Since the fixed workflow triggers on `pull_request`, you will need to open one
to get a green run.

```bash
python checker.py 0x0B
```
