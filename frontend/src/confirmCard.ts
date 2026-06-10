/**
 * Tier 1 confirmation card (Stage D).
 *
 * The backend's safety layer gates risky actions (delete/move/overwrite, drive
 * apps, keystrokes, raw scripts) behind an explicit confirmation. When one is
 * pending it sends `{type:"confirm_request", id, summary, detail, targets,
 * warning}`; this card renders it in the holographic style and replies with
 * `{type:"confirm_response", id, allow}` via the supplied callback.
 *
 * "Remember this choice" is intentionally absent — destructive actions always
 * ask.
 */

import "./confirmCard.css";

export interface ConfirmRequest {
  id: string;
  summary: string;
  detail?: string;
  targets?: string[];
  tier?: number;
  warning?: string | null;
}

export interface ConfirmCard {
  show(req: ConfirmRequest, onResolve: (allow: boolean) => void): void;
  hide(): void;
  isOpen(): boolean;
}

export function createConfirmCard(): ConfirmCard {
  const root = document.createElement("div");
  root.className = "confirm-card";
  root.setAttribute("role", "alertdialog");
  root.setAttribute("aria-modal", "true");
  root.innerHTML = `
    <div class="confirm-backdrop"></div>
    <div class="confirm-box">
      <div class="confirm-head">
        <span class="confirm-badge">CONFIRM</span>
        <span class="confirm-title">Action needs your approval, sir.</span>
      </div>
      <div class="confirm-summary"></div>
      <div class="confirm-warning" hidden></div>
      <ul class="confirm-targets"></ul>
      <div class="confirm-actions">
        <button class="confirm-btn deny" type="button">Deny</button>
        <button class="confirm-btn allow" type="button">Allow</button>
      </div>
    </div>`;
  document.body.appendChild(root);

  const summaryEl = root.querySelector(".confirm-summary") as HTMLElement;
  const warningEl = root.querySelector(".confirm-warning") as HTMLElement;
  const targetsEl = root.querySelector(".confirm-targets") as HTMLUListElement;
  const allowBtn = root.querySelector(".confirm-btn.allow") as HTMLButtonElement;
  const denyBtn = root.querySelector(".confirm-btn.deny") as HTMLButtonElement;
  const backdrop = root.querySelector(".confirm-backdrop") as HTMLElement;

  let resolver: ((allow: boolean) => void) | null = null;

  function resolve(allow: boolean) {
    const r = resolver;
    resolver = null;
    root.classList.remove("open");
    document.removeEventListener("keydown", onKey);
    if (r) r(allow);
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "Escape") resolve(false);
    else if (e.key === "Enter") resolve(true);
  }

  allowBtn.addEventListener("click", () => resolve(true));
  denyBtn.addEventListener("click", () => resolve(false));
  // Clicking the backdrop is a deny (fail-safe), never an allow.
  backdrop.addEventListener("click", () => resolve(false));

  return {
    show(req, onResolve) {
      // A new request supersedes any pending one (deny the old).
      if (resolver) resolve(false);
      resolver = onResolve;
      summaryEl.textContent = req.summary || "Proceed with this action?";
      if (req.warning) {
        warningEl.textContent = req.warning;
        warningEl.hidden = false;
      } else {
        warningEl.hidden = true;
      }
      targetsEl.innerHTML = "";
      for (const t of req.targets || []) {
        const li = document.createElement("li");
        li.textContent = t;
        targetsEl.appendChild(li);
      }
      root.classList.add("open");
      document.addEventListener("keydown", onKey);
      denyBtn.focus();
    },
    hide() {
      resolve(false);
    },
    isOpen() {
      return resolver !== null;
    },
  };
}
