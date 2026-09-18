const CARD_TAG = "dockhand-logs-card";
const DEFAULT_TAIL = 200;
const DEFAULT_MAX_LINES = 3000;
const MAX_TAIL = 5000;
const MAX_VISIBLE_LINES = 5000;
const DOCUMENTATION_URL =
  "https://github.com/cgfm/dockhand-hacs#dashboard-card-live-container-logs";

const TEXT = {
  de: {
    show: "Logs anzeigen",
    back: "Zurück",
    connecting: "Verbindung wird aufgebaut …",
    reconnecting: "Home Assistant verbindet neu …",
    live: "LIVE",
    ended: "Stream beendet",
    paused: "Pausiert",
    pause: "Pause",
    resume: "Fortsetzen",
    clear: "Leeren",
    autoscroll: "Auto-Scroll",
    retry: "Neu verbinden",
    waiting: "Warte auf Logausgabe …",
    entityMissing: "Die konfigurierte Entität wurde nicht gefunden.",
    attributesMissing:
      "Der Entität fehlen entry_id, container_id oder environment_id.",
    adminOnly: "Live-Logs sind nur für Home-Assistant-Administratoren verfügbar.",
  },
  en: {
    show: "Show logs",
    back: "Back",
    connecting: "Connecting …",
    reconnecting: "Home Assistant is reconnecting …",
    live: "LIVE",
    ended: "Stream ended",
    paused: "Paused",
    pause: "Pause",
    resume: "Resume",
    clear: "Clear",
    autoscroll: "Auto-scroll",
    retry: "Reconnect",
    waiting: "Waiting for log output …",
    entityMissing: "The configured entity was not found.",
    attributesMissing:
      "The entity does not provide entry_id, container_id, or environment_id.",
    adminOnly: "Live logs are available only to Home Assistant administrators.",
  },
};

const isDockhandContainerEntity = (hass, entityId) => {
  const attributes = hass?.states?.[entityId]?.attributes;
  if (!attributes) return false;
  const envId = attributes.environment_id ?? attributes.env_id;
  return Boolean(
    attributes.entry_id &&
      attributes.container_id &&
      envId !== undefined &&
      envId !== null,
  );
};

class DockhandLogsCard extends HTMLElement {
  static getStubConfig(hass, entities = [], entitiesFallback = []) {
    const candidates = [
      ...entities,
      ...entitiesFallback,
      ...Object.keys(hass?.states || {}),
    ];
    const entity = candidates.find((entityId) =>
      isDockhandContainerEntity(hass, entityId),
    );
    return {
      entity: entity || "",
      tail: DEFAULT_TAIL,
      max_lines: DEFAULT_MAX_LINES,
    };
  }

