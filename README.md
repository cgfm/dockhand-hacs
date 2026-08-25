# Dockhand Integration for Home Assistant

[![GitHub Release][releases-shield]][releases]
[![HACS Custom][hacsbadge]][hacs]
[![License][license-shield]](LICENSE)
[![AI Assisted][ai-assisted-shield]][ai-assisted]
[![CI][ci-shield]][ci]
[![Validation][validation-shield]][validation]

[releases-shield]: https://img.shields.io/github/release/cgfm/dockhand-hacs.svg?style=for-the-badge
[releases]: https://github.com/cgfm/dockhand-hacs/releases
[license-shield]: https://img.shields.io/github/license/cgfm/dockhand-hacs.svg?style=for-the-badge
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[hacs]: https://github.com/hacs/integration
[ai-assisted-shield]: https://img.shields.io/badge/AI-Assisted%20Development-blueviolet.svg?style=for-the-badge
[ai-assisted]: #ai-assisted-development
[ci-shield]: https://img.shields.io/github/actions/workflow/status/cgfm/dockhand-hacs/ci.yml?branch=main&style=for-the-badge&label=CI
[ci]: https://github.com/cgfm/dockhand-hacs/actions/workflows/ci.yml
[validation-shield]: https://img.shields.io/github/actions/workflow/status/cgfm/dockhand-hacs/validate.yml?branch=main&style=for-the-badge&label=HA%20validation
[validation]: https://github.com/cgfm/dockhand-hacs/actions/workflows/validate.yml

A local-polling Home Assistant custom integration for monitoring and controlling Docker containers managed by [Dockhand](https://dockhand.pro/).

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=cgfm&repository=dockhand-hacs&category=integration)

## Requirements

- Home Assistant 2026.8.0 or newer
- A Dockhand instance reachable from Home Assistant
- A local Dockhand account when API authentication is enabled (OIDC/SSO login is not supported by the API client)

## Features

Each Dockhand environment is represented as a parent device. Stacks and containers are linked to their environment as child devices.

Container devices provide state, image/tag, health, CPU, memory, network and block-I/O sensors; a running binary sensor; and guarded start, stop, pause, unpause, restart and image-update buttons. Statistics are available only while Dockhand returns current stats for a running container. A container without a Docker healthcheck reports no health value rather than being treated as unhealthy.

Environment devices provide total, running and stopped container counts. Stack devices provide status and container counts plus active and problem binary sensors.

### Stable container identities

Version 1.2.0 no longer uses the mutable Docker runtime ID as the Home Assistant device identity. It resolves containers in this order:

1. the current Docker runtime ID for rename tracking;
2. Docker Compose project, service and replica labels for recreate/rename tracking;
3. the normalized container name for standalone container recreation.

The resulting logical identity is persisted per Home Assistant config entry and per Dockhand environment. Different Dockhand config entries and environments cannot share devices or entity unique IDs. Compose replicas are separated by their replica number, and a single snapshot cannot assign one identity to two containers.

## Installation

### HACS (recommended)

1. Open HACS and select **Custom repositories**.
2. Add `https://github.com/cgfm/dockhand-hacs` as an **Integration**.
3. Install **Dockhand**.
4. Restart Home Assistant.
5. Add **Dockhand** under **Settings → Devices & services**.

### Manual

Copy `custom_components/dockhand` to `<config>/custom_components/dockhand`, restart Home Assistant, then add the integration from **Settings → Devices & services**.

## Configuration

Enter the Dockhand base URL, optional local username/password, and whether TLS certificates must be verified. TLS verification is enabled by default. Disable it only for a deliberately trusted self-signed installation; plain HTTP and disabled verification do not protect credentials on the network.

The setup flow lets you choose monitored environments. The options flow changes the 10–300 second polling interval and environment selection, then reloads the entry through Home Assistant's normal lifecycle. **Reconfigure** changes URL, authentication and TLS settings. Expired sessions automatically trigger one serialized login retry; persistent authentication failure starts Home Assistant reauthentication.

## Upgrade from 1.1.0 to 1.2.0

