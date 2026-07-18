/**
 * VALET — Settings Panel
 *
 * Overlay panel for API keys, connection status, preferences, and system info.
 * Slides in from the right with glass-morphism styling.
 */

// Eye icons for the password show/hide toggle (open = will reveal, slashed = will hide).
const EYE_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`;
const EYE_OFF_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`;

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
  version: string;
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
  account_email?: string;
  account_plan?: string;
  license_key?: string;
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
        <p>Welcome to VALET. Enter your license key on the right to start talking to Vee and controlling your Mac. You'll find it in your purchase confirmation, or in your account at valetvoice.vercel.app.</p>
      </div>

      <!-- Tab nav — User Settings sits first as the primary tab. Hidden
           during first-time setup wizard so the linear flow isn't interrupted. -->
      <nav class="settings-tabs" id="settings-tabs">
        <button class="settings-tab active" data-tab="user" id="tab-btn-user">User Settings</button>
        <button class="settings-tab" data-tab="computer" id="tab-btn-computer">Console Settings</button>
      </nav>

      <div class="settings-body">

        <!-- ─── COMPUTER SETTINGS TAB (monochrome — refined dark) ───────── -->
        <div class="settings-tab-content console-mono" data-tab="computer" id="tab-content-computer">

        <!-- VOICE & INPUT -->
        <section class="settings-section" id="section-api-keys">
          <div class="mono-eyebrow">Voice &amp; Input</div>

          <div class="mono-seg settings-voice-toggle setup-hide">
            <button class="voice-opt active" id="voice-male" data-voice="male" type="button">British Male</button>
            <button class="voice-opt" id="voice-female" data-voice="female" type="button">British Female</button>
          </div>

          <div class="mono-row setup-hide">
            <div class="mono-row-text">
              <div class="mono-row-title">Always listening</div>
              <div class="mono-row-sub">On: responds to “Hey Vee”. Off: hold ⌃⌥ to talk</div>
            </div>
            <label class="mono-switch"><input type="checkbox" id="input-always-listening" /><span class="mono-slider"></span></label>
          </div>

          <div class="mono-row setup-hide">
            <div class="mono-row-text">
              <div class="mono-row-title">Working hum</div>
              <div class="mono-row-sub">A subtle ambient tone while VALET works</div>
            </div>
            <label class="mono-switch"><input type="checkbox" id="input-working-hum" /><span class="mono-slider"></span></label>
          </div>

          <div class="mono-row setup-hide">
            <div class="mono-row-text">
              <div class="mono-row-title">Share crash reports</div>
              <div class="mono-row-sub">Error metadata only, never content</div>
            </div>
            <label class="mono-switch"><input type="checkbox" id="input-telemetry" /><span class="mono-slider"></span></label>
          </div>
        </section>

        <div class="mono-divider"></div>

        <!-- PERMISSIONS -->
        <section class="settings-section" id="section-permissions">
          <div class="mono-eyebrow-row">
            <span class="mono-eyebrow">Permissions</span>
            <span class="mono-legend">
              <span class="mono-legend-item"><span class="mono-dot on"></span>granted</span>
              <span class="mono-legend-item"><span class="mono-dot"></span>needs action</span>
            </span>
          </div>
          <div class="perm-list" id="settings-perm-list">
            <div class="account-hint">Checking permissions…</div>
          </div>
          <div class="settings-actions">
            <button class="settings-btn" id="btn-recheck-perms">Re-check</button>
          </div>
        </section>

        <div class="mono-divider"></div>

        <!-- THIS MAC -->
        <section class="settings-section" id="section-status">
          <div class="mono-eyebrow">This Mac</div>
          <div class="mono-row mono-server">
            <span class="status-dot" id="status-server"></span>
            <span class="mono-row-title">Server</span>
            <span class="status-detail" id="status-server-detail"></span>
          </div>
          <div class="mono-conn-grid">
            <div class="mono-conn"><span class="status-dot" id="status-claude-cli"></span><span>Claude Code</span></div>
            <div class="mono-conn"><span class="status-dot" id="status-calendar"></span><span>Calendar</span></div>
            <div class="mono-conn"><span class="status-dot" id="status-mail"></span><span>Gmail</span></div>
            <div class="mono-conn"><span class="status-dot" id="status-notes"></span><span>Apple Notes</span></div>
          </div>
          <button class="settings-btn mono-restart" id="btn-restart-server" type="button">Restart server</button>
          <div class="mono-version" id="status-version">VALET —</div>
        </section>

        <!-- Advanced: offline license, Google connector, raw system info (kept for
             function; the section IDs are preserved for the first-run setup flow). -->
        <details class="mono-advanced" id="section-sysinfo">
          <summary>Advanced</summary>

          <section id="section-accounts">
            <div class="settings-field">
              <label>Offline activation key</label>
              <div class="settings-input-row">
                <input type="password" id="input-license-key" placeholder="PRODUCT-XXXX-XXXX-XXXX-XXXX" />
                <button class="settings-btn" id="btn-test-license">Test</button>
                <span class="status-dot" id="status-license"></span>
              </div>
              <div class="settings-hint">Plan, billing and license live at <a href="https://valetvoice.vercel.app/account" target="_blank" rel="noreferrer">your account ↗</a>. Paste a key here only to activate offline.</div>
              <div class="settings-actions"><button class="settings-btn" id="btn-save-keys">Save</button></div>
            </div>
            <div class="settings-field">
              <label>Google Account</label>
              <div class="account-row">
                <span class="status-dot" id="status-google"></span>
                <span id="google-email-label">Not connected</span>
                <button class="settings-btn" id="btn-google-connect" style="margin-left:auto">Connect</button>
                <button class="settings-btn" id="btn-google-disconnect" style="display:none">Disconnect</button>
              </div>
              <div class="account-hint" id="google-hint">Connecting opens Google's consent screen in a new tab.</div>
            </div>
          </section>

          <div class="sysinfo-grid">
            <div class="sysinfo-row"><span class="sysinfo-label">Memory entries</span><span id="sysinfo-memory">--</span></div>
            <div class="sysinfo-row"><span class="sysinfo-label">Tasks</span><span id="sysinfo-tasks">--</span></div>
            <div class="sysinfo-row"><span class="sysinfo-label">Server port</span><span id="sysinfo-port">--</span></div>
            <div class="sysinfo-row"><span class="sysinfo-label">Uptime</span><span id="sysinfo-uptime">--</span></div>
          </div>
        </details>

        </div>
        <!-- ─── /COMPUTER SETTINGS TAB ──────────────────────────────── -->


        <!-- ─── USER SETTINGS TAB (primary / default-active) ────────── -->
        <div class="settings-tab-content active" data-tab="user" id="tab-content-user">

        <!-- Account login — signs in and auto-fills the license key + profile -->
        <section class="settings-section" id="section-account-login">
          <h3>Account</h3>
          <!-- Signed-in state (shown once an account email is on file) -->
          <div id="account-signedin" style="display:none">
            <div class="acct-row">
              <div class="acct-avatar" id="account-avatar">·</div>
              <div class="acct-id">
                <div class="acct-name-line">
                  <strong id="account-name-label"></strong>
                  <span class="acct-badge" id="account-plan-label">—</span>
                </div>
                <div class="acct-sub">Signed in</div>
              </div>
              <button class="settings-btn" id="btn-account-signout">Sign out</button>
            </div>
            <span class="settings-hint" id="login-status-in"></span>
          </div>
          <!-- Sign-in form -->
          <div id="account-form">
            <div class="settings-hint">Sign in with your VALET account to pull in your license and profile automatically.</div>
            <div class="settings-field">
              <label>Email</label>
              <input type="email" id="login-email" autocomplete="username" />
            </div>
            <div class="settings-field">
              <label>Password</label>
              <div class="pw-field">
                <input type="password" id="login-password" autocomplete="current-password" />
                <button type="button" class="pw-toggle" id="login-eye" aria-label="Show password" title="Show password">${EYE_SVG}</button>
              </div>
            </div>
            <div class="settings-actions">
              <span class="settings-hint" id="login-status"></span>
              <button class="settings-btn primary" id="btn-account-login">Log in</button>
            </div>
          </div>
        </section>

        <!-- How VALET addresses you (the only per-machine identity bits; the rest
             lives on the web dashboard). Auto-saved on change. -->
        <section class="settings-section" id="section-preferences">
          <h3>How VALET addresses you</h3>

          <div class="settings-field">
            <label>Your name</label>
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

          <div class="acct-saved" id="prefs-saved">Saved automatically</div>
        </section>

        <!-- Everything else is managed on the web account (single source of truth). -->
        <section class="settings-section">
          <div class="web-card">
            <div class="web-card-title">Manage everything else on the web</div>
            <div class="web-card-sub">Your profile and about-you, billing and license, and connected accounts.</div>
            <ul class="web-card-list">
              <li>Profile, location and personalization</li>
              <li>Plan, billing and license</li>
              <li>Connected accounts</li>
            </ul>
            <a class="settings-btn primary web-card-btn" href="https://valetvoice.vercel.app/account" target="_blank" rel="noreferrer">Open dashboard ↗</a>
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

    const versionEl = document.getElementById("status-version");
    if (versionEl) versionEl.textContent = status.version ? `VALET v${status.version}` : "VALET —";

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
    const locEl = document.getElementById("input-location") as HTMLInputElement | null;
    if (dobEl) dobEl.value = prefs.date_of_birth || "";
    if (locEl) locEl.value = prefs.hometown_city || prefs.address || "";
    applyBioSummary(prefs.bio_summary, prefs.bio_summary_updated, prefs.bio_source_count);
    renderAccountState(prefs.account_email || null, {
      name: prefs.user_name, plan: prefs.account_plan, licenseKey: prefs.license_key,
    });
  } catch (e) {
    console.error("[settings] failed to load preferences:", e);
  }
}

