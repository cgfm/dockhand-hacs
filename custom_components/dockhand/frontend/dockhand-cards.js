const DOCUMENTATION_BASE = "https://github.com/cgfm/dockhand-hacs";

const MODELS = {
  environment: "Docker Environment",
  container: "Docker Container",
  stack: "Docker Stack",
};

const TEXT = {
  de: {
    overview: "Dockhand Übersicht",
    container: "Dockhand Container",
    stack: "Dockhand Stack",
    all: "Alle",
    problems: "Probleme",
    updates: "Updates",
    stacks: "Stacks",
    running: "Laufend",
    stopped: "Gestoppt",
    problem: "Problem",
    update: "Update",
    containers: "Container",
    noItems: "Keine passenden Einträge.",
    missing: "Die konfigurierte Dockhand-Entität wurde nicht gefunden.",
    wrongDevice: "Die Entität gehört nicht zum erwarteten Dockhand-Gerät.",
    details: "Details öffnen",
    logs: "Logs",
    hideLogs: "Logs ausblenden",
    checkUpdates: "Updates prüfen",
    cpu: "CPU",
    memory: "Arbeitsspeicher",
    health: "Gesundheit",
    image: "Image",
    network: "Netzwerk",
    storage: "Datenträger",
    start: "Starten",
    stop: "Stoppen",
    pause: "Pausieren",
    unpause: "Fortsetzen",
    restart: "Neu starten",
    doUpdate: "Aktualisieren",
    active: "Aktiv",
    inactive: "Inaktiv",
    affected: "Betroffene Container",
    confirm: "Aktion „{action}“ für {name} ausführen?",
    actionFailed: "Dockhand-Aktion fehlgeschlagen: {error}",
  },
  en: {
    overview: "Dockhand overview",
    container: "Dockhand container",
    stack: "Dockhand stack",
    all: "All",
    problems: "Problems",
    updates: "Updates",
    stacks: "Stacks",
    running: "Running",
    stopped: "Stopped",
    problem: "Problem",
    update: "Update",
    containers: "Containers",
    noItems: "No matching entries.",
    missing: "The configured Dockhand entity was not found.",
    wrongDevice: "The entity does not belong to the expected Dockhand device.",
    details: "Open details",
    logs: "Logs",
    hideLogs: "Hide logs",
    checkUpdates: "Check updates",
    cpu: "CPU",
    memory: "Memory",
    health: "Health",
    image: "Image",
    network: "Network",
    storage: "Storage",
    start: "Start",
    stop: "Stop",
    pause: "Pause",
    unpause: "Resume",
    restart: "Restart",
    doUpdate: "Update",
    active: "Active",
    inactive: "Inactive",
    affected: "Affected containers",
    confirm: "Run “{action}” for {name}?",
    actionFailed: "Dockhand action failed: {error}",
  },
};

const COMMON_STYLE = `
  :host { display: block; }
  ha-card {
    color: var(--primary-text-color);
    background: var(--ha-card-background, var(--card-background-color));
    overflow: hidden;
  }
  .header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 16px 16px 10px;
  }
  .header ha-icon { color: var(--primary-color); }
  .title { min-width: 0; flex: 1; font-size: 20px; font-weight: 500; }
  .subtitle { color: var(--secondary-text-color); font-size: 12px; margin-top: 2px; }
  .content { padding: 6px 16px 16px; }
  .chips { display: flex; flex-wrap: wrap; gap: 7px; }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    min-height: 26px;
    padding: 0 9px;
    border-radius: 14px;
    background: var(--secondary-background-color);
    color: var(--secondary-text-color);
    font-size: 12px;
  }
  .chip ha-icon { --mdc-icon-size: 15px; }
  .chip.good { color: var(--success-color, #43a047); }
  .chip.warn { color: var(--warning-color, #f9a825); }
  .chip.bad { color: var(--error-color); }
  .message { color: var(--secondary-text-color); padding: 18px 0; text-align: center; }
  .metric-label { color: var(--secondary-text-color); font-size: 12px; }
  .metric-value { margin-top: 3px; font-size: 15px; }
  button.icon {
    width: 38px;
    height: 38px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    color: var(--primary-text-color);
    background: transparent;
    cursor: pointer;
  }
  button.icon:hover { background: var(--secondary-background-color); }
  button.icon:disabled { opacity: .35; cursor: default; }
  button.icon.danger { color: var(--error-color); }
  button.icon ha-icon { --mdc-icon-size: 21px; }
`;

