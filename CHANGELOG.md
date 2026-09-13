# Changelog

All notable changes to this project are documented here. Releases use `vMAJOR.MINOR.PATCH` Git tags.

## [Unreleased]

## [1.3.0] - 2026-09-13

### Added

- On-demand container live logs through Dockhand SSE, an admin-only Home Assistant WebSocket subscription, and a responsive Lovelace custom card.
- Chunk-safe SSE parsing for LF/CRLF streams, JSON event payloads, heartbeats, split UTF-8/TCP chunks and final unterminated events.
- Bounded browser log buffers, pause/resume, local clear, auto-scroll handling, reconnect support and distinct stderr styling.
- Lifecycle and security tests covering unsubscribe, config-entry unload, concurrent viewers, strict target validation and sanitized upstream errors.
- Dockhand-native image-update detection through its persisted pending-update endpoint, exposed as one binary sensor per container.
- An environment-level **Check image updates** button that delegates fresh registry checks to Dockhand.
- API, coordinator and entity tests for pending updates, multi-environment isolation, container recreation, rate limits and action refreshes.

### Changed

- Container image-update buttons are now available only when Dockhand reports a pending update; successful actions immediately refresh the coordinator.
- Pending-update endpoint failures are isolated from normal container, stack and statistics refreshes.

### Security

- Live log contents remain transient: they are not polled, recorded, added to entities or written to integration logs, and Dockhand credentials remain backend-only.

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

[1.3.0]: https://github.com/cgfm/dockhand-hacs/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/cgfm/dockhand-hacs/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/cgfm/dockhand-hacs/releases/tag/v1.1.0
