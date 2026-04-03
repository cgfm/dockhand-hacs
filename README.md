# Dockhand Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration to monitor and control Docker containers managed by [Dockhand](https://dockhand.pro/).

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=cgfm&repository=dockhand-hacs&category=integration)

---

## Features

### Device Hierarchy

Each Dockhand **environment** appears as a parent device. **Stacks** and **containers** are child devices of their environment.

```
Environment (e.g. "Home Server")
├── Stack (e.g. "monitoring")
│   └── [stack entities]
└── Container (e.g. "nginx")
    └── [container entities]
```

### Container Entities

| Entity | Type | Description |
|--------|------|-------------|
| State | Sensor | Container state (`running`, `exited`, `paused`, …) |
| Image | Sensor | Full Docker image reference |
| Image version | Sensor | Tag or digest extracted from the image reference |
| Health | Sensor | Health check result (`healthy`, `unhealthy`, `starting`) |
| CPU | Sensor | CPU usage in % |
| Memory usage | Sensor | Memory consumption in MB |
| Memory | Sensor | Memory usage in % |
| Network RX | Sensor | Received data in MB (total increasing) |
| Network TX | Sensor | Transmitted data in MB (total increasing) |
| Disk read | Sensor | Block device read in MB (total increasing) |
| Disk write | Sensor | Block device write in MB (total increasing) |
| Running | Binary Sensor | `on` when the container is running |
| Start | Button | Start the container |
| Stop | Button | Stop the container |
| Pause | Button | Pause the container |
| Unpause | Button | Unpause the container |
| Restart | Button | Restart the container |
| Update | Button | Pull a fresh image and recreate the container |

### Stack Entities

| Entity | Type | Description |
|--------|------|-------------|
| Status | Sensor | Stack status (`active` / `inactive`) |
| Containers | Sensor | Total container count in the stack |
| Running containers | Sensor | Count of running containers |
| Stopped containers | Sensor | Count of stopped containers |
| Active | Binary Sensor | `on` when the stack is active |
| Problem | Binary Sensor | `on` when any container is not running or unhealthy |

### Environment Entities

| Entity | Type | Description |
|--------|------|-------------|
| Containers | Sensor | Total container count in the environment |
| Running containers | Sensor | Count of running containers |
| Stopped containers | Sensor | Count of stopped containers |

---

## Installation

### HACS (recommended)

Click the button above, or:

1. Open HACS in Home Assistant
2. Click **⋮** → **Custom repositories**
3. Add `https://github.com/cgfm/dockhand-hacs` with category **Integration**
4. Search for **Dockhand** and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/dockhand` folder into your HA `config/custom_components/` directory
2. Restart Home Assistant

---

## Configuration

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for **Dockhand**
3. Enter:
   - **URL**: The URL of your Dockhand instance (e.g. `http://192.168.1.100:3000`)
   - **Username / Password**: Only required when Dockhand authentication is enabled
   - **Verify SSL**: Enable when using HTTPS with a valid certificate
4. Select which environments to monitor
5. Done!

### Options

After setup you can adjust the **update interval** (default: 30 seconds) via the integration options.

---

## Authentication

| Scenario | What to do |
|----------|-----------|
| Auth disabled | Provide only the URL — no credentials needed |
| Local auth | Enter username and password; the integration handles session cookies and auto re-login on 401 |
| OIDC / SSO | Not supported via API — create a local Dockhand user for HA access |

---

## Example Automations

### Notify when a container stops

```yaml
automation:
  - alias: "Container stopped notification"
    trigger:
      - platform: state
        entity_id: binary_sensor.nginx_running
        from: "on"
        to: "off"
    action:
      - service: notify.mobile_app
        data:
          title: "Container stopped"
          message: "nginx is no longer running!"
```

### Alert on stack problem

```yaml
automation:
  - alias: "Stack problem alert"
    trigger:
      - platform: state
        entity_id: binary_sensor.monitoring_problem
        to: "on"
    action:
      - service: notify.mobile_app
        data:
          title: "Stack issue"
          message: "One or more containers in 'monitoring' need attention."
```

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Cannot connect | Ensure the Dockhand URL is reachable from your HA host |
| Authentication failed | Verify username/password; OIDC users need a local account |
| No containers shown | Confirm environments are selected and Dockhand can reach the Docker daemon |
| Stats unavailable | Stats are only fetched for running containers |
| Health always unknown | Container has no `HEALTHCHECK` defined in its image |

Use **Settings → Devices & Services → Dockhand → Download Diagnostics** for debug info.

---

## Requirements

- Home Assistant 2024.1.0 or newer
- Dockhand instance accessible from the HA host
- Python 3.12+ (bundled with modern HA)

---

## License

MIT

## Credits

Built by [@cgfm](https://github.com/cgfm) for the Home Assistant community.  
Dockhand is created by [Finsys](https://github.com/Finsys/dockhand).
