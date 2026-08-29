# 0x06 — Pipeline architecture: graph, reuse, concurrency

## Concept (read this once, ~8 minutes)

Your pipeline is a program. It has control flow, data flow, functions, and a
concurrency model. It is also a program that nobody refactors, that everybody
copies from, and that runs thousands of times a month. Treat it like code.

### The graph

`needs:` is the only ordering primitive. Everything without it starts at once.

```
   ┌── lint ──────┐
   │              │
   ├── typecheck ─┼──► build ──► image ──► deploy
   │              │      ▲
   └── test ──────┘      │
                    fan-in: build waits for all three
```

Two shapes to aim for:

- **Fan out early.** Anything that can fail independently should fail
  independently, in parallel. Your wall-clock time is the longest path, not the
  sum.
- **Fan in before you ship.** One job that depends on all the gates is what
  makes "everything passed" a single, checkable fact.

The anti-pattern is the ladder: `lint → test → build → deploy`, each waiting for
the last. It feels tidy and costs you the sum of all four every single time.

Two details worth knowing before they bite:

- A skipped or failed dependency skips the dependents. If you want a job to run
  anyway, `if: ${{ always() }}` — and then handle the failure case explicitly,
  or you have built a job that runs on the wreckage of everything else.
- `needs:` is also how you get data between jobs, which is the next section.

### Functions: composite actions and reusable workflows

Two different tools that people reach for interchangeably and should not.

| | **Composite action** | **Reusable workflow** |
|---|---|---|
| Is | a bundle of *steps* | a bundle of *jobs* |
| Runs on | the caller's machine | its own machines |
| Good for | "checkout, set up Python, restore cache" | "the whole quality gate" |
| Lives in | `.github/actions/<name>/action.yml` | `.github/workflows/<name>.yml` |
| Called with | `uses: ./.github/actions/setup` | `uses: ./.github/workflows/x.yml` at the *job* level |
| Can | not define jobs, matrices, or `runs-on` | define everything, including its own matrix |

The rule of thumb: if you are copying five steps into three jobs, that is a
composite action. If you are copying a whole job into four repositories, that is
a reusable workflow.

A composite action needs `shell:` on every `run:` step — it has no default,
which is the single most common error the first time you write one.

A reusable workflow declares its interface:

```yaml
on:
  workflow_call:
    inputs:
      python-version:
        type: string
        default: "3.11"
    secrets:
      token:
        required: false
```

and is called at the job level:

```yaml
jobs:
  quality:
    uses: ./.github/workflows/reusable-quality.yml
    with:
      python-version: "3.12"
```

In the run's job list its jobs appear as `quality / test` — caller job, slash,
callee job. That naming is how you can tell, from the outside, that reuse
actually happened.

### Concurrency: the one that silently corrupts things

By default, five pushes start five runs, and all five race. For a test suite
that is just waste. For a **deploy** it is a correctness bug: run A and run B
both deploy, B finishes first, A finishes second, and production ends up on the
older commit. The logs of both runs look perfect.

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true      # CI: kill the stale run
```

```yaml
concurrency:
  group: deploy-production
  cancel-in-progress: false     # deploys: queue, never abandon halfway
```

Two settings, two completely different intentions:

- **CI on a branch** — cancel. Nobody wants the result for a commit that has
  already been replaced.
- **Deploys** — queue. Cancelling a deploy halfway leaves the target in a state
  nobody designed.

Choose the group carefully. Too narrow (`github.sha`) and nothing ever collides,
so the setting does nothing. Too broad (a constant, for CI) and one busy branch
blocks the whole team — which is one of the ways two jobs with no `needs:`
between them still end up serialised, as you may have discovered in `0x03`.

### Not everything needs to run every time

A README typo should not run a matrix build. Two mechanisms:

```yaml
on:
  push:
    paths-ignore: ["**.md", "docs/**"]
```

…which prevents the *run* — clean, and slightly dangerous if the check is
required for merge, because a check that never runs is a check that never
reports (GitHub treats it as pending, and your pull request waits forever).

Or a filter job whose output the heavy jobs read:

```yaml
  changes:
    outputs:
      code: ${{ steps.filter.outputs.code }}
  test:
    needs: changes
    if: needs.changes.outputs.code == 'true'
