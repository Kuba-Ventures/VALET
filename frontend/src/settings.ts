/**
 * VALET — Settings Panel
 *
 * Overlay panel for API keys, connection status, preferences, and system info.
 * Slides in from the right with glass-morphism styling.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface StatusResponse {
  claude_code_installed: boolean;
  calendar_accessible: boolean;
  mail_accessible: boolean;
  notes_accessible: boolean;
  google_connected: boolean;
  google_email: string;
  google_credentials_present: boolean;
  memory_count: number;
  task_count: number;
  server_port: number;
  uptime_seconds: number;
  env_keys_set: {
    license: boolean;
    proxy_base_url: string;
    license_status: string;
    fish_voice_id: boolean;
    user_name: string;
  };
}

interface PreferencesResponse {
  user_name: string;
  honorific: string;
  calendar_accounts: string;
  date_of_birth: string;
  address: string;
  hometown_city: string;
  bio_summary: string;
  bio_summary_updated: string;
  bio_source_count: number;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let panelEl: HTMLElement | null = null;
let isOpen = false;
let isFirstTimeSetup = false;
let setupStep = 0; // 0=license, 1=test, 2=name, 3=done

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function apiGet<T>(url: string): Promise<T> {
  const res = await fetch(url);
  return res.json();
}

async function apiPost<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

// ---------------------------------------------------------------------------
// Panel HTML
// ---------------------------------------------------------------------------

function buildPanelHTML(): string {
  return `
    <div class="settings-backdrop" id="settings-backdrop"></div>
    <div class="settings-panel" id="settings-panel-inner">
      <div class="settings-header">
        <h2>Settings</h2>
        <button class="settings-close" id="settings-close">&times;</button>
      </div>

      <div class="settings-welcome" id="settings-welcome" style="display:none">
        <p>Welcome to VALET. Let's get you set up.</p>
      </div>

      <!-- Tab nav — User Settings sits first as the primary tab. Hidden
           during first-time setup wizard so the linear flow isn't interrupted. -->
      <nav class="settings-tabs" id="settings-tabs">
        <button class="settings-tab active" data-tab="user" id="tab-btn-user">User Settings</button>
        <button class="settings-tab" data-tab="computer" id="tab-btn-computer">Computer Settings</button>
      </nav>

      <div class="settings-body">

        <!-- ─── COMPUTER SETTINGS TAB ───────────────────────────────── -->
        <div class="settings-tab-content" data-tab="computer" id="tab-content-computer">

        <!-- License -->
        <section class="settings-section" id="section-api-keys">
          <h3>License</h3>

          <div class="settings-field">
            <label>License Key</label>
            <div class="settings-input-row">
              <input type="password" id="input-license-key" placeholder="PRODUCT-XXXX-XXXX-XXXX-XXXX" />
              <button class="settings-btn" id="btn-test-license">Test</button>
              <span class="status-dot" id="status-license"></span>
            </div>
          </div>

          <div class="settings-field">
            <label>Proxy URL</label>
            <div class="settings-input-row">
              <input type="text" id="input-proxy-url" placeholder="https://valetvoice.vercel.app" />
            </div>
          </div>

          <div class="settings-field">
            <label>Voice</label>
            <div class="settings-input-row settings-voice-toggle">
              <button class="settings-btn voice-opt active" id="voice-male" data-voice="male" type="button">British Male</button>
              <button class="settings-btn voice-opt" id="voice-female" data-voice="female" type="button">British Female</button>
            </div>
            <div class="settings-hint" id="voice-female-hint" hidden>Add a British female Fish voice ID below to enable.</div>
          </div>

          <div class="settings-field">
            <label>Voice ID (advanced)</label>
            <div class="settings-input-row">
              <input type="text" id="input-fish-voice-id" placeholder="Fish reference_id override" />
              <button class="settings-btn" id="btn-save-voice-id">Save</button>
            </div>
          </div>

          <div class="settings-actions">
            <button class="settings-btn primary" id="btn-save-keys">Save</button>
          </div>
        </section>

        <!-- Connection Status (Computer) -->
        <section class="settings-section" id="section-status">
          <h3>Connection Status</h3>
          <div class="status-grid">
            <div class="status-row"><span class="status-dot" id="status-claude-cli"></span><span>Claude Code CLI</span></div>
            <div class="status-row"><span class="status-dot" id="status-calendar"></span><span>Google Calendar</span></div>
            <div class="status-row"><span class="status-dot" id="status-mail"></span><span>Gmail</span></div>
            <div class="status-row"><span class="status-dot" id="status-notes"></span><span>Apple Notes</span></div>
            <div class="status-row"><span class="status-dot" id="status-server"></span><span>Server</span><span class="status-detail" id="status-server-detail"></span></div>
          </div>
        </section>

        <!-- System Info (Computer) -->
        <section class="settings-section" id="section-sysinfo">
          <h3>System Info</h3>
          <div class="sysinfo-grid">
            <div class="sysinfo-row"><span class="sysinfo-label">Memory entries</span><span id="sysinfo-memory">--</span></div>
            <div class="sysinfo-row"><span class="sysinfo-label">Tasks</span><span id="sysinfo-tasks">--</span></div>
            <div class="sysinfo-row"><span class="sysinfo-label">Server port</span><span id="sysinfo-port">--</span></div>
            <div class="sysinfo-row"><span class="sysinfo-label">Uptime</span><span id="sysinfo-uptime">--</span></div>
          </div>
        </section>

        </div>
        <!-- ─── /COMPUTER SETTINGS TAB ──────────────────────────────── -->


        <!-- ─── USER SETTINGS TAB (primary / default-active) ────────── -->
        <div class="settings-tab-content active" data-tab="user" id="tab-content-user">

        <!-- Connected Accounts -->
        <section class="settings-section" id="section-accounts">
          <h3>Connected Accounts</h3>
          <div class="settings-field">
            <label>Google Account</label>
            <div class="account-row">
              <span class="status-dot" id="status-google"></span>
              <span id="google-email-label">Not connected</span>
              <button class="settings-btn" id="btn-google-connect" style="margin-left:auto">Connect</button>
              <button class="settings-btn" id="btn-google-disconnect" style="display:none">Disconnect</button>
            </div>
            <div class="account-hint" id="google-hint">Connecting will open Google's consent screen in a new browser tab.</div>
          </div>
        </section>

        <!-- User Preferences -->
        <section class="settings-section" id="section-preferences">
          <h3>User Preferences</h3>

          <div class="settings-field">
            <label>Your Name</label>
            <input type="text" id="input-user-name" placeholder="Your name" />
          </div>

          <div class="settings-field">
            <label>Honorific</label>
            <select id="input-honorific">
              <option value="sir">Sir</option>
              <option value="ma'am">Ma'am</option>
              <option value="none">None</option>
            </select>
          </div>

          <div class="settings-field">
            <label>Calendar Accounts</label>
            <textarea id="input-calendar-accounts" rows="2" placeholder="auto (or comma-separated emails)"></textarea>
          </div>

          <div class="settings-actions">
            <button class="settings-btn primary" id="btn-save-prefs">Save Preferences</button>
          </div>
        </section>

        <!-- Personalized -->
        <section class="settings-section" id="section-personalized">
          <h3>Personalized</h3>

          <div class="settings-field">
            <label>Date of Birth</label>
            <input type="date" id="input-date-of-birth" />
          </div>

          <div class="settings-field">
            <label>Address / Where You Live</label>
            <textarea id="input-address" rows="2" placeholder="City, region, or full address"></textarea>
          </div>

          <div class="settings-field">
            <label>Hometown (city for weather)</label>
            <input type="text" id="input-hometown-city" placeholder="St. Petersburg, FL" />
          </div>

          <div class="settings-field">
            <label>About You, written by VALET</label>
            <div class="bio-summary" id="bio-summary-display">VALET hasn't built your profile yet. Click Regenerate after a few conversations.</div>
            <div class="bio-meta">
              <span id="bio-source-count">0 notes</span>
              <span class="bio-meta-sep">·</span>
              <span id="bio-updated">never updated</span>
            </div>
          </div>

          <div class="settings-actions">
            <button class="settings-btn" id="btn-regenerate-bio">Regenerate Profile</button>
            <button class="settings-btn primary" id="btn-save-personalized">Save Personalized</button>
          </div>
        </section>

        </div>
        <!-- ─── /USER SETTINGS TAB ──────────────────────────────────── -->


        <!-- Setup Navigation (first-time only) -->
        <div class="setup-nav" id="setup-nav" style="display:none">
          <button class="settings-btn primary" id="btn-setup-next">Next</button>
        </div>

      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Panel lifecycle
// ---------------------------------------------------------------------------

function createPanel(): HTMLElement {
  const container = document.createElement("div");
  container.id = "settings-container";
  container.innerHTML = buildPanelHTML();
  document.body.appendChild(container);
  return container;
}

function setDotStatus(id: string, status: "green" | "red" | "yellow" | "off") {
  const dot = document.getElementById(id);
  if (!dot) return;
  dot.className = "status-dot";
  if (status !== "off") dot.classList.add(`status-${status}`);
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

async function loadStatus() {
  try {
    const status = await apiGet<StatusResponse>("/api/settings/status");

    setDotStatus("status-claude-cli", status.claude_code_installed ? "green" : "red");
    setDotStatus("status-calendar", status.calendar_accessible ? "green" : "red");
    setDotStatus("status-mail", status.mail_accessible ? "green" : "red");
    setDotStatus("status-notes", status.notes_accessible ? "green" : "red");
    setDotStatus("status-server", "green");
    applyGoogleStatus(status.google_connected, status.google_email, status.google_credentials_present);

    const serverDetail = document.getElementById("status-server-detail");
    if (serverDetail) serverDetail.textContent = `port ${status.server_port} | up ${formatUptime(status.uptime_seconds)}`;

    // License status dot
    setDotStatus("status-license", status.env_keys_set.license ? "green" : "red");

    // System info
    const memEl = document.getElementById("sysinfo-memory");
    if (memEl) memEl.textContent = String(status.memory_count);
    const taskEl = document.getElementById("sysinfo-tasks");
    if (taskEl) taskEl.textContent = String(status.task_count);
    const portEl = document.getElementById("sysinfo-port");
    if (portEl) portEl.textContent = String(status.server_port);
    const upEl = document.getElementById("sysinfo-uptime");
    if (upEl) upEl.textContent = formatUptime(status.uptime_seconds);

    return status;
  } catch (e) {
    console.error("[settings] failed to load status:", e);
    setDotStatus("status-server", "red");
    return null;
  }
}

async function loadPreferences() {
  try {
    const prefs = await apiGet<PreferencesResponse>("/api/settings/preferences");
    const nameEl = document.getElementById("input-user-name") as HTMLInputElement;
    const honEl = document.getElementById("input-honorific") as HTMLSelectElement;
    const calEl = document.getElementById("input-calendar-accounts") as HTMLTextAreaElement;
    if (nameEl) nameEl.value = prefs.user_name || "";
    if (honEl) honEl.value = prefs.honorific || "sir";
    if (calEl) calEl.value = prefs.calendar_accounts || "auto";

    const dobEl = document.getElementById("input-date-of-birth") as HTMLInputElement | null;
    const addrEl = document.getElementById("input-address") as HTMLTextAreaElement | null;
    const homeEl = document.getElementById("input-hometown-city") as HTMLInputElement | null;
    if (dobEl) dobEl.value = prefs.date_of_birth || "";
    if (homeEl) homeEl.value = prefs.hometown_city || "";
    if (addrEl) addrEl.value = prefs.address || "";
    applyBioSummary(prefs.bio_summary, prefs.bio_summary_updated, prefs.bio_source_count);
  } catch (e) {
    console.error("[settings] failed to load preferences:", e);
  }
}

function formatRelativeTime(iso: string): string {
  if (!iso) return "never updated";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return iso;
  const secs = Math.max(0, (Date.now() - ts) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function applyGoogleStatus(connected: boolean, email: string, credsPresent: boolean) {
  setDotStatus("status-google", connected ? "green" : "red");
  const label = document.getElementById("google-email-label");
  const connectBtn = document.getElementById("btn-google-connect") as HTMLButtonElement | null;
  const disconnectBtn = document.getElementById("btn-google-disconnect") as HTMLButtonElement | null;
  const hint = document.getElementById("google-hint");
  if (label) label.textContent = connected ? (email || "Connected") : "Not connected";
  if (connectBtn) connectBtn.style.display = connected ? "none" : "";
  if (disconnectBtn) disconnectBtn.style.display = connected ? "" : "none";
  if (hint) {
    if (!credsPresent) {
      hint.textContent = "Missing google_credentials.json. Download the OAuth client JSON (Desktop app) from Google Cloud Console and place it at the project root.";
      hint.classList.add("warn");
      if (connectBtn) connectBtn.disabled = true;
    } else if (connected) {
      hint.textContent = "VALET can read your Gmail and Google Calendar.";
      hint.classList.remove("warn");
      if (connectBtn) connectBtn.disabled = false;
    } else {
      hint.textContent = "Connecting will open Google's consent screen in a new browser tab.";
      hint.classList.remove("warn");
      if (connectBtn) connectBtn.disabled = false;
    }
  }
}

function applyBioSummary(summary: string, updated: string, sourceCount: number) {
  const display = document.getElementById("bio-summary-display");
  const countEl = document.getElementById("bio-source-count");
  const updatedEl = document.getElementById("bio-updated");
  if (display) {
    if (summary) {
      display.textContent = summary;
      display.classList.remove("empty");
    } else {
      display.textContent = "VALET hasn't built your profile yet. Add facts via voice ('remember this about me: ...') or click Regenerate.";
      display.classList.add("empty");
    }
  }
  if (countEl) countEl.textContent = `${sourceCount} ${sourceCount === 1 ? "note" : "notes"} on file`;
  if (updatedEl) updatedEl.textContent = updated ? `updated ${formatRelativeTime(updated)}` : "never updated";
}

function activateTab(name: "computer" | "user") {
  document.querySelectorAll<HTMLElement>(".settings-tab").forEach((el) => {
    el.classList.toggle("active", el.dataset.tab === name);
  });
  document.querySelectorAll<HTMLElement>(".settings-tab-content").forEach((el) => {
    el.classList.toggle("active", el.dataset.tab === name);
  });
}

function wireEvents() {
  // Tab switching
  document.getElementById("tab-btn-computer")?.addEventListener("click", () => activateTab("computer"));
  document.getElementById("tab-btn-user")?.addEventListener("click", () => activateTab("user"));

  // Voice picker (British Male / British Female). Persona is unchanged — this
  // only swaps the Fish TTS model. Writes VALET_VOICE; takes effect on the next
  // spoken reply (no restart needed).
  const setActiveVoice = (voice: string) => {
    document.querySelectorAll<HTMLElement>(".voice-opt").forEach((b) => {
      b.classList.toggle("active", b.dataset.voice === voice);
    });
  };
  async function loadVoice() {
    try {
      const cfg = await apiGet<{ voice?: string; voice_female_available?: boolean }>("/api/config");
      setActiveVoice(cfg.voice === "female" ? "female" : "male");
      const hint = document.getElementById("voice-female-hint");
      if (hint) hint.hidden = !!cfg.voice_female_available;
    } catch {
      /* keep default */
    }
  }
  document.querySelectorAll<HTMLElement>(".voice-opt").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const voice = btn.dataset.voice || "male";
      setActiveVoice(voice);
      await apiPost("/api/settings/keys", { key_name: "VALET_VOICE", key_value: voice });
    });
  });
  void loadVoice();

  // Close
  document.getElementById("settings-close")?.addEventListener("click", closeSettings);
  document.getElementById("settings-backdrop")?.addEventListener("click", closeSettings);

  // Save keys
  document.getElementById("btn-save-keys")?.addEventListener("click", async () => {
    const licenseKey = (document.getElementById("input-license-key") as HTMLInputElement).value.trim();
    const proxyUrl = (document.getElementById("input-proxy-url") as HTMLInputElement).value.trim();

    if (licenseKey) {
      await apiPost("/api/settings/keys", { key_name: "LICENSE_KEY", key_value: licenseKey });
    }
    if (proxyUrl) {
      await apiPost("/api/settings/keys", { key_name: "PROXY_BASE_URL", key_value: proxyUrl });
    }
    await loadStatus();
  });

  // Save voice ID
  document.getElementById("btn-save-voice-id")?.addEventListener("click", async () => {
    const voiceId = (document.getElementById("input-fish-voice-id") as HTMLInputElement).value.trim();
    if (voiceId) {
      await apiPost("/api/settings/keys", { key_name: "FISH_VOICE_ID", key_value: voiceId });
    }
  });

  // Test License (validates against the proxy)
  document.getElementById("btn-test-license")?.addEventListener("click", async () => {
    setDotStatus("status-license", "yellow");
    const key = (document.getElementById("input-license-key") as HTMLInputElement).value.trim();
    try {
      const result = await apiPost<{ valid: boolean; status?: string; error?: string }>("/api/settings/test-license", { key_value: key || undefined });
      setDotStatus("status-license", result.valid ? "green" : "red");
    } catch {
      setDotStatus("status-license", "red");
    }
  });

  // Save preferences — both Save buttons post the full form, so editing in
  // either section and clicking either button persists everything.
  async function saveAllPreferences() {
    const user_name = (document.getElementById("input-user-name") as HTMLInputElement).value.trim();
    const honorific = (document.getElementById("input-honorific") as HTMLSelectElement).value;
    const calendar_accounts = (document.getElementById("input-calendar-accounts") as HTMLTextAreaElement).value.trim();
    const date_of_birth = (document.getElementById("input-date-of-birth") as HTMLInputElement)?.value.trim() || "";
    const address = (document.getElementById("input-address") as HTMLTextAreaElement)?.value.trim() || "";
    const hometown_city = (document.getElementById("input-hometown-city") as HTMLInputElement)?.value.trim() || "";
    await apiPost("/api/settings/preferences", {
      user_name, honorific, calendar_accounts, date_of_birth, address, hometown_city,
    });
    await loadStatus();
  }
  document.getElementById("btn-save-prefs")?.addEventListener("click", saveAllPreferences);
  document.getElementById("btn-save-personalized")?.addEventListener("click", saveAllPreferences);

  // Regenerate profile — VALET synthesizes a fresh summary from accumulated notes.
  document.getElementById("btn-regenerate-bio")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-regenerate-bio") as HTMLButtonElement | null;
    const display = document.getElementById("bio-summary-display");
    if (btn) { btn.disabled = true; btn.textContent = "Generating..."; }
    if (display) display.classList.add("loading");
    try {
      type RegenResp = { success: boolean; summary?: string; updated?: string; source_count?: number; error?: string; message?: string };
      const resp = await apiPost<RegenResp>("/api/settings/bio/regenerate", {});
      if (resp.success) {
        applyBioSummary(resp.summary || "", resp.updated || "", resp.source_count || 0);
      } else if (display) {
        display.textContent = `Couldn't generate profile: ${resp.error || "unknown error"}`;
      }
    } catch (e) {
      if (display) display.textContent = `Couldn't generate profile: ${String(e)}`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Regenerate Profile"; }
      if (display) display.classList.remove("loading");
    }
  });

  // Google connect
  document.getElementById("btn-google-connect")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-google-connect") as HTMLButtonElement | null;
    const hint = document.getElementById("google-hint");
    if (btn) { btn.disabled = true; btn.textContent = "Waiting for consent..."; }
    if (hint) hint.textContent = "Complete the Google consent flow in the browser tab that just opened.";
    try {
      type ConnResp = { success: boolean; email?: string; error?: string };
      const resp = await apiPost<ConnResp>("/api/google/connect", {});
      if (!resp.success && hint) hint.textContent = `Connect failed: ${resp.error || "unknown error"}`;
    } catch (e) {
      if (hint) hint.textContent = `Connect failed: ${String(e)}`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Connect"; }
      await loadStatus();
    }
  });

  // Google disconnect
  document.getElementById("btn-google-disconnect")?.addEventListener("click", async () => {
    await apiPost("/api/google/disconnect", {});
    await loadStatus();
  });

  // Setup next button
  document.getElementById("btn-setup-next")?.addEventListener("click", advanceSetup);
}