/** Toggle the Account section between the sign-in form and a compact signed-in
 *  state (name · tier · license), so the form and the populated profile aren't
 *  shown at the same time. `id` is the name to display, falling back to email. */
function renderAccountState(
  email: string | null,
  opts: { name?: string | null; plan?: string | null; licenseKey?: string | null } = {},
) {
  const form = document.getElementById("account-form");
  const signedIn = document.getElementById("account-signedin");
  if (email) {
    const nameLabel = document.getElementById("account-name-label");
    const planLabel = document.getElementById("account-plan-label");
    const avatar = document.getElementById("account-avatar");
    const display = (opts.name && opts.name.trim()) || email;
    const first = display.split(/[\s@]/)[0] || display;
    if (nameLabel) nameLabel.textContent = first;
    if (planLabel) planLabel.textContent = (opts.plan || "").toUpperCase() || "ACTIVE";
    if (avatar) avatar.textContent = (first[0] || "·").toUpperCase();
    if (form) form.style.display = "none";
    if (signedIn) signedIn.style.display = "";
  } else {
    if (form) form.style.display = "";
    if (signedIn) signedIn.style.display = "none";
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

interface Contact { name: string; email: string; }

async function loadContacts() {
  try {
    const { contacts } = await apiGet<{ contacts: Contact[] }>("/api/contacts");
    renderContacts(contacts || []);
  } catch {
    renderContacts([]);
  }
}

function renderContacts(contacts: Contact[]) {
  const el = document.getElementById("contacts-list");
  if (!el) return;
  if (!contacts.length) {
    el.innerHTML = `<div class="account-hint">No contacts saved yet.</div>`;
    return;
  }
  el.innerHTML = contacts
    .map(
      (c) => `
      <div class="account-row" style="gap:8px">
        <span style="font-weight:600">${escapeHtml(c.name)}</span>
        <span class="account-hint" style="margin:0">${escapeHtml(c.email)}</span>
        <button class="settings-btn" data-contact-remove="${escapeHtml(c.name)}" style="margin-left:auto">Remove</button>
      </div>`
    )
    .join("");
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] || c)
  );
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