const ACTIONS = {
  start: { icon: "mdi:play", label: "start" },
  stop: { icon: "mdi:stop", label: "stop", confirm: true, danger: true },
  pause: { icon: "mdi:pause", label: "pause" },
  unpause: { icon: "mdi:play-pause", label: "unpause" },
  restart: { icon: "mdi:restart", label: "restart", confirm: true },
  update: { icon: "mdi:update", label: "doUpdate", confirm: true },
};

const createElement = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};

const registryEntries = (hass) => Object.values(hass?.entities || {});
const deviceEntries = (hass) => Object.values(hass?.devices || {});

const deviceForEntity = (hass, entityId) => {
  const registry = hass?.entities?.[entityId];
  return registry?.device_id ? hass?.devices?.[registry.device_id] : undefined;
};

const isDockhandModel = (hass, entityId, model) => {
  const registry = hass?.entities?.[entityId];
  const device = deviceForEntity(hass, entityId);
  return Boolean(
    registry?.platform === "dockhand" &&
      device?.manufacturer === "Dockhand" &&
      device?.model === model,
  );
};

const candidateEntity = (hass, entities, entitiesFallback, model) => {
  const candidates = [
    ...(entities || []),
    ...(entitiesFallback || []),
    ...Object.keys(hass?.states || {}),
  ];
  return candidates.find((entityId) => isDockhandModel(hass, entityId, model));
};

const entitiesForDevice = (hass, deviceId) => {
  const byRole = new Map();
  for (const entry of registryEntries(hass)) {
    if (entry.platform !== "dockhand" || entry.device_id !== deviceId) continue;
    if (entry.translation_key && !byRole.has(entry.translation_key)) {
      byRole.set(entry.translation_key, entry.entity_id);
    }
  }
  return byRole;
};

const stateForRole = (hass, roles, role) => {
  const entityId = roles.get(role);
  return entityId ? hass?.states?.[entityId] : undefined;
};

const entityForRole = (roles, role) => roles.get(role);
const isOn = (state) => state?.state === "on";
const isAvailable = (state) => Boolean(state && state.state !== "unavailable");

const formattedState = (hass, state) => {
  if (!state) return "–";
  if (typeof hass?.formatEntityState === "function") {
    return hass.formatEntityState(state);
  }
  const unit = state.attributes?.unit_of_measurement;
  return `${state.state}${unit ? ` ${unit}` : ""}`;
};

const deviceName = (device, fallbackState) =>
  device?.name_by_user ||
  device?.name ||
  fallbackState?.attributes?.friendly_name ||
  fallbackState?.entity_id ||
  "Dockhand";

const parentDeviceId = (device) => device?.via_device_id || device?.parent_device_id;

const childDevices = (hass, environmentId, model) =>
  deviceEntries(hass)
    .filter(
      (device) =>
        device.manufacturer === "Dockhand" &&
        device.model === model &&
        parentDeviceId(device) === environmentId,
    )
    .sort((left, right) =>
      deviceName(left).localeCompare(deviceName(right), hass?.language || "en", {
        sensitivity: "base",
      }),
    );

const primaryEntity = (roles, preferred) => {
  for (const role of preferred) {
    if (roles.has(role)) return roles.get(role);
  }
  return roles.values().next().value;
};

const containerInfo = (hass, device) => {
  const roles = entitiesForDevice(hass, device.id);
  const status = stateForRole(hass, roles, "container_state");
  const running = stateForRole(hass, roles, "container_running");
  const health = stateForRole(hass, roles, "container_health");
  const update = stateForRole(hass, roles, "image_update_available");
  const isRunning = running ? isOn(running) : status?.state === "running";
  const unhealthy = health?.state === "unhealthy";
  return {
    device,
    roles,
    status,
    running,
    health,
    update,
    isRunning,
    hasProblem: !isRunning || unhealthy,
    hasUpdate: isOn(update),
    name: deviceName(device, status || running),
  };
};

const stackInfo = (hass, device) => {
  const roles = entitiesForDevice(hass, device.id);
  const status = stateForRole(hass, roles, "stack_status");
  const active = stateForRole(hass, roles, "stack_active");
  const problem = stateForRole(hass, roles, "stack_problem");
  return {
    device,
    roles,
    status,
    active,
    problem,
    hasProblem: isOn(problem),
    name: deviceName(device, status || active),
  };
};

