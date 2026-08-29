# 0x07 — Environments, approvals, a real deploy

## Concept (read this once, ~9 minutes)

So far the pipeline has been continuous *integration*: everything happens on
GitHub's machines and nothing leaves. Delivery starts when something you built
becomes something running somewhere, and that raises three questions that CI
never had to answer:

1. **Where** does it go, and how do the two "wheres" differ?
2. **Who** says yes, and when?
3. **How** does a runner in a datacentre reach the thing you are deploying to?

### An environment is an object, not a word

In GitHub, an environment is a real resource with its own protection rules,
variables and secrets. A job opts into it:

```yaml
  deploy:
    environment:
      name: staging
      url: http://localhost:8081
```

Declaring that does four things at once:

- creates a **deployment record** you can query later (this is what your DORA
  metrics in `0x0A` will be computed from),
- applies the environment's protection rules **before the job starts**,
- injects that environment's variables and secrets, and nobody else's,
- puts the URL on the pull request and the repository home page.

That third point is the one that makes environments worth using: the production
database password is not available to a job targeting staging. Repository
secrets are available to everything; environment secrets are not.

### Approvals are the one manual gate you are allowed

Everything else in this course is automated on purpose. A required reviewer on
`production` is different: it is not checking whether the tests passed — a
machine did that — it is a human deciding *now is a good time*. Friday at 17:00,
mid-incident, during a sale: those are judgements, not checks.

When a job hits a protected environment it **pauses before running**, the
reviewers get notified, and the run waits (up to 30 days). Approve or reject in
the UI, or:

```bash
gh api repos/{owner}/{repo}/actions/runs/<id>/pending_deployments \
  -f state=approved -f environment_ids[]=<env id> -f comment="ship it"
```

A note on honesty: an approval gate that people click without reading is worse
than no gate, because it launders a decision nobody made. If you find yourself
approving reflexively, the fix is to make the pipeline's summary good enough
that the decision is real — which is what `$GITHUB_STEP_SUMMARY` was for.

### Config belongs to the environment, not the image

```
   ONE image  ───►  staging   (APP_ENV=staging, small replica count, test data)
        │
        └──────►  production  (APP_ENV=production, real scale, real data)
```

If staging and production run *different images*, then staging tests staging,
and you learn nothing about production. Same artifact, different configuration,
injected at deploy time. That is the whole reason `/version` in this service
reads `APP_ENV`, `GIT_SHA` and `IMAGE_TAG` from the environment instead of
having them compiled in.

GitHub gives you two injection points per environment:

- **variables** (`vars.APP_ENV`) — visible, non-secret, appear in logs,
- **secrets** (`secrets.DB_PASSWORD`) — masked, environment-scoped.

### Getting to your own machine: the self-hosted runner

A GitHub-hosted runner cannot reach `localhost` on your laptop. There are two
honest ways to deploy something you can actually look at: pay for a cloud
target, or run the runner on your own machine. This course does the second.

```
   GitHub                                 your machine
   ──────                                 ────────────
   job queued ──► "any runner labelled     run.cmd polls, picks up the job,
                   [self-hosted, local]"   runs the steps HERE, with your
                                           docker, your ports, your files
```

The runner **pulls** work over an outbound HTTPS connection — no inbound port,
no firewall change. And then it executes whatever your workflows tell it to,
as your user, on your machine.

> **Read this before you register one.** GitHub recommends against self-hosted
> runners on public repositories, because a pull request from a stranger can
> propose a workflow change that runs on your hardware. For this course:
> - go to **Settings → Actions → General → Fork pull request workflows from
>   outside collaborators** and choose **Require approval for all external
>   contributors**;
> - do not merge or approve workflow runs from people you do not know;
> - stop the runner (`Ctrl+C`) when you finish for the day.
>
> The exposure is small on a personal course repository. It is not zero, and
> "I did not know it ran on my laptop" is a bad thing to discover later.

### What "deploy" means here

Staging in this module is one Docker container on your machine, on port 8081.
That is deliberately unglamorous, and it still exercises every idea that a real
deploy has:

- pull the exact image the pipeline built (by digest, not by tag),
- inject configuration from the environment,
- replace the running instance,
- verify it answers, with the commit you expect.

