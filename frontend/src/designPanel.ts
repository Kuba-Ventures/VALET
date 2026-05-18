/**
 * JARVIS Design Panel — design-partner conversation surface.
 *
 * Backend sends events as { type: "design_event", event: {...} } over WebSocket;
 * main.ts routes them here via `handleEvent()`. Mirrors processPanel.ts in
 * shape (factory, pointer-capture drag, localStorage position) — distinct
 * visual styling via `--dp-*` tokens.
 */

import "./designPanel.css";

// ---------------------------------------------------------------------------
// Types — mirror design_partner.py emission shape.
// ---------------------------------------------------------------------------

export type DesignEventType =
  | "design.state_changed"
  | "design.topic_set"
  | "design.decision_added"
  | "design.question_asked"
  | "design.assumption_added"
  | "design.context_surfaced"
  | "design.draft_updated";

export type SessionState = "IDLE" | "DESIGNING" | "BUILDING";

export interface DesignEvent {
  type: DesignEventType | string;
  session_id: string;
  title: string;
  detail: string;
  status: "pending" | "active" | "done" | "error";
  timestamp: number;
  payload: Record<string, unknown> & {
    state?: SessionState;
    ready_to_ship?: boolean;
    topic?: string;
    self_mod?: boolean;
    draft_markdown?: string;
    draft?: Record<string, unknown>;
    changed?: string[];
    entry?: { type: string; title: string; detail?: string };
    project_path?: string;
  };
}

export interface DesignPanelHandle {
  handleEvent(event: DesignEvent): void;
  close(): void;
  destroy(): void;
  onShipClick(handler: () => void): void;
  onScrapClick(handler: () => void): void;
}