const iconButton = (icon, title, handler, options = {}) => {
  const button = createElement("button", `icon${options.danger ? " danger" : ""}`);
  button.type = "button";
  button.title = title;
  button.setAttribute("aria-label", title);
  button.disabled = Boolean(options.disabled);
  const haIcon = createElement("ha-icon");
  haIcon.setAttribute("icon", icon);
  button.append(haIcon);
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    handler();
  });
  return button;
};

class DockhandBaseCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = undefined;
    this._hass = undefined;
    this._renderQueued = false;
  }

  set hass(hass) {
    this._hass = hass;
    this._queueRender();
  }

  connectedCallback() {
    this._queueRender();
  }

  _queueRender() {
    if (this._renderQueued) return;
    this._renderQueued = true;
    queueMicrotask(() => {
      this._renderQueued = false;
      if (this.isConnected) this._render();
    });
  }

  _language() {
    return this._hass?.language === "de" ? "de" : "en";
  }

  _t(key, replacements = {}) {
    let value = TEXT[this._language()][key];
    for (const [name, replacement] of Object.entries(replacements)) {
      value = value.replace(`{${name}}`, replacement);
    }
    return value;
  }

  _notify(message) {
    this.dispatchEvent(
      new CustomEvent("hass-notification", {
        detail: { message },
        bubbles: true,
        composed: true,
      }),
    );
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId },
        bubbles: true,
        composed: true,
      }),
    );
  }

  async _press(entityId, action, name) {
    if (!entityId || !this._hass) return;
    const definition = ACTIONS[action];
    const label = this._t(definition.label);
    if (
      definition.confirm &&
      !window.confirm(this._t("confirm", { action: label, name }))
    ) {
      return;
    }
    try {
      await this._hass.callService("button", "press", { entity_id: entityId });
    } catch (error) {
      this._notify(
        this._t("actionFailed", { error: error?.message || String(error) }),
      );
    }
  }

  _actionButtons(info, compact = false) {
    const holder = createElement("div", compact ? "actions compact" : "actions");
    for (const [action, definition] of Object.entries(ACTIONS)) {
      const entityId = entityForRole(info.roles, action);
      if (!entityId || !isAvailable(this._hass?.states?.[entityId])) continue;
      holder.append(
        iconButton(
          definition.icon,
          this._t(definition.label),
          () => this._press(entityId, action, info.name),
          definition,
        ),
      );
    }
    return holder;
  }

  _error(message) {
    const card = createElement("ha-card");
    const body = createElement("div", "message", message);
    card.append(body);
    this.shadowRoot.replaceChildren(createElement("style", "", COMMON_STYLE), card);
  }
}

class DockhandOverviewCard extends DockhandBaseCard {
  static getStubConfig(hass, entities = [], entitiesFallback = []) {
    return {
      entity:
        candidateEntity(
          hass,
          entities,
          entitiesFallback,
          MODELS.environment,
        ) || "",
      default_view: "all",
      show_metrics: true,
      show_actions: true,
    };
  }

  static getConfigForm() {
    const german = document.documentElement.lang?.startsWith("de");
    const labels = german
      ? {
          entity: "Umgebung",
          name: "Titel",
          default_view: "Startansicht",
          show_metrics: "Metriken anzeigen",
          show_actions: "Aktionen anzeigen",
          required: "Eine Dockhand-Umgebungsentität ist erforderlich.",
        }
      : {
          entity: "Environment",
          name: "Title",
          default_view: "Initial view",
          show_metrics: "Show metrics",
          show_actions: "Show actions",
          required: "A Dockhand environment entity is required.",
        };
    return {
      schema: [
        {
          name: "entity",
          required: true,
          selector: {
            entity: {
              filter: {
                domain: "sensor",
                integration: "dockhand",
                device: { manufacturer: "Dockhand", model: MODELS.environment },
              },
            },
          },
        },
        { name: "name", selector: { text: {} } },
        {
          name: "default_view",
          selector: {
            select: {
              mode: "dropdown",
              options: [
                { value: "all", label: german ? "Alle" : "All" },
                { value: "problems", label: german ? "Probleme" : "Problems" },
                { value: "updates", label: "Updates" },
                { value: "stacks", label: "Stacks" },
              ],
            },
          },
        },
        {
          type: "grid",
          name: "",
          flatten: true,
          schema: [
            { name: "show_metrics", selector: { boolean: {} } },
            { name: "show_actions", selector: { boolean: {} } },
          ],
        },
      ],
      computeLabel: (schema) => labels[schema.name],
      assertConfig: (config) => {
        if (!config.entity) throw new Error(labels.required);
      },
    };
  }