// macOS permissions shown in Console Settings. Reuses the same backend the
// onboarding wizard uses (/api/permissions/{status,open}); microphone is granted
// via the native prompt (getUserMedia), the rest deep-link to System Settings.
interface SettingsPerm { granted: boolean | null; label?: string }
const PERM_TARGET: Record<string, string> = {
  full_disk_access: "full_disk",
  microphone: "microphone",
  automation: "automation",
  speech_recognition: "speech_recognition",
  input_monitoring: "input_monitoring",
};

/**
 * Real microphone-grant state, read in the webview (where the mic lives). The
 * backend returns null for mic ("prompts on first use") since it can't cleanly
 * detect it; the Permissions API can, so the dot goes green once allowed.
 * Returns null if unsupported (keeps the backend value).
 */
async function micGranted(): Promise<boolean | null> {
  try {
    const s = await navigator.permissions.query({ name: "microphone" as PermissionName });
    if (s.state === "granted") return true;
    if (s.state === "denied") return false;
    return null;
  } catch {
    return null;
  }
}

async function loadSettingsPermissions() {
  const list = document.getElementById("settings-perm-list");
  if (!list) return;
  try {
    const status = await apiGet<Record<string, SettingsPerm>>("/api/permissions/status");
    // Refine microphone client-side — the backend can't detect the grant.
    if (status.microphone) {
      const mic = await micGranted();
      if (mic !== null) status.microphone.granted = mic;
    }
    const keys = ["microphone", "speech_recognition", "calendars", "automation", "accessibility", "screen_recording", "input_monitoring", "full_disk_access"].filter((k) => status[k]);
    list.innerHTML = keys
      .map((k) => {
        const p = status[k];
        const dot = p.granted === true ? "green" : p.granted === false ? "red" : "yellow";
        const label = p.label || k;
        let side: string;
        if (p.granted === true) {
          side = `<span style="margin-left:auto;opacity:0.6">Granted</span>`;
        } else if (k === "microphone") {
          side = `<button class="settings-btn" data-perm-mic="1" style="margin-left:auto">Enable</button>`;
        } else if (k === "automation" || k === "calendars" || k === "accessibility" || k === "screen_recording" || k === "input_monitoring") {
          // Inline native prompt rather than a Settings deep-link. (Accessibility,
          // Screen Recording + Input Monitoring grants land in System Settings +
          // need a relaunch, so their dots stay red until Re-check; the button
          // falls back to Open Settings.)
          side = `<button class="settings-btn" data-perm-trigger="${k}" style="margin-left:auto">Enable</button>`;
        } else {
          side = `<button class="settings-btn" data-perm-open="${PERM_TARGET[k]}" style="margin-left:auto">Open Settings</button>`;
        }
        return `<div class="account-row" data-perm-key="${k}">
            <span class="status-dot status-${dot}"></span>
            <span>${label}</span>
            ${side}
          </div>`;
      })
      .join("");
  } catch {
    list.innerHTML = `<div class="account-hint">Couldn't load permissions.</div>`;
  }
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
      const cfg = await apiGet<{ voice?: string; voice_female_available?: boolean; telemetry?: boolean }>("/api/config");
      setActiveVoice(cfg.voice === "female" ? "female" : "male");
      const hint = document.getElementById("voice-female-hint");
      if (hint) hint.hidden = !!cfg.voice_female_available;
      const tel = document.getElementById("input-telemetry") as HTMLInputElement | null;
      if (tel) tel.checked = !!cfg.telemetry;
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
  // Opt-in telemetry consent (off by default).
  document.getElementById("input-telemetry")?.addEventListener("change", async (e) => {
    const on = (e.target as HTMLInputElement).checked;
    await apiPost("/api/settings/keys", { key_name: "VALET_TELEMETRY", key_value: on ? "on" : "off" });
  });
  // "Always listening" — controls the wake-word mode (one source of truth with
  // the orb's Active/Asleep). Off = hold ⌃⌥ to talk; the chord always works.
  const alwaysListen = document.getElementById("input-always-listening") as HTMLInputElement | null;
  if (alwaysListen) {
    alwaysListen.checked = localStorage.getItem("valet.wakeListening") === "active";
    alwaysListen.addEventListener("change", () => {
      (window as unknown as { __valetSetListening?: (on: boolean) => void })
        .__valetSetListening?.(alwaysListen.checked);
    });
  }

  // "Working hum" — the ambient tone that fades in while VALET works. Enabled
  // by default; the flag lives in localStorage["valet.hum.enabled"] ("0" = off),
  // the same key the hum module reads on load.
  const workingHum = document.getElementById("input-working-hum") as HTMLInputElement | null;
  if (workingHum) {
    workingHum.checked = localStorage.getItem("valet.hum.enabled") !== "0";
    workingHum.addEventListener("change", () => {
      (window as unknown as { __valetSetHum?: (on: boolean) => void })
        .__valetSetHum?.(workingHum.checked);
    });
  }
  void loadVoice();

  // Close
  document.getElementById("settings-close")?.addEventListener("click", closeSettings);
  document.getElementById("settings-backdrop")?.addEventListener("click", closeSettings);

  // Save keys (license only — Proxy URL is a fixed default; Voice ID moved to
  // the web account dashboard and is applied to the app from there).
  document.getElementById("btn-save-keys")?.addEventListener("click", async () => {
    const licenseKey = (document.getElementById("input-license-key") as HTMLInputElement).value.trim();
    if (licenseKey) {
      await apiPost("/api/settings/keys", { key_name: "LICENSE_KEY", key_value: licenseKey });
    }
    await loadStatus();
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
  // Save only the in-app fields (name + honorific). The backend POST writes every
  // field it receives, so we MERGE: fetch current prefs and re-send the web-managed
  // ones unchanged (calendar / DOB / location), or they'd be blanked.
  async function saveAllPreferences() {
    const user_name = (document.getElementById("input-user-name") as HTMLInputElement | null)?.value.trim() ?? "";
    const honorific = (document.getElementById("input-honorific") as HTMLSelectElement | null)?.value ?? "sir";
    let cur: Partial<PreferencesResponse> = {};
    try { cur = await apiGet<PreferencesResponse>("/api/settings/preferences"); } catch { /* keep empty */ }
    await apiPost("/api/settings/preferences", {
      user_name,
      honorific,
      calendar_accounts: cur.calendar_accounts ?? "auto",
      date_of_birth: cur.date_of_birth ?? "",
      address: cur.address ?? "",
      hometown_city: cur.hometown_city ?? "",
    });
    await loadStatus();
  }
  // Auto-save on change (no Save button); flash the "Saved automatically" note.
  function flashSaved() {
    const el = document.getElementById("prefs-saved");
    if (!el) return;
    el.classList.add("on");
    window.setTimeout(() => el.classList.remove("on"), 1500);
  }
  const autoSave = async () => { await saveAllPreferences(); flashSaved(); };
  document.getElementById("input-user-name")?.addEventListener("change", autoSave);
  document.getElementById("input-honorific")?.addEventListener("change", autoSave);
  document.getElementById("btn-save-prefs")?.addEventListener("click", saveAllPreferences);
  document.getElementById("btn-save-personalized")?.addEventListener("click", saveAllPreferences);

  document.getElementById("btn-restart-server")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget as HTMLButtonElement;
    btn.disabled = true;
    btn.textContent = "Restarting…";
    try { await apiPost("/api/restart", {}); } catch { /* the server is going down; expected */ }
    setTimeout(() => { btn.disabled = false; btn.textContent = "Restart server"; }, 4000);
  });

  // Account login — provisions the license key + profile, then refreshes the
  // fields below so they fill in immediately.
  document.getElementById("btn-account-login")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-account-login") as HTMLButtonElement | null;
    const status = document.getElementById("login-status");
    const email = (document.getElementById("login-email") as HTMLInputElement | null)?.value.trim() || "";
    const password = (document.getElementById("login-password") as HTMLInputElement | null)?.value || "";
    const setStatus = (t: string, warn = false) => {
      if (status) { status.textContent = t; status.className = warn ? "settings-hint warn" : "settings-hint"; }
    };
    if (!email || !password) { setStatus("Enter your email and password.", true); return; }
    if (btn) { btn.disabled = true; btn.textContent = "Signing in…"; }
    setStatus("Signing in…");
    try {
      const res = await apiPost<{
        ok: boolean; error?: string; has_license?: boolean; plan?: string | null;
        needs_relaunch?: boolean; profile_applied?: string[]; name?: string; license_key?: string;
      }>("/api/account/login", { email, password });
      if (!res.ok) { setStatus(res.error || "Sign-in failed.", true); return; }
      // Clear the password field; never keep it around.
      const pwEl = document.getElementById("login-password") as HTMLInputElement | null;
      if (pwEl) pwEl.value = "";
      await loadPreferences();   // pulls the freshly-written name/honorific/DOB/location
      // Collapse to the signed-in state (hides the form so it isn't shown next
      // to the now-populated profile), and leave first-run setup mode if we
      // were in it (e.g. logged in from the setup-mode account form).
      exitSetupMode();
      renderAccountState(email, { name: res.name, plan: res.plan, licenseKey: res.license_key });
      const note = document.getElementById("login-status-in");
      const parts: string[] = [];
      if (!res.has_license) parts.push("No license on this account yet.");
      if (res.needs_relaunch) parts.push("Restart VALET to activate your license.");
      if (note) { note.textContent = parts.join(" "); note.className = parts.length ? "settings-hint warn" : "settings-hint"; }
    } catch {
      setStatus("Couldn't reach the account server.", true);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Log in"; }
    }
  });

  // Sign out — clear the stored account email so the form reappears. Leaves the
  // license + profile in place (you're still licensed); this just lets you
  // switch accounts.
  document.getElementById("btn-account-signout")?.addEventListener("click", async () => {
    try { await apiPost("/api/settings/keys", { key_name: "ACCOUNT_EMAIL", key_value: "" }); } catch { /* best effort */ }
    renderAccountState(null);
  });
  // Show/hide the password via the eye toggle inside the field.
  document.getElementById("login-eye")?.addEventListener("click", () => {
    const pw = document.getElementById("login-password") as HTMLInputElement | null;
    const eye = document.getElementById("login-eye");
    if (!pw || !eye) return;
    const reveal = pw.type === "password";
    pw.type = reveal ? "text" : "password";
    eye.innerHTML = reveal ? EYE_OFF_SVG : EYE_SVG;
    eye.setAttribute("aria-label", reveal ? "Hide password" : "Show password");
  });

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

  // Contacts — add
  document.getElementById("btn-contact-add")?.addEventListener("click", async () => {
    const nameEl = document.getElementById("contact-name") as HTMLInputElement | null;
    const emailEl = document.getElementById("contact-email") as HTMLInputElement | null;
    const name = (nameEl?.value || "").trim();
    const email = (emailEl?.value || "").trim();
    if (!name || !email.includes("@")) return;
    await apiPost("/api/contacts", { name, email });
    if (nameEl) nameEl.value = "";
    if (emailEl) emailEl.value = "";
    await loadContacts();
  });

  // Contacts — remove (delegated, survives re-render)
  document.getElementById("contacts-list")?.addEventListener("click", async (e) => {
    const btn = (e.target as HTMLElement).closest("button") as HTMLButtonElement | null;
    const name = btn?.dataset.contactRemove;
    if (!name) return;
    await fetch("/api/contacts", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    await loadContacts();
  });

  // Permissions (Console) — delegated so it survives the dynamic re-render.
  document.getElementById("settings-perm-list")?.addEventListener("click", async (e) => {
    const btn = (e.target as HTMLElement).closest("button") as HTMLButtonElement | null;
    if (!btn) return;
    if (btn.dataset.permMic) {
      // Native macOS mic prompt — the reliable one-click grant path.
      btn.disabled = true;
      btn.textContent = "Requesting...";
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((t) => t.stop());
        await loadSettingsPermissions();
      } catch {
        // Previously denied: the prompt won't show — fall back to Settings.
        btn.disabled = false;
        btn.textContent = "Open Settings";
        delete btn.dataset.permMic;
        btn.dataset.permOpen = "microphone";
      }
      return;
    }
    if (btn.dataset.permTrigger) {
      // Fire the native prompt inline (automation = control System Events;
      // calendars = full Calendar access).
      const target = btn.dataset.permTrigger;
      btn.disabled = true;
      btn.textContent = "Requesting...";
      const res = await apiPost<{ ok: boolean; granted?: boolean }>(
        "/api/permissions/trigger", { target },
      );
      if (res?.granted) {
        // Mark granted in-place — don't reload (automation can't be silently
        // re-detected; calendars would re-read fine but this is simpler). The
        // grant persists in macOS regardless.
        const dot = btn.closest(".account-row")?.querySelector(".status-dot");
        if (dot) dot.className = "status-dot status-green";
        btn.textContent = "Granted";
      } else {
        btn.disabled = false;
        btn.textContent = "Open Settings";
        btn.dataset.permOpen = target;  // automation | calendars
        delete btn.dataset.permTrigger;
      }
      return;
    }
    if (btn.dataset.permOpen) {
      btn.disabled = true;
      await apiPost("/api/permissions/open", { target: btn.dataset.permOpen });
      setTimeout(() => (btn.disabled = false), 1200);
    }
  });
  document.getElementById("btn-recheck-perms")?.addEventListener("click", loadSettingsPermissions);

  // Setup next button
  document.getElementById("btn-setup-next")?.addEventListener("click", advanceSetup);
}