```

…which produces a *skipped* job. Skipped counts as success for branch
protection, so required checks stay satisfiable. That is usually what you want
on a repository with protected branches.

### Data between jobs, and the page a human reads

Jobs share no filesystem. They share two channels:

```yaml
    outputs:
      digest: ${{ steps.build.outputs.digest }}
    steps:
      - id: build
        run: echo "digest=sha256:abc…" >> "$GITHUB_OUTPUT"
```

and then `${{ needs.build.outputs.digest }}` downstream. Compute the version,
the tag, the digest **once**, and pass it. Recomputing it in the deploy job is
how the thing you tested and the thing you shipped drift apart.

`$GITHUB_STEP_SUMMARY` is the other half: anything you append (markdown) shows
up on the run's page.

```bash
echo "### Built \`$TAG\`" >> "$GITHUB_STEP_SUMMARY"
echo "| coverage | $COV% |" >> "$GITHUB_STEP_SUMMARY"
```

It costs one line and it is the difference between a colleague reading your
pipeline's result and a colleague opening seven collapsed log sections.

## You're done when you can answer these without looking

- Four jobs, each 3 minutes, chained. What is the wall-clock time, and what
  could it be?
- When is a composite action the wrong tool and a reusable workflow the right
  one?
- Give a concrete scenario where missing `concurrency` on a deploy workflow puts
  the wrong commit in production.
- `paths-ignore` versus a filter job: which one breaks a required status check,
  and why?
- The build job computes an image digest. Why should the deploy job not compute
  it again?

## General requirements

- **`.github/actions/setup/action.yml`** — a composite action.
- A reusable workflow (any name) with `on: workflow_call`, called by another.
- Verify with `python checker.py 0x06`.

---

## Tasks

### 0. Turn the ladder into a graph (mandatory)

Restructure `ci.yml` into at least four jobs, with at least two of them starting
immediately, and at least one job that waits on two or more others.

Before and after, note the total run duration. Write the two numbers in
`PROGRESS.md` — it is the most satisfying measurement in this course.

### 1. Extract the repetition into a composite action (mandatory)

**File:** `.github/actions/setup/action.yml`

The "checkout, set up Python, restore the dependency cache, install" sequence
now appears in several jobs. Make it one action:

```yaml
name: setup
description: Python + dependencies, cached
inputs:
  python-version:
    default: "3.11"
runs:
  using: composite
  steps:
    - uses: actions/setup-python@<sha>   # pinned, per 0x05
      with:
        python-version: ${{ inputs.python-version }}
    - shell: bash
      run: pip install -r requirements.lock
```

Use it from at least two jobs. Note that a composite action does **not** check
out your repository for you — the caller has to have done that first.

### 2. Extract a whole job into a reusable workflow (mandatory)

Create a workflow with `on: workflow_call` that takes at least one input, and
call it from `ci.yml` at the job level with `with:`.

Afterwards, look at the run's job list and find the `caller / callee` names —
the checker looks for exactly that as evidence the call really happened.

### 3. Stop racing yourself (mandatory)

Add a `concurrency:` block to `ci.yml`, keyed on the workflow and the ref, with
`cancel-in-progress: true`. Then push two commits within a minute and watch the
first run get cancelled.

```bash
gh run list --limit 5     # one of them should say "cancelled"
```

Think about what the right setting would be for `deploy.yml`, which you will
write in `0x07`. It is not this one.

### 4. Let a documentation change be cheap (mandatory)

Add path filtering, either with `paths-ignore` on the trigger or with a filter
job the heavy jobs check. Then commit a change to a markdown file **alone** and
push it.

The checker accepts either proof: a run containing a skipped job, or a
docs-only commit that produced no full pipeline. If your repository has required
checks already, prefer the filter-job form and reread why.

### 5. Pass a value, and write a summary (mandatory)

Have one job compute something — the version, the short SHA, the image tag —
write it to `$GITHUB_OUTPUT`, declare it in the job's `outputs:`, and consume it
downstream via `needs.<job>.outputs.<name>`.

Then append a small markdown table to `$GITHUB_STEP_SUMMARY`: version, image
tag, coverage. Open the run page and read it as if you were a colleague checking
what shipped.

```bash
python checker.py 0x06
```