  constructor() {
    super();
    this._activeView = undefined;
  }

  setConfig(config) {
    if (!config?.entity) throw new Error("Define a Dockhand environment entity");
    const views = ["all", "problems", "updates", "stacks"];
    if (config.default_view && !views.includes(config.default_view)) {
      throw new Error("default_view must be all, problems, updates, or stacks");
    }
    this._config = {
      ...config,
      default_view: config.default_view || "all",
      show_metrics: config.show_metrics !== false,
      show_actions: config.show_actions !== false,
    };
    this._activeView = this._config.default_view;
    this._queueRender();
  }

  getCardSize() {
    return 6;
  }

  getGridOptions() {
    return { columns: 12, rows: "auto", min_rows: 3 };
  }

  _render() {
    if (!this._config || !this._hass) return;
    const selectedState = this._hass.states?.[this._config.entity];
    if (!selectedState) return this._error(this._t("missing"));
    const environment = deviceForEntity(this._hass, this._config.entity);
    if (environment?.model !== MODELS.environment) {
      return this._error(this._t("wrongDevice"));
    }

    const containers = childDevices(
      this._hass,
      environment.id,
      MODELS.container,
    ).map((device) => containerInfo(this._hass, device));
    const stacks = childDevices(this._hass, environment.id, MODELS.stack).map(
      (device) => stackInfo(this._hass, device),
    );
    const running = containers.filter((item) => item.isRunning).length;
    const problems = containers.filter((item) => item.hasProblem).length;
    const updates = containers.filter((item) => item.hasUpdate).length;
    const environmentRoles = entitiesForDevice(this._hass, environment.id);

    const style = createElement("style", "", `${COMMON_STYLE}
      .summary { padding: 0 16px 12px; }
      .tabs { display: flex; gap: 4px; padding: 0 12px 8px; overflow-x: auto; }
      .tab {
        border: 0; border-radius: 18px; padding: 8px 12px; cursor: pointer;
        color: var(--secondary-text-color); background: transparent; white-space: nowrap;
      }
      .tab.active { color: var(--primary-text-color); background: var(--secondary-background-color); }
      .rows { border-top: 1px solid var(--divider-color); }
      .row {
        display: grid; grid-template-columns: minmax(130px, 1fr) minmax(120px, auto) auto;
        gap: 12px; align-items: center; min-height: 58px; padding: 6px 10px 6px 16px;
        border-bottom: 1px solid var(--divider-color); cursor: pointer;
      }
      .row:last-child { border-bottom: 0; }
      .row:hover { background: color-mix(in srgb, var(--primary-text-color) 4%, transparent); }
      .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
      .state { color: var(--secondary-text-color); font-size: 12px; margin-top: 3px; }
      .metrics { display: flex; gap: 14px; color: var(--secondary-text-color); font-size: 12px; }
      .actions { display: flex; align-items: center; justify-content: flex-end; }
      .actions.compact button.icon { width: 34px; height: 34px; }
      @media (max-width: 600px) {
        .row { grid-template-columns: minmax(100px, 1fr) auto; }
        .metrics { display: none; }
      }
    `);
    const card = createElement("ha-card");
    const header = createElement("div", "header");
    const icon = createElement("ha-icon");
    icon.setAttribute("icon", "mdi:view-dashboard-outline");
    const titleWrap = createElement("div", "title");
    titleWrap.append(
      document.createTextNode(
        this._config.name || deviceName(environment, selectedState) || this._t("overview"),
      ),
    );
    titleWrap.append(
      createElement("div", "subtitle", `${containers.length} ${this._t("containers")}`),
    );
    header.append(icon, titleWrap);
    const checkEntity = entityForRole(environmentRoles, "check_image_updates");
    if (checkEntity) {
      header.append(
        iconButton("mdi:refresh", this._t("checkUpdates"), async () => {
          try {
            await this._hass.callService("button", "press", {
              entity_id: checkEntity,
            });
          } catch (error) {
            this._notify(
              this._t("actionFailed", {
                error: error?.message || String(error),
              }),
            );
          }
        }),
      );
    }
    card.append(header);

    const summary = createElement("div", "summary chips");
    summary.append(
      this._chip("mdi:play-circle", `${running} ${this._t("running")}`, "good"),
      this._chip("mdi:stop-circle", `${containers.length - running} ${this._t("stopped")}`),
      this._chip("mdi:alert-circle", `${problems} ${this._t("problems")}`, problems ? "bad" : ""),
      this._chip("mdi:update", `${updates} ${this._t("updates")}`, updates ? "warn" : ""),
    );
    card.append(summary);

    const tabs = createElement("div", "tabs");
    for (const view of ["all", "problems", "updates", "stacks"]) {
      const tab = createElement(
        "button",
        `tab${view === this._activeView ? " active" : ""}`,
        this._t(view),
      );
      tab.type = "button";
      tab.addEventListener("click", () => {
        this._activeView = view;
        this._queueRender();
      });
      tabs.append(tab);
    }
    card.append(tabs);

    const rows = createElement("div", "rows");
    if (this._activeView === "stacks") {
      stacks.forEach((item) => rows.append(this._stackRow(item)));
      if (!stacks.length) rows.append(createElement("div", "message", this._t("noItems")));
    } else {
      const filtered = containers.filter((item) => {
        if (this._activeView === "problems") return item.hasProblem;
        if (this._activeView === "updates") return item.hasUpdate;
        return true;
      });
      filtered.forEach((item) => rows.append(this._containerRow(item)));
      if (!filtered.length) rows.append(createElement("div", "message", this._t("noItems")));
    }
    card.append(rows);
    this.shadowRoot.replaceChildren(style, card);
  }