// ---------------------------------------------------------------------------
// First-time setup wizard
// ---------------------------------------------------------------------------

function enterSetupMode() {
  isFirstTimeSetup = true;
  setupStep = 0;

  const welcome = document.getElementById("settings-welcome");
  if (welcome) welcome.style.display = "block";

  const nav = document.getElementById("setup-nav");
  if (nav) nav.style.display = "flex";

  // Hide the tab nav while the wizard takes over. The wizard shows
  // sections by id directly; the tab system would just get in the way.
  // Also surface BOTH tab panels so the wizard can show any section.
  const tabs = document.getElementById("settings-tabs");
  if (tabs) tabs.style.display = "none";
  document.querySelectorAll<HTMLElement>(".settings-tab-content").forEach((el) => {
    el.classList.add("active");
  });

  // Hide sections except API keys
  showSetupStep(0);
}

function showSetupStep(step: number) {
  const sections = ["section-api-keys", "section-status", "section-accounts", "section-preferences", "section-personalized", "section-sysinfo"];
  sections.forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (step === 0 && i === 0) el.style.display = "";
    else if (step === 1 && i === 0) el.style.display = "";
    else if (step === 2 && i === 2) el.style.display = "";
    else if (step === 3) el.style.display = "";
    else el.style.display = "none";
  });

  const nextBtn = document.getElementById("btn-setup-next");
  if (nextBtn) {
    if (step === 0) nextBtn.textContent = "Next: Test Keys";
    else if (step === 1) nextBtn.textContent = "Next: Set Your Name";
    else if (step === 2) nextBtn.textContent = "Finish Setup";
    else nextBtn.style.display = "none";
  }
}

