# 0x08 — Release engineering

## Concept (read this once, ~8 minutes)

Continuous delivery does not mean everything reaches users the moment it is
merged. It means everything is *releasable* the moment it is merged, and that
releasing is a decision rather than a project. This module is about making that
decision cheap, and making the thing you decided about identifiable forever
after.

### A version is a promise about compatibility

```
   v  MAJOR . MINOR . PATCH
      │       │       └── bug fix; nobody has to do anything
      │       └────────── new capability; existing callers unaffected
      └────────────────── something that used to work no longer does
```

The number is not a marketing device or a measure of effort. It answers exactly
one question for the person consuming your artifact: *what do I have to do if I
upgrade?* Bumping MAJOR for a big feature and PATCH for a breaking change is not
a style choice, it is a lie with a schedule attached.

Where does the number live? Somewhere singular. Here it is
`pyproject.toml`'s `version`, and the git tag must agree with it — the checker
enforces that, because an artifact that disagrees with the tag that produced it
will eventually cost somebody a very confusing afternoon.

Conventional commits (`feat:`, `fix:`, `feat!:`) exist to make this mechanical:
the commit messages since the last tag tell you which component to bump, and
tools like `release-please` or `semantic-release` will do it for you. Worth
knowing about; not required here.

### The tag is the trigger

```yaml
on:
  push:
    tags: ["v*.*.*"]
```

A tag push is a deliberate act, which makes it the right event for a release.
Note two things: the workflow file must already be on the default branch, and
in a tag-triggered run `github.ref` is `refs/tags/v1.0.0` while
`github.ref_name` is `v1.0.0`.

Tags are also the thing people quietly break. `git tag -f` moves a tag that
other people have already fetched, which means their `v1.0.0` and yours are
different code with the same name. Never move a release tag. Cut a new one; they
are free.

### Promotion, not rebuilding

Here is the mistake almost every first pipeline makes:

```
   ✗   commit ──► build+test ──► image A ──► deploy to staging
       tag    ──► build      ──► image B ──► deploy to production
```

Image B was never tested. It is *probably* identical to A — same commit, after
all — unless a base image moved, a dependency resolved differently, or a
transient network error changed what got installed. You have quietly reintroduced
everything module `0x01` was about.

```
   ✓   commit ──► build+test ──► image A ──► staging
       tag    ──► RETAG A as v1.0.0 ──────► production
```

Retagging moves a *name* onto an existing digest. Nothing is rebuilt, so the
bits in production are the bits that passed:

```bash
docker buildx imagetools create \
  --tag ghcr.io/you/urlshort:v1.0.0 \
  ghcr.io/you/urlshort:sha-4f21ab9
```

Afterwards both tags resolve to the same `sha256:…`. That equality is the
property the checker verifies, and it is the technical heart of this module:
**a release is a promotion, not a production.**

### A release is also a communication

The GitHub Release object is the human-facing half:

- **notes** — what changed, for people who did not read the diffs,
- **assets** — the wheel, the SBOM, checksums; something a user can download,
- **the tag** — the machine-readable anchor everything else hangs off.

`gh release create v1.0.0 --generate-notes` will draft notes from merged pull
requests, which is a good starting point and a poor finishing one: generated
notes say what was merged, not what changed for the user. Edit them.

### CHANGELOG.md

Yes, even with generated release notes. The changelog is the version that lives
in the repository, works offline, is diffable, and survives GitHub. Keep it
grouped (Added / Changed / Fixed / Security), newest first, with each entry
pointing at a pull request or commit so a reader can get to the actual change.

The test of a changelog: can someone upgrading two versions decide, from it
alone, whether they need to do anything?

### What the image should be able to tell you

`docker/metadata-action` writes OCI labels into the image config:

```
org.opencontainers.image.version   = 1.0.0
org.opencontainers.image.revision  = 4f21ab9…      (the commit)
org.opencontainers.image.source    = https://github.com/you/repo
```