  _chip(iconName, text, className = "") {
    const chip = createElement("span", `chip ${className}`.trim());
    const icon = createElement("ha-icon");
    icon.setAttribute("icon", iconName);
    chip.append(icon, document.createTextNode(text));
    return chip;
  }

  _containerRow(info) {
    const row = createElement("div", "row");
    const main = createElement("div");
    main.append(createElement("div", "name", info.name));
    const statusText = info.status
      ? formattedState(this._hass, info.status)
      : info.isRunning
        ? this._t("running")
        : this._t("stopped");
    main.append(createElement("div", "state", statusText));
    row.append(main);

    const metrics = createElement("div", "metrics");
    if (this._config.show_metrics) {
      metrics.append(
        createElement("span", "", `CPU ${formattedState(this._hass, stateForRole(this._hass, info.roles, "cpu_percent"))}`),
        createElement("span", "", `RAM ${formattedState(this._hass, stateForRole(this._hass, info.roles, "memory_percent"))}`),
      );
    }
    row.append(metrics);
    if (this._config.show_actions) row.append(this._actionButtons(info, true));
    else row.append(createElement("div"));
    row.addEventListener("click", () =>
      this._moreInfo(primaryEntity(info.roles, ["container_state", "container_running"])),
    );
    return row;
  }

  _stackRow(info) {
    const row = createElement("div", "row");
    const main = createElement("div");
    main.append(createElement("div", "name", info.name));
    main.append(
      createElement(
        "div",
        "state",
        info.status ? formattedState(this._hass, info.status) : isOn(info.active) ? this._t("active") : this._t("inactive"),
      ),
    );
    row.append(main);
    const metrics = createElement("div", "metrics");
    metrics.append(
      createElement("span", "", `${formattedState(this._hass, stateForRole(this._hass, info.roles, "stack_running_count"))} ${this._t("running")}`),
      createElement("span", "", `${formattedState(this._hass, stateForRole(this._hass, info.roles, "stack_stopped_count"))} ${this._t("stopped")}`),
    );
    row.append(metrics, createElement("div"));
    row.addEventListener("click", () =>
      this._moreInfo(primaryEntity(info.roles, ["stack_status", "stack_active"])),
    );
    return row;
  }
}

class DockhandContainerCard extends DockhandBaseCard {
  static getStubConfig(hass, entities = [], entitiesFallback = []) {
    return {
      entity:
        candidateEntity(hass, entities, entitiesFallback, MODELS.container) || "",
      show_metrics: true,
      show_actions: true,
      show_logs: true,
      tail: 200,
    };
  }

  static getConfigForm() {
    return detailConfigForm(MODELS.container, true);
  }

