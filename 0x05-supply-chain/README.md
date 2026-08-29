# 0x05 — Secrets, permissions, supply chain

## Concept (read this once, ~9 minutes)

Your pipeline is the most privileged thing in your organisation. It can read
every secret, write to every branch, publish to every registry, and it does so
unattended, triggered by events that people outside your team can cause.

Think about that last clause for a second. `on: pull_request` means *anyone*
can propose code and cause your infrastructure to execute something.

This module is about closing that down without making the pipeline useless.

### The token you did not know you had

Every run gets a `GITHUB_TOKEN`, automatically, scoped to your repository. Its
default permissions depend on a repository setting — historically
"read and write to everything". A single compromised action step with a
write-scoped token can push to `main`, publish a release, or open a pull request
that approves itself.

So declare permissions explicitly, and declare them small:

```yaml
permissions:
  contents: read          # at the top of the file: the floor for every job

jobs:
  publish:
    permissions:
      contents: read
      packages: write     # the exception, where it is needed, and nowhere else
```

A job-level block **replaces** the top-level one, so list everything that job
needs. Anything you do not list is `none`.

### The fork problem, and `pull_request_target`

```
   on: pull_request           runs the FORK's code, with NO secrets,
                              and a read-only token.        ← safe by design

   on: pull_request_target    runs the BASE repo's workflow, with FULL secrets
                              and a writable token, in the context of a PR
                              that anyone can open.         ← a loaded gun
```

`pull_request_target` exists for jobs that must label or comment on a PR without
running its code. The moment you add `actions/checkout` with
`ref: github.event.pull_request.head.sha` to one, you are executing a stranger's
code with your secrets in the environment. This is not theoretical; it is one of
the most exploited patterns in the ecosystem, and it is drill 6 in module
`0x0B`.

Rule of thumb: if you need a secret to validate a pull request, you have a
design problem, not a permissions problem.

### Masking is a net, not a design

GitHub replaces known secret values with `***` in logs. It is genuinely useful
and it fails in completely ordinary ways: base64 the value, split it across
lines, pass it through a tool that reformats JSON, and the mask misses. So:

- pass secrets through `env:` rather than interpolating them into shell lines
  (`${{ secrets.X }}` inside a `run:` string becomes part of the script text
  itself, which is also how shell injection gets in),
- never `echo` one "just to check",
- treat any secret that has appeared in a log as burnt — because it has been.

### Rotation: deleting the line does not help

Once a credential lands in a commit, it is in the history, in every clone, in
every fork, and in GitHub's cached views of that commit. Rewriting history
(`git filter-repo`) helps a little and is disruptive; the only real fix is to
**invalidate the credential at the source** and issue a new one. Order of
operations, always:

1. Rotate the secret at the provider (the old value must stop working).
2. Update wherever it is stored (`gh secret set …`).
3. Remove it from the code and merge.
4. Then, optionally, clean the history.

Doing 3 first buys you nothing and wastes the minutes that matter.

### The supply chain, in one picture

```
   your code ──┐
   your deps ──┼──► build ──► image ──► registry ──► cluster
   your actions┘         ▲                              ▲
                         │                              │
             everything above this line runs        and whatever ends up
             with your token and your secrets       here, runs in production
```

Four questions follow from it, and this module answers each with a control:

| Question | Control |
|----------|---------|
| Is the code free of committed secrets? | secret scanning (gitleaks) |
| Do my dependencies have known holes? | `pip-audit` / `trivy`, on a schedule |
| Is the *action* I run the one I reviewed? | pin actions to a commit SHA |
| Can I prove what went into this image? | SBOM + build provenance |

### Why pinning actions matters more than it looks

`uses: some/action@v3` is a **tag**, and a tag is a mutable pointer in someone
else's repository. If their account is compromised, `v3` becomes whatever the
attacker wants, and it runs inside your pipeline with your token — retroactively,
in every workflow that references it. Pin the commit:

```yaml
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
```

The comment is not decoration. Without it nobody will ever dare upgrade the pin,
and an un-upgradable pin is how you end up running a two-year-old action with a
known vulnerability. Dependabot's `github-actions` ecosystem updates both the
SHA and the comment for you — which is why the two controls belong together.

### Scanning on a schedule, not only on push

A CVE is published against code you did not change. If your only scan runs on
push, a repository that is quiet for three weeks is unscanned for three weeks.
Schedule it.

### SBOM and provenance, briefly

An **SBOM** is a machine-readable list of what is inside your artifact. Its value
shows up on the day a name is in the news and someone asks "are we affected?" —
with an SBOM per build that is a query; without it, it is an archaeology
project.

