# 0x09 — Deployment strategies and rollback

## Concept (read this once, ~10 minutes)

Every deployment strategy is an answer to the same question: **during the change,
what does a user see?** Not "how do I get the new version onto the machines" —
that part is easy. The interesting part is the minutes in between, and what
happens when the new version is wrong.

### The three shapes

```
   ROLLING          old ███░░  new ░░███       one at a time, both versions
                    both serve traffic during   live simultaneously for minutes
                    the change

   BLUE / GREEN     blue  ███ (live)            two complete fleets, traffic
                    green ███ (idle, warm)      switches at one instant
                          ▲ flip the switch

   CANARY           stable ████ (90%)           new version takes a slice,
                    canary  █   (10%)           you watch, then widen or abort
```

| | Rolling | Blue/green | Canary |
|---|---|---|---|
| Extra capacity | a little | 2× | a little |
| Rollback speed | a rollout | instant (flip back) | instant (scale to 0) |
| Both versions live at once | yes | no | yes, deliberately |
| Catches "it is broken" | slowly | at the switch | with real traffic, small blast radius |
| Needs | nothing special | double the resources | traffic splitting + observability |

Rolling is the default and is right most of the time. Blue/green is for changes
you cannot half-apply. Canary is for changes whose failure mode is *statistical*
rather than binary — a latency regression, a 2% error rate, a memory leak — which
no smoke test will ever catch.

The constraint everyone forgets: rolling and canary both mean **two versions of
your code talk to one database at the same time**. Your migrations have to be
backward compatible, or the strategy is a fiction. That is not a Kubernetes
problem, it is a design problem, and it is the reason expand/contract migrations
exist.

### A deploy is not done until something checked

```
   kubectl apply            "the API server accepted my YAML"
   rollout status           "the pods are Ready"
   smoke test               "the thing actually answers correctly"   ← done
```

Each line is a strictly stronger claim. Stopping at the first is how you get a
green pipeline over a dead service; a readiness probe gets you the second; only
a request from outside gets you the third.

Keep smoke tests small and about *this* deployment: is it up, is it the version
I just deployed, does the one critical path work. Not the test suite — that ran
before the build. Something like:

```bash
curl -fsS "$URL/healthz"
test "$(curl -fsS "$URL/version" | jq -r .git_sha)" = "$GITHUB_SHA"
```

That second line is the one that catches the deploy which silently did nothing.

### Rollback: the number that matters is minutes

When the smoke test fails, you have two options:

- **Roll forward** — fix and deploy again. Honest, and it takes as long as your
  pipeline takes.
- **Roll back** — return to the last known-good artifact. Seconds, if you
  prepared for it.

You need both, and you need the second to be boring and automatic:

```yaml
      - name: Roll back
        if: failure()
        run: kubectl -n cicd rollout undo deploy/api
```

Rollback works because Kubernetes kept the previous ReplicaSet, and because you
deployed an **immutable digest**. If you deployed `:latest`, "the previous
version" is a name that now points at the same broken image, and your rollback
deploys the bug again — which is a genuinely memorable way to learn about tags.

Two things rollback does *not* undo: database migrations, and anything you sent
to a third party. Plan those separately; note them in your runbook.

### Auditability

Six weeks from now, someone will ask "when did this change, and who did it?".
Answer it by stamping the deployment itself:

```yaml
metadata:
  annotations:
    cicd.course/git-sha: 4f21ab9…
    cicd.course/run-url: https://github.com/you/repo/actions/runs/123
    cicd.course/deployed-by: github-actions
```

Now the cluster can tell you which run put that code there, and the run tells you
which commit, which pull request, which reviewer. That chain — pod to run to
commit to review — is the thing that turns an incident from an investigation
into a lookup.

### Kubernetes bits you will need

```bash
kubectl create namespace cicd
kubectl -n cicd set image deploy/api api=ghcr.io/you/urlshort@sha256:…
kubectl -n cicd rollout status deploy/api --timeout=120s
kubectl -n cicd rollout history deploy/api
kubectl -n cicd rollout undo deploy/api
kubectl -n cicd annotate deploy/api cicd.course/git-sha=$GITHUB_SHA --overwrite
```

For blue/green, the switch is a **Service selector**:

```yaml
spec:
  selector:
    app: api
    colour: green      # change this one word, and traffic moves
```

For a canary, the Service selector must match **both** deployments (`app: api`
only), and the traffic split is just the ratio of Ready pods: 1 canary pod
against 9 stable pods is roughly 10% of requests, because kube-proxy load
balances per connection across all endpoints. Crude, free, and enough to see an
error rate move.

## You're done when you can answer these without looking