  static getConfigForm() {
    const german = document.documentElement.lang?.startsWith("de");
    const labels = german
      ? {
          tail: "Anfängliche Logzeilen",
          max_lines: "Maximal gepufferte Zeilen",
          entityHelp: "Sensor eines Dockhand-Containers auswählen.",
          entityRequired:
            "Der visuelle Editor benötigt eine Dockhand-Container-Entität.",
        }
      : {
          tail: "Initial log lines",
          max_lines: "Maximum buffered lines",
          entityHelp: "Select a sensor belonging to a Dockhand container.",
          entityRequired:
            "The visual editor requires a Dockhand container entity.",
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
                device: {
                  manufacturer: "Dockhand",
                  model: "Docker Container",
                },
              },
            },
          },
        },
        { name: "name", selector: { text: {} } },
        {
          type: "grid",
          name: "",
          flatten: true,
          column_min_width: "160px",
          schema: [
            {
              name: "tail",
              selector: {
                number: { min: 1, max: MAX_TAIL, step: 1, mode: "box" },
              },
            },
            {
              name: "max_lines",
              selector: {
                number: {
                  min: 100,
                  max: MAX_VISIBLE_LINES,
                  step: 100,
                  mode: "box",
                },
              },
            },
          ],
        },
      ],
      computeLabel: (schema) => labels[schema.name],
      computeHelper: (schema) =>
        schema.name === "entity" ? labels.entityHelp : undefined,
      assertConfig: (config) => {
        if (!config.entity) throw new Error(labels.entityRequired);
      },
    };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = undefined;
    this._hass = undefined;
    this._rendered = false;
    this._open = false;
    this._paused = false;
    this._autoScroll = true;
    this._programmaticScroll = false;
    this._lines = [];
    this._pausedLines = [];
    this._unsubscribe = undefined;
    this._connection = undefined;
    this._subscriptionGeneration = 0;
    this._connecting = false;
    this._reconnectQueued = false;
  }

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error("Dockhand logs card configuration is required");
    }
    const explicit = config.entry_id && config.container_id && config.env_id;
    if (!config.entity && !explicit) {
      throw new Error(
        "Define entity, or define entry_id, container_id, and env_id",
      );
    }

    const tail = config.tail ?? DEFAULT_TAIL;
    if (!Number.isInteger(tail) || tail < 1 || tail > MAX_TAIL) {
      throw new Error(`tail must be an integer from 1 to ${MAX_TAIL}`);
    }
    const maxLines = config.max_lines ?? DEFAULT_MAX_LINES;
    if (
      !Number.isInteger(maxLines) ||
      maxLines < 100 ||
      maxLines > MAX_VISIBLE_LINES
    ) {
      throw new Error(
        `max_lines must be an integer from 100 to ${MAX_VISIBLE_LINES}`,
      );
    }

    const changed = JSON.stringify(this._config) !== JSON.stringify(config);
    this._config = { ...config, tail, max_lines: maxLines };
    if (changed && this._open) {
      this._open = false;
      this._disconnect();
    }
    this._renderShell();
    this._updateView();
  }

  set hass(hass) {
    const previousTarget = this._safeTargetKey();
    this._hass = hass;
    this._renderShell();
    this._updateHeader();

    const nextTarget = this._safeTargetKey();
    if (
      this._open &&
      this._unsubscribe &&
      previousTarget &&
      nextTarget &&
      previousTarget !== nextTarget
    ) {
      this._queueReconnect();
    }
  }

  connectedCallback() {
    this._renderShell();
    this._updateView();
    if (this._open && !this._unsubscribe && !this._connecting) {
      this._connect();
    }
  }

  disconnectedCallback() {
    this._disconnect();
  }

  getCardSize() {
    return this._open ? 6 : 2;
  }

  getGridOptions() {
    return {
      columns: 12,
      rows: this._open ? 6 : 2,
      min_columns: 12,
      min_rows: 2,
    };
  }

  openLogs() {
    this._setOpen(true);
  }

  closeLogs() {
    this._setOpen(false);
  }

  _language() {
    return this._hass?.language === "de" ? "de" : "en";
  }

  _t(key) {
    return TEXT[this._language()][key];
  }

  _renderShell() {
    if (this._rendered || !this.shadowRoot) return;
    this._rendered = true;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card {
          overflow: hidden;
          color: var(--primary-text-color);
          background: var(--ha-card-background, var(--card-background-color));
        }
        .header {
          min-height: 56px;
          padding: 10px 14px;
          display: flex;
          align-items: center;
          gap: 10px;
          border-bottom: 1px solid transparent;
          box-sizing: border-box;
        }
        .open .header { border-bottom-color: var(--divider-color); }
        .title-wrap { min-width: 0; flex: 1; }
        .title {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          font-size: 1rem;
          font-weight: 500;
        }
        .state { color: var(--secondary-text-color); font-size: .78rem; }
        .badge {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          color: var(--secondary-text-color);
          font-size: .75rem;
          font-weight: 600;
          letter-spacing: .04em;
          white-space: nowrap;
        }
        .dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: var(--disabled-text-color);
        }
        .dot.running, .dot.live { background: var(--success-color, #43a047); }
        button {
          min-height: 40px;
          padding: 0 14px;
          border: 0;
          border-radius: 20px;
          color: var(--primary-text-color);
          background: var(--secondary-background-color);
          font: inherit;
          cursor: pointer;
          touch-action: manipulation;
        }
        button:focus-visible {
          outline: 2px solid var(--primary-color);
          outline-offset: 2px;
        }
        .back {
          width: 40px;
          min-width: 40px;
          padding: 0;
          background: transparent;
          font-size: 1.7rem;
          line-height: 1;
        }
        .closed-view {
          min-height: 88px;
          padding: 12px 16px 20px;
          display: grid;
          place-items: center;
          box-sizing: border-box;
        }
        .show-logs {
          min-width: min(220px, 80vw);
          color: var(--text-primary-color, white);
          background: var(--primary-color);
          font-weight: 500;
        }
        .open-view { display: none; }
        .open .closed-view { display: none; }
        .open .open-view { display: block; }
        .log-area {
          position: relative;
          height: clamp(280px, 52vh, 560px);
          overflow: auto;
          overscroll-behavior: contain;
          background: var(--code-editor-background-color, rgba(0, 0, 0, .035));
          scrollbar-gutter: stable;
        }
        .logs {
          min-width: max-content;
          padding: 10px 12px;
          box-sizing: border-box;
          font-family: var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace);
          font-size: .78rem;
          line-height: 1.55;
        }
        .log-line { display: flex; white-space: pre; }
        .timestamp {
          flex: 0 0 auto;
          margin-right: 10px;
          color: var(--secondary-text-color);
          user-select: none;
        }
        .message { color: var(--primary-text-color); }
        .stderr .message { color: var(--error-color, #db4437); }
        .stream-marker {
          width: 3px;
          margin-right: 7px;
          border-radius: 2px;
          background: transparent;
        }
        .stderr .stream-marker { background: var(--error-color, #db4437); }
        .empty {
          position: absolute;
          inset: 0;
          display: grid;
          place-items: center;
          padding: 24px;
          color: var(--secondary-text-color);
          text-align: center;
          pointer-events: none;
        }
        .log-area.has-lines .empty { display: none; }
        .notice {
          min-height: 20px;
          padding: 7px 12px;
          color: var(--secondary-text-color);
          background: var(--secondary-background-color);
          font-size: .76rem;
          box-sizing: border-box;
        }
        .notice.error { color: var(--error-color, #db4437); }
        .controls {
          padding: 8px;
          display: flex;
          align-items: center;
          gap: 6px;
          border-top: 1px solid var(--divider-color);
          overflow-x: auto;
        }
        .controls button {
          min-width: max-content;
          flex: 1 0 auto;
          background: transparent;
          font-size: .82rem;
        }
        .controls button.active {
          color: var(--primary-color);
          background: color-mix(in srgb, var(--primary-color) 12%, transparent);
        }
        .retry[hidden] { display: none; }
        @media (max-width: 480px) {
          .header { padding-inline: 8px 10px; }
          .log-area { height: 55vh; min-height: 260px; }
          .logs { padding: 8px; font-size: .73rem; }
          .timestamp { margin-right: 7px; }
          .controls { padding: 5px; }
          .controls button { padding-inline: 10px; font-size: .78rem; }
        }
      </style>
      <ha-card>
        <div class="header">
          <button class="back" type="button" aria-label="Back">‹</button>
          <div class="title-wrap">
            <div class="title"></div>
            <div class="state"></div>
          </div>
          <div class="badge"><span class="badge-text"></span><span class="dot"></span></div>
        </div>
        <div class="closed-view">
          <button class="show-logs" type="button"></button>
        </div>
        <div class="open-view">
          <div class="log-area" tabindex="0" role="log" aria-live="off">
            <div class="logs"></div>
            <div class="empty"></div>
          </div>
          <div class="notice"></div>
          <div class="controls">
            <button class="pause" type="button"></button>
            <button class="clear" type="button"></button>
            <button class="autoscroll active" type="button"></button>
            <button class="retry" type="button" hidden></button>
          </div>
        </div>
      </ha-card>`;

    this._els = {
      card: this.shadowRoot.querySelector("ha-card"),
      back: this.shadowRoot.querySelector(".back"),
      title: this.shadowRoot.querySelector(".title"),
      state: this.shadowRoot.querySelector(".state"),
      badgeText: this.shadowRoot.querySelector(".badge-text"),
      dot: this.shadowRoot.querySelector(".dot"),
      show: this.shadowRoot.querySelector(".show-logs"),
      area: this.shadowRoot.querySelector(".log-area"),
      logs: this.shadowRoot.querySelector(".logs"),
      empty: this.shadowRoot.querySelector(".empty"),
      notice: this.shadowRoot.querySelector(".notice"),
      pause: this.shadowRoot.querySelector(".pause"),
      clear: this.shadowRoot.querySelector(".clear"),
      autoscroll: this.shadowRoot.querySelector(".autoscroll"),
      retry: this.shadowRoot.querySelector(".retry"),
    };

    this._els.show.addEventListener("click", () => this._setOpen(true));
    this._els.back.addEventListener("click", () => this._setOpen(false));
    this._els.pause.addEventListener("click", () => this._togglePause());
    this._els.clear.addEventListener("click", () => this._clear());
    this._els.autoscroll.addEventListener("click", () => {
      this._autoScroll = true;
      this._updateControls();
      this._scrollToBottom();
    });
    this._els.retry.addEventListener("click", () => this._reconnect());
    this._els.area.addEventListener("scroll", () => this._handleScroll(), {
      passive: true,
    });
  }

  _updateView() {
    if (!this._rendered || !this._config) return;
    this._els.card.classList.toggle("open", this._open);
    this._els.back.style.display = this._open ? "inline-grid" : "none";
    this._els.back.setAttribute("aria-label", this._t("back"));
    this._els.show.textContent = this._t("show");
    this._els.empty.textContent = this._t("waiting");
    this._els.clear.textContent = this._t("clear");
    this._els.retry.textContent = this._t("retry");
    this._updateHeader();
    this._updateControls();
  }

  _updateHeader() {
    if (!this._rendered || !this._config) return;
    const entity = this._config.entity
      ? this._hass?.states?.[this._config.entity]
      : undefined;
    const name =
      this._config.name ||
      entity?.attributes?.friendly_name ||
      this._config.container_id?.slice(0, 12) ||
      "Dockhand";
    this._els.title.textContent = name;

    if (this._open) {
      this._els.state.textContent = "";
      return;
    }
    const rawState = entity?.state;
    const running = rawState === "running" || rawState === "on";
    this._els.state.textContent = entity ? rawState : "";
    this._els.badgeText.textContent = running
      ? "RUN"
      : rawState
        ? String(rawState).toUpperCase()
        : "";
    this._els.dot.className = `dot${running ? " running" : ""}`;
  }

  _resolveTarget() {
    if (!this._config) throw new Error("Missing card configuration");
    if (!this._config.entity) {
      return {
        entry_id: this._config.entry_id,
        container_id: this._config.container_id,
        env_id: this._config.env_id,
        tail: this._config.tail,
      };
    }

    const entity = this._hass?.states?.[this._config.entity];
    if (!entity) throw new Error(this._t("entityMissing"));
    const attributes = entity.attributes || {};
    const envId = attributes.environment_id ?? attributes.env_id;
    if (!attributes.entry_id || !attributes.container_id || !envId) {
      throw new Error(this._t("attributesMissing"));
    }
    return {
      entry_id: attributes.entry_id,
      container_id: attributes.container_id,
      env_id: envId,
      tail: this._config.tail,
    };
  }

  _safeTargetKey() {
    try {
      const target = this._resolveTarget();
      return `${target.entry_id}:${target.env_id}:${target.container_id}`;
    } catch (_error) {
      return undefined;
    }
  }

  _setOpen(open) {
    if (this._open === open) return;
    this._open = open;
    this._updateView();
    if (open) {
      this._connect();
    } else {
      this._disconnect();
    }
  }

  async _connect() {
    if (!this._hass || !this._config || this._connecting || !this.isConnected) {
      return;
    }
    this._connecting = true;
    const generation = ++this._subscriptionGeneration;
    this._setNotice(this._t("connecting"));
    this._els.badgeText.textContent = "…";
    this._els.dot.className = "dot";
    this._els.retry.hidden = true;

    try {
      const target = this._resolveTarget();
      const connection = this._hass.connection;
      this._connection = connection;
      this._addConnectionListeners(connection);
      const unsubscribe = await connection.subscribeMessage(
        (event) => this._handleEvent(event),
        { type: "dockhand/subscribe_logs", ...target },
      );
      if (generation !== this._subscriptionGeneration || !this._open) {
        await unsubscribe();
        return;
      }
      this._unsubscribe = unsubscribe;
    } catch (error) {
      if (generation !== this._subscriptionGeneration) return;
      this._removeConnectionListeners();
      const unauthorized = error?.code === "unauthorized";
      this._setError(
        unauthorized ? this._t("adminOnly") : error?.message || String(error),
      );
    } finally {
      if (generation === this._subscriptionGeneration) {
        this._connecting = false;
      }
    }
  }

  _disconnect() {
    this._subscriptionGeneration += 1;
    this._connecting = false;
    this._removeConnectionListeners();
    const unsubscribe = this._unsubscribe;
    this._unsubscribe = undefined;
    if (unsubscribe) {
      Promise.resolve(unsubscribe()).catch(() => undefined);
    }
  }

  async _reconnect() {
    this._disconnect();
    if (this._open) await this._connect();
  }

  _queueReconnect() {
    if (this._reconnectQueued) return;
    this._reconnectQueued = true;
    queueMicrotask(async () => {
      this._reconnectQueued = false;
      if (this._open) await this._reconnect();
    });
  }

  _addConnectionListeners(connection) {
    this._removeConnectionListeners();
    this._connection = connection;
    this._onDisconnected = () => this._setNotice(this._t("reconnecting"));
    this._onReady = () => this._setNotice(this._t("connecting"));
    connection.addEventListener("disconnected", this._onDisconnected);
    connection.addEventListener("ready", this._onReady);
  }

  _removeConnectionListeners() {
    if (!this._connection) return;
    if (this._onDisconnected) {
      this._connection.removeEventListener("disconnected", this._onDisconnected);
    }
    if (this._onReady) {
      this._connection.removeEventListener("ready", this._onReady);
    }
    this._connection = undefined;
    this._onDisconnected = undefined;
    this._onReady = undefined;
  }

  _handleEvent(event) {
    if (!this._open || !event || typeof event !== "object") return;
    if (event.event === "connected") {
      this._els.badgeText.textContent = this._t("live");
      this._els.dot.className = "dot live";
      this._setNotice("");
      this._els.retry.hidden = true;
      return;
    }
    if (event.event === "log" && typeof event.text === "string") {
      const entries = this._eventLines(event);
      if (this._paused) {
        this._pausedLines.push(...entries);
        this._trimArray(this._pausedLines);
        this._updateControls();
      } else {
        this._appendEntries(entries);
      }
      return;
    }
    if (event.event === "end") {
      this._els.badgeText.textContent = this._t("ended");
      this._els.dot.className = "dot";
      this._setNotice(event.reason || this._t("ended"));
      this._els.retry.hidden = false;
      return;
    }
    if (event.event === "error") {
      this._setError(event.error || "Dockhand log stream error");
    }
  }

  _eventLines(event) {
    const timestamp = new Intl.DateTimeFormat(this._language(), {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date());
    const parts = event.text.split(/\r?\n/);
    if (parts.length > 1 && parts.at(-1) === "") parts.pop();
    return parts.map((text) => ({
      timestamp,
      text,
      stream: event.stream === "stderr" ? "stderr" : "stdout",
    }));
  }

  _appendEntries(entries) {
    if (!entries.length) return;
    this._lines.push(...entries);
    const removed = this._trimArray(this._lines);
    for (let index = 0; index < removed; index += 1) {
      this._els.logs.firstElementChild?.remove();
    }

    const fragment = document.createDocumentFragment();
    for (const entry of entries.slice(-this._config.max_lines)) {
      const row = document.createElement("div");
      row.className = `log-line ${entry.stream}`;
      const marker = document.createElement("span");
      marker.className = "stream-marker";
      const timestamp = document.createElement("span");
      timestamp.className = "timestamp";
      timestamp.textContent = entry.timestamp;
      const message = document.createElement("span");
      message.className = "message";
      message.textContent = entry.text;
      row.append(marker, timestamp, message);
      fragment.append(row);
    }
    this._els.logs.append(fragment);
    this._els.area.classList.toggle("has-lines", this._lines.length > 0);
    if (this._autoScroll) this._scrollToBottom();
  }

  _trimArray(array) {
    const overflow = Math.max(0, array.length - this._config.max_lines);
    if (overflow) array.splice(0, overflow);
    return overflow;
  }

  _togglePause() {
    this._paused = !this._paused;
    if (!this._paused && this._pausedLines.length) {
      const pending = this._pausedLines;
      this._pausedLines = [];
      this._appendEntries(pending);
    }
    this._updateControls();
  }

  _clear() {
    this._lines = [];
    this._pausedLines = [];
    this._els.logs.textContent = "";
    this._els.area.classList.remove("has-lines");
    this._updateControls();
  }

  _updateControls() {
    if (!this._rendered) return;
    const buffered = this._pausedLines.length;
    this._els.pause.textContent = this._paused
      ? `${this._t("resume")}${buffered ? ` (${buffered})` : ""}`
      : this._t("pause");
    this._els.pause.classList.toggle("active", this._paused);
    this._els.autoscroll.textContent = `${this._t("autoscroll")} ${
      this._autoScroll ? "●" : "○"
    }`;
    this._els.autoscroll.classList.toggle("active", this._autoScroll);
  }

  _handleScroll() {
    if (this._programmaticScroll || !this._autoScroll) return;
    const area = this._els.area;
    const distance = area.scrollHeight - area.scrollTop - area.clientHeight;
    if (distance > 32) {
      this._autoScroll = false;
      this._updateControls();
    }
  }

  _scrollToBottom() {
    requestAnimationFrame(() => {
      if (!this._autoScroll || !this.isConnected) return;
      this._programmaticScroll = true;
      this._els.area.scrollTop = this._els.area.scrollHeight;
      requestAnimationFrame(() => {
        this._programmaticScroll = false;
      });
    });
  }

  _setNotice(message) {
    this._els.notice.textContent = message;
    this._els.notice.classList.remove("error");
  }

  _setError(message) {
    this._els.badgeText.textContent = "ERROR";
    this._els.dot.className = "dot";
    this._els.notice.textContent = message;
    this._els.notice.classList.add("error");
    this._els.retry.hidden = false;
  }
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, DockhandLogsCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TAG)) {
  window.customCards.push({
    type: CARD_TAG,
    name: "Dockhand Logs Card",
    description: "On-demand live logs for a Dockhand container",
    preview: false,
    documentationURL: DOCUMENTATION_URL,
    getEntitySuggestion: (hass, entityId) =>
      isDockhandContainerEntity(hass, entityId)
        ? {
            config: {
              type: `custom:${CARD_TAG}`,
              entity: entityId,
            },
          }
        : null,
  });
}
