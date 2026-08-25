# Changelog

All notable changes to this project are documented here. Releases use `vMAJOR.MINOR.PATCH` Git tags.

## [1.2.0] - 2026-08-25

### Added

- Stable, config-entry- and environment-scoped container identities that survive Docker recreation.
- Compose project/service/replica identity support using sanitized Docker inspect labels.
- Restart-safe, idempotent device/entity registry migration from 1.1.x identifiers.
- Environment and stack devices/entities, image/version/health/block-I/O sensors, and lifecycle/update buttons.
- Reauthentication, reconfigure and reload-aware options flows.
- Privacy-preserving diagnostics and full English/German plus fallback translation schemas.
- Pytest, Ruff, mypy, hassfest and HACS validation workflows, plus a manually approved release workflow.

### Fixed

- Duplicate devices/entities caused by mutable Docker runtime IDs.
- Migration conflicts that could prefer `_4` over `_2` or discard the original unsuffixed entity ID.
- Completed partial migrations that left active entities on `_2`/`_3` IDs even after the original ID became free.
- Cross-config-entry device lookup/removal and deprecated Home Assistant device-registry APIs.
- Immediate deletion after empty/transient API results; removal now requires a persisted seven-day grace period.
- Duplicate runtime entity objects after a resource disappeared and returned.
- Parent-device races and deprecated `via_device` identifiers on Home Assistant 2026.8.
- Concurrent 401 login storms, missing request timeouts, unsafe error-body logging, invalid JSON and unbounded rate-limit delays.
- Unsafe container IDs/actions in API paths and unavailable buttons for impossible lifecycle actions.
- Stale statistics availability, malformed stack details, nonfinite metrics, image registry ports and containers without healthchecks.
- Diagnostics retaining resource metadata and coordinator retention of sensitive Docker inspect fields.
- Options changes partially mutating a live coordinator instead of using Home Assistant reload.
- Incorrect repository links and inconsistent HACS/Home Assistant minimum versions.

### Changed

- Home Assistant 2026.8.0 is now the minimum supported version.
- TLS certificate verification is enabled by default for newly configured entries.
- Removed the redundant `aiohttp` package requirement because Home Assistant supplies the client runtime.

### Upgrade warning

Create a full Home Assistant backup before upgrading from 1.1.x. The registry migration is designed to preserve the best existing entity IDs and user customizations, but a downgrade alone cannot reverse registry identifiers. Restore the pre-upgrade backup for rollback.

## [1.1.0]

- Initial published Dockhand integration release.

[1.2.0]: https://github.com/cgfm/dockhand-hacs/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/cgfm/dockhand-hacs/releases/tag/v1.1.0
