/**
 * First-run setup wizard (Phase J).
 *
 * A guided multi-step flow shown on the first open of every new build: license
 * key, the macOS permissions Vee needs, voice, profile, and connections. The
 * "seen" marker is keyed to the build id from /api/config, so every new download
 * re-runs onboarding (a stale flag can never suppress it) and a fresh rebuild
 * re-triggers it for testing. Non-blocking: the user can skip steps.
 */

import "./onboarding.css";

const SEEN_KEY = "valet_onboarded_build";

// ---- backend helpers -------------------------------------------------------

async function getJSON<T>(url: string): Promise<T | null> {
  try { return (await fetch(url)).json() as Promise<T>; } catch { return null; }
}
async function postJSON<T = unknown>(url: string, body: unknown): Promise<T | null> {
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return (await r.json()) as T;
  } catch { return null; }
}
const saveKey = (key_name: string, key_value: string) => postJSON("/api/settings/keys", { key_name, key_value });

// ---- permission helpers (carried from the original screen) -----------------

interface Permission { granted: boolean | null; label: string; why: string; note?: string; required_v1?: boolean; }
type PermStatus = Record<string, Permission>;

const TARGET_FOR = (key: string): string =>
  key === "full_disk_access" ? "full_disk"
  : key === "microphone" ? "microphone"
  : key === "automation" ? "automation"
  : "accessibility";

function pill(p: Permission): { text: string; cls: string } {
  if (p.granted === true) return { text: "Granted", cls: "ok" };
  if (p.granted === false) return { text: "Needs setup", cls: "warn" };
  if (p.required_v1 === false) return { text: "Not needed yet", cls: "muted" };
  return { text: "Prompts on first use", cls: "muted" };
}

// ---- wizard state ----------------------------------------------------------

interface State {
  step: number;
  buildId: string;
  perms: PermStatus | null;
  voice: "male" | "female";
}

const STEP_TITLES = ["Welcome", "License", "Permissions", "Voice", "About you", "Connections", "Done"];

// ---- per-step body renderers ----------------------------------------------

function welcomeBody(): string {
  return `
    <h2 class="ob-title">Welcome to VALET.</h2>
    <p class="ob-sub">Vee is a voice assistant that acts across your Mac. This quick setup gets you talking in about a minute. You can change anything later in Settings.</p>
    <ul class="ob-list">
      <li>Activate with your license key</li>
      <li>Grant the permissions Vee needs to act</li>
      <li>Pick a voice and tell Vee about you</li>
    </ul>`;
}

function licenseBody(): string {
  return `
    <h2 class="ob-title">Activate VALET.</h2>
    <p class="ob-sub">Paste the license key from your purchase email. It unlocks everything, no API keys of your own required.</p>
    <div class="ob-field">
      <label class="ob-label">License key</label>
      <input id="ob-license" class="ob-input" type="text" placeholder="PRODUCT-XXXX-XXXX-XXXX-XXXX-XXXX" autocomplete="off" spellcheck="false" />
    </div>
    <div class="ob-inline">
      <button class="ob-btn ghost" id="ob-license-test">Activate</button>
      <span id="ob-license-status" class="ob-status"></span>
    </div>`;
}

function permsBody(status: PermStatus | null): string {
  if (!status) return `<h2 class="ob-title">Permissions.</h2><p class="ob-sub">Could not reach the backend yet. You can grant these later in Settings.</p>`;
  const rows = ["microphone", "automation", "accessibility", "full_disk_access"]
    .filter((k) => status[k])
    .map((k) => {
      const p = status[k]; const s = pill(p);
      const canOpen = p.granted === false || k === "automation" || k === "microphone";
      return `
        <div class="ob-row" data-key="${k}">
          <div class="ob-row-main">
            <div class="ob-row-label">${p.label}</div>
            <div class="ob-row-why">${p.why}${p.note ? ` <span class="ob-note">${p.note}</span>` : ""}</div>
          </div>
          <div class="ob-row-side">
            <span class="ob-pill ${s.cls}">${s.text}</span>
            ${canOpen ? `<button class="ob-open" data-target="${TARGET_FOR(k)}">Open Settings</button>` : ""}
          </div>
        </div>`;
    }).join("");
  return `
    <h2 class="ob-title">A few permissions.</h2>
    <p class="ob-sub">Vee acts across your Mac. Grant what you're comfortable with. Microphone is required to talk to Vee; the rest unlock more actions.</p>
    <div class="ob-rows">${rows}</div>
    <div class="ob-inline"><button class="ob-btn ghost" id="ob-recheck">Re-check</button></div>`;
}