// ---------------------------------------------------------------------------
// First-time setup wizard
// ---------------------------------------------------------------------------

function enterSetupMode() {
  isFirstTimeSetup = true;
  setupStep = 0;

  // Strip the License section to the bare minimum (license key only) during
  // first run; .setup-hide fields (proxy, voice, voice id, telemetry) reappear
  // in normal settings.
  const panel = document.getElementById("settings-panel-inner");
  if (panel) panel.classList.add("first-run");

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
  const sections = ["section-api-keys", "section-status", "section-accounts", "section-preferences", "section-personalized", "section-sysinfo", "section-permissions"];
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

/**
 * Leave first-run setup mode and restore normal tabbed settings. Idempotent —
 * safe to call when not in setup mode. Called both when the setup wizard
 * finishes AND whenever a licensed user opens Settings, so a session that
 * entered setup mode while unlicensed doesn't stay stuck there after login.
 */
function exitSetupMode() {
  isFirstTimeSetup = false;
  const panel = document.getElementById("settings-panel-inner");
  if (panel) panel.classList.remove("first-run");
  const welcome = document.getElementById("settings-welcome");
  if (welcome) welcome.style.display = "none";
  const nav = document.getElementById("setup-nav");
  if (nav) nav.style.display = "none";
  ["section-api-keys", "section-status", "section-accounts", "section-preferences", "section-personalized", "section-sysinfo", "section-permissions"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.style.display = "";
  });
  const tabs = document.getElementById("settings-tabs");
  if (tabs) tabs.style.display = "";
  activateTab("user");
}

async function advanceSetup() {
  setupStep++;
  if (setupStep >= 3) {
    // Done — restore normal settings and close.
    exitSetupMode();
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
  void loadSettingsPermissions();

  // First-run setup only while unlicensed; once licensed, always restore normal
  // settings (so a session that entered setup mode before login doesn't stay
  // stuck there with the welcome banner + login form).
  if (status && !status.env_keys_set.license) {
    enterSetupMode();
  } else {
    exitSetupMode();
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
