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

// Eye icons for the password show/hide toggle.
const EYE_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`;
const EYE_OFF_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`;

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

/**
 * Real microphone-grant state, read where the mic actually lives — the webview.
 * The backend can't cleanly detect this, so it returns null ("prompts on first
 * use"); we override with the Permissions API so the indicator goes green once
 * the user has allowed the mic. Returns null if unsupported (keeps backend value).
 */
async function micGranted(): Promise<boolean | null> {
  try {
    const s = await navigator.permissions.query({ name: "microphone" as PermissionName });
    if (s.state === "granted") return true;
    if (s.state === "denied") return false;
    return null; // "prompt"
  } catch {
    return null;
  }
}

/** Load permission status from the backend, then refine microphone client-side. */
async function loadPerms(): Promise<PermStatus | null> {
  const perms = await getJSON<PermStatus>("/api/permissions/status");
  if (perms?.microphone) {
    const mic = await micGranted();
    if (mic !== null) perms.microphone.granted = mic;
  }
  return perms;
}

// ---- permission helpers (carried from the original screen) -----------------

interface Permission { granted: boolean | null; label: string; why: string; note?: string; required_v1?: boolean; }
type PermStatus = Record<string, Permission>;

const TARGET_FOR = (key: string): string =>
  key === "full_disk_access" ? "full_disk"
  : key === "microphone" ? "microphone"
  : key === "automation" ? "automation"
  : key === "screen_recording" ? "screen_recording"
  : key === "speech_recognition" ? "speech_recognition"
  : key === "input_monitoring" ? "input_monitoring"
  : "accessibility";

function pill(p: Permission): { text: string; cls: string } {
  if (p.granted === true) return { text: "Granted", cls: "ok" };
  if (p.granted === false) return { text: "Needs setup", cls: "warn" };
  if (p.required_v1 === false) return { text: "Not needed yet", cls: "muted" };
  return { text: "Prompts on first use", cls: "muted" };
}

// ---- wizard state ----------------------------------------------------------

type Narrate = (text: string) => void;
type StartDemo = () => void;

interface State {
  step: number;
  buildId: string;
  perms: PermStatus | null;
  voice: "male" | "female";
  speak: Narrate;
  demo: StartDemo;
}

const STEP_TITLES = ["Welcome", "License", "Permissions", "Voice", "About you", "Done"];

// What Vee says when each step appears (the hand-holding voice-over). Kept to one
// or two short sentences — butler tone, no em-dashes (the backend strips them).
const STEP_NARRATION = [
  "Good day. I'm Vee, your assistant. Let's get you set up. It only takes a minute, and you can change anything later.",
  "First, let's activate your copy. Sign in with your VALET account, or paste the license key from your purchase email.",
  "Now the permissions I need to act for you. Grant each one and I'll confirm as it turns green.",
  "How would you like me to sound? Pick a voice and I'll say hello.",
  "Tell me a little about you, so I can address you properly. All of this stays on your Mac.",
  "You're all set. Hold Control and Option anytime, from any app, and just talk to me.",
];

/** Speak the line for the current step. */
function narrateStep(state: State): void {
  const line = STEP_NARRATION[state.step];
  if (line) state.speak(line);
}

/**
 * Reload permissions and, when one has newly flipped to granted, have Vee
 * acknowledge it out loud (one at a time, so acks never overlap).
 */
