# 0x01 — The build: pinned, cached, reproducible

## Concept (read this once, ~8 minutes)

A build turns a commit into an artifact. The only three properties that matter:

- **Deterministic** — the same commit produces the same artifact.
- **Hermetic** — it does not depend on what happened to be on the machine.
- **Fast enough** — because a gate people wait for is a gate people keep.

Most broken pipelines fail the first two while optimising the third.

### `requirements.txt` is not a lock file

There are two different jobs hiding under "dependencies":

```
   requirements.txt          "what this project needs"
   fastapi>=0.110,<1.0       ranges, direct deps only, humans edit this
          │
          │  resolution — a solver picks exact versions, once
          ▼
   requirements.lock         "what we actually install"
   fastapi==0.115.0          exact pins, transitive deps too, machines write it
   starlette==0.38.6
   anyio==4.6.0  …
```

If CI installs from the ranges, then the resolver runs on every build and your
pipeline is a subscription to other people's release schedules. The build that
passed at 09:00 and the one that failed at 14:00 are the same commit. You will
spend the afternoon diffing your own code.

Pin everything, commit the lock, and change it deliberately — that is what
Dependabot is for (module `0x05`).

```bash
python -m venv .venv && .venv/Scripts/activate     # Windows
pip install -r requirements-dev.txt
pip freeze --exclude-editable > requirements.lock
```

(`pip-tools` or `uv pip compile` produce a nicer lock with a header saying how it
was generated. Either is fine here.)

### Cache versus artifact — they are not the same thing

Beginners reach for the wrong one constantly.

| | **Cache** | **Artifact** |
|---|---|---|
| For | things you *could* recompute | things you produced and must keep |
| Keyed by | a string you compute | a name you choose |
| If it is missing | the build is slower | the build is wrong |
| Lives | ~7 days, evicted under pressure | a fixed retention, downloadable |
| Cross-job | any job, any run | any job that `needs:` the producer |

A wheel is an artifact. `~/.cache/pip` is a cache. If your pipeline breaks when
the cache is cold, you have built a dependency on the cache, and one day it will
be cold.

### Cache keys are the whole story

```yaml
- uses: actions/cache@v4
  with:
    path: ~/.cache/pip
    key: pip-${{ runner.os }}-${{ hashFiles('requirements.lock') }}
    restore-keys: |
      pip-${{ runner.os }}-
```

Three things to understand:

1. **A cache entry is immutable.** Save under a key once and that content is
   frozen. This is why the key must contain a hash of the inputs: a key that
   never changes serves you last month's dependencies forever.
2. **A key that always changes is worse than no cache at all.** `key:
   pip-${{ github.sha }}` misses every single time and then pays the upload cost
   anyway. That is drill 1 in module `0x0B`, and it is extremely common.
3. **`restore-keys` are prefixes for partial hits.** Lock file changed? The
   exact key misses, the prefix hits, you get *most* of the packages and pip
   downloads the few that moved.

Cache scope is worth knowing before it confuses you: a run on a branch can read
caches from its own branch and from the default branch, but **not** from other
branches. Which means a cache written on a feature branch does not help your
colleague, and the entry that matters most is the one `main` writes.

### Reproducibility, and why your wheel changes when your code did not

Build the same commit twice and diff the two wheels. They usually differ. The
causes, in order of frequency:

- **Timestamps** baked into the zip entries.
- **File ordering** from the filesystem.
- **Absolute paths** captured in metadata.
- **Dependency drift** — you did not pin (see above).

The first one is fixed by an environment variable that most Python build
backends honour, including hatchling:

```bash
export SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct)
```

Now the wheel's timestamps come from the commit, not the wall clock, and two
builds of one commit are byte-identical. Why care? Because "the artifact is a
pure function of the commit" is what lets you say *the thing in production is
the thing that passed the tests* — and later, when someone asks whether a
compromised runner swapped your binary, it is the only way to check.

### Build once

```
   ┌─ lint ─┐
   │        │
   ├─ test ─┼──► build ──► [artifact] ──► scan ──► publish ──► deploy
   │        │                  ▲
   └─ types ┘                  └── every downstream job DOWNLOADS this,
                                   nobody rebuilds it
```