function voiceBody(voice: "male" | "female"): string {
  const card = (v: "male" | "female", label: string, desc: string) => `
    <button class="ob-voice ${voice === v ? "sel" : ""}" data-voice="${v}">
      <div class="ob-voice-label">${label}</div>
      <div class="ob-voice-desc">${desc}</div>
    </button>`;
  return `
    <h2 class="ob-title">Pick a voice.</h2>
    <p class="ob-sub">How Vee sounds back to you. Change it anytime in Settings.</p>
    <div class="ob-voices">
      ${card("male", "British, male", "Calm and measured")}
      ${card("female", "British, female", "Warm and clear")}
    </div>`;
}

function profileBody(): string {
  return `
    <h2 class="ob-title">About you.</h2>
    <p class="ob-sub">So Vee can greet you and use the right context. All optional, all local to your Mac.</p>
    <div class="ob-field"><label class="ob-label">Name</label><input id="ob-name" class="ob-input" type="text" placeholder="What should Vee call you?" /></div>
    <div class="ob-field"><label class="ob-label">Date of birth</label><input id="ob-dob" class="ob-input" type="date" /></div>
    <div class="ob-field"><label class="ob-label">Location</label><input id="ob-loc" class="ob-input" type="text" placeholder="City you live in" /></div>`;
}

function connectionsBody(): string {
  const conn = (id: string, label: string, desc: string, soon: boolean) => `
    <div class="ob-row">
      <div class="ob-row-main">
        <div class="ob-row-label">${label}</div>
        <div class="ob-row-why">${desc}</div>
      </div>
      <div class="ob-row-side">
        ${soon
          ? `<span class="ob-pill muted">Coming soon</span>`
          : `<button class="ob-open" data-connect="${id}">Connect</button>`}
      </div>
    </div>`;
  return `
    <h2 class="ob-title">Connections.</h2>
    <p class="ob-sub">Link the apps Vee can reach into. You can add these now or later in Settings.</p>
    <div class="ob-rows">
      ${conn("google_cal", "Google Calendar", "Read and create events by voice.", true)}
      ${conn("gmail", "Gmail", "Triage and draft mail.", true)}
      ${conn("mcp", "MCP servers", "Connect tools that speak the Model Context Protocol.", true)}
      ${conn("apps", "Other apps", "Spotify, Notes, the browser, and more work out of the box.", true)}
    </div>
    <p class="ob-fineprint">Google and Gmail connections are landing in a coming update. Everything else is ready to use the moment you finish setup.</p>`;
}

function doneBody(): string {
  return `
    <h2 class="ob-title">You're set.</h2>
    <p class="ob-sub">Say "Hey Vee" or just start talking. Ask for anything, from a quick question to a multi-step task across your apps.</p>
    <div class="ob-done-orb"></div>`;
}

// ---- step wiring -----------------------------------------------------------

function wireStep(state: State, root: HTMLElement): void {
  const step = state.step;

  if (step === 1) {
    const input = root.querySelector<HTMLInputElement>("#ob-license");
    const statusEl = root.querySelector<HTMLElement>("#ob-license-status");
    root.querySelector("#ob-license-test")?.addEventListener("click", async () => {
      const key = input?.value.trim() || "";
      if (!key) { if (statusEl) { statusEl.textContent = "Enter your license key."; statusEl.className = "ob-status warn"; } return; }
      if (statusEl) { statusEl.textContent = "Checking..."; statusEl.className = "ob-status"; }
      await saveKey("LICENSE_KEY", key);
      const res = await postJSON<{ valid: boolean; status?: string; error?: string }>("/api/settings/test-license", { key_value: key });
      if (statusEl) {
        if (res?.valid) { statusEl.textContent = "Activated. You're good to go."; statusEl.className = "ob-status ok"; }
        else { statusEl.textContent = res?.error || "Could not validate that key."; statusEl.className = "ob-status warn"; }
      }
    });
  }

  if (step === 2) {
    root.querySelectorAll<HTMLButtonElement>(".ob-open").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        await postJSON("/api/permissions/open", { target: btn.dataset.target });
        setTimeout(() => (btn.disabled = false), 1200);
      });
    });
    root.querySelector("#ob-recheck")?.addEventListener("click", async () => {
      state.perms = await getJSON<PermStatus>("/api/permissions/status");
      renderBody(state, root);
    });
  }

  if (step === 3) {
    root.querySelectorAll<HTMLButtonElement>(".ob-voice").forEach((btn) => {
      btn.addEventListener("click", async () => {
        state.voice = (btn.dataset.voice as "male" | "female") || "male";
        await saveKey("VALET_VOICE", state.voice);
        renderBody(state, root);
      });
    });
  }
}