async function refreshPerms(state: State): Promise<void> {
  const prev = state.perms;
  const next = await loadPerms();
  if (next && prev) {
    for (const k of Object.keys(next)) {
      if (next[k]?.granted === true && prev[k]?.granted !== true) {
        state.speak(`${next[k].label}, granted.`);
        break;
      }
    }
  }
  state.perms = next;
}

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
    <!-- Signed-in summary — replaces the whole form once login succeeds. -->
    <div id="ob-activated" style="display:none">
      <p class="ob-sub">Signed in as <strong id="ob-acct-name"></strong>. You're good to go — press Continue.</p>
      <div class="ob-acct-meta">
        <div><span class="ob-acct-k">Plan</span><span id="ob-acct-plan">—</span></div>
        <div><span class="ob-acct-k">License</span><span id="ob-acct-license" class="ob-acct-lic">—</span></div>
      </div>
    </div>
    <!-- Activate form (login or license key) -->
    <div id="ob-activate-form">
      <p class="ob-sub">Sign in with your VALET account — it pulls in your license and profile automatically. No API keys of your own required.</p>
      <div class="ob-field">
        <label class="ob-label">Email</label>
        <input id="ob-login-email" class="ob-input" type="email" autocomplete="username" spellcheck="false" />
      </div>
      <div class="ob-field">
        <label class="ob-label">Password</label>
        <div class="ob-pw-field">
          <input id="ob-login-password" class="ob-input" type="password" autocomplete="current-password" />
          <button type="button" class="ob-pw-toggle" id="ob-login-eye" aria-label="Show password">${EYE_SVG}</button>
        </div>
      </div>
      <div class="ob-inline">
        <button class="ob-btn primary" id="ob-login">Log in</button>
        <span id="ob-login-status" class="ob-status"></span>
      </div>
      <p class="ob-sub" style="margin-top:22px">Or paste the license key from your purchase email:</p>
      <div class="ob-field">
        <label class="ob-label">License key</label>
        <input id="ob-license" class="ob-input" type="text" placeholder="PRODUCT-XXXX-XXXX-XXXX-XXXX-XXXX" autocomplete="off" spellcheck="false" />
      </div>
      <div class="ob-inline">
        <button class="ob-btn ghost" id="ob-license-test">Activate</button>
        <span id="ob-license-status" class="ob-status"></span>
      </div>
    </div>`;
}

function permsBody(status: PermStatus | null): string {
  if (!status) return `<h2 class="ob-title">Permissions.</h2><p class="ob-sub">Could not reach the backend yet. You can grant these later in Settings.</p>`;
  const rows = ["microphone", "speech_recognition", "calendars", "automation", "accessibility", "screen_recording", "full_disk_access"]
    .filter((k) => status[k])
    .map((k) => {
      const p = status[k]; const s = pill(p);
      const canOpen = p.granted === false || k === "automation" || k === "microphone";
      // Microphone / Calendar / Automation get an active "Enable" that fires the
      // native macOS prompt inline; others deep-link to Settings.
      const sideBtn =
        k === "microphone" && p.granted !== true
          ? `<button class="ob-open" data-mic-enable="1">Enable microphone</button>`
          : k === "automation" && p.granted !== true
            ? `<button class="ob-open" data-automation-enable="1">Enable</button>`
            : k === "calendars" && p.granted !== true
              ? `<button class="ob-open" data-calendars-enable="1">Enable</button>`
              : k === "accessibility" && p.granted !== true
                ? `<button class="ob-open" data-accessibility-enable="1">Enable</button>`
                : k === "screen_recording" && p.granted !== true
                  ? `<button class="ob-open" data-screenrec-enable="1">Enable</button>`
                  : k === "speech_recognition"
                    ? `<button class="ob-open" data-target="speech_recognition">Open Settings</button>`
                    : canOpen
                      ? `<button class="ob-open" data-target="${TARGET_FOR(k)}">Open Settings</button>`
                      : "";
      return `
        <div class="ob-row" data-key="${k}">
          <div class="ob-row-main">
            <div class="ob-row-label">${p.label}</div>
            <div class="ob-row-why">${p.why}${p.note ? ` <span class="ob-note">${p.note}</span>` : ""}</div>
          </div>
          <div class="ob-row-side">
            <span class="ob-pill ${s.cls}">${s.text}</span>
            ${sideBtn}
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