async function advanceSetup() {
  setupStep++;
  if (setupStep >= 3) {
    // Done — save everything and close
    isFirstTimeSetup = false;
    const welcome = document.getElementById("settings-welcome");
    if (welcome) welcome.style.display = "none";
    const nav = document.getElementById("setup-nav");
    if (nav) nav.style.display = "none";

    // Show all sections
    ["section-api-keys", "section-status", "section-accounts", "section-preferences", "section-personalized", "section-sysinfo"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.style.display = "";
    });

    // Restore the tab nav and default back to User Settings (primary tab).
    const tabs = document.getElementById("settings-tabs");
    if (tabs) tabs.style.display = "";
    activateTab("user");

    closeSettings();
    return;
  }
  showSetupStep(setupStep);
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function openSettings() {
  if (isOpen) return;
  isOpen = true;

  if (!panelEl) {
    panelEl = createPanel();
    wireEvents();
  }

  panelEl.style.display = "block";

  // Trigger animation
  requestAnimationFrame(() => {
    panelEl!.classList.add("open");
  });

  // Load data
  const status = await loadStatus();
  await loadPreferences();

  // Check for first-time setup
  if (status && !status.env_keys_set.license) {
    enterSetupMode();
  }
}

export function closeSettings() {
  if (!panelEl || !isOpen) return;
  isOpen = false;
  panelEl.classList.remove("open");
  setTimeout(() => {
    if (panelEl) panelEl.style.display = "none";
  }, 300);
}

export function isSettingsOpen(): boolean {
  return isOpen;
}

/**
 * Check if first-time setup is needed and auto-open.
 */
export async function checkFirstTimeSetup(): Promise<boolean> {
  try {
    const status = await apiGet<StatusResponse>("/api/settings/status");
    if (!status.env_keys_set.license) {
      openSettings();
      return true;
    }
  } catch {
    // Server not ready yet, skip
  }
  return false;
}
