# 0x04 — Containers: build, tag, publish

## Concept (read this once, ~9 minutes)

Up to now your pipeline produced a wheel. A wheel needs a Python, and a Python
needs a machine somebody configured. A container image is the answer to *"and
what else does it need?"* — a filesystem plus the configuration to start a
process in it, with no assumptions about the host beyond a kernel.

### An image is layers, and layers are a cache

```
   FROM python:3.11-slim@sha256:…        ← layer 0, shared by everything
   RUN pip install -r requirements.lock  ← layer 1, changes when deps change
   COPY src/ /app/src/                   ← layer 2, changes on every commit
```

Each instruction produces a layer, and a layer is reused if its inputs are
unchanged. This is why the order of your Dockerfile is a performance decision:

```dockerfile
COPY . /app                       ✗  any change busts the dependency layer
RUN pip install -r requirements.lock

COPY requirements.lock /app/      ✓  dependencies rebuild only when they change
RUN pip install -r requirements.lock
COPY src/ /app/src/
```

Same result, wildly different build times. Put the things that change rarely
first, and the things that change every commit last.

### Multi-stage: ship the output, not the workshop

```dockerfile
FROM python:3.11-slim@sha256:… AS builder
RUN pip install build && python -m build --wheel

FROM python:3.11-slim@sha256:… AS runtime
COPY --from=builder /app/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
USER app
```

The compiler, the build tools, the caches, the `.git` directory — all of it stays
in the builder stage and never ships. Smaller image, faster pulls, and every
package you did not install is a CVE you do not have to triage on a Tuesday.

### Tags move; digests do not

This is the most important idea in the module.

```
   ghcr.io/you/urlshort:latest   →  a pointer someone can repoint at any time
   ghcr.io/you/urlshort@sha256:9f2…  →  content. Immutable. The bits themselves.
```

`:latest` on Monday and `:latest` on Wednesday can be two different images.
If your pipeline tests `:latest` and then deploys `:latest`, there is a window
where those are not the same thing — and that window is where the incident
lives. Humans use tags. Machines use digests. From module `0x07` onwards, every
deploy in this course refers to a digest.

Pin your **base image** by digest too, for the same reason: `python:3.11-slim`
is rebuilt regularly, so "the build worked yesterday" tells you nothing about
what you built today.

```bash
docker buildx imagetools inspect python:3.11-slim --format '{{.Manifest.Digest}}'
```

Yes, this means you have to update it deliberately. That is what Dependabot's
`docker` ecosystem is for, and you will wire it up in `0x05`.

### Naming an image so you can find it again

`docker/metadata-action` generates tags and OCI labels from the event:

```yaml
- id: meta
  uses: docker/metadata-action@v5
  with:
    images: ghcr.io/${{ github.repository_owner }}/urlshort
    tags: |
      type=sha,prefix=sha-
      type=ref,event=branch
      type=semver,pattern=v{{version}}
```

That gives you `sha-4f21ab9` (which commit), `main` (which branch), and later
`v1.0.0` (which release), plus labels like
`org.opencontainers.image.revision` baked into the image config. The labels
matter: an image that can tell you which commit produced it is one you can
debug from a running container, and module `0x08` checks for exactly that.

### Publishing to GHCR from Actions

GitHub's own registry needs no new credentials — the automatic `GITHUB_TOKEN`
can push, if you grant it:

```yaml
    permissions:
      contents: read
      packages: write      # only in the job that publishes
    steps:
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
```

Note the shape: read at the top of the file, write **in one job**. That habit is
the whole of module `0x05` in miniature.

### Build cache across runs

Every run gets a clean machine, so the layer cache is empty unless you bring it:

```yaml
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

`mode=max` caches intermediate stages too, which is what makes a multi-stage
build fast on the second run. In the log you will see `CACHED` next to reused
steps — that word is the evidence the checker looks for.

### Root, and why not

By default a container process is uid 0. Combined with a mounted docker socket
or a kernel escape it becomes root on the host; combined with a writable
filesystem it means an RCE in your app can rewrite your app. Add a user, own the
files it needs, and `USER` it before `CMD`. It costs two lines.

## You're done when you can answer these without looking

- Every commit rebuilds all your dependencies. What is wrong with the
  Dockerfile?
- What is the practical difference between deploying `:latest` and deploying
  `@sha256:…`?
- Your image is 1.1 GB for a 40 KB app. Name the three biggest likely causes.
- Why does the publish job need `packages: write` while the rest of the workflow
  does not?
- Where does the commit SHA live inside a published image, and how would you
  read it out of a running container?

## General requirements

- File: **`Dockerfile`** at the repository root.
- Image: **`ghcr.io/<your-user>/urlshort`**. After the first push, make the
  package **public** (Package → Settings → Change visibility) so the checker and
  `docker pull` can read it without a login.
- Verify with `python checker.py 0x04`.

---

## Tasks

### 0. Write a Dockerfile you would let out of the building (mandatory)

**File:** `Dockerfile`

Requirements the checker enforces:

- at least two stages, and at least one of them named (`FROM … AS builder`),
- every base image pinned by `@sha256:` digest,
- a `USER` that is not root in the final stage,
- no credential-shaped `ENV`/`ARG` defaults.

Requirements it cannot enforce but you should meet anyway: order the layers so a
code change does not reinstall dependencies, use `--no-cache-dir` for pip, and
have the container start the app with `python -m urlshort`.

Build and run it locally before you go anywhere near CI:

```bash
docker build -t urlshort:dev .
docker run --rm -p 8000:8000 urlshort:dev
curl http://localhost:8000/healthz
```

### 1. Lint the Dockerfile (mandatory)

Add hadolint to the pipeline (`hadolint/hadolint-action`, or run its container).
It will find things you did not think of — pinned apt packages, missing
`--no-install-recommends`, shell forms that break signal handling.

Where it is wrong for your case, disable the specific rule *with a comment
saying why*, in the Dockerfile:

```dockerfile
# hadolint ignore=DL3013
```

### 2. Publish to GHCR from the pipeline (mandatory)

Add a job that logs in to `ghcr.io` and pushes the image, using
`docker/build-push-action`. Grant `packages: write` **on that job only**.

After the first successful push, go to your profile → Packages → `urlshort` →
Package settings, and make it public.

```bash
gh api users/{owner}/packages/container/urlshort/versions --jq '.[0].metadata.container.tags'
```

If that 403s: `gh auth refresh -s read:packages`.

### 3. Tag it so the tag means something (mandatory)

Use `docker/metadata-action` to produce at least:

- a commit tag — `sha-<short sha>`,
- a moving tag — `main` or `latest`.

Then check the registry and confirm the commit tag corresponds to a real commit
of yours. This is the tag every later module deploys from.

### 4. Make the layer cache work across runs (mandatory)

Add `cache-from: type=gha` and `cache-to: type=gha,mode=max`. Push twice and
compare the build job's duration. In the second run's log, look for `CACHED`.

If nothing is cached on the second run with an unchanged Dockerfile, something
in an early layer is changing every time — a `COPY . .` too high up, or a
timestamp in a file you copy.

### 5. Run what you published (mandatory)

Pull the image the pipeline built and run it, leaving it up for the checker:

```bash
docker pull ghcr.io/<you>/urlshort:main
docker run -d --name urlshort-local -p 8000:8000 ghcr.io/<you>/urlshort:main
curl http://localhost:8000/healthz
docker image inspect ghcr.io/<you>/urlshort:main --format '{{.Config.User}} {{.Size}}'
```

The checker requires: the image present locally, under 400 MB, a non-root
`Config.User`, a running container named `urlshort-local`, and `/healthz`
answering on port 8000.

If you are over 400 MB, the usual culprits are a full `python:3.11` instead of
`-slim`, build tools left in the runtime stage, or a `COPY . .` that dragged in
`.git` (check your `.dockerignore` — one is already in the repo).

```bash
python checker.py 0x04
```
