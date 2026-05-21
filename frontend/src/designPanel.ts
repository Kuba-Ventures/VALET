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
    project_name?: string;
    has_target?: boolean;
    git_branch?: string;
  };
}

export interface DesignPanelHandle {
  handleEvent(event: DesignEvent): void;
  close(): void;
  destroy(): void;
  onShipClick(handler: () => void): void;
  onScrapClick(handler: () => void): void;
  onMergeClick(handler: () => void): void;
  onTargetSelect(handler: (path: string) => void): void;
  onAgentSelect(handler: (agent: string) => void): void;
  tryAutoCloseIfIdle(): void;
}

interface ProjectEntry {
  name: string;
  path: string;
  source?: string;
}

interface AgentEntry {
  name: string;
  description: string;
  source?: string;
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
  let mergeHandler: (() => void) | null = null;
  let targetSelectHandler: ((path: string) => void) | null = null;
  let agentSelectHandler: ((agent: string) => void) | null = null;
  let projectsLoaded = false;
  let agentsLoaded = false;
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
      <div class="dp-build-view" data-dp-build-view>
        <div class="dp-build-status">
          <div class="dp-build-spinner"></div>
          <div class="dp-build-text">
            <div class="dp-build-label">Building with Claude Code</div>
            <div class="dp-build-topic" data-dp-build-topic></div>
            <div class="dp-build-branch" data-dp-build-branch></div>
          </div>
        </div>
        <div class="dp-build-hint">Watch the Claude pane in Cursor — JARVIS will wait here.</div>
        <div class="dp-actions">
          <button class="dp-btn dp-btn-scrap"  data-dp-build-scrap>Scrap branch</button>
          <button class="dp-btn dp-btn-ship"   data-dp-build-merge>Merge to main</button>
        </div>
      </div>
      <div class="dp-draft">
        <div class="dp-draft-header">
          <span>Draft prompt</span>
          <span class="dp-draft-ready" data-dp-ready>Ready to ship</span>
        </div>
        <div class="dp-draft-body" data-dp-draft><em class="empty">(no draft yet)</em></div>
        <div class="dp-target" data-dp-target-row>
          <span class="dp-target-label">Target</span>
          <select class="dp-target-select" data-dp-target-select>
            <option value="" data-default>(no project: will paste into Cursor)</option>
          </select>
          <span class="dp-target-branch" data-dp-target-branch></span>
          <button class="dp-target-refresh" data-dp-target-refresh title="Re-scan projects">↻</button>
        </div>
        <div class="dp-target" data-dp-agent-row>
          <span class="dp-target-label">Agent</span>
          <select class="dp-target-select" data-dp-agent-select>
            <option value="" data-default>(auto: pick from draft content)</option>
          </select>
        </div>
        <div class="dp-actions">
          <button class="dp-btn dp-btn-scrap" data-dp-scrap>Scrap</button>
          <button class="dp-btn dp-btn-ship"  data-dp-ship title="Ship the draft to Cursor's claude terminal">Ship to Claude Code</button>
        </div>
      </div>
    </div>
  `;

  const frameEl       = root.querySelector<HTMLElement>(".dp-frame")!;
  const topicEl       = root.querySelector<HTMLElement>("[data-dp-topic]")!;
  const badgeEl       = root.querySelector<HTMLElement>("[data-dp-badge]")!;
  const timelineEl    = root.querySelector<HTMLElement>("[data-dp-timeline]")!;
  const draftEl       = root.querySelector<HTMLElement>("[data-dp-draft]")!;
  const readyEl       = root.querySelector<HTMLElement>("[data-dp-ready]")!;
  const shipBtn       = root.querySelector<HTMLButtonElement>("[data-dp-ship]")!;
  const scrapBtn      = root.querySelector<HTMLButtonElement>("[data-dp-scrap]")!;
  const closeBtn      = root.querySelector<HTMLButtonElement>("[data-dp-close]")!;
  const handle        = root.querySelector<HTMLElement>("[data-dp-handle]")!;
  const targetSelectEl = root.querySelector<HTMLSelectElement>("[data-dp-target-select]")!;
  const targetBranchEl = root.querySelector<HTMLElement>("[data-dp-target-branch]")!;
  const targetRefreshBtn = root.querySelector<HTMLButtonElement>("[data-dp-target-refresh]")!;
  const agentSelectEl  = root.querySelector<HTMLSelectElement>("[data-dp-agent-select]")!;
  const buildTopicEl   = root.querySelector<HTMLElement>("[data-dp-build-topic]")!;
  const buildBranchEl  = root.querySelector<HTMLElement>("[data-dp-build-branch]")!;
  const buildScrapBtn  = root.querySelector<HTMLButtonElement>("[data-dp-build-scrap]")!;
  const buildMergeBtn  = root.querySelector<HTMLButtonElement>("[data-dp-build-merge]")!;

  closeBtn.addEventListener("click", () => close());
  shipBtn.addEventListener("click", () => shipHandler && shipHandler());
  scrapBtn.addEventListener("click", () => scrapHandler && scrapHandler());
  buildScrapBtn.addEventListener("click", () => scrapHandler && scrapHandler());
  buildMergeBtn.addEventListener("click", () => mergeHandler && mergeHandler());
  targetSelectEl.addEventListener("change", () => {
    const path = targetSelectEl.value;
    if (path && targetSelectHandler) targetSelectHandler(path);
  });
  targetRefreshBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    projectsLoaded = false;
    loadProjects();
  });
  agentSelectEl.addEventListener("change", () => {
    if (agentSelectHandler) agentSelectHandler(agentSelectEl.value);
  });

  async function loadAgents() {
    try {
      const res = await fetch("/api/agents");
      const data = await res.json();
      const list: AgentEntry[] = data.agents || [];
      const prev = agentSelectEl.value;
      const defaultOpt = agentSelectEl.querySelector<HTMLOptionElement>("option[data-default]");
      agentSelectEl.innerHTML = "";
      if (defaultOpt) agentSelectEl.appendChild(defaultOpt);
      // Sort: project-local > user > builtin; alphabetical within source.
      const order: Record<string, number> = { project: 0, user: 1, builtin: 2 };
      list.sort((a, b) => {
        const oa = order[a.source ?? "builtin"] ?? 9;
        const ob = order[b.source ?? "builtin"] ?? 9;
        if (oa !== ob) return oa - ob;
        return a.name.localeCompare(b.name);
      });
      for (const a of list) {
        const opt = document.createElement("option");
        opt.value = a.name;
        opt.textContent = a.name;
        opt.title = a.description;
        agentSelectEl.appendChild(opt);
      }
      if (prev) agentSelectEl.value = prev;
      agentsLoaded = true;
    } catch (err) {
      console.warn("[designPanel] /api/agents failed", err);
    }
  }

  async function loadProjects() {
    try {
      const res = await fetch("/api/projects");
      const data = await res.json();
      const projects: ProjectEntry[] = data.projects || [];
      // Preserve the currently-selected value so a manual refresh doesn't
      // reset the user's pick.
      const prev = targetSelectEl.value;
      // Wipe existing options except the default placeholder.
      const defaultOpt = targetSelectEl.querySelector<HTMLOptionElement>("option[data-default]");
      targetSelectEl.innerHTML = "";
      if (defaultOpt) targetSelectEl.appendChild(defaultOpt);
      else {
        const opt = document.createElement("option");
        opt.value = "";
        opt.dataset.default = "";
        opt.textContent = "(no project: will paste into Cursor)";
        targetSelectEl.appendChild(opt);
      }
      // Sort: alias-source first (recent + remembered), then fs-scan.
      projects.sort((a, b) => {
        if (a.source === "alias" && b.source !== "alias") return -1;
        if (b.source === "alias" && a.source !== "alias") return 1;
        return a.name.localeCompare(b.name);
      });
      for (const p of projects) {
        const opt = document.createElement("option");
        opt.value = p.path;
        opt.textContent = p.name;
        opt.title = p.path;
        targetSelectEl.appendChild(opt);
      }
      // Restore prior selection if still present.
      if (prev) targetSelectEl.value = prev;
      projectsLoaded = true;
    } catch (err) {
      console.warn("[designPanel] /api/projects failed", err);
    }
  }

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
    // Lazy-load project + agent lists the first time the panel becomes visible.
    if (!projectsLoaded) loadProjects();
    if (!agentsLoaded) loadAgents();
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
    // Switch the panel between design layout (timeline + draft + target +
    // ship/scrap) and build layout (compact status + merge/scrap). The
    // building class is the single source of truth for which DOM children
    // are visible — see designPanel.css `.dp-frame.building-mode`. Toggled
    // on the inner .dp-frame, NOT on #design-panel-root, so the CSS
    // selector matches.
    frameEl.classList.toggle("building-mode", state === "BUILDING");
  }

  function setTarget(_name: string, branch: string, hasTarget: boolean, path: string = "") {
    // Ship button stays clickable regardless of target — the backend will
    // auto-paste the draft into Cursor's claude terminal whether or not a
    // project is tied to the session. The dropdown reflects the current
    // target (driven by emit_state from the backend).
    if (hasTarget && path) {
      // Try to select the matching <option>. If the project isn't in the
      // list yet (e.g. came in via voice register), add it on the fly.
      const has = Array.from(targetSelectEl.options).some((o) => o.value === path);
      if (!has) {
        const opt = document.createElement("option");
        opt.value = path;
        opt.textContent = path.split("/").filter(Boolean).pop() || path;
        opt.title = path;
        targetSelectEl.appendChild(opt);
      }
      targetSelectEl.value = path;
      targetBranchEl.textContent = branch ? `· ${branch}` : "";
      shipBtn.title = "Ship the draft to Cursor's claude terminal";
    } else {
      targetSelectEl.value = "";
      targetBranchEl.textContent = "";
      shipBtn.title = "Paste the draft into Cursor's claude terminal";
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
      const hasTarget = !!event.payload.has_target;
      const projectName = (event.payload.project_name as string) || "";
      const projectPath = (event.payload.project_path as string) || "";
      const branch = (event.payload.git_branch as string) || "";
      setTarget(projectName, branch, hasTarget, projectPath);
      // Reflect the session's current agent pick in the dropdown so refreshing
      // or switching tabs doesn't desync. Empty string = "(auto)".
      const sessionAgent = (event.payload.agent as string) || "";
      if (agentSelectEl.value !== sessionAgent) {
        // If the agent isn't in the dropdown yet (race with /api/agents),
        // add it on the fly so the value sticks.
        const has = Array.from(agentSelectEl.options).some((o) => o.value === sessionAgent);
        if (!has && sessionAgent) {
          const opt = document.createElement("option");
          opt.value = sessionAgent;
          opt.textContent = sessionAgent;
          agentSelectEl.appendChild(opt);
        }
        agentSelectEl.value = sessionAgent;
      }
      // Populate the build view with the same context the design header
      // shows. After ship-it, the panel flips into building mode and this
      // is the info the user needs (topic + which branch is live).
      if (state === "BUILDING") {
        buildTopicEl.textContent = (event.payload.topic as string) || "";
        buildBranchEl.textContent = branch ? `branch: ${branch}` : (projectName ? `project: ${projectName}` : "no project tied");
      }
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
    onMergeClick: (h) => { mergeHandler = h; },
    onTargetSelect: (h) => { targetSelectHandler = h; },
    onAgentSelect: (h) => { agentSelectHandler = h; },
    /** Called from main.ts when JARVIS returns to idle. Closes the panel
     * ONLY if no design conversation is active — i.e. state is IDLE.
     * BUILDING-mode panels stay visible so the user keeps seeing the
     * spinner + merge/scrap buttons; DESIGNING-mode panels stay because
     * the conversation is still live. */
    tryAutoCloseIfIdle: () => {
      if (currentState === "IDLE") close();
    },
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
