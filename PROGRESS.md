# Progress

Tick a module when `python checker.py <module>` is fully green.

- [ ] `0x00` The repo, the runner, the first green run — 5 tasks
- [ ] `0x01` The build: pinned, cached, reproducible — 6 tasks
- [ ] `0x02` The test gate — 6 tasks
- [ ] `0x03` Static analysis, and the local mirror — 5 tasks
- [ ] `0x04` Containers: build, tag, publish — 6 tasks
- [ ] `0x05` Secrets, permissions, supply chain — 6 tasks
- [ ] `0x06` Pipeline architecture — 6 tasks
- [ ] `0x07` Environments, approvals, a real deploy — 6 tasks
- [ ] `0x08` Release engineering — 6 tasks
- [ ] `0x09` Deployment strategies and rollback — 6 tasks
- [ ] `0x0A` The feedback loop — 5 tasks
- [ ] `0x0B` Debugging drills — 6 tasks
- [ ] `0x0C` Final project — 8 tasks

**77 tasks.**

## Account and machine state

Some modules need something that lives outside the repository. Tick them off so
you know what you have set up:

- [ ] `gh auth login`, with scopes `read:packages` and `workflow`
- [ ] repository created, **public**, default branch `main`
- [ ] GHCR package `urlshort` exists and is **public** (Package settings →
      Change visibility) — needed from `0x04`
- [ ] repository secret `SMOKE_TOKEN` — needed by `0x05`
- [ ] self-hosted runner registered with the label `local` — needed from `0x07`
- [ ] environments `staging` and `production`, with a required reviewer on
      production — needed from `0x07`
- [ ] environment variables `APP_ENV` (different per environment) — `0x07`
- [ ] a Kubernetes cluster (`kubectl get nodes`) — needed by `0x09`
- [ ] branch protection or a ruleset on `main` — needed by `0x0A`

## The pipeline as it stands

Keep this honest. It is the fastest way to see what is still missing.

| Stage | Where | Done |
|-------|-------|------|
| lint + format | `ci.yml` | [ ] |
| type check | `ci.yml` | [ ] |
| unit tests (matrix) | `ci.yml` | [ ] |
| coverage gate | `ci.yml` | [ ] |
| slow tests | `nightly.yml` | [ ] |
| secret scan | | [ ] |
| dependency scan | | [ ] |
| Dockerfile lint | | [ ] |
| image build + push | | [ ] |
| SBOM + provenance | | [ ] |
| deploy to staging | `deploy.yml` | [ ] |
| smoke test | `deploy.yml` | [ ] |
| auto rollback | `deploy.yml` | [ ] |
| release on tag | `release.yml` | [ ] |
| promote by digest | `release.yml` | [ ] |
| production approval | environment | [ ] |

## Notes to self

Things that surprised you, and the numbers you measured (pipeline duration,
image size, rollback time). Fill this in as you go — six months from now it is
the most useful page in the repo.
