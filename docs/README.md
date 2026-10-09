# TupoNVR documentation

[Project](../README.md) · [Русский](README_RU.md)

Start with [installation](installation.md); use the guides below for operation and deployment decisions. English files use `<topic>.md`, Russian translations use `<topic>_RU.md` in this same directory. Each paired guide links to its translation. Commands, defaults, features, and limitations should be updated together.

| Topic | English | Русский |
|---|---|---|
| Camera YAML bootstrap, import, export and migration | [Camera configuration](camera-configuration.md) | [Конфигурация камер](camera-configuration_RU.md) |
| Installation and first camera | [Installation](installation.md) | [Установка](installation_RU.md) |
| Environment variables and container permissions | [Configuration](configuration.md) | [Конфигурация](configuration_RU.md) |
| Camera editing, schedules, webhooks, language | [Operating guide](operations.md) | [Эксплуатация](operations_RU.md) |
| Retention, external mounts, backup and recovery | [Storage](storage.md) | [Хранилища](storage_RU.md) |
| Recording time, synchronized playback, export and snapshots | [Recording and archive](time-and-archive.md) | [Запись и архив](time-and-archive_RU.md) |
| Previewing and deleting recorded segments | [Manual cleanup](recording-cleanup.md) | [Ручная очистка](recording-cleanup_RU.md) |
| Login, media boundaries, HTTPS and migration | [Security](security-hardening.md) | [Безопасность](security-hardening_RU.md) |
| Bridge / Linux host networking, WebRTC, NAT and VPN | [Networking](networking.md) | [Сеть](networking_RU.md) |
| Service diagnostics, camera health and compatibility | [Troubleshooting](troubleshooting.md) | [Диагностика](troubleshooting_RU.md) |
| Components and recording internals | [Architecture](architecture.md) | [Архитектура](architecture_RU.md) |
| Interface and HTTP routes; live schema at `/docs` | [API](api.md) | [API](api_RU.md) |
| Dependency licenses and image distribution | [Third-party notices](third-party.md) | [Стороннее ПО](third-party_RU.md) |

## Project and maintainer references

These development, policy and historical references remain in English:

- [Contributing and validation](../CONTRIBUTING.md)
- [Vulnerability reporting policy](../SECURITY.md); deployment guidance is bilingual above.
- [Changelog](../CHANGELOG.md)
- [Release preparation](releasing.md): publication workflow, architecture validation and owner checklist.
- [Historical UX audit](ux-audit.md): interface decisions and recorded validation; later time/archive changes supersede its UTC-only observations.

## Documentation assets

[Archive screenshot](images/archive-synchronized.png): actual UI from browser tests with synthetic video, three cameras playing and a fourth with a gap. It demonstrates the interface, not real-camera compatibility. No hosted demo or additional screenshots are included; synthetic camera testing is described in the contribution guide.

[Development and validation](development.md) — Docker builds and source workflows for contributors.
