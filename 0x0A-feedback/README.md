# 0x0A — The feedback loop: gates, protection, metrics

## Concept (read this once, ~8 minutes)

Everything you have built so far is **advisory**. There is nothing stopping you,
or anyone else, from pushing straight to `main` at 18:55 on a Friday with a red
pipeline. A gate that depends on everyone choosing to walk through it is not a
gate; it is a sign.

This module makes the pipeline binding, and then measures whether it is helping.

### Protection: the rules a repository enforces on its own

```
   push to main ────► rejected
   pull request ────► checks must pass ──► review must approve ──► merge
```

GitHub has two mechanisms that do this. **Branch protection** is the older,
per-branch one. **Rulesets** are the newer one: named, layerable, applicable to
patterns, with a bypass list and an evaluate-only mode. Either satisfies this
module; rulesets are where the platform is going.

The settings that matter, and why:

| Setting | Prevents |
|---|---|
| Require a pull request | the 18:55 Friday push |
| Require status checks | merging red |
| Require branches up to date | passing against a stale base (at the cost of re-running) |
| Block force pushes | rewriting history others have pulled |
| Require review / CODEOWNERS | one person changing something alone |
| Include administrators | you, in a hurry, being the exception |

### The gotcha with required checks: they are named after jobs

You require a check called `test`. Later you add a matrix and the checks become
`test (3.11)` and `test (3.12)`. The check named `test` now never reports —
and a required check that never reports leaves every pull request **pending
forever**. Same failure mode if you add a path filter that skips the job on
docs-only changes and skipping is not producing a check result.

Two habits avoid this:

- require the names you actually see in the Checks tab, and re-check them after
  you change a matrix;
- prefer a filter job that *skips* the heavy job (skipped counts as success)
  over a trigger-level filter that stops the run existing at all.

### CODEOWNERS

```
*                    @you
/.github/workflows/  @you @someone-who-cares-about-supply-chain
```

CODEOWNERS turns "someone should look at pipeline changes" into an automatic
review request. On a solo project it looks silly. It is still the right place to
express *which parts of this repository need a second pair of eyes* — and
workflow files, which can exfiltrate every secret you have, are the obvious
first entry.

### Merge hygiene

Squash, merge commit, rebase — pick one and let the button enforce it. This
course suggests squash: one pull request becomes one commit on `main`, which
makes `main` a list of changes rather than a list of keystrokes, and makes
`git revert` of a whole feature a single operation. Turn on "automatically
delete head branches" unless you enjoy a branch list with 300 entries.

### Measuring the thing you built

Four numbers (from the DORA research) describe delivery performance. They are
useful because they trade off against each other; optimising one alone is
visible in the others.

| Metric | Question | Computed here from |
|---|---|---|
| **Deployment frequency** | how often do you ship? | deployment records |
| **Lead time for changes** | commit → running in production | commit time → deployment time |
| **Change failure rate** | what fraction of deploys need a fix or rollback? | failed deploys / all deploys |
| **Time to restore** | how long from broken to fixed? | failure → next success |

Two warnings, because these numbers are easy to abuse:

- **They describe a system, not a person.** Used to compare individuals they
  become a target, and a metric that is a target stops measuring anything.
- **They only mean something together.** Deployment frequency alone rewards
  shipping garbage quickly; change failure rate alone rewards never shipping.

Your own history is the right data set to start from, and you already have it:
GitHub's API knows every run, every deployment, and every commit's timestamp.

```bash
gh api repos/{owner}/{repo}/deployments --jq '.[] | {environment, sha, created_at}'
gh api "repos/{owner}/{repo}/actions/workflows/deploy.yml/runs?per_page=100" \
  --jq '.workflow_runs[] | {conclusion, created_at, updated_at}'
```

### The number you will care about most

Median pipeline duration. Not because it is a DORA metric — it is not — but
because it sets the pace of everything else. A 4-minute gate gets waited for; a
20-minute gate gets worked around, and every workaround you have read about in
this course (merging red, `continue-on-error`, deploying by hand) starts as a
reasonable response to waiting too long.

## You're done when you can answer these without looking

- Every pull request is stuck "waiting for status to be reported". Give two
  causes.
- What breaks when you add a matrix to a job whose check is required?
- Why is "skipped" a better outcome than "no run" for a required check?
- Change failure rate went down and lead time went up. What probably happened?
- Which of the four DORA metrics can you improve without changing anything about
  how you write code?

## General requirements

- Protection on `main` — branch protection or a ruleset.
- Files: **`.github/pull_request_template.md`**, **`CODEOWNERS`** (root or
  `.github/`), **`0x0A-feedback/metrics.py`**.
- Verify with `python checker.py 0x0A`.

---

## Tasks

### 0. Make main unpushable (mandatory)

Require a pull request, require your status checks (at minimum a `test` one),
and block force pushes. UI, or:

```bash
gh api -X PUT repos/{owner}/{repo}/branches/main/protection \
  -F required_status_checks.strict=true \
  -f 'required_status_checks.contexts[]=test (3.11)' \
  -F enforce_admins=true \
  -F required_pull_request_reviews.required_approving_review_count=0 \
  -F restrictions=
```

Get the check names right — copy them out of a recent run's Checks tab. Then try
`git push origin main` and enjoy being refused by your own repository.

> On a private repository this needs GitHub Pro. Make the repo public (module
> `0x00` asked you to) or accept a red task here.

### 1. Get blocked, then merge (mandatory)

Open a pull request that fails a check. Watch the merge button stay disabled.
Fix it on the same branch, watch the check turn green on the same head commit,
then merge.

The checker looks for exactly that: a merged pull request whose head commit has
both a failed and a successful run of the same check. That shape — told no,
fixed, told yes — is the loop this whole course exists to make fast.

### 2. Merge hygiene (mandatory)

- Enable squash merging; disable at least one of the other two.
- Enable "automatically delete head branches".
- Add `.github/pull_request_template.md` — what changed, why, how it was
  verified, how to roll it back.
- Add `CODEOWNERS` with at least one rule. Include `/.github/workflows/`.

### 3. Make the gate fast enough that people wait for it (mandatory)

The checker takes the median duration of your recent green runs and requires it
to be **8 minutes or less**.

If you are over, in rough order of payoff: check the cache is hitting
(`0x01`), make sure the jobs are actually parallel (`0x03`), move slow tests to
nightly (`0x02`), reuse the image layer cache (`0x04`), skip heavy jobs for
docs-only changes (`0x06`).

Write the before and after numbers in `PROGRESS.md`.

### 4. Measure your own delivery (mandatory)

**File:** `0x0A-feedback/metrics.py`

Write a script that queries the GitHub API — through `gh api` in a subprocess,
or `urllib` with a token — and prints your four DORA numbers over the last 30
days:

```
deployment frequency : 2.3 per day (69 deployments)
lead time            : 42.5 minutes (median commit -> deployed)
change failure rate  : 12.5 % (3 of 24 deploys failed)
time to restore      : 18.0 minutes (median failure -> next success)
```

It must actually query GitHub (the checker rejects hardcoded numbers), exit 0,
and print each of the four labels with a number. Keep it simple: use `deploy.yml`
runs and the `deployments` API, define "failure" as a deploy run that concluded
`failure`, and "restore" as the gap to the next successful one.

Then read your own numbers and be honest about what they say. You will use them
again in the final module.

```bash
python checker.py 0x0A
```
