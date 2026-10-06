# First public release: maintainer guide

The target is **v0.1.0**. This document prepares publication; no public repository, release, image, or license is assumed to exist yet.

## Version and license

`frontend/package.json` is the authoritative application version. Its lockfile must agree. The backend reads this manifest in development and its bundled copy in Docker; FastAPI exposes it in OpenAPI. Release preflight requires a matching `v<version>` tag. Update package.json/package-lock.json together when preparing a new release.

0.x is usable but evolving. Patch releases fix bugs. Minor releases add capabilities and, before 1.0, can include documented compatibility changes. Never promise that an older image can open a newer database.

The owner approved **Apache-2.0**. Its full text is in `LICENSE`, included in the image, and its SPDX identifier is read from `frontend/package.json`. Preserve the vendored MediaMTX reader license and review dependency obligations before publishing. Release preflight refuses to publish without a license file and identifier.

## GitHub setup

Create the intended public repository and select `master` as its default branch. Configure Actions to allow the official actions used here. Workflows use read-only `contents` permissions; fork PRs never receive Docker Hub credentials. Do not use `pull_request_target` to execute contributions.

Create a **dockerhub** GitHub environment with required reviewers and deployment restrictions allowing only reviewed release tags. Put `DOCKERHUB_TOKEN` in that environment, not in normal CI. The release checkout must be an ancestor of `origin/master`; required branch checks protect the source being released.

Configure repository variables:

| Variable | Value |
|---|---|
| `DOCKERHUB_USERNAME` | Docker Hub login account allowed to publish |
| `DOCKERHUB_IMAGE` | Lowercase `namespace/repository`, for example `example/tuponvr`; no tag |
| `DOCKER_PLATFORMS` | Default `linux/amd64`; after native ARM validation, `linux/amd64,linux/arm64` |

Configure environment secret `DOCKERHUB_TOKEN` as a scoped Docker Hub access token with image push permission. Do not use an account password or put credentials in `.env`, build arguments, source files, or PR workflows. The image namespace can be an organization different from the login account if that account has permission.

Protect `master`: require pull requests/review and successful **validate**, **Docker (amd64)**, and **Docker (arm64)** checks; disable force pushes and deletion. Review the exact check names after the first CI run. Enable GitHub private vulnerability reporting before publishing `SECURITY.md` as the disclosure policy.

Official actions follow this repository's version-tag convention (checkout v6, setup-python v5, setup-node v7, and current Docker action major versions), and monthly Dependabot updates cover Actions, pip, npm, and Docker bases. For stricter supply-chain policy, resolve and review full commit SHA pins before publication and keep version comments; no action commit hashes are invented here. See [GitHub secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use).

## CI behavior

`.github/workflows/ci.yml` runs on PRs, pushes to master, manual dispatch, and trusted release reuse. It installs pinned Python/Node dependencies, checks dependency consistency and Ruff F rules, runs frontend tests/type checking/builds and backend tests, downloads a checksum-verified MediaMTX binary, and runs the synthetic RTSP integration and Chromium suites.

Separate native Docker jobs run on `ubuntu-24.04` (amd64) and `ubuntu-24.04-arm` (arm64), use Buildx/GitHub layer caching, build without pushing, and run `scripts/smoke.py`. This clean deployment test uses fresh bind mounts and configuration, a synthetic FFmpeg publisher, real MediaMTX, authenticated APIs, recording, missing-marker protection/recovery, archive downloads/ranges, timezone settings, and restart persistence. It does not use real cameras or developer state.

## Release behavior

`.github/workflows/release.yml` triggers only when a GitHub Release is **published**, never on an ordinary master push. It verifies master ancestry, version/prerelease consistency, license, image name, and platform selection. It reruns CI without registry secrets, then waits for the **dockerhub** environment approval before its publishing job can receive the token.

For stable `v0.1.0`, Docker metadata tooling produces `0.1.0`, `0.1`, and `latest`; it does not create `0`. Prerelease versions such as `v0.1.0-rc.1` produce only their full prerelease tag, with no minor or latest aliases. The verifier compares the release ID with GitHub’s latest stable release before assigning the minor/latest aliases. Older or non-latest releases publish only their full version tag, preventing them from moving stable aliases backwards. Publish in increasing version order and review which release GitHub marks latest.