const POSITION_KEY = "jarvis.designPanel.pos";

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function createDesignPanel(rootId: string = "design-panel-root"): DesignPanelHandle {
  let currentSessionId: string | null = null;
  let currentState: SessionState = "IDLE";
  let shipHandler: (() => void) | null = null;
  let scrapHandler: (() => void) | null = null;
  let draggingFrom: { x: number; y: number; panelLeft: number; panelTop: number } | null = null;

  const root = ensureRootContainer(rootId);
  root.innerHTML = `
    <div class="dp-frame">
      <div class="dp-handle" data-dp-handle>
        <div class="dp-handle-grip"></div>
        <div class="dp-title-wrap">
          <span class="dp-title">Design</span>
          <span class="dp-topic" data-dp-topic></span>
          <span class="dp-state-badge" data-state="DESIGNING" data-dp-badge>Designing</span>
        </div>
        <button class="dp-close" data-dp-close title="Close">×</button>
      </div>
      <div class="dp-timeline" data-dp-timeline></div>
      <div class="dp-draft">
        <div class="dp-draft-header">
          <span>Draft prompt</span>
          <span class="dp-draft-ready" data-dp-ready>Ready to ship</span>
        </div>
        <div class="dp-draft-body" data-dp-draft><em class="empty">(no draft yet)</em></div>
        <div class="dp-actions">
          <button class="dp-btn dp-btn-scrap" data-dp-scrap>Scrap</button>
          <button class="dp-btn dp-btn-ship"  data-dp-ship>Ship to Claude Code</button>
        </div>
      </div>
    </div>
  `;

  const topicEl    = root.querySelector<HTMLElement>("[data-dp-topic]")!;
  const badgeEl    = root.querySelector<HTMLElement>("[data-dp-badge]")!;
  const timelineEl = root.querySelector<HTMLElement>("[data-dp-timeline]")!;
  const draftEl    = root.querySelector<HTMLElement>("[data-dp-draft]")!;
  const readyEl    = root.querySelector<HTMLElement>("[data-dp-ready]")!;
  const shipBtn    = root.querySelector<HTMLButtonElement>("[data-dp-ship]")!;
  const scrapBtn   = root.querySelector<HTMLButtonElement>("[data-dp-scrap]")!;
  const closeBtn   = root.querySelector<HTMLButtonElement>("[data-dp-close]")!;
  const handle     = root.querySelector<HTMLElement>("[data-dp-handle]")!;

  closeBtn.addEventListener("click", () => close());
  shipBtn.addEventListener("click", () => shipHandler && shipHandler());
  scrapBtn.addEventListener("click", () => scrapHandler && scrapHandler());

  // ---- Drag (pointer capture, same pattern as processPanel) -----------
  handle.addEventListener("pointerdown", (e) => {
    if ((e.target as HTMLElement).closest("[data-dp-close]")) return;
    if ((e.target as HTMLElement).closest("[data-dp-ship]")) return;
    if ((e.target as HTMLElement).closest("[data-dp-scrap]")) return;
    const rect = root.getBoundingClientRect();
    draggingFrom = { x: e.clientX, y: e.clientY, panelLeft: rect.left, panelTop: rect.top };
    root.classList.add("dragging");
    handle.setPointerCapture(e.pointerId);
  });
  handle.addEventListener("pointermove", (e) => {
    if (!draggingFrom) return;
    const dx = e.clientX - draggingFrom.x;
    const dy = e.clientY - draggingFrom.y;
    const { width, height } = root.getBoundingClientRect();
    const left = clamp(draggingFrom.panelLeft + dx, 0, window.innerWidth - width);
    const top  = clamp(draggingFrom.panelTop + dy, 0, window.innerHeight - height);
    root.style.left = `${left}px`;
    root.style.top  = `${top}px`;
    root.style.right = "auto";
  });
  const endDrag = (e: PointerEvent) => {
    if (!draggingFrom) return;
    draggingFrom = null;
    root.classList.remove("dragging");
    try { handle.releasePointerCapture(e.pointerId); } catch {}
    const rect = root.getBoundingClientRect();
    try { localStorage.setItem(POSITION_KEY, JSON.stringify({ left: rect.left, top: rect.top })); } catch {}
  };
  handle.addEventListener("pointerup", endDrag);
  handle.addEventListener("pointercancel", endDrag);

  // Restore saved position.
  try {
    const saved = localStorage.getItem(POSITION_KEY);
    if (saved) {
      const { left, top } = JSON.parse(saved);
      if (typeof left === "number" && typeof top === "number") {
        root.style.left = `${left}px`;
        root.style.top = `${top}px`;
        root.style.right = "auto";
      }
    }
  } catch {}

  function show() {
    root.classList.add("visible");
  }
  function close() {
    root.classList.remove("visible");
  }

  function setState(state: SessionState, ready: boolean) {
    currentState = state;
    const label = state === "DESIGNING" ? "Designing" : state === "BUILDING" ? "Building" : "Idle";
    badgeEl.textContent = label;
    badgeEl.setAttribute("data-state", state);
    if (ready && state === "DESIGNING") {
      readyEl.classList.add("visible");
      shipBtn.classList.add("primed");
    } else {
      readyEl.classList.remove("visible");
      shipBtn.classList.remove("primed");
    }
  }

  function setTopic(topic: string) {
    topicEl.textContent = topic;
    topicEl.title = topic;
  }

  function appendEntry(kind: string, title: string, detail: string) {
    const div = document.createElement("div");
    div.className = `dp-entry dp-entry-${kind}`;
    div.innerHTML = `
      <div class="dp-entry-icon"></div>
      <div class="dp-entry-body">
        <div class="dp-entry-title"></div>
        ${detail ? `<div class="dp-entry-detail"></div>` : ""}
      </div>
    `;
    div.querySelector(".dp-entry-title")!.textContent = title || "";
    const detEl = div.querySelector(".dp-entry-detail");
    if (detEl) detEl.textContent = detail || "";
    timelineEl.appendChild(div);
    timelineEl.scrollTop = timelineEl.scrollHeight;
  }

  function setDraft(markdown: string) {
    draftEl.innerHTML = markdown ? renderMarkdown(markdown) : `<em class="empty">(no draft yet)</em>`;
    draftEl.classList.remove("flash");
    void draftEl.offsetWidth; // restart animation
    draftEl.classList.add("flash");
  }

  function clearTimelineAndDraft() {
    timelineEl.innerHTML = "";
    draftEl.innerHTML = `<em class="empty">(no draft yet)</em>`;
    readyEl.classList.remove("visible");
    shipBtn.classList.remove("primed");
  }

  function handleEvent(event: DesignEvent) {
    // New session — reset UI to fresh state
    if (event.session_id && event.session_id !== currentSessionId) {
      currentSessionId = event.session_id;
      clearTimelineAndDraft();
    }
    show();

    const t = event.type;
    if (t === "design.state_changed") {
      const state = (event.payload.state as SessionState) || "IDLE";
      const ready = !!event.payload.ready_to_ship;
      setState(state, ready);
      if (event.payload.topic) setTopic(event.payload.topic);
      if (typeof event.payload.draft_markdown === "string") {
        setDraft(event.payload.draft_markdown);
      }
      // On IDLE transition, clear visuals after a short beat
      if (state === "IDLE") {
        currentSessionId = null;
        window.setTimeout(() => { if (currentState === "IDLE") close(); }, 1500);
      }
      return;
    }
    if (t === "design.topic_set") {
      setTopic(event.title || "untitled");
      return;
    }
    if (t === "design.draft_updated") {
      const md = (event.payload.draft_markdown as string) || "";
      setDraft(md);
      return;
    }
    if (t.startsWith("design.")) {
      // decision_added / question_asked / assumption_added / context_surfaced
      const kind = (event.payload.entry?.type) || extractKind(t);
      appendEntry(kind, event.title, event.detail);
      return;
    }
  }

  return {
    handleEvent,
    close,
    destroy: () => { root.innerHTML = ""; },
    onShipClick: (h) => { shipHandler = h; },
    onScrapClick: (h) => { scrapHandler = h; },
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function ensureRootContainer(id: string): HTMLElement {
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement("div");
    el.id = id;
    document.body.appendChild(el);
  }
  return el;
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function extractKind(eventType: string): string {
  // "design.assumption_added" -> "assumption"
  const m = eventType.match(/^design\.(\w+?)_/);
  return m ? m[1] : "decision";
}

/**
 * Minimal markdown renderer — handles the subset our draft uses:
 * `## heading`, paragraphs, `- list items`, and inline `code`.
 * No raw HTML pass-through; all text is HTML-escaped first.
 */
function renderMarkdown(md: string): string {
  const esc = (s: string) => s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  const lines = md.split("\n");
  const out: string[] = [];
  let inList = false;
  let paragraph: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      out.push(`<p>${inlineFmt(paragraph.join(" "))}</p>`);
      paragraph = [];
    }
  };
  const closeList = () => {
    if (inList) { out.push("</ul>"); inList = false; }
  };

  function inlineFmt(s: string): string {
    return esc(s).replace(/`([^`\n]+)`/g, "<code>$1</code>");
  }

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      flushParagraph();
      closeList();
      continue;
    }
    const h2 = line.match(/^##\s+(.+)$/);
    if (h2) {
      flushParagraph();
      closeList();
      out.push(`<h3>${esc(h2[1])}</h3>`);
      continue;
    }
    const li = line.match(/^-\s+(.+)$/);
    if (li) {
      flushParagraph();
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${inlineFmt(li[1])}</li>`);
      continue;
    }
    // Italic-only short note like "_(empty)_"
    const em = line.match(/^_(.+)_$/);
    if (em) {
      flushParagraph();
      closeList();
      out.push(`<p><em class="empty">${esc(em[1])}</em></p>`);
      continue;
    }
    paragraph.push(line);
  }
  flushParagraph();
  closeList();
  return out.join("\n");
}