A deploy is not "the workflow finished". A deploy is *reality changed and you
checked*. Module `0x09` turns that check into a gate with a rollback behind it.

## You're done when you can answer these without looking

- Name three things declaring `environment: production` does that a plain job
  does not.
- Why can a repository secret be read by a staging deploy but an environment
  secret cannot?
- Staging and production run different images. What has stopped being true?
- Your self-hosted runner is offline. What does the queued deploy job do?
- What exactly makes a self-hosted runner risky on a public repository, and what
  reduces that risk?

## General requirements

- Workflow: **`.github/workflows/deploy.yml`**, deploying on pushes to `main`.
- Runner: self-hosted, labelled **`local`**; deploy job `runs-on: [self-hosted, local]`.
- Environments: **`staging`** and **`production`**.
- Container: **`urlshort-staging`**, published on **port 8081**.
- Verify with `python checker.py 0x07`.

---

## Tasks

### 0. Register a runner on your machine (mandatory)

Settings → Actions → Runners → **New self-hosted runner**, pick Windows, and run
the commands it gives you. When it asks for labels, add `local`:

```powershell
./config.cmd --url https://github.com/<you>/<repo> --token <token> --labels local
./run.cmd
```

Leave that window open — it is your runner. Then write `deploy.yml` with:

```yaml
jobs:
  deploy:
    runs-on: [self-hosted, local]
```

and get one job to run on it successfully. `actions-runner/` and `_work/` are
already in `.gitignore`; keep them out of the repository.

The checker verifies a runner labelled `local` exists **and** that a deploy job
actually ran on it.

### 1. Create the two environments (mandatory)

```bash
gh api -X PUT repos/{owner}/{repo}/environments/staging
gh api -X PUT repos/{owner}/{repo}/environments/production
```

Then, in the UI, restrict `staging` to deployments from `main` (Deployment
branches → Selected branches). An environment any branch can deploy to is not
an environment, it is a shared folder.

### 2. Put a human in front of production (mandatory)

On the `production` environment, add yourself as a **required reviewer**. Add a
job to `deploy.yml` that targets `environment: production` (it can do almost
nothing for now — echo the image digest it would deploy).

Push, and watch the run stop and wait for you. Approve it. Then look at the run
timeline and see the pause recorded.

### 3. Make the deployment record real (mandatory)

The staging deploy job must declare `environment:` with **both** a `name:` and a
`url:` (`http://localhost:8081`). Then:

```bash
gh api repos/{owner}/{repo}/deployments --jq '.[0] | {environment, sha, created_at}'
```

The checker requires a staging deployment whose latest status is `success`.

### 4. Actually deploy the image, and prove what is running (mandatory)

In the deploy job, on your self-hosted runner: pull the image the pipeline built
for **this commit** and (re)start the container.

```yaml
      - name: Deploy to staging
        env:
          IMAGE: ghcr.io/${{ github.repository_owner }}/urlshort@${{ needs.build.outputs.digest }}
        run: |
          docker rm -f urlshort-staging 2>/dev/null || true
          docker run -d --name urlshort-staging -p 8081:8000 \
            -e APP_ENV="${{ vars.APP_ENV }}" \
            -e GIT_SHA="${{ github.sha }}" \
            -e IMAGE_TAG="sha-${GITHUB_SHA::7}" \
            "$IMAGE"
```

Then check it by hand, the way the checker will:

```bash
curl http://localhost:8081/version
```

It must report `environment: staging` and a `git_sha` that is a real commit of
yours and matches the deployment GitHub recorded. If `git_sha` says `unknown`,
you have just discovered why the `/version` endpoint exists.

> `${{ ... }}` interpolation inside `run:` is convenient and, for values a
> stranger can influence, dangerous — module `0x05`. `github.sha` is safe; a
> branch name or a PR title is not.

### 5. Configure by environment, not by image (mandatory)

```bash
gh variable set APP_ENV --env staging --body staging
gh variable set APP_ENV --env production --body production
```

Read them in the workflow with `${{ vars.APP_ENV }}`. The checker requires both
environments to define `APP_ENV`, with **different** values, and the Dockerfile
not to hardcode an environment.

Once this passes, you have the property that makes the rest of the course
possible: the artifact is identical everywhere, and only the configuration
moves.

```bash
python checker.py 0x07
```