- A rolling update to a service that is at capacity, with `maxUnavailable: 0`.
  What has to be true for it to make progress?
- Why is blue/green the wrong choice for a change that includes a database
  migration you cannot reverse?
- Your smoke test passes but users report errors within 10 minutes. Which
  strategy would have caught it, and how?
- You deployed `:latest` and rolled back. Why are you still broken?
- What exactly does `kubectl rollout undo` restore, and where was that stored?

## General requirements

- A cluster: Docker Desktop's Kubernetes or `kind`. (`python checker.py doctor`
  will tell you.) Without one, this module reports as skipped.
- Namespaces: **`cicd`** (rolling), **`cicd-bg`** (blue/green),
  **`cicd-canary`** (canary).
- Deploys come from **`deploy.yml`** on your self-hosted runner from `0x07`.
- Files: `0x09-deploy/bluegreen.md`, `0x09-deploy/rollback.md`.
- Verify with `python checker.py 0x09`.

---

## Tasks

### 0. Deploy a digest to the cluster from CI (mandatory)

Extend `deploy.yml` so that after staging it deploys to Kubernetes: namespace
`cicd`, Deployment `api`, image referenced **by digest**
(`ghcr.io/<you>/urlshort@sha256:…`), all replicas Ready, and annotations
recording the commit and the run URL.

Keep the manifests in `0x09-deploy/` — they are your notes as much as your
inputs.

If the image is in a private package, the cluster needs a pull secret; making
the package public (as `0x04` suggested) avoids the detour.

### 1. Put a smoke test between the deploy and "done" (mandatory)

After `kubectl rollout status`, add a step **named** something containing
`smoke` that:

- hits `/healthz` and requires a 200,
- hits `/version` and requires the `git_sha` to equal the commit being deployed.

Then break it on purpose: deploy an image digest that does not serve (an old
one, or a deliberately broken build), and watch the smoke step fail the run.
The checker requires a failed deploy run whose failing step is the smoke test.

Reaching the service from the runner: a `NodePort` on 30080, or
`kubectl port-forward` in the background, or `kubectl run --rm` a curl pod
inside the cluster. Any of them is fine; the last one is the most portable.

### 2. Roll back automatically (mandatory)

Add an `if: failure()` step that runs `kubectl rollout undo`, and let it happen
for real after task 1's failure. Afterwards the cluster must be back to a good
state:

```bash
kubectl -n cicd rollout history deploy/api
kubectl -n cicd get deploy api -o jsonpath='{.spec.template.spec.containers[0].image}'
```

The checker requires revision 3 or higher (deploy, break, undo), the Deployment
`Available`, and the running image to be the good digest.

### 3. Blue and green (mandatory)

**File:** `0x09-deploy/bluegreen.md`

In namespace `cicd-bg`: two Deployments, `api-blue` and `api-green`, running
**different** image digests, both with Ready pods. One Service, `api`, whose
selector includes `colour: blue` or `colour: green`.

Do the cutover by hand and time it:

```bash
kubectl -n cicd-bg patch svc api -p '{"spec":{"selector":{"app":"api","colour":"green"}}}'
```

In `bluegreen.md` (80+ words): what exactly changed at the moment of cutover,
what happens to in-flight requests, what it costs to keep the idle side warm,
and how you would roll back. The word *selector* must appear, because that is
the whole mechanism.

### 4. A canary (mandatory)

In namespace `cicd-canary`: `api-stable` with at least 4 Ready replicas and
`api-canary` with at least 1, on different images, both carrying the label
`app: api`. One Service, `api`, selecting `app: api` **only** — no colour, no
track — so both sets of pods are endpoints.

Confirm the split is real:

```bash
kubectl -n cicd-canary get endpoints api -o jsonpath='{.subsets[*].addresses[*].ip}'
for i in $(seq 30); do curl -s $URL/version | jq -r .image_tag; done | sort | uniq -c
```

That last command is the point of the exercise: you should see roughly a 9:1
split. The checker requires the canary to be between 5% and 30% of the fleet.

### 5. Make every deploy auditable, and roll one back by hand (mandatory)

**File:** `0x09-deploy/rollback.md`

Annotate the `cicd` Deployment with `git-sha`, `run-url` and `deployed-by`
(the checker looks for all three).

Then do a rollback manually, with a stopwatch: note the time you decide, the
command you run, and the time the service is healthy again.

Write it up in 120+ words: the command (`rollout undo`), why deploying by
**digest** is what makes it work, what the rollback did *not* undo, and the
elapsed time. That number is your MTTR floor — the best you can do on a good
day, with a fix that is already known. Real incidents are slower.

```bash
python checker.py 0x09
```
