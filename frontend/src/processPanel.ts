/**
 * JARVIS Process Panel — live activity stream beside the orb.
 *
 * The backend `ProcessEventBus` broadcasts events as
 *     { type: "process_event", event: {...} }
 * messages over WebSocket. main.ts routes them here via `handleEvent()`.
 *
 * The panel auto-appears on the first event of a task, auto-dismisses
 * 2s after the last active task finishes, and dismisses immediately on
 * close button click or `close_panel` server message.
 */

import "./processPanel.css";

// ---------------------------------------------------------------------------
// Types — mirror the backend Event dataclass from process_events.py
// ---------------------------------------------------------------------------

export type EventStatus = "pending" | "active" | "done" | "error";

export type EventType =
  | "task_start"
  | "task_done"
  | "step"
  | "screenshot"
  | "browser_action"
  | "app_launch"
  | "text_write"
  | "code_task"
  | "task_queued"
  | "error";

export interface ProcessEvent {
  id: string;
  task_id: string;
  timestamp: number;
  type: EventType;
  status: EventStatus;
  title: string;
  detail: string;
  payload: Record<string, unknown>;
}

export interface ProcessPanel {
  handleEvent(event: ProcessEvent): void;
  close(): void;
  destroy(): void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DISMISS_AFTER_DONE_MS = 2000;
const POSITION_KEY = "jarvis.processPanel.pos";
const CODE_LINES_COLLAPSED = 20;

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function createProcessPanel(rootId: string = "process-panel-root"): ProcessPanel {
  // Per-task code-block elements so consecutive code_task events for the same
  // task append to a single terminal block rather than spawning new ones.
  const codeBlocks = new Map<string, { container: HTMLElement; lines: HTMLElement; expandBtn: HTMLButtonElement; collapsed: boolean; }>();

  // Active task count — drives auto-dismiss. A task is "open" between its
  // task_start and task_done events.
  let activeTaskCount = 0;
  let dismissTimer: number | undefined;

  // Drag state.
  let draggingFrom: { x: number; y: number; panelLeft: number; panelTop: number } | null = null;

  const root = ensureRootContainer(rootId);
  root.innerHTML = `
    <div class="pp-frame">
      <div class="pp-handle" data-pp-handle>
        <div class="pp-handle-grip"></div>
        <div class="pp-title">Process</div>
        <button class="pp-close" data-pp-close title="Close">×</button>
      </div>
      <div class="pp-stream" data-pp-stream></div>
    </div>
  `;

  const handle = root.querySelector<HTMLElement>("[data-pp-handle]")!;
  const stream = root.querySelector<HTMLElement>("[data-pp-stream]")!;
  const closeBtn = root.querySelector<HTMLElement>("[data-pp-close]")!;

  restorePosition();

  closeBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    closeAndClear();
  });

  // Drag — pointer events so it works for mouse and touch alike.
  handle.addEventListener("pointerdown", (e) => {
    // Ignore drag if the user is clicking the close button.
    if ((e.target as HTMLElement).closest("[data-pp-close]")) return;
    draggingFrom = {
      x: e.clientX,
      y: e.clientY,
      panelLeft: root.getBoundingClientRect().left,
      panelTop: root.getBoundingClientRect().top,
    };
    root.classList.add("dragging");
    handle.setPointerCapture(e.pointerId);
  });
  handle.addEventListener("pointermove", (e) => {
    if (!draggingFrom) return;
    const dx = e.clientX - draggingFrom.x;
    const dy = e.clientY - draggingFrom.y;
    const { width, height } = root.getBoundingClientRect();
    // Clamp to window so the panel can never be dragged off-screen.
    const left = clamp(draggingFrom.panelLeft + dx, 0, window.innerWidth - width);
    const top = clamp(draggingFrom.panelTop + dy, 0, window.innerHeight - height);
    root.style.left = `${left}px`;
    root.style.top = `${top}px`;
    root.style.right = "auto"; // override the default anchor
  });
  handle.addEventListener("pointerup", (e) => {
    if (!draggingFrom) return;
    draggingFrom = null;
    root.classList.remove("dragging");
    handle.releasePointerCapture(e.pointerId);
    savePosition();
  });
  handle.addEventListener("pointercancel", () => {
    draggingFrom = null;
    root.classList.remove("dragging");
  });

  // -----------------------------------------------------------------------
  // Position persistence
  // -----------------------------------------------------------------------

  function savePosition() {
    try {
      const r = root.getBoundingClientRect();
      localStorage.setItem(POSITION_KEY, JSON.stringify({ left: r.left, top: r.top }));
    } catch { /* localStorage may be blocked */ }
  }

  function restorePosition() {
    try {
      const raw = localStorage.getItem(POSITION_KEY);
      if (!raw) return;
      const { left, top } = JSON.parse(raw);
      if (typeof left === "number" && typeof top === "number") {
        // Re-clamp in case the window has resized since we saved.
        root.style.left = `${clamp(left, 0, window.innerWidth - 380)}px`;
        root.style.top = `${clamp(top, 0, window.innerHeight - 120)}px`;
        root.style.right = "auto";
      }
    } catch { /* ignore malformed */ }
  }

  // -----------------------------------------------------------------------
  // Visibility / dismiss
  // -----------------------------------------------------------------------

  function show() {
    cancelDismiss();
    root.classList.add("visible");
  }

  function scheduleDismiss() {
    cancelDismiss();
    dismissTimer = window.setTimeout(() => {
      closeAndClear();
    }, DISMISS_AFTER_DONE_MS);
  }

  function cancelDismiss() {
    if (dismissTimer !== undefined) {
      clearTimeout(dismissTimer);
      dismissTimer = undefined;
    }
  }

  function closeAndClear() {
    cancelDismiss();
    root.classList.remove("visible");
    activeTaskCount = 0;
    // Clear contents AFTER the slide-out transition so the user doesn't see
    // it flash empty.
    window.setTimeout(() => {
      stream.innerHTML = "";
      codeBlocks.clear();
    }, 260);
  }

  // -----------------------------------------------------------------------
  // Rendering
  // -----------------------------------------------------------------------

  function handleEvent(event: ProcessEvent) {
    // First event of any kind shows the panel.
    show();

    if (event.type === "task_start") {
      activeTaskCount++;
      renderEventRow(event);
      return;
    }

    if (event.type === "task_done") {
      activeTaskCount = Math.max(0, activeTaskCount - 1);
      // Find the matching task_start row and update its icon to ✓/✗.
      const startRow = stream.querySelector<HTMLElement>(
        `.pp-event-task_start[data-task-id="${cssEscape(event.task_id)}"]`,
      );
      if (startRow) {
        startRow.classList.remove("pp-status-active");
        startRow.classList.add(`pp-status-${event.status}`);
      }
      if (activeTaskCount === 0) scheduleDismiss();
      return;
    }

    if (event.type === "code_task") {
      appendCodeLine(event);
      return;
    }

    renderEventRow(event);
  }

  function renderEventRow(event: ProcessEvent) {
    const row = document.createElement("div");
    row.className = `pp-event pp-event-${event.type} pp-status-${event.status}`;
    row.dataset.taskId = event.task_id;
    row.dataset.eventId = event.id;

    const icon = document.createElement("div");
    icon.className = "pp-event-icon";

    const body = document.createElement("div");
    body.className = "pp-event-body";

    const title = document.createElement("div");
    title.className = "pp-event-title";
    title.textContent = event.title || event.type;
    body.appendChild(title);

    // Type-specific body extras
    if (event.type === "browser_action") {
      const url = (event.payload?.url as string) || "";
      if (url) {
        const urlEl = document.createElement("div");
        urlEl.className = "pp-event-url";
        urlEl.textContent = url;
        body.appendChild(urlEl);
      }
      if (event.detail) {
        const det = document.createElement("div");
        det.className = "pp-event-detail";
        det.textContent = event.detail;
        body.appendChild(det);
      }
    } else if (event.type === "screenshot") {
      const path = (event.payload?.path as string) || "";
      if (path) {
        const img = document.createElement("img");
        img.className = "pp-screenshot-thumb";
        img.src = screenshotURL(path);
        img.alt = event.title;
        img.addEventListener("click", () => openLightbox(img.src));
        body.appendChild(img);
      }
      if (event.detail) {
        const det = document.createElement("div");
        det.className = "pp-event-detail";
        det.textContent = event.detail;
        body.appendChild(det);
      }
    } else if (event.detail) {
      const det = document.createElement("div");
      det.className = "pp-event-detail";
      det.textContent = event.detail;
      body.appendChild(det);
    }

    row.appendChild(icon);
    row.appendChild(body);

    // Newest at top.
    stream.insertBefore(row, stream.firstChild);
  }

  function appendCodeLine(event: ProcessEvent) {
    let block = codeBlocks.get(event.task_id);
    if (!block) {
      block = createCodeBlock(event.task_id);
      codeBlocks.set(event.task_id, block);
      stream.insertBefore(block.container, stream.firstChild);
    }
    const line = document.createElement("span");
    line.className = "pp-code-line";
    line.textContent = event.detail || "";
    block.lines.appendChild(line);

    // Trim collapsed block if it overflows the cap. We keep the full DOM
    // when expanded so the user can scroll history.
    if (block.collapsed) {
      while (block.lines.children.length > CODE_LINES_COLLAPSED) {
        block.lines.removeChild(block.lines.firstChild!);
      }
    }
    block.lines.scrollTop = block.lines.scrollHeight;
  }

  function createCodeBlock(taskId: string) {
    const container = document.createElement("div");
    container.className = "pp-code-block";
    container.dataset.taskId = taskId;

    const header = document.createElement("div");
    header.className = "pp-code-header";
    header.innerHTML = `<span>claude code</span>`;
    const expandBtn = document.createElement("button");
    expandBtn.className = "pp-code-expand";
    expandBtn.textContent = "expand";
    header.appendChild(expandBtn);

    const lines = document.createElement("div");
    lines.className = "pp-code-lines collapsed";

    container.appendChild(header);
    container.appendChild(lines);

    const block = { container, lines, expandBtn, collapsed: true };
    expandBtn.addEventListener("click", () => {
      block.collapsed = !block.collapsed;
      lines.classList.toggle("collapsed", block.collapsed);
      expandBtn.textContent = block.collapsed ? "expand" : "collapse";
    });

    return block;
  }

  // -----------------------------------------------------------------------
  // Lightbox for screenshots
  // -----------------------------------------------------------------------

  function openLightbox(src: string) {
    const box = document.createElement("div");
    box.className = "pp-lightbox";
    box.innerHTML = `<img src="${src}" alt="screenshot" />`;
    box.addEventListener("click", () => box.remove());
    document.body.appendChild(box);
  }

  // -----------------------------------------------------------------------
  // Window resize — re-clamp position so the panel never floats off-screen.
  // -----------------------------------------------------------------------
  window.addEventListener("resize", () => {
    if (root.style.left && root.style.top) {
      const r = root.getBoundingClientRect();
      root.style.left = `${clamp(r.left, 0, window.innerWidth - r.width)}px`;
      root.style.top = `${clamp(r.top, 0, window.innerHeight - r.height)}px`;
    }
  });

  return {
    handleEvent,
    close: closeAndClear,
    destroy: () => {
      cancelDismiss();
      root.remove();
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

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

function cssEscape(s: string): string {
  // Minimal escape for attribute-selector use. Backend task_ids are 8-char
  // hex slices of uuid4 so this only needs to defend against the worst case.
  return s.replace(/["\\]/g, "\\$&");
}

/** Build the URL the frontend uses to fetch a screenshot.
 *  Backend serves data/screenshots/* under /screenshots/*. The event payload
 *  contains the path relative to data/screenshots/, e.g. "<task_id>/before.png". */
function screenshotURL(path: string): string {
  const trimmed = path.replace(/^\/+/, "");
  return `/screenshots/${trimmed}`;
}