  constructor() {
    super();
    this._logsOpen = false;
    this._logCard = undefined;
    this._logEntity = undefined;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._logsOpen && this._logCard) {
      this._logCard.hass = hass;
      return;
    }
    this._queueRender();
  }

  setConfig(config) {
    if (!config?.entity) throw new Error("Define a Dockhand container entity");
    const tail = config.tail ?? 200;
    if (!Number.isInteger(tail) || tail < 1 || tail > 5000) {
      throw new Error("tail must be an integer from 1 to 5000");
    }
    const closeLogs =
      this._config?.entity !== config.entity || config.show_logs === false;
    this._config = {
      ...config,
      show_metrics: config.show_metrics !== false,
      show_actions: config.show_actions !== false,
      show_logs: config.show_logs !== false,
      tail,
    };
    if (closeLogs) {
      this._logsOpen = false;
      this._closeLogs();
    }
    this._queueRender();
  }

  disconnectedCallback() {
    this._closeLogs();
  }

  getCardSize() {
    return this._logsOpen ? 8 : 4;
  }

  getGridOptions() {
    return { columns: 6, rows: "auto", min_rows: 3 };
  }

  _render() {
    if (!this._config || !this._hass) return;
    const selectedState = this._hass.states?.[this._config.entity];
    if (!selectedState) return this._error(this._t("missing"));
    const device = deviceForEntity(this._hass, this._config.entity);
    if (device?.model !== MODELS.container) return this._error(this._t("wrongDevice"));
    const info = containerInfo(this._hass, device);
    const oldLogCard = this._logCard;

    const style = createElement("style", "", `${COMMON_STYLE}
      .status { display: flex; flex-wrap: wrap; gap: 7px; padding: 0 16px 12px; }
      .metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; padding: 12px 16px; border-top: 1px solid var(--divider-color); }
      .metric { min-width: 0; }
      .metric-value { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .actions { display: flex; align-items: center; gap: 2px; padding: 8px 10px; border-top: 1px solid var(--divider-color); }
      .actions .spacer { flex: 1; }
      .logs { padding: 0 12px 12px; }
      .logs dockhand-logs-card { --ha-card-border-width: 0; }
    `);
    const card = createElement("ha-card");
    const header = createElement("div", "header");
    const icon = createElement("ha-icon");
    icon.setAttribute("icon", "mdi:docker");
    const title = createElement("div", "title", this._config.name || info.name);
    header.append(icon, title);
    header.append(
      iconButton("mdi:information-outline", this._t("details"), () =>
        this._moreInfo(primaryEntity(info.roles, ["container_state", "container_running"])),
      ),
    );
    card.append(header);

    const status = createElement("div", "status chips");
    status.append(
      this._chip(
        info.isRunning ? "mdi:play-circle" : "mdi:stop-circle",
        info.status ? formattedState(this._hass, info.status) : info.isRunning ? this._t("running") : this._t("stopped"),
        info.isRunning ? "good" : "bad",
      ),
    );
    if (info.health) {
      status.append(
        this._chip("mdi:heart-pulse", formattedState(this._hass, info.health), info.health.state === "unhealthy" ? "bad" : "good"),
      );
    }
    if (info.hasUpdate) status.append(this._chip("mdi:update", this._t("update"), "warn"));
    card.append(status);

    if (this._config.show_metrics) card.append(this._metrics(info.roles));

    if (this._config.show_actions || this._config.show_logs) {
      const actions = this._config.show_actions
        ? this._actionButtons(info)
        : createElement("div", "actions");
      actions.append(createElement("span", "spacer"));
      if (this._config.show_logs) {
        actions.append(
          iconButton(
            this._logsOpen ? "mdi:text-box-remove-outline" : "mdi:text-box-outline",
            this._logsOpen ? this._t("hideLogs") : this._t("logs"),
            () => {
              this._logsOpen = !this._logsOpen;
              if (!this._logsOpen) this._closeLogs();
              this._queueRender();
            },
          ),
        );
      }
      card.append(actions);
    }

    const logHost = createElement("div", "logs");
    if (this._logsOpen && this._config.show_logs) {
      const logEntity = primaryEntity(info.roles, ["container_state", "container_running"]);
      this._logCard = oldLogCard || document.createElement("dockhand-logs-card");
      if (this._logEntity !== logEntity || !oldLogCard) {
        this._logCard.setConfig({ entity: logEntity, tail: this._config.tail });
        this._logEntity = logEntity;
      }
      this._logCard.hass = this._hass;
      logHost.append(this._logCard);
      card.append(logHost);
    }
    this.shadowRoot.replaceChildren(style, card);
    if (this._logsOpen && typeof this._logCard?.openLogs === "function") {
      this._logCard.openLogs();
    }
  }

  _closeLogs() {
    this._logCard?.remove();
    this._logCard = undefined;
    this._logEntity = undefined;
  }

  _chip(iconName, text, className = "") {
    const chip = createElement("span", `chip ${className}`.trim());
    const icon = createElement("ha-icon");
    icon.setAttribute("icon", iconName);
    chip.append(icon, document.createTextNode(text));
    return chip;
  }

  _metrics(roles) {
    const metrics = createElement("div", "metrics");
    const entries = [
      ["cpu", "cpu_percent"],
      ["memory", "memory_percent"],
      ["image", "container_image_tag"],
      ["network", "network_rx", "network_tx"],
      ["storage", "block_read", "block_write"],
    ];
    for (const [label, firstRole, secondRole] of entries) {
      const first = stateForRole(this._hass, roles, firstRole);
      const second = secondRole ? stateForRole(this._hass, roles, secondRole) : undefined;
      if (!first && !second) continue;
      const metric = createElement("div", "metric");
      metric.append(createElement("div", "metric-label", this._t(label)));
      metric.append(
        createElement(
          "div",
          "metric-value",
          second
            ? `${formattedState(this._hass, first)} / ${formattedState(this._hass, second)}`
            : formattedState(this._hass, first),
        ),
      );
      metrics.append(metric);
    }
    return metrics;
  }
}