The image carries title, description, source, version, Git revision, and license labels. Native BuildKit provenance (`mode=max`) and SBOM attestations are enabled, with a separate release cache. Never pass secrets as build arguments: provenance can expose argument values. See [Docker attestations](https://docs.docker.com/build/ci/github-actions/attestations/), [metadata](https://docs.docker.com/build/ci/github-actions/manage-tags-labels/), and [caching](https://docs.docker.com/build/ci/github-actions/cache/).

Release credentials are confined to the publishing job after validation/review. The release tag must point to trusted reviewed source, including the Dockerfile and npm lockfile/scripts. Protect the release environment and review any workflow changes. No self-hosted runner is required.

## Architecture support

The Node UI build runs on the builder's native platform and emits portable static files. The runtime uses Python 3.12 slim, Debian FFmpeg/CA certificates/tzdata, and pip dependencies. MediaMTX is a separate version-pinned official image. Pydantic Core is the significant native Python dependency; arm64 wheel availability must be checked along with all transitives.

Local amd64 builds and recording tests can be verified here. **Native arm64 execution has not been verified in this preparation environment.** The ARM CI job must pass on GitHub, followed by a real target-machine deployment test, before enabling ARM publishing or advertising support. Multi-platform publishing is prepared but defaults to amd64. There is no 32-bit ARM claim.

## Security/privacy review

The initial audit scanned every file snapshot across eight reachable commits and the tracked working tree. No confirmed real credentials, private keys, tracked runtime databases, recordings, or binary screenshots were found. Credential-like strings in tests are synthetic fixtures; the UI camera IP is a generic example. Git author names/emails are owner-identifying metadata and should be reviewed before public publication. This is a practical pattern/manual review, not a guarantee that every possible secret has been detected; inaccessible runtime database contents and unreachable Git objects were not inspected.

Local `.env` contains configured authentication and local SQLite/runtime files exist outside version control. Do not distribute the working directory as a raw archive. `.gitignore` excludes these artifacts; the Docker context uses an allowlist of production inputs so runtime files, Git metadata, logs, screenshots, IDE state, and environment files are excluded. No credential rotation or history rewrite was performed. No exposed historical credential requiring rotation was confirmed. The dependency audit found a high-severity indexed-source-map denial-of-service advisory in the build-time `source-map-js` dependency; its lockfile was updated from 1.2.1 to the compatible patched 1.2.2. If the owner discovers any real historical secret, rotate it and approve history cleanup before publishing; use a separately reviewed history-cleaning process rather than editing history during release preparation.

## Owner checklist

- [x] Owner selected Apache-2.0; license text and package/image metadata prepared.
- [ ] Review the audit, author metadata, and public source; rotate/clean history only if a real historical secret is found.
- [ ] Configure and test a private security disclosure channel; confirm GitHub private vulnerability reporting is enabled.
- [ ] Create the public GitHub repository and Docker Hub repository with the intended names.
- [ ] Create the scoped Docker Hub token; configure variables and the protected dockerhub environment.
- [ ] Enable branch protection and review workflow action references/pinning policy.
- [ ] Push the reviewed repository manually and confirm all CI checks, including native ARM if intended.
- [ ] Run documented clean deployment and restart/persistence tests on the intended hardware/storage.
- [ ] Add/review authentic Overview, live Multiview, and Storage screenshots; the supplied Archive screenshot uses synthetic footage.
- [ ] Decide whether to enable ARM publishing only after its tests pass; leave the default otherwise.
- [ ] Review third-party notices and corresponding-source obligations for the exact distributed image; see [third-party details](third-party.md).
- [ ] Review changelog and upgrade/backup instructions, then manually create and publish GitHub Release v0.1.0.
- [ ] Verify Docker Hub tags, OCI metadata, provenance/SBOM, and pull on a clean machine.
- [ ] Test published-image deployment on amd64 and arm64 if advertised, plus update/persistence and backup restoration.

These are owner actions, not actions performed by the preparation agent.

## Coupled React dependency updates

Dependabot groups `react`, `react-dom`, `@types/react`, and `@types/react-dom` in one version-update PR. React and React DOM require identical runtime versions; React DOM types may require a newer React type version. Review these four packages together and run the full CI, including browser tests. Do not bypass peer dependency errors with `--force` or `--legacy-peer-deps`.

When adopting this configuration, close obsolete separate React/React DOM update PRs and use the grouped replacement. Re-running the old PRs without changing their dependencies will reproduce the same failures.