Which means that months later, holding nothing but a running container, you can
answer "what is this and where did it come from":

```bash
docker inspect <image> --format '{{json .Config.Labels}}'
```

## You're done when you can answer these without looking

- You fixed a bug and, in the same release, removed a deprecated endpoint. What
  is the next version number?
- Why is rebuilding the image on the tag a correctness problem and not just a
  waste of runner minutes?
- Someone force-moved `v1.2.0` to a newer commit. What breaks, and for whom?
- What does a GitHub Release give you that a git tag does not?
- Given only a running container, how do you find the commit it was built from?

## General requirements

- Workflow: **`.github/workflows/release.yml`**, triggered by `v*.*.*` tags.
- File: **`CHANGELOG.md`**.
- `pyproject.toml`'s `version` must match the newest tag.
- Tag a commit that `main` has already built an image for — the release promotes
  that image.
- Verify with `python checker.py 0x08`.

---

## Tasks

### 0. Cut a version (mandatory)

Set `version = "1.0.0"` in `pyproject.toml`, merge it to `main`, let CI build
the image for that commit, then tag it:

```bash
git tag -a v1.0.0 -m "first release"
git push origin v1.0.0
```

Order matters: the tag must point at a commit whose image already exists in the
registry, because the release is going to promote that image rather than build a
new one.

### 1. Make the tag do the work (mandatory)

**File:** `.github/workflows/release.yml`

Trigger on `tags: ["v*.*.*"]`. Inside, you will need `contents: write` (to
create the release) and `packages: write` (to retag the image) — on the jobs
that need them, not at the top.

### 2. Publish a release worth reading (mandatory)

Create a GitHub Release for the tag, with notes and at least one attached file:

```yaml
      - uses: softprops/action-gh-release@<sha>
        with:
          generate_release_notes: true
          files: dist/*.whl
```

Then **edit the notes**. The checker wants at least 30 words; it cannot tell
whether they are useful, but your future self can.

Where does the wheel come from? Not from a fresh build — download the artifact
the CI run for that commit already produced, or attach the one from the build
job in this run if you rebuild only the wheel. The image, in the next task, is
where rebuilding is genuinely forbidden.

### 3. Write the changelog (mandatory)

**File:** `CHANGELOG.md`

Group entries under at least two of Added / Changed / Fixed / Removed /
Security, mention `1.0.0`, and point at least one entry at a pull request
(`#12`) or a commit SHA. 100 words minimum.

### 4. Promote the image; do not rebuild it (mandatory)

In `release.yml`, retag the existing digest:

```bash
docker buildx imagetools create \
  --tag ghcr.io/${{ github.repository_owner }}/urlshort:${{ github.ref_name }} \
  ghcr.io/${{ github.repository_owner }}/urlshort:sha-${SHORT_SHA}
```

The checker verifies three things: `release.yml` contains no image build, it
does contain a promotion command, and in the registry the `v1.0.0` tag and the
`sha-…` tag resolve to **the same digest**.

Confirm it yourself:

```bash
docker buildx imagetools inspect ghcr.io/<you>/urlshort:v1.0.0 --format '{{.Manifest.Digest}}'
docker buildx imagetools inspect ghcr.io/<you>/urlshort:sha-<short> --format '{{.Manifest.Digest}}'
```

Two identical lines is the whole lesson.

### 5. Make the image self-describing (mandatory)

Ensure `docker/metadata-action` (or explicit `LABEL`s) writes at least
`org.opencontainers.image.version` and `org.opencontainers.image.revision`, then
pull the released image and read them back:

```bash
docker pull ghcr.io/<you>/urlshort:v1.0.0
docker inspect ghcr.io/<you>/urlshort:v1.0.0 --format '{{json .Config.Labels}}'
```

The version label must match the tag, and the revision must be a commit that
exists in your repository. If the labels are empty, the metadata action's
`labels` output is not reaching the build step.

```bash
python checker.py 0x08
```
