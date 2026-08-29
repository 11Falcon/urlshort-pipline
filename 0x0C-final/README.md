# 0x0C — Final project: the whole pipeline

## What this is

Everything you built exists, but it grew module by module. This module is where
it becomes one thing that a team could actually use: a commit that walks all the
way from a pull request to production, with every gate in place, and a runbook
that lets somebody who is not you handle the night it goes wrong.

There is very little new material here. There is a lot of joining up.

```
   pull request
        │
        ├── lint ──────────┐
        ├── typecheck ─────┤
        ├── test (3.11) ───┼── all required to merge
        ├── test (3.12) ───┤
        └── security ──────┘
        │
   merge to main
        │
        ├── build ──► wheel + image ──► SBOM + provenance ──► GHCR
        │                   │
        │                   └── digest ──────────────┐
        │                                            ▼
        └── deploy: staging (auto) ──► smoke ──► [ok] ──► recorded deployment
                          │
                          └── [fail] ──► rollback ──► red run, nobody paged
        │
   tag v1.1.0
        │
        └── release: notes + assets ──► promote the SAME digest to :v1.1.0
                          │
                          └── deploy: production ──► APPROVAL ──► canary ──►
                                        smoke ──► widen, or roll back
```

The property that has to hold all the way across that picture: **one build, one
digest, promoted**. If at any point something gets rebuilt, the thing in
production is not the thing that was tested, and every gate to the left of that
point was theatre.

## Requirements

- Everything stays green in the earlier modules. Running the whole checker
  (`python checker.py`) is the real exit criterion; the tasks below are the new
  work.
- Files: **`0x0C-final/RUNBOOK.md`**, **`0x0C-final/report.md`**.
- A second release, **`v1.1.0`** or higher, cut through the finished pipeline.
- Verify with `python checker.py 0x0C`.

---

## Tasks

### 0. One pipeline, all the gates, on every pull request (mandatory)

`ci.yml` must have jobs named like `lint`, `typecheck`, `test` and `build`, and
must run on `pull_request`. Open a pull request and confirm at least four
distinct checks report on it.

This is also the moment to re-check your required checks in the branch
protection: the names have probably drifted since `0x0A`.

### 1. The thing deployed is the thing that was tested (mandatory)

The `api` Deployment in namespace `cicd` must run an image **by digest**, that
digest must be one GitHub actually published for your package, and the
Deployment must record the commit it came from.

Trace it yourself, once, end to end — this is the single most valuable five
minutes in the course:

```bash
kubectl -n cicd get deploy api -o jsonpath='{.spec.template.spec.containers[0].image}'
kubectl -n cicd get deploy api -o jsonpath='{.metadata.annotations}'
gh api users/{owner}/packages/container/urlshort/versions --jq '.[].name' | head
gh run view <the run in the annotation>
```

Pod → digest → run → commit → pull request → review. If any link in that chain
is missing, an incident becomes an investigation.

### 2. The supply chain holds end to end (mandatory)

In the final state of the repository: every action pinned to a SHA, secret
scanning, dependency scanning, Dockerfile linting, an SBOM, provenance, and a
base image pinned by digest.

Nothing new — but things drift as you refactor, and the point of this task is to
notice that they did.

### 3. Merging to main deploys to staging, by itself (mandatory)

No manual step. The newest staging deployment's commit must equal the current
`main`.

If it does not, one of two things is true: the deploy did not fire, or somebody
(you) deployed by hand. Both are worth knowing, and both are what this check is
for.

### 4. A release goes out, and production waits for a person (mandatory)

Cut `v1.1.0` (or higher) through the finished pipeline: tag → release with notes
and assets → promote the digest → deploy to `production`, gated by the required
reviewer.

Approve it, and watch the deployment record appear. Then write down — in the
runbook — who is allowed to approve, and what they are actually supposed to
check before they do.

### 5. Production deploys are survivable (mandatory)

`deploy.yml` keeps its smoke test, its `if: failure()` path, and its rollback.
The cluster should still show a canary (or the blue/green pair) standing, so the
end state is a cluster you could genuinely ship to rather than a museum of
finished exercises.

### 6. Write the runbook (mandatory)

**File:** `0x0C-final/RUNBOOK.md`, 300+ words, with **literal commands**.

Write it for a colleague at 03:00 who has never seen your pipeline. It must
cover at least:

- **What is running right now** — how to find the version, the digest, and the
  commit in staging and in production.
- **How to deploy** — the normal path, and how to tell it worked.
- **How to roll back** — the exact command, how long it takes, and what it does
  *not* undo.
- **How to read a failure** — where the logs are, `gh run view --log-failed`,
  which failures are safe to re-run and which are not.
- **Who approves production**, and what they check.
- **What to do if the pipeline itself is broken** — you cannot ship the fix
  through the thing that is broken. What is the escape hatch, and who is allowed
  to use it?

That last question is the one most runbooks skip, and it is the one you will
need.

### 7. Report on your own delivery (mandatory)

**File:** `0x0C-final/report.md`, 250+ words.

Run your `0x0A` metrics script and quote the numbers. Then:

- what the four numbers say about this pipeline, honestly;
- at least one improvement you actually made and the before/after number
  (pipeline duration, image size, rollback time — you have been writing these in
  `PROGRESS.md`);
- the biggest remaining risk, and what you would build next if this were a real
  service with real users.

Good answers to that last one usually are not more pipeline. They are the things
this course could not give you: an alert that pages a human when the canary's
error rate rises, a staging environment with realistic data, database migrations
that can be rolled back, and someone other than you who knows how it works.

```bash
python checker.py 0x0C
python checker.py          # all 77
```

## Afterwards

Two things worth doing once the checker is green:

1. Delete your self-hosted runner (`./config.cmd remove --token …`) or stop it.
   It is a standing execution surface on your machine.
2. Read your own `ci.yml` top to bottom and ask which parts you would keep if
   you started again. The answer is usually "fewer, and faster" — which is the
   direction every pipeline should be moved in, forever.