/** Persist whatever the current step collected before moving on. */
async function saveStep(state: State, root: HTMLElement): Promise<void> {
  if (state.step === 4) {
    const name = root.querySelector<HTMLInputElement>("#ob-name")?.value.trim() || "";
    const dob = root.querySelector<HTMLInputElement>("#ob-dob")?.value.trim() || "";
    const loc = root.querySelector<HTMLInputElement>("#ob-loc")?.value.trim() || "";
    await postJSON("/api/settings/preferences", {
      user_name: name, date_of_birth: dob, address: loc, hometown_city: loc,
    });
  }
}

// ---- frame + navigation ----------------------------------------------------

function bodyFor(state: State): string {
  switch (state.step) {
    case 0: return welcomeBody();
    case 1: return licenseBody();
    case 2: return permsBody(state.perms);
    case 3: return voiceBody(state.voice);
    case 4: return profileBody();
    case 5: return connectionsBody();
    default: return doneBody();
  }
}

function renderBody(state: State, root: HTMLElement): void {
  const body = root.querySelector<HTMLElement>(".ob-body");
  const back = root.querySelector<HTMLButtonElement>("#ob-back");
  const next = root.querySelector<HTMLButtonElement>("#ob-next");
  if (!body || !back || !next) return;
  body.innerHTML = bodyFor(state);
  back.style.visibility = state.step === 0 ? "hidden" : "visible";
  next.textContent = state.step === STEP_TITLES.length - 1 ? "Start using VALET" : "Continue";
  root.querySelectorAll<HTMLElement>(".ob-dot").forEach((d, i) => d.classList.toggle("on", i <= state.step));
  wireStep(state, root);
}

function render(state: State, root: HTMLElement): void {
  const dots = STEP_TITLES.map((_, i) => `<span class="ob-dot ${i === 0 ? "on" : ""}"></span>`).join("");
  root.innerHTML = `
    <div class="ob-backdrop"></div>
    <div class="ob-card ob-wizard" role="dialog" aria-label="VALET setup">
      <div class="ob-head">
        <div class="ob-brand">VALET</div>
        <div class="ob-dots">${dots}</div>
      </div>
      <div class="ob-body">${bodyFor(state)}</div>
      <div class="ob-actions">
        <button class="ob-btn ghost" id="ob-back">Back</button>
        <button class="ob-btn primary" id="ob-next">Continue</button>
      </div>
    </div>`;

  root.querySelector("#ob-back")?.addEventListener("click", () => {
    if (state.step > 0) { state.step--; renderBody(state, root); }
  });
  root.querySelector("#ob-next")?.addEventListener("click", async () => {
    await saveStep(state, root);
    if (state.step >= STEP_TITLES.length - 1) {
      localStorage.setItem(SEEN_KEY, state.buildId);
      root.remove();
      return;
    }
    state.step++;
    renderBody(state, root);
  });

  wireStep(state, root);
}

/** Show the wizard on the first open of each new build. No-op once finished. */
export async function maybeShowOnboarding(): Promise<void> {
  const cfg = await getJSON<{ build_id?: string; voice?: string }>("/api/config");
  const buildId = cfg?.build_id || "dev";
  if (localStorage.getItem(SEEN_KEY) === buildId) return; // already onboarded this build
  const perms = await getJSON<PermStatus>("/api/permissions/status");
  const state: State = {
    step: 0,
    buildId,
    perms,
    voice: cfg?.voice === "female" ? "female" : "male",
  };
  const root = document.createElement("div");
  root.id = "valet-onboarding";
  document.body.appendChild(root);
  render(state, root);
}
