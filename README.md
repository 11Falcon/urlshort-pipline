# CI/CD, by shipping a real service to a real place

Thirteen modules. Each one opens with a short piece of reading — the mental
model, not the reference manual — and then makes you produce state that a
checker inspects. Not YAML you *wrote*: pipeline runs that actually happened,
images that actually exist in a registry, deployments GitHub actually recorded,
pods actually running your digest.

There is no way to pass a task here by describing a pipeline. The checker asks
GitHub what your pipeline did.

You will finish able to build a delivery pipeline that a team can trust: fast
gates, a reproducible build, a signed and scanned artifact, staged deployments
with an approval, a canary, an automatic rollback — and the ability to say, out
loud, why each piece is there and what happens when it fails.

## The service

`src/urlshort/` is a small URL shortener: `/healthz`, `/version`, `POST
/shorten`, `GET /r/{code}`. It exists to be built, tested, containerised,
published, deployed, broken and rolled back. You will barely change its code.
It already has a test suite, a slow test, and a `/version` endpoint that reports
the commit it was built from — because a deploy you cannot identify is a deploy
you cannot roll back.

## What you need

| Thing | Why | Check |
|-------|-----|-------|
| **Python 3.11+** | the app, the tests, `checker.py` | `python --version` |
| **PyYAML** | the checker reads your workflow files | `pip install pyyaml` |
| **git** | all of it | `git --version` |
| **GitHub CLI (`gh`)** | the checker reads your runs, artifacts, environments | `gh auth status` |
| **A GitHub account** | this course runs on GitHub Actions | |
| **Docker Desktop** | from `0x04` on | `docker info` |
| **A Kubernetes cluster** | `0x09` only — Docker Desktop's, or `kind` | `kubectl get nodes` |

Two setup notes that will save you an hour:

```bash
gh auth login
gh auth refresh -s read:packages,workflow
```

`read:packages` lets the checker see the images you publish to GHCR;
`workflow` lets `gh` push changes to `.github/workflows/`.

**Make your repository public.** Actions minutes are free on public repos, and
branch protection (module `0x0A`) is free there too. If it must be private,
everything works except `0x0A` task 0, which needs GitHub Pro.

## Getting started

```bash
python checker.py doctor
```

That prints what you have, what you are missing, and what each gap will block.
Then open `0x00-first-run/README.md` and start.

## How a module works

Read the module README top to bottom first — it is five to ten minutes and the
tasks assume it. Then do the tasks in order.

```bash
python checker.py            # everything
python checker.py 0x03       # one module
python checker.py 0x03 2     # one task
```

A task passes when the world is in the described state. Where that state is a
file (a workflow, a Dockerfile, a runbook), the task names the file. Where it is
*history* — "a run that failed at the smoke test", "a cancelled run", "a pull
request that was blocked and then merged" — you have to actually make it happen.
That is deliberate. Half of what this course teaches is what a pipeline looks
like when it goes wrong, and you cannot learn that from a green run.

Expect the checker to be slower than you are used to: it is talking to the
GitHub API and downloading logs. `python checker.py 0x04` is much quicker than
running everything.

## The modules

| # | Module | The question it answers |
|---|--------|-------------------------|
| `0x00` | The repo, the runner, the first green run | What actually happens between `git push` and a green tick? |
| `0x01` | The build | Why does the same commit produce two different builds, and how much of your day is spent reinstalling dependencies? |
| `0x02` | The test gate | When is a test suite a gate, and when is it decoration? |
| `0x03` | Static analysis | How do you stop arguing about style, and why must local run exactly what CI runs? |
| `0x04` | Containers | What are you actually shipping, and how do you name it so you can find it again? |
| `0x05` | Secrets and supply chain | Your pipeline has write access to everything. Who else does? |
| `0x06` | Pipeline architecture | Your pipeline is a program. Is it a good one? |
| `0x07` | Environments and approvals | Where does the software go, who says yes, and how does the runner reach your machine? |
| `0x08` | Release engineering | What is a version, and what exactly gets promoted? |
| `0x09` | Deploy strategies and rollback | How do you change production without a maintenance window — and get back? |
| `0x0A` | The feedback loop | Who enforces this when you are on holiday, and how do you know if it is working? |
| `0x0B` | Debugging drills | Six broken pipelines. Fix them, and say what was wrong. |
| `0x0C` | Final project | The whole thing, end to end, on one commit. |

**77 tasks.** Track them in `PROGRESS.md`.

## Rules

- Never edit `checker.py` or `ci_kit.py`.
- Never make a check pass by weakening the thing it checks. Deleting the test
  that fails is not a fix, and neither is `continue-on-error`. (Module `0x0B`
  drill 3 is built entirely out of people who disagreed.)
- Prefer the official documentation for a piece of syntax you are unsure about:
  `docs.github.com/actions` and `gh help api`. Workflow syntax changes; the
  ideas in the module READMEs do not.
- When something does not run at all, the answer is nearly always in the
  trigger, and the second most likely answer is that the file is not on the
  default branch yet.

## A word on cost and blast radius

Everything here runs on free GitHub-hosted runners except module `0x07`, which
asks you to register a **self-hosted runner on your own machine** so the
pipeline can deploy to something you can actually look at. A self-hosted runner
executes code your workflows tell it to. Keep the repository yours, do not
accept pull requests from strangers while it is running, and stop it
(`Ctrl+C` in the runner window) when you are done for the day. Module `0x07`
says all of this again at the point where it matters.
