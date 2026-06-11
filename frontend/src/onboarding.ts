/**
 * First-run permissions onboarding (Phase J).
 *
 * Shown once (tracked in localStorage). Reads /api/permissions/status and walks
 * the user through the macOS permissions VALET needs, with a deep-link that
 * opens the right System Settings pane and a Re-check button. Non-blocking, so
 * the user can continue even if something is still ungranted.
 */

import "./onboarding.css";

const SEEN_KEY = "valet_onboarded_v1";

interface Permission {
  granted: boolean | null;
  label: string;
  why: string;
  note?: string;
  required_v1?: boolean;
}
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

async function fetchStatus(): Promise<PermStatus | null> {
  try {
    const res = await fetch("/api/permissions/status");
    return (await res.json()) as PermStatus;
  } catch {
    return null;
  }
}

function rowHTML(key: string, p: Permission): string {
  const s = pill(p);
  const canOpen = p.granted === false || key === "automation" || key === "microphone";
  return `
    <div class="ob-row" data-key="${key}">
      <div class="ob-row-main">
        <div class="ob-row-label">${p.label}</div>
        <div class="ob-row-why">${p.why}${p.note ? ` <span class="ob-note">${p.note}</span>` : ""}</div>
      </div>
      <div class="ob-row-side">
        <span class="ob-pill ${s.cls}">${s.text}</span>
        ${canOpen ? `<button class="ob-open" data-target="${TARGET_FOR(key)}">Open Settings</button>` : ""}
      </div>
    </div>`;
}

function render(root: HTMLElement, status: PermStatus): void {
  const rows = ["microphone", "full_disk_access", "automation", "accessibility"]
    .filter((k) => status[k])
    .map((k) => rowHTML(k, status[k]))
    .join("");
  root.innerHTML = `
    <div class="ob-backdrop"></div>
    <div class="ob-card" role="dialog" aria-label="Welcome to VALET">
      <div class="ob-brand">VALET</div>
      <h2 class="ob-title">A few permissions, please.</h2>
      <p class="ob-sub">Vee acts across your Mac. Grant what you're comfortable with. You can change these anytime in System Settings.</p>
      <div class="ob-rows">${rows}</div>
      <div class="ob-actions">
        <button class="ob-btn ghost" id="ob-recheck">Re-check</button>
        <button class="ob-btn primary" id="ob-continue">Get started</button>
      </div>
    </div>`;

  root.querySelectorAll<HTMLButtonElement>(".ob-open").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      await fetch("/api/permissions/open", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: btn.dataset.target }),
      }).catch(() => {});
      setTimeout(() => (btn.disabled = false), 1200);
    });
  });

  root.querySelector("#ob-recheck")?.addEventListener("click", async () => {
    const fresh = await fetchStatus();
    if (fresh) render(root, fresh);
  });

  root.querySelector("#ob-continue")?.addEventListener("click", () => {
    localStorage.setItem(SEEN_KEY, "1");
    root.remove();
  });
}

/** Show the onboarding once, after the backend is reachable. No-op if seen. */
export async function maybeShowOnboarding(): Promise<void> {
  if (localStorage.getItem(SEEN_KEY)) return;
  const status = await fetchStatus();
  if (!status) return; // backend not up yet; try again next launch
  const root = document.createElement("div");
  root.id = "valet-onboarding";
  document.body.appendChild(root);
  render(root, status);
}