Rebuilding downstream is the classic mistake because it *works*. It just quietly
means the thing you scanned and the thing you deployed are two different builds
that happen to come from the same source. Every step after the build should
consume the artifact, never recreate it. This idea comes back in module `0x08`
as "promote the digest, do not rebuild".

## You're done when you can answer these without looking

- Your build passed this morning and fails now, on the same commit. Give three
  causes, in the order you would check them.
- Why is `key: build-${{ github.sha }}` worse than no cache?
- Where do `restore-keys` help, and where do they silently hurt?
- Which of these belongs in a cache and which in an artifact: the wheel, the
  pip download directory, the coverage XML, the Docker layer cache?
- Two builds of one commit produce different wheels. Name the most likely cause
  and the fix.

## General requirements

- Lock file: **`requirements.lock`**, committed.
- Everything still lives in `.github/workflows/ci.yml`.
- Verify with `python checker.py 0x01`.

---

## Tasks

### 0. Pin the world (mandatory)

**File:** `requirements.lock`

Generate a fully resolved lock file — every line `name==version`, transitive
dependencies included — and make `ci.yml` install from it instead of from the
ranges.

Keep `requirements.txt` and `requirements-dev.txt` as the human-editable
statements of intent. The lock is the machine's answer to them.

> The checker requires at least five pinned lines and no unpinned ones. If your
> lock has five lines, you generated it from the wrong file.

### 1. Cache the dependency downloads, and prove the key works (mandatory)

Add a cache keyed on a hash of `requirements.lock`, with a prefix
`restore-keys`. Then run the pipeline **twice** and compare the install step's
duration between run 1 and run 2.

```bash
gh run list --workflow ci.yml --limit 3
gh api repos/:owner/:repo/actions/caches --jq '.actions_caches[] | {key,size_in_bytes}'
```

The checker wants: `hashFiles('requirements.lock')` in the key, at least one
cache entry stored, and a log line in your newest green run showing a cache was
restored. The first run always misses — that is the point of running it twice.

### 2. Produce something (mandatory)

Add a step that builds the wheel and uploads it:

```yaml
      - run: pip install build && python -m build --wheel
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/*.whl
          retention-days: 7
```

Download it from the run page — or `gh run download <id>` — and look inside it
(`python -m zipfile -l dist/*.whl`). Knowing what is in your artifact is
underrated.

### 3. Build once, consume downstream (mandatory)

Split the pipeline so that one job (`build`) produces the wheel and a **second**
job consumes it: `needs: build`, then `actions/download-artifact`, then install
the wheel and run something against it — a smoke import, the fast tests, your
choice.

```yaml
  verify:
    needs: build
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/download-artifact@v4
        with: { name: dist }
      - run: pip install ./*.whl && python -c "import urlshort; print(urlshort.__version__)"
```

The checker verifies the `needs:` edge exists and that the consuming job does not
quietly rebuild the wheel for itself.

### 4. Make the build reproducible, and prove it (mandatory)

In the build job, set `SOURCE_DATE_EPOCH` from the commit, build the wheel
**twice** into different directories, and print the sha256 of each in this exact
shape:

```yaml
      - name: Build twice and compare
        run: |
          export SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct)
          python -m build --wheel --outdir out1
          python -m build --wheel --outdir out2
          for d in out1 out2; do
            echo "REPRODUCIBLE $(sha256sum $d/*.whl | cut -d' ' -f1)"
          done
```

The checker reads the log, extracts every `REPRODUCIBLE <sha256>` line, and
requires at least two of them, all identical.

If they differ: unzip both wheels and diff the file listings *with timestamps*
(`python -m zipfile -l`). What differs will tell you exactly which of the four
causes you hit.

### 5. Put a timeout on every job (mandatory)

Add `timeout-minutes:` to every job in every workflow you own. The default is
**360 minutes**. A job that hangs waiting for input it will never get costs you
six hours of runner time and, worse, six hours before anyone notices the
pipeline is stuck rather than slow.

Pick a number a little above your worst honest run — ten minutes is generous for
everything in this course.

```bash
python checker.py 0x01
```
