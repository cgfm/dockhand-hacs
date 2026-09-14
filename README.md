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

Version 1.3.0 adds administrator-only, on-demand live container logs through the discoverable [**Dockhand Logs Card**](#dashboard-card-live-container-logs) and Dockhand-native image-update detection without requiring DIUN. Both features keep Dockhand responsible for Docker access while Home Assistant provides a secure display and explicit user actions.

Each Dockhand environment is represented as a parent device. Stacks and containers are linked to their environment as child devices.

Container devices provide state, image/tag, health, CPU, memory, network and block-I/O sensors; running and image-update binary sensors; and guarded start, stop, pause, unpause, restart and image-update buttons. Statistics are available only while Dockhand returns current stats for a running container. A container without a Docker healthcheck reports no health value rather than being treated as unhealthy.

Environment devices provide total, running and stopped container counts plus a manual image-update-check button. Stack devices provide status and container counts plus active and problem binary sensors.

### Image updates detected by Dockhand

DIUN is not required for this workflow. Dockhand performs registry checks and persistently records pending image updates; Home Assistant only reads that cached result, displays it and lets a user start an update. A normal Home Assistant coordinator refresh calls only Dockhand's read-only pending-update endpoint and never starts a registry scan.

```text
Dockhand Scheduler
    ↓
Image Registry Check
    ↓
Dockhand Pending Updates
    ↓
dockhand-hacs
    ↓
binary_sensor.<container>_image_update_available
    ↓
HA Notification / Dashboard
    ↓
button.<container>_update_image
    ↓
Dockhand pulls + recreates container
```

Recommended operation:

- In Dockhand, enable the `env_update_check` scheduler, leave **Auto Update** off and run it at an appropriate interval, for example every six hours.
- Home Assistant reads Dockhand's persisted findings during its normal polling cycle.
- The environment's **Check image updates** button can start an on-demand Dockhand registry check. It is also the fallback when the installed Dockhand version does not provide the scheduler.
- A container's **Image update available** binary sensor turns on for a pending update. Its attributes contain only the configured image and Dockhand check time.
- The existing **Update image** button becomes available only while that container has a pending update. Dockhand still performs the pull and recreate; after success Home Assistant refreshes immediately and the pending state disappears.

Home Assistant does not install updates automatically, run a second periodic registry scheduler or fabricate version numbers for an `UpdateEntity`. Dockhand remains responsible for detection and installation, while Home Assistant provides status, automations and explicit user actions.

For example, notify a phone when Dockhand finds an image update:

```yaml
automation:
  - alias: Docker image update available
    triggers:
      - trigger: state
        entity_id:
          - binary_sensor.paperless_image_update_available
        to: "on"
    actions:
      - action: notify.mobile_app_phone
        data:
          title: Docker Update
          message: Paperless has a new container image available.
```

## Dashboard cards

Dockhand registers four cards automatically. After installing or updating the integration, restart Home Assistant and hard-refresh the browser page (or fully close and reopen the Companion App). Open **Edit dashboard → Add card** and search for **Dockhand**. No manual Lovelace resource is required.

All cards have a visual editor and are suggested under **Community** when a compatible Dockhand entity is selected. They resolve related entities through Home Assistant's device registry, so renamed entity IDs and recreated containers do not need hard-coded mappings.

### Dockhand Overview Card

`custom:dockhand-overview-card` summarizes one Dockhand environment. It shows running/stopped/problem/update totals and switchable **All**, **Problems**, **Updates** and **Stacks** views. Container actions use the integration's existing guarded button entities; stop, restart and update require confirmation. The refresh icon invokes the environment's **Check image updates** button.

Choose any Dockhand environment sensor in the visual editor, or use YAML:

```yaml
type: custom:dockhand-overview-card
entity: sensor.dockhand_container_count
name: Docker
default_view: problems
show_metrics: true
show_actions: true
```

`default_view` accepts `all`, `problems`, `updates` or `stacks`. Stopped and unhealthy containers appear under **Problems**; an update appears only when Dockhand reports it as pending.

### Dockhand Container Card

`custom:dockhand-container-card` combines a container's state, health, CPU, memory, image and I/O values with its currently available lifecycle/update actions. Its log button embeds the same administrator-only live log viewer described below and opens no connection until requested.

Choose any entity belonging to the Dockhand container device:

```yaml
type: custom:dockhand-container-card
entity: sensor.paperless_state
name: Paperless
show_metrics: true
show_actions: true
show_logs: true
tail: 200
```

### Dockhand Stack Card

`custom:dockhand-stack-card` displays stack state, total/running/stopped container counts and the names Dockhand reports for problem containers.

```yaml
type: custom:dockhand-stack-card
entity: sensor.paperless_stack_status
show_containers: true
```

### Dashboard card: live container logs

Available since version 1.3.0, Dockhand includes a small, mobile-friendly **Dockhand Logs Card** (`custom:dockhand-logs-card`). Opening the card creates one Home Assistant WebSocket subscription and one upstream Dockhand SSE connection for that viewer. Closing/removing the card immediately unsubscribes and closes the SSE connection. No log polling runs in the background, and log contents are never written to entity states, attributes, the recorder, diagnostics, or Python logs.

After installing or updating Dockhand, restart Home Assistant and refresh the browser page. In the Companion App, fully close and reopen the app. Then add the card through the dashboard editor:

1. Open the dashboard, choose **Edit dashboard**, then **Add card**.
2. Search for **Dockhand Logs Card** in the card list. Alternatively, select a Dockhand container entity first; the card is offered in the **Community** suggestions for compatible entities.
3. Select any sensor belonging to the desired Dockhand container. The container state sensor is a good default.
4. Optionally change the displayed name, initial line count (`tail`) and maximum browser buffer (`max_lines`) in the visual editor.

The integration serves and registers the JavaScript modules automatically. Do not add a Lovelace resource manually.

You can also add or edit the card in YAML with any Dockhand **container** sensor or running binary sensor:

```yaml
type: custom:dockhand-logs-card
entity: sensor.paperless_state
name: Paperless
tail: 200
```

The entity supplies the current runtime container ID, environment ID and config-entry ID. This is preferred over hard-coding a Docker runtime ID because Dockhand entities retain their logical identity when a container is recreated. The entity must come from this integration; the backend verifies the requested ID against the selected environment and current coordinator snapshot.

An explicit configuration is also supported when no suitable entity is available:

```yaml
type: custom:dockhand-logs-card
entry_id: 01JEXAMPLECONFIGENTRY
container_id: a1b2c3d4e5f6
env_id: 1
name: Paperless
tail: 200
max_lines: 3000
```

`tail` accepts 1–5000 initial lines and defaults to 200. `max_lines` controls the bounded browser-only buffer (100–5000, default 3000). Auto-scroll starts enabled and stops when the viewer scrolls upward. Pause keeps the subscription open but buffers only up to `max_lines`; Clear removes only the local card buffer.

Live logs are intentionally restricted to Home Assistant administrators. The browser talks only to Home Assistant and never receives Dockhand credentials or session cookies. Each browser/viewer has an independent stream.

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

Buttons are available only when the current container state permits the corresponding operation. The update button additionally requires a pending update reported by Dockhand; it does not serve as an implicit force-pull button. It asks Dockhand to pull the configured image and recreate the container, then requests an immediate coordinator refresh. Test this operation on non-critical containers first and keep application-specific backups; Dockhand, Docker and the container image determine the actual recreate behavior.

## Diagnostics and privacy

Downloaded diagnostics redact the Dockhand URL, username and password. They contain aggregate counts and state/health totals only—not container, environment, stack or image names. Docker inspect is used only for newly observed runtime IDs, and only Compose labels are retained; environment variables, mounts and other inspect data are discarded.

## Troubleshooting

- **Cannot connect:** verify routing, reverse-proxy path, TLS trust and that Home Assistant can reach Dockhand.
- **Authentication failed:** use a local Dockhand account and complete the reauthentication flow.
- **No containers:** verify the selected environments and Dockhand's Docker connection.
- **Stats unavailable:** Dockhand stats are requested only for running containers; individual stats failures do not discard the main snapshot.
- **Image update sensor unavailable:** the installed Dockhand version may not support `GET /api/containers/check-updates`, the configured account may lack permission, or the endpoint may be temporarily rate-limited. Other Dockhand entities continue working. Upgrade Dockhand or use Dockhand's own update UI until the endpoint is available.
- **No update is shown yet:** enable Dockhand's `env_update_check` scheduler or press **Check image updates** on the environment device. Normal Home Assistant polling deliberately does not perform registry checks.
- **Registry/Docker Hub rate limit:** wait for the provider's limit to reset and run the manual check again. Partial per-image failures remain Dockhand's responsibility and do not replace already persisted successful findings.
- **Update failed after a recreate:** refresh the integration first. The button always uses the newest runtime ID in the coordinator snapshot, but Dockhand can reject an action when the container changes again between refresh and button press.
- **A legacy device remains after migration:** inspect the Home Assistant log for an ownership, unique-ID or foreign-entity warning. Do not edit `.storage`; report the sanitized warning and diagnostics in the [issue tracker](https://github.com/cgfm/dockhand-hacs/issues).
- **A removed device remains:** this is expected during the seven-day safety grace period. A later integration reload/restart evaluates cleanup.
- **A Dockhand card is missing from the card picker:** restart Home Assistant after installing/updating the integration, then hard-refresh the browser page or fully close and reopen the Companion App. Search for **Dockhand** in the card list, or choose a compatible Dockhand entity and look under **Community**. The modules are served below `/dockhand/frontend/`; do not add a manual Lovelace resource.
- **The logs card is unknown in YAML:** first follow the restart and frontend-refresh steps above. If the problem remains, open `/dockhand/frontend/dockhand-logs-card.js` on the same Home Assistant host; a 404 response means the integration files or restart are incomplete.
- **Live logs require an administrator:** log streams may expose passwords, tokens and personal data written by applications, so non-admin dashboard users cannot subscribe.
- **Container or environment unavailable:** use a current Dockhand container entity in the card and verify that its environment is selected in the integration options. Explicit Docker runtime IDs can change after a recreate.
- **Dockhand denied/unavailable:** verify that the configured local Dockhand account can open container logs, the Home Assistant host can reach Dockhand, and any reverse proxy permits long-lived `text/event-stream` responses without buffering or a short read timeout.
- **Stream ended after an integration reload:** entry reload deliberately cancels every open SSE task. Use **Reconnect** in the card (or close and reopen it) after the entry has loaded again.

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