**Provenance** is a signed statement of *how* the artifact was built: which
repository, which commit, which workflow, on which runner.
`actions/attest-build-provenance` produces one and GitHub stores it, so later
`gh attestation verify` can tell you whether the image in your registry really
came from your pipeline or was pushed by somebody with a stolen token.

## You're done when you can answer these without looking

- What can the default `GITHUB_TOKEN` do, and how do you find out for your repo?
- Explain the difference between `pull_request` and `pull_request_target` to
  someone who wants the second one because "the first one cannot see secrets".
- A secret appeared in a log for four minutes before you deleted the run. What
  do you do?
- Why is a version tag on a third-party action a supply chain risk, and what
  does the pin cost you?
- Someone asks whether your service ships a vulnerable version of a library.
  What is the fastest way to answer for a build from three months ago?

## General requirements

- All workflows: a top-level `permissions:` block with no write scopes.
- Files: **`.github/dependabot.yml`**, **`0x05-supply-chain/rotation.md`**.
- Repository secret: **`SMOKE_TOKEN`** (any value — it is used for real in
  `0x07`).
- The SBOM must be produced by **`ci.yml`** and uploaded as an artifact whose
  name contains `sbom`.
- Verify with `python checker.py 0x05`.

---

## Tasks

### 0. Least privilege everywhere (mandatory)

Add `permissions: {contents: read}` at the top of every workflow, then grant
exactly what each job needs at the job level (`packages: write` for the publish
job, and later `id-token: write` / `attestations: write` for provenance).

While you are here, look at Settings → Actions → General → Workflow permissions
and see what your repository's default actually is.

### 1. Pin every action to a commit (mandatory)

Replace every `uses: owner/action@v4` with a 40-character commit SHA and a
comment naming the version:

```bash
gh api repos/actions/checkout/commits/v4 --jq .sha
gh api repos/docker/build-push-action/commits/v6 --jq .sha
```

The checker scans every workflow **and** every composite action in the repo, so
whatever you add later must be pinned too — including the drill files in module
`0x0B` when you copy them in.

### 2. Catch a secret, then handle it properly (mandatory)

**File:** `0x05-supply-chain/rotation.md`

Add a secret-scanning job (`gitleaks/gitleaks-action`, or run the binary).
Then plant one, on a branch, on purpose:

```bash
git switch -c leak-test
echo "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE" > config.env
git add config.env && git commit -m "test: plant a fake credential" && git push -u origin leak-test
```

Watch the scan fail. Then clean it up: remove the file, and make sure no
`AKIA…`-shaped string survives anywhere in your working tree.

In `rotation.md` (120+ words) write the incident up as if it had been real: what
was exposed, the order in which you would act, why deleting the line is not the
fix, and what you would do about the copy that is still in the git history and
in every fork. The checker requires the words *history* and *rotate* to appear,
because they are the two facts that people skip.

### 3. Watch the dependencies (mandatory)

**File:** `.github/dependabot.yml`

Configure three ecosystems — `pip`, `docker`, and `github-actions` — with a
weekly schedule. Then add a vulnerability scan (`pip-audit`, or `trivy` against
your image) to a workflow that runs on a **schedule**, not only on push.

Look at the first pull requests Dependabot opens. Notice that the
`github-actions` ones bump both the SHA and the comment you wrote in task 1 —
that is the payoff for the comment.

### 4. Use a secret without leaking it (mandatory)

```bash
gh secret set SMOKE_TOKEN
```

Reference it from a workflow through `env:`, never inside a `run:` string:

```yaml
      - name: Call the thing
        env:
          TOKEN: ${{ secrets.SMOKE_TOKEN }}
        run: curl -sS -H "Authorization: Bearer $TOKEN" "$URL"
```

The checker verifies the secret exists, that a workflow uses it, that nothing
echoes a secret, and that no workflow in the repository uses
`pull_request_target`.

### 5. Prove what you shipped (mandatory)

In `ci.yml`, next to the image build:

- generate an SBOM (`anchore/sbom-action`, or `docker buildx build --sbom=true`)
  and upload it as an artifact whose name contains `sbom`,
- attest the build with `actions/attest-build-provenance`, which needs
  `id-token: write` and `attestations: write` on that job.

Then open the SBOM and find `fastapi` in it. Then try:

```bash
gh attestation verify oci://ghcr.io/<you>/urlshort:main --repo <you>/<repo>
```

That command is the whole point: it answers "did my pipeline build this?" with
evidence rather than trust.

```bash
python checker.py 0x05
```