function doneBody(state: State): string {
  // Offer the live guided demo only when Accessibility is granted (it needs to
  // read the screen and move the cursor); otherwise nudge toward granting it.
  const canDemo = state.perms?.accessibility?.granted === true;
  const demo = canDemo
    ? `<button class="ob-btn primary ob-demo" id="ob-demo">Show me what you can do</button>`
    : `<p class="ob-fineprint">Grant Accessibility above to see Vee glide your cursor and control the screen.</p>`;
  return `
    <h2 class="ob-title">You're set.</h2>
    <p class="ob-sub">Hold Control and Option from any app and just talk. Ask for anything, from a quick question to a multi-step task across your apps.</p>
    <div class="ob-done-orb"></div>
    <div class="ob-inline" style="justify-content:center">${demo}</div>`;
}

// ---- step wiring -----------------------------------------------------------

function wireStep(state: State, root: HTMLElement): void {
  const step = state.step;

  if (step === 1) {
    // Account login — provisions the license key + profile in one step. The
    // backend writes them to .env, so the later "About you" step pre-fills.
    const loginBtn = root.querySelector<HTMLButtonElement>("#ob-login");
    const loginStatus = root.querySelector<HTMLElement>("#ob-login-status");
    const eye = root.querySelector<HTMLButtonElement>("#ob-login-eye");
    eye?.addEventListener("click", () => {
      const pw = root.querySelector<HTMLInputElement>("#ob-login-password");
      if (!pw) return;
      const reveal = pw.type === "password";
      pw.type = reveal ? "text" : "password";
      eye.innerHTML = reveal ? EYE_OFF_SVG : EYE_SVG;
      eye.setAttribute("aria-label", reveal ? "Hide password" : "Show password");
    });
    loginBtn?.addEventListener("click", async () => {
      const email = root.querySelector<HTMLInputElement>("#ob-login-email")?.value.trim() || "";
      const pwEl = root.querySelector<HTMLInputElement>("#ob-login-password");
      const password = pwEl?.value || "";
      const set = (t: string, cls: string) => { if (loginStatus) { loginStatus.textContent = t; loginStatus.className = `ob-status ${cls}`; } };
      if (!email || !password) { set("Enter your email and password.", "warn"); return; }
      loginBtn.disabled = true; loginBtn.textContent = "Signing in…"; set("Signing in…", "");
      try {
        const res = await postJSON<{ ok: boolean; error?: string; has_license?: boolean; plan?: string | null; name?: string; license_key?: string }>(
          "/api/account/login", { email, password },
        );
        if (pwEl) pwEl.value = "";
        if (res?.ok && res.has_license) {
          // Replace the entire form with a clean signed-in summary.
          const nameEl = root.querySelector<HTMLElement>("#ob-acct-name");
          const planEl = root.querySelector<HTMLElement>("#ob-acct-plan");
          const licEl = root.querySelector<HTMLElement>("#ob-acct-license");
          if (nameEl) nameEl.textContent = (res.name && res.name.trim()) || email;
          if (planEl) planEl.textContent = res.plan || "Active";
          if (licEl) licEl.textContent = res.license_key || "—";
          const form = root.querySelector<HTMLElement>("#ob-activate-form");
          const done = root.querySelector<HTMLElement>("#ob-activated");
          if (form) form.style.display = "none";
          if (done) done.style.display = "";
        } else if (res?.ok) {
          set("Signed in, but no license on this account yet.", "warn");
        } else {
          set(res?.error || "Sign-in failed.", "warn");
        }
      } catch {
        set("Couldn't reach the account server.", "warn");
      } finally {
        loginBtn.disabled = false; loginBtn.textContent = "Log in";
      }
    });

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
        // Microphone: fire the native macOS permission prompt directly. This is
        // the reliable grant path (one "Allow" click, no Settings hunting).
        if (btn.dataset.micEnable) {
          btn.disabled = true;
          btn.textContent = "Requesting...";
          try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            stream.getTracks().forEach((t) => t.stop()); // release immediately
            await refreshPerms(state);
            renderBody(state, root); // mic should now read Granted
          } catch {
            // Previously denied or blocked: native prompt won't show, so guide
            // the user to System Settings instead.
            delete btn.dataset.micEnable;
            btn.dataset.target = "microphone";
            btn.textContent = "Open Settings";
            btn.disabled = false;
          }
          return;
        }
        // Automation: fire the native "control System Events" prompt inline,
        // then reflect the grant the trigger reports back.
        if (btn.dataset.automationEnable) {
          btn.disabled = true;
          btn.textContent = "Requesting...";
          const res = await postJSON<{ ok: boolean; granted?: boolean }>(
            "/api/permissions/trigger", { target: "automation" },
          );
          if (res?.granted) {
            await refreshPerms(state);
            if (state.perms?.automation) state.perms.automation.granted = true;
            renderBody(state, root);
          } else {
            // Denied/cancelled — macOS won't re-prompt; point to Settings.
            delete btn.dataset.automationEnable;
            btn.dataset.target = "automation";
            btn.textContent = "Open Settings";
            btn.disabled = false;
          }
          return;
        }
        // Accessibility: fire the native "grant Accessibility" prompt. The grant
        // itself lands in System Settings and needs a relaunch, so it usually
        // reads not-granted right after — fall back to Open Settings + Re-check.
        if (btn.dataset.accessibilityEnable) {
          btn.disabled = true;
          btn.textContent = "Requesting...";
          const res = await postJSON<{ ok: boolean; granted?: boolean }>(
            "/api/permissions/trigger", { target: "accessibility" },
          );
          if (res?.granted) {
            await refreshPerms(state);
            renderBody(state, root);
          } else {
            delete btn.dataset.accessibilityEnable;
            btn.dataset.target = "accessibility";
            btn.textContent = "Open Settings";
            btn.disabled = false;
          }
          return;
        }
        // Screen Recording: fire the native prompt. Like Accessibility, the grant
        // lands in System Settings and needs a relaunch, so it usually still reads
        // not-granted right after — fall back to Open Settings + Re-check.
        if (btn.dataset.screenrecEnable) {
          btn.disabled = true;
          btn.textContent = "Requesting...";
          const res = await postJSON<{ ok: boolean; granted?: boolean }>(
            "/api/permissions/trigger", { target: "screen_recording" },
          );
          if (res?.granted) {
            await refreshPerms(state);
            renderBody(state, root);
          } else {
            delete btn.dataset.screenrecEnable;
            btn.dataset.target = "screen_recording";
            btn.textContent = "Open Settings";
            btn.disabled = false;
          }
          return;
        }
        // Calendar: request full EventKit access inline (native prompt).
        if (btn.dataset.calendarsEnable) {
          btn.disabled = true;
          btn.textContent = "Requesting...";
          const res = await postJSON<{ ok: boolean; granted?: boolean }>(
            "/api/permissions/trigger", { target: "calendars" },
          );
          if (res?.granted) {
            await refreshPerms(state);
            if (state.perms?.calendars) state.perms.calendars.granted = true;
            renderBody(state, root);
          } else {
            delete btn.dataset.calendarsEnable;
            btn.dataset.target = "calendars";
            btn.textContent = "Open Settings";
            btn.disabled = false;
          }
          return;
        }
        btn.disabled = true;
        await postJSON("/api/permissions/open", { target: btn.dataset.target });
        setTimeout(() => (btn.disabled = false), 1200);
      });
    });
    root.querySelector("#ob-recheck")?.addEventListener("click", async () => {
      await refreshPerms(state);
      renderBody(state, root);
    });
  }

  if (step === 3) {
    root.querySelectorAll<HTMLButtonElement>(".ob-voice").forEach((btn) => {
      btn.addEventListener("click", async () => {
        state.voice = (btn.dataset.voice as "male" | "female") || "male";
        await saveKey("VALET_VOICE", state.voice);
        renderBody(state, root);
        // Saved to .env live, so the next narration speaks in the chosen voice.
        state.speak("This is how I'll sound. You can change it anytime in Settings.");
      });
    });
  }

  if (step === 4) {
    // Pre-fill the profile from already-saved preferences so nothing is re-typed.
    void getJSON<{ user_name?: string; date_of_birth?: string; hometown_city?: string; address?: string }>(
      "/api/settings/preferences",
    ).then((p) => {
      if (!p) return;
      const name = root.querySelector<HTMLInputElement>("#ob-name");
      const dob = root.querySelector<HTMLInputElement>("#ob-dob");
      const loc = root.querySelector<HTMLInputElement>("#ob-loc");
      if (name && p.user_name) name.value = p.user_name;
      if (dob && p.date_of_birth) dob.value = p.date_of_birth;
      if (loc && (p.hometown_city || p.address)) loc.value = p.hometown_city || p.address || "";
    });
  }

  if (step === 5) {
    const demoBtn = root.querySelector<HTMLButtonElement>("#ob-demo");
    demoBtn?.addEventListener("click", () => {
      demoBtn.disabled = true;
      demoBtn.textContent = "Watch your cursor…";
      state.demo();
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
    default: return doneBody(state);
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

  // Hide Back on the first step (renderBody handles it after navigation).
  const backInit = root.querySelector<HTMLButtonElement>("#ob-back");
  if (backInit) backInit.style.visibility = state.step === 0 ? "hidden" : "visible";

  root.querySelector("#ob-back")?.addEventListener("click", () => {
    if (state.step > 0) { state.step--; renderBody(state, root); narrateStep(state); }
  });
  root.querySelector("#ob-next")?.addEventListener("click", async () => {
    await saveStep(state, root);
    if (state.step >= STEP_TITLES.length - 1) {
      localStorage.setItem(SEEN_KEY, state.buildId);
      localStorage.removeItem("valet_force_onboarding"); // clear the replay/test flag
      root.remove();
      return;
    }
    state.step++;
    renderBody(state, root);
    narrateStep(state);
  });

  // Speak the welcome line on the first user gesture (which also unlocks the
  // AudioContext), so Vee greets the user instead of the wizard opening silent.
  const greet = () => {
    root.removeEventListener("pointerdown", greet);
    root.removeEventListener("keydown", greet);
    if (state.step === 0) narrateStep(state);
  };
  root.addEventListener("pointerdown", greet);
  root.addEventListener("keydown", greet);

  wireStep(state, root);
}

/** Show the wizard on the first open of each new build. No-op once finished.
 *  Returns true if the wizard was shown — the caller skips the Settings
 *  setup-mode auto-open in that case, so the two first-run flows don't stack. */
export async function maybeShowOnboarding(
  speak: Narrate = () => {},
  startDemo: StartDemo = () => {},
): Promise<boolean> {
  // Dev/test override: force the wizard regardless of the seen-flag or an already
  // entitled license. Set localStorage "valet_force_onboarding" = "1", or load
  // with ?onboard=1 — lets us replay onboarding without wiping the .env license.
  const forced =
    localStorage.getItem("valet_force_onboarding") === "1" ||
    new URLSearchParams(location.search).get("onboard") === "1";

  const cfg = await getJSON<{ build_id?: string; voice?: string }>("/api/config");
  const buildId = cfg?.build_id || "dev";
  if (!forced && localStorage.getItem(SEEN_KEY) === buildId) return false; // already onboarded this build
  // If the license is already ENTITLED, the user has been through setup before
  // (e.g. they quit mid-onboarding to grant a permission, then relaunched) — don't
  // make them redo it. Mark this build seen and stay out of the way; everything is
  // editable in Settings.
  //
  // We gate on entitlement, NOT mere key presence: a stale, canceled, or wrong
  // key is present but not entitled, and in that case we WANT onboarding to run
  // so the user has a path to enter a working key — otherwise the assistant just
  // dead-ends on the licence gate with no way back into setup.
  const status = await getJSON<{ env_keys_set?: { license_entitled?: boolean } }>(
    "/api/settings/status",
  );
  if (!forced && status?.env_keys_set?.license_entitled) {
    localStorage.setItem(SEEN_KEY, buildId);
    return false;
  }
  const perms = await loadPerms();
  const state: State = {
    step: 0,
    buildId,
    perms,
    voice: cfg?.voice === "female" ? "female" : "male",
    speak,
    demo: startDemo,
  };
  const root = document.createElement("div");
  root.id = "valet-onboarding";
  document.body.appendChild(root);
  render(state, root);
  return true;
}