class DockhandStackCard extends DockhandBaseCard {
  static getStubConfig(hass, entities = [], entitiesFallback = []) {
    return {
      entity: candidateEntity(hass, entities, entitiesFallback, MODELS.stack) || "",
      show_containers: true,
    };
  }

  static getConfigForm() {
    return detailConfigForm(MODELS.stack, false);
  }

  setConfig(config) {
    if (!config?.entity) throw new Error("Define a Dockhand stack entity");
    this._config = { ...config, show_containers: config.show_containers !== false };
    this._queueRender();
  }

  getCardSize() {
    return 3;
  }

  getGridOptions() {
    return { columns: 6, rows: "auto", min_rows: 2 };
  }

  _render() {
    if (!this._config || !this._hass) return;
    const selectedState = this._hass.states?.[this._config.entity];
    if (!selectedState) return this._error(this._t("missing"));
    const device = deviceForEntity(this._hass, this._config.entity);
    if (device?.model !== MODELS.stack) return this._error(this._t("wrongDevice"));
    const info = stackInfo(this._hass, device);
    const total = stateForRole(this._hass, info.roles, "stack_container_count");
    const running = stateForRole(this._hass, info.roles, "stack_running_count");
    const stopped = stateForRole(this._hass, info.roles, "stack_stopped_count");
    const affected = info.problem?.attributes?.problem_containers || [];

    const style = createElement("style", "", `${COMMON_STYLE}
      .status { padding: 0 16px 14px; }
      .counts { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid var(--divider-color); }
      .count { padding: 13px 16px; text-align: center; }
      .count + .count { border-left: 1px solid var(--divider-color); }
      .count strong { display: block; font-size: 20px; }
      .problems { padding: 12px 16px 16px; border-top: 1px solid var(--divider-color); }
      .problems ul { margin: 7px 0 0; padding-left: 20px; }
    `);
    const card = createElement("ha-card");
    const header = createElement("div", "header");
    const icon = createElement("ha-icon");
    icon.setAttribute("icon", "mdi:layers-triple-outline");
    const title = createElement("div", "title", this._config.name || info.name);
    header.append(icon, title);
    header.append(
      iconButton("mdi:information-outline", this._t("details"), () =>
        this._moreInfo(primaryEntity(info.roles, ["stack_status", "stack_active"])),
      ),
    );
    card.append(header);
    const status = createElement("div", "status chips");
    status.append(
      this._chip(
        isOn(info.active) ? "mdi:check-circle" : "mdi:alert-circle",
        info.status ? formattedState(this._hass, info.status) : isOn(info.active) ? this._t("active") : this._t("inactive"),
        info.hasProblem ? "bad" : "good",
      ),
    );
    card.append(status);

    if (this._config.show_containers) {
      const counts = createElement("div", "counts");
      for (const [state, label] of [
        [total, "containers"],
        [running, "running"],
        [stopped, "stopped"],
      ]) {
        const count = createElement("div", "count");
        count.append(
          createElement("strong", "", formattedState(this._hass, state)),
          createElement("span", "metric-label", this._t(label)),
        );
        counts.append(count);
      }
      card.append(counts);
    }
    if (affected.length) {
      const problems = createElement("div", "problems");
      problems.append(createElement("div", "metric-label", this._t("affected")));
      const list = createElement("ul");
      affected.forEach((name) => list.append(createElement("li", "", String(name))));
      problems.append(list);
      card.append(problems);
    }
    this.shadowRoot.replaceChildren(style, card);
  }

