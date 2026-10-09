# Contributing to TupoNVR

Keep changes focused on practical home/small-installation workflows. Preserve stream-copy recording and the two-service deployment. Discuss substantial features before implementing them; avoid adding infrastructure merely to match a larger VMS.

## Development and validation

See [the developer guide](docs/development.md) for local Docker builds, running from source, frontend/backend development, testing and debugging. Regular users should follow [installation](docs/installation.md).

## Pull requests

Explain the problem, resulting behavior, and validation. Add meaningful tests for bugs and features, update both interface languages when changing UI text, and update documentation/configuration when needed. Separate unrelated changes. Persistent-data, recording, storage, and authentication changes need explicit design review.

Report vulnerabilities as described in [SECURITY.md](SECURITY.md), not in public issues. Contributions are made under the project’s [Apache-2.0 license](LICENSE). See [release preparation](docs/releasing.md) for maintainer steps.