Version 1.2.0 includes a one-time, restart-safe device/entity registry migration. Create a full Home Assistant backup before installing it.

1. Create and download a full Home Assistant backup.
2. Record important automations/dashboards that reference Dockhand entities.
3. Install 1.2.0 in a test Home Assistant instance first when possible.
4. Update the custom integration and restart Home Assistant once.
5. Wait for the first successful Dockhand refresh.
6. Verify the Dockhand integration, device hierarchy, original entity IDs, automations and dashboards before deleting the backup.

### What the migration does

- It operates only on devices and entities owned by the config entry currently being set up.
- It reuses the device/entity with the best existing entity ID: an unsuffixed ID wins, then the lowest numeric suffix (`_2` before `_3`, and so on).
- After duplicate removal, a winning suffixed entity reclaims the original unsuffixed ID when that ID is free; unrelated conflicts are never replaced.
- It changes registry unique IDs and device links while preserving the chosen entity ID and user customizations where possible.
- It resumes safely after a partial prior migration and is idempotent on repeated setup/reload.
- It creates environment devices before stack/container children and uses Home Assistant 2026.8 device IDs for parent links.
- It never treats an empty or malformed required API response as permission to delete current resources.
- Resources absent from successful API snapshots are retained for a persisted seven-day grace period. Cleanup is evaluated during later entry setup, not every poll.
- An unknown unique-ID schema, a foreign entity reference or an ownership conflict keeps the legacy record and writes a warning instead of deleting data.

Current duplicate devices for a container are consolidated during migration after their recognized entities have been transferred. Entities whose resources are temporarily absent remain registered and unavailable, so returning resources do not acquire new `_2`/`_3` IDs.

### Rollback

The safe rollback is to restore the full pre-upgrade Home Assistant backup. Downgrading only the integration to 1.1.0 is not a registry rollback: 1.1.0 does not understand the new stable identifiers and can create Docker-ID-based duplicates again.

## Container actions

Buttons are available only when the current container state permits the corresponding operation. The update button asks Dockhand to pull the configured image and recreate the container. Test this operation on non-critical containers first and keep application-specific backups; Dockhand, Docker and the container image determine the actual recreate behavior.

## Diagnostics and privacy

Downloaded diagnostics redact the Dockhand URL, username and password. They contain aggregate counts and state/health totals only—not container, environment, stack or image names. Docker inspect is used only for newly observed runtime IDs, and only Compose labels are retained; environment variables, mounts and other inspect data are discarded.

## Troubleshooting

- **Cannot connect:** verify routing, reverse-proxy path, TLS trust and that Home Assistant can reach Dockhand.
- **Authentication failed:** use a local Dockhand account and complete the reauthentication flow.
- **No containers:** verify the selected environments and Dockhand's Docker connection.
- **Stats unavailable:** Dockhand stats are requested only for running containers; individual stats failures do not discard the main snapshot.
- **A legacy device remains after migration:** inspect the Home Assistant log for an ownership, unique-ID or foreign-entity warning. Do not edit `.storage`; report the sanitized warning and diagnostics in the [issue tracker](https://github.com/cgfm/dockhand-hacs/issues).
- **A removed device remains:** this is expected during the seven-day safety grace period. A later integration reload/restart evaluates cleanup.

## Development and validation

The pinned test environment targets Home Assistant 2026.8.3 and Python 3.14. Run:

```bash
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install --requirement requirements_test.txt
ruff format --check custom_components tests
ruff check custom_components tests
mypy custom_components/dockhand
pytest --cov=custom_components.dockhand --cov-report=term-missing
```

CI also runs official hassfest and HACS validation. Release publication is manual: the `Publish release` workflow accepts an existing `vMAJOR.MINOR.PATCH` tag, validates it against the manifest and uses the `release` environment. Configure required reviewers for that environment before its first use.

## License

[MIT](LICENSE)

## AI-assisted development

AI tools assisted with parts of implementation, review, testing and documentation. Maintainer review, reproducible automated validation and release approval remain required.

## Credits

Built by [@cgfm](https://github.com/cgfm) for the Home Assistant community. Dockhand is created by [Finsys](https://github.com/Finsys/dockhand).