  _chip(iconName, text, className = "") {
    const chip = createElement("span", `chip ${className}`.trim());
    const icon = createElement("ha-icon");
    icon.setAttribute("icon", iconName);
    chip.append(icon, document.createTextNode(text));
    return chip;
  }
}

const detailConfigForm = (model, includeContainerOptions) => {
  const german = document.documentElement.lang?.startsWith("de");
  const labels = german
    ? {
        entity: model === MODELS.stack ? "Stack" : "Container",
        name: "Titel",
        show_metrics: "Metriken anzeigen",
        show_actions: "Aktionen anzeigen",
        show_logs: "Logs anbieten",
        show_containers: "Container-Zähler anzeigen",
        tail: "Anfängliche Logzeilen",
        required: "Eine Dockhand-Entität ist erforderlich.",
      }
    : {
        entity: model === MODELS.stack ? "Stack" : "Container",
        name: "Title",
        show_metrics: "Show metrics",
        show_actions: "Show actions",
        show_logs: "Offer logs",
        show_containers: "Show container counts",
        tail: "Initial log lines",
        required: "A Dockhand entity is required.",
      };
  const schema = [
    {
      name: "entity",
      required: true,
      selector: {
        entity: {
          filter: {
            integration: "dockhand",
            device: { manufacturer: "Dockhand", model },
          },
        },
      },
    },
    { name: "name", selector: { text: {} } },
  ];
  if (includeContainerOptions) {
    schema.push(
      {
        type: "grid",
        name: "",
        flatten: true,
        schema: [
          { name: "show_metrics", selector: { boolean: {} } },
          { name: "show_actions", selector: { boolean: {} } },
          { name: "show_logs", selector: { boolean: {} } },
        ],
      },
      {
        name: "tail",
        selector: { number: { min: 1, max: 5000, step: 1, mode: "box" } },
      },
    );
  } else {
    schema.push({ name: "show_containers", selector: { boolean: {} } });
  }
  return {
    schema,
    computeLabel: (item) => labels[item.name],
    assertConfig: (config) => {
      if (!config.entity) throw new Error(labels.required);
    },
  };
};

const registerCard = (tag, cardClass, metadata) => {
  if (!customElements.get(tag)) customElements.define(tag, cardClass);
  window.customCards = window.customCards || [];
  if (!window.customCards.some((card) => card.type === tag)) {
    window.customCards.push({ type: tag, ...metadata });
  }
};

registerCard("dockhand-overview-card", DockhandOverviewCard, {
  name: "Dockhand Overview Card",
  description: "Environment overview with problems, updates, stacks, and actions.",
  preview: true,
  documentationURL: `${DOCUMENTATION_BASE}#dockhand-overview-card`,
  getEntitySuggestion: (hass, entityId) =>
    isDockhandModel(hass, entityId, MODELS.environment)
      ? {
          config: {
            type: "custom:dockhand-overview-card",
            entity: entityId,
            default_view: "all",
          },
        }
      : null,
});

registerCard("dockhand-container-card", DockhandContainerCard, {
  name: "Dockhand Container Card",
  description: "Container status, metrics, actions, and live logs.",
  preview: true,
  documentationURL: `${DOCUMENTATION_BASE}#dockhand-container-card`,
  getEntitySuggestion: (hass, entityId) =>
    isDockhandModel(hass, entityId, MODELS.container)
      ? {
          config: {
            type: "custom:dockhand-container-card",
            entity: entityId,
          },
        }
      : null,
});

registerCard("dockhand-stack-card", DockhandStackCard, {
  name: "Dockhand Stack Card",
  description: "Stack health and container counts.",
  preview: true,
  documentationURL: `${DOCUMENTATION_BASE}#dockhand-stack-card`,
  getEntitySuggestion: (hass, entityId) =>
    isDockhandModel(hass, entityId, MODELS.stack)
      ? {
          config: {
            type: "custom:dockhand-stack-card",
            entity: entityId,
          },
        }
      : null,
});
