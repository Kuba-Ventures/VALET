/**
 * VALET Process Panel — live activity stream beside the orb.
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
  | "pointer_highlight"
  | "cursor_control"
  | "error"
  // Claude Code structured tool calls (from work_mode stream-json parser)
  | "tool.file_read"
  | "tool.file_write"
  | "tool.file_edit"
  | "tool.bash"
  | "tool.web_search"
  | "tool.web_fetch"
  | "tool.glob"
  | "tool.grep"
  | "tool.task"
  | "tool.thinking"
  | "tool.result"
  // Haiku-middleware structured result cards
  | "result.web"
  | "result.product"
  | "result.location"
  | "result.image"
  | "result.markdown"
  | "result.weather"
  | "result.sports"
  | "result.markets"
  | "result.news"
  // Per-source preview card emitted during research (live), one per
  // successful web_fetch. Distinct from `result.web` which is the
  // model's final reading list summary.
  | "result.research_source"
  // Live counter — updates the panel header chip rather than appending
  // a row. Payload carries {fetched, searched}.
  | "research.progress";

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
  /** Toggle the "· design" indicator next to the panel title. Called from
   *  main.ts when the design partner's state transitions in/out of DESIGNING. */
  setDesignActive(active: boolean): void;
  tryAutoClose(): void;
  /** True while one or more tasks are mid-flight (between task_start and
   *  task_done). main.ts uses this to keep its coarse idle-close watchdog
   *  OFF during active background work, so a quiet gap in a long job (e.g. a
   *  dispatched build) never force-closes the panel — the panel's own
   *  task-driven dismiss owns closure in that case. */
  hasActiveTasks(): boolean;
  /** Toggle the "· dictation" indicator (amber). Distinct from · design
   *  so the user knows their voice is being captured verbatim, not
   *  routed through the design partner. */
  setDictationActive(active: boolean): void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DISMISS_AFTER_DONE_MS = 2000;
const POSITION_KEY = "valet.processPanel.pos";
const CODE_LINES_COLLAPSED = 20;

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

import { createFloatingPanelsLayer, type FloatingPanelsLayer } from "./floatingPanels";

export function createProcessPanel(rootId: string = "process-panel-root"): ProcessPanel {
  // Result cards (product/location/web/image/research_source) render as
  // independent floating panels owned by this layer — not as rows inside
  // the Process Panel. The Process Panel keeps only the live event log
  // (searches, fetches, voice interjection breadcrumb, progress chip).
  const floatingLayer: FloatingPanelsLayer = createFloatingPanelsLayer();

  // Per-task code-block elements so consecutive code_task events for the same
  // task append to a single terminal block rather than spawning new ones.
  const codeBlocks = new Map<string, { container: HTMLElement; lines: HTMLElement; expandBtn: HTMLButtonElement; collapsed: boolean; }>();

  // Active task count — drives auto-dismiss. A task is "open" between its
  // task_start and task_done events.
  let activeTaskCount = 0;
  let dismissTimer: number | undefined;

  // When design mode is active, this panel hides and drops events on the
  // floor — the design panel owns the surface. Toggled via setDesignActive.
  let designSuppressed = false;

  // Pin state — when true, auto-dismiss is suspended. SESSION-ONLY: the panel
  // always loads UNPINNED and the pin is never persisted across reloads.
  //
  // History: an earlier build auto-pinned on the first result.* card and wrote
  // the pinned flag to localStorage. That behavior is gone, but the persisted
  // "1" it left behind survived hard refreshes and silently wedged the panel
  // open forever (every auto-dismiss path early-returns on `pinned`). Pin is a
  // transient "keep this up while I read it" affordance, not a durable
  // preference — so it must not outlive the page. We also proactively clear the
  // stale key on init so any browser still carrying it is healed.
  const PIN_KEY = "valet.processPanel.pinned";
  try { localStorage.removeItem(PIN_KEY); } catch { /* localStorage may be blocked */ }
  let pinned = false;

  // Drag state.
  let draggingFrom: { x: number; y: number; panelLeft: number; panelTop: number } | null = null;

  const root = ensureRootContainer(rootId);
  root.innerHTML = `
    <div class="pp-frame">
      <div class="pp-handle" data-pp-handle>
        <div class="pp-handle-grip"></div>
        <div class="pp-title">Process</div>
        <span class="pp-design-indicator" data-pp-design-indicator hidden>· design</span>
        <span class="pp-dictation-indicator" data-pp-dictation-indicator hidden>· dictation</span>
        <div class="pp-progress" data-pp-progress hidden></div>
        <button class="pp-pin" data-pp-pin title="Pin (disable auto-dismiss)" aria-pressed="${pinned}">${pinned ? "◉" : "◌"}</button>
        <button class="pp-close" data-pp-close title="Close">×</button>
      </div>
      <div class="pp-stream" data-pp-stream></div>
    </div>
  `;

  const handle = root.querySelector<HTMLElement>("[data-pp-handle]")!;
  const stream = root.querySelector<HTMLElement>("[data-pp-stream]")!;
  const closeBtn = root.querySelector<HTMLElement>("[data-pp-close]")!;
  const pinBtn = root.querySelector<HTMLButtonElement>("[data-pp-pin]")!;
  const progressChip = root.querySelector<HTMLElement>("[data-pp-progress]")!;
  const designIndicator = root.querySelector<HTMLElement>("[data-pp-design-indicator]")!;
  const dictationIndicator = root.querySelector<HTMLElement>("[data-pp-dictation-indicator]")!;

  pinBtn.addEventListener("click", (e) => { e.stopPropagation(); togglePin(); });

  function setPinned(v: boolean) {
    // Session-only: deliberately NOT persisted (see PIN_KEY note above).
    pinned = v;
    pinBtn.textContent = v ? "◉" : "◌";
    pinBtn.setAttribute("aria-pressed", String(v));
    pinBtn.title = v ? "Unpin (allow auto-dismiss)" : "Pin (disable auto-dismiss)";
    if (v) cancelDismiss();
  }
  function togglePin() { setPinned(!pinned); }

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
    if (designSuppressed) return;  // design mode owns the surface
    cancelDismiss();
    root.classList.add("visible");
  }

  function scheduleDismiss() {
    cancelDismiss();
    if (pinned) return;  // pin button held: never auto-dismiss
    dismissTimer = window.setTimeout(() => {
      closeAndClear();
    }, DISMISS_AFTER_DONE_MS);
  }

  /** Schedule auto-dismiss once all tracked tasks have finished.
   *
   *  Auto-dismiss is driven purely by task completion — NOT by floating
   *  cards. Result cards (weather, product, location…) are independent,
   *  persistent surfaces with their own close buttons; a card left over
   *  from an earlier turn must never keep this transient task-progress
   *  panel open. (It used to: the panel gated dismiss on cardCount === 0,
   *  so a lingering weather card from a prior query wedged the panel open
   *  after the next task — e.g. opening a project — completed.) */
  function maybeScheduleDismiss() {
    scheduleDismiss();
  }

  // Floating cards no longer hold the panel open. We still nudge a dismiss
  // when the last card is closed while idle, so tidying away a lingering
  // result card also tidies away an otherwise-empty panel — but a card
  // mounting never cancels a task-driven dismiss.
  floatingLayer.onChange((count) => {
    if (count === 0 && activeTaskCount === 0) {
      scheduleDismiss();
    }
  });

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
    // While design mode is active the design panel is the only surface the
    // user wants to see; drop process events on the floor rather than
    // rendering behind/beside it.
    if (designSuppressed) return;

    // First event of any kind shows the panel.
    show();

    // Cards no longer live inside this panel — they spawn as independent
    // floating panels (see floatingPanels.ts). The old auto-pin-on-result.*
    // behavior was for the in-panel rendering path and is no longer
    // relevant; auto-dismiss now defers on its own while floating cards
    // are still up (gated below in the task_done branch).

    if (event.type === "task_start") {
      // Increment BEFORE dismissing prior cards. The dismiss can drop the
      // floating card count to 0, which triggers the onChange dismiss
      // scheduler; keeping activeTaskCount > 0 prevents the panel from
      // auto-dismissing during the same tick.
      activeTaskCount++;

      // Rule 2 (chunk 18): on a fresh research task_start, instantly clear
      // every floating panel from any previous research task. All floating
      // panels are research-originated by construction, so a blanket
      // "not this task" predicate is correct. Non-research task_starts
      // (browse, build, project_lookup, …) leave existing cards alone.
      const isResearchTask = (event.title || "").startsWith("Researching:");
      if (isResearchTask) {
        floatingLayer.dismissPriorResearchCards(event.task_id);
      }

      floatingLayer.resetLayout();
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

      // Rule 1 (chunk 18): on every task_done, dismiss source-preview
      // cards belonging to the completing task. Source cards are only
      // emitted by research, so on a non-research task_done this is a
      // safe no-op. Task-ID scoped so a concurrent research task's
      // sources survive.
      floatingLayer.dismissResearchSources(event.task_id);

      // Clear the progress chip when all tasks finish so the next session
      // doesn't show stale counts.
      if (activeTaskCount === 0) {
        hideProgressChip();
        maybeScheduleDismiss();
      }
      return;
    }

    if (event.type === "research.progress") {
      // Header chip update — no row insertion.
      updateProgressChip(event);
      return;
    }

    if (event.type === "code_task") {
      appendCodeLine(event);
      return;
    }

    renderEventRow(event);
  }

  function updateProgressChip(event: ProcessEvent) {
    const p = (event.payload || {}) as Record<string, unknown>;
    const fetched = Number(p.fetched ?? 0);
    const searched = Number(p.searched ?? 0);
    if (fetched === 0 && searched === 0) {
      hideProgressChip();
      return;
    }
    const parts: string[] = [];
    if (fetched > 0) parts.push(`${fetched} source${fetched === 1 ? "" : "s"}`);
    if (searched > 0) parts.push(`${searched} search${searched === 1 ? "" : "es"}`);
    progressChip.textContent = parts.join(" · ");
    progressChip.hidden = false;
  }

  function hideProgressChip() {
    progressChip.hidden = true;
    progressChip.textContent = "";
  }

  function renderEventRow(event: ProcessEvent) {
    // Haiku-middleware structured cards render via a dedicated path with
    // richer layout (image, price, address, links) than the standard row.
    if (String(event.type).startsWith("result.")) {
      renderResultCard(event);
      return;
    }

    const row = document.createElement("div");
    // Convert "tool.file_read" → "tool_file_read" so the dot doesn't split
    // into two CSS class names.
    const typeClass = String(event.type).replace(/\./g, "_");
    row.className = `pp-event pp-event-${typeClass} pp-status-${event.status}`;
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

  /** Build a card's content DOM (without outer chrome). Used by the
   *  floating-panels layer to mount each card as an independent panel.
   *  result.markdown is special-cased — it stays in the Process Panel
   *  stream as a collapsible details block, not a floating card. */
  function buildCardContent(event: ProcessEvent): HTMLElement {
    const kind = String(event.type).replace(/^result\./, "");
    const card = document.createElement("div");
    card.className = `pp-card pp-card-${kind}`;
    card.dataset.taskId = event.task_id;
    card.dataset.eventId = event.id;

    populateCardContent(card, event, kind);
    return card;
  }

  function renderResultCard(event: ProcessEvent) {
    const kind = String(event.type).replace(/^result\./, "");

    // Non-markdown cards become independent floating panels — they're
    // owned by the floating layer, not the Process Panel's stream.
    if (kind !== "markdown") {
      floatingLayer.mountCard(event, () => buildCardContent(event));
      return;
    }

    // Markdown card: collapsible "Full response" block, stays in the
    // Process Panel as the live log's tail summary. Not a floating panel.
    const card = document.createElement("div");
    card.className = `pp-card pp-card-${kind}`;
    card.dataset.taskId = event.task_id;
    card.dataset.eventId = event.id;
    const md = (event.payload?.markdown as string) || event.detail || "";
    const det = document.createElement("details");
    det.className = "pp-card-md-details";
    const sum = document.createElement("summary");
    sum.textContent = "Full response";
    det.appendChild(sum);
    const pre = document.createElement("pre");
    pre.className = "pp-card-md-pre";
    pre.textContent = md;
    det.appendChild(pre);
    card.appendChild(det);
    stream.appendChild(card);  // markdown appends (tail), not prepend
  }

  function populateCardContent(card: HTMLElement, event: ProcessEvent, kind: string) {
    // Live per-source preview card emitted during research — compact
    // horizontal layout: thumbnail left, title + snippet right, hostname
    // chip on top. Renders separately from the final result.* card pipeline
    // so it can use a different visual idiom.
    if (kind === "research_source") {
      const p = (event.payload || {}) as Record<string, unknown>;
      const url = (p.url as string) || "";
      const title = (p.title as string) || event.title || (p.hostname as string) || url;
      const hostname = (p.hostname as string) || "";
      const ogImage = (p.og_image_url as string) || "";
      const snippet = (p.snippet as string) || event.detail || "";

      const thumb = document.createElement("div");
      thumb.className = "pp-source-thumb";
      if (ogImage) {
        const img = document.createElement("img");
        img.src = ogImage;
        img.alt = title;
        img.referrerPolicy = "no-referrer";
        img.loading = "lazy";
        img.addEventListener("error", () => {
          // Fall back to a hostname-initial monogram if the image 404s.
          img.remove();
          thumb.classList.add("pp-source-thumb-mono");
          thumb.textContent = (hostname || "?").slice(0, 1).toUpperCase();
        });
        thumb.appendChild(img);
      } else {
        thumb.classList.add("pp-source-thumb-mono");
        thumb.textContent = (hostname || "?").slice(0, 1).toUpperCase();
      }
      card.appendChild(thumb);

      const body = document.createElement("div");
      body.className = "pp-source-body";

      if (hostname) {
        const host = document.createElement("div");
        host.className = "pp-source-host";
        host.textContent = hostname;
        body.appendChild(host);
      }

      const titleEl = document.createElement("div");
      titleEl.className = "pp-source-title";
      if (url) {
        const a = document.createElement("a");
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = title;
        titleEl.appendChild(a);
      } else {
        titleEl.textContent = title;
      }
      body.appendChild(titleEl);

      if (snippet) {
        const snip = document.createElement("div");
        snip.className = "pp-source-snippet";
        snip.textContent = snippet;
        body.appendChild(snip);
      }

      card.appendChild(body);
      return;
    }

    // Weather card — dedicated 4-section layout (header, now, alert,
    // 7-day strip). Bypasses the generic chrome since none of the standard
    // fields (title/summary/imageUrl) fit a weather widget.
    if (kind === "weather") {
      populateWeatherCard(card, event);
      return;
    }

    // Sports scores card — dedicated layout (league header → live / upcoming /
    // recent game rows). Payload built by sports.build_card_payload.
    if (kind === "sports") {
      populateSportsCard(card, event);
      return;
    }

    // Markets quote card — name, big price, colored daily change.
    if (kind === "markets") {
      populateMarketsCard(card, event);
      return;
    }

    // News card — topic header → headline rows (title + source, linked).
    if (kind === "news") {
      populateNewsCard(card, event);
      return;
    }

    // (markdown branch handled in renderResultCard before reaching here.)

    const p = (event.payload || {}) as Record<string, unknown>;
    const title = (p.title as string) || event.title;
    const summary = (p.summary as string) || event.detail || "";
    const sourceUrl = (p.source_url as string) || "";
    const imageUrl = (p.image_url as string) || "";
    const price = (p.price as string) || "";
    const address = (p.address as string) || "";

    // Header: title (+ price chip for product)
    const head = document.createElement("div");
    head.className = "pp-card-head";
    const titleEl = document.createElement("div");
    titleEl.className = "pp-card-title";
    titleEl.textContent = title;
    head.appendChild(titleEl);
    if (price && kind === "product") {
      const priceEl = document.createElement("div");
      priceEl.className = "pp-card-price";
      priceEl.textContent = price;
      head.appendChild(priceEl);
    }
    card.appendChild(head);

    // Image (product / image card)
    if ((kind === "product" || kind === "image") && imageUrl) {
      const img = document.createElement("img");
      img.className = "pp-card-image";
      img.src = imageUrl;
      img.alt = title;
      img.referrerPolicy = "no-referrer";
      img.loading = "lazy";
      img.addEventListener("click", () => openLightbox(imageUrl));
      img.addEventListener("error", () => { img.style.display = "none"; });
      card.appendChild(img);
    }

    // Summary
    if (summary) {
      const sum = document.createElement("div");
      sum.className = "pp-card-summary";
      sum.textContent = summary;
      card.appendChild(sum);
    }

    // Address (location card) — text + Maps link (no static-map image:
    // would need a map provider API key, not configured tonight).
    if (kind === "location" && address) {
      const addr = document.createElement("div");
      addr.className = "pp-card-address";
      addr.textContent = address;
      card.appendChild(addr);
      const mapsLink = document.createElement("a");
      mapsLink.className = "pp-card-link";
      mapsLink.href = `https://maps.apple.com/?q=${encodeURIComponent(address)}`;
      mapsLink.target = "_blank";
      mapsLink.rel = "noopener noreferrer";
      mapsLink.textContent = "Open in Maps ↗";
      card.appendChild(mapsLink);
    }

    // Source link (everyone except location)
    if (sourceUrl && kind !== "location") {
      const link = document.createElement("a");
      link.className = "pp-card-link";
      link.href = sourceUrl;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      const display = sourceUrl.replace(/^https?:\/\//, "").replace(/\/$/, "");
      link.textContent = (display.length > 48 ? display.slice(0, 45) + "…" : display) + " ↗";
      card.appendChild(link);
    }
  }

  /** Native news card. Payload built by news.build_card_payload:
   * { topic, items: [{title, source, link}] }. Topic header → headline rows. */
  function populateNewsCard(card: HTMLElement, event: ProcessEvent) {
    interface NewsItem { title: string; source: string; link: string; }
    interface NewsPayload { topic: string; items: NewsItem[]; }
    const p = (event.payload || {}) as unknown as NewsPayload;

    const head = document.createElement("div");
    head.className = "pp-news-topic";
    head.textContent = p.topic || "News";
    card.appendChild(head);

    for (const it of (p.items || []).slice(0, 6)) {
      const row = it.link ? document.createElement("a") : document.createElement("div");
      row.className = "pp-news-row";
      if (it.link) {
        (row as HTMLAnchorElement).href = it.link;
        (row as HTMLAnchorElement).target = "_blank";
        (row as HTMLAnchorElement).rel = "noopener noreferrer";
      }
      const title = document.createElement("div");
      title.className = "pp-news-title";
      title.textContent = it.title;
      row.appendChild(title);
      if (it.source) {
        const src = document.createElement("div");
        src.className = "pp-news-source";
        src.textContent = it.source;
        row.appendChild(src);
      }
      card.appendChild(row);
    }
  }

  /** Native markets quote card. Payload built by markets.build_card_payload:
   * { symbol, name, price (formatted string), change_pct, change, currency,
   * is_index }. Layout: name + ticker → big price → colored daily change. */
  function populateMarketsCard(card: HTMLElement, event: ProcessEvent) {
    interface MarketsPayload {
      symbol: string; name: string; price: string;
      change_pct: number | null; change: number | null;
      currency: string; is_index: boolean;
    }
    const p = (event.payload || {}) as unknown as MarketsPayload;

    const head = document.createElement("div");
    head.className = "pp-mkt-head";
    const nm = document.createElement("div");
    nm.className = "pp-mkt-name";
    nm.textContent = p.name || p.symbol || "";
    head.appendChild(nm);
    if (p.symbol) {
      const tk = document.createElement("div");
      tk.className = "pp-mkt-ticker";
      tk.textContent = p.symbol;
      head.appendChild(tk);
    }
    card.appendChild(head);

    const price = document.createElement("div");
    price.className = "pp-mkt-price";
    price.textContent = p.price || "—";
    card.appendChild(price);

    if (p.change_pct !== null && p.change_pct !== undefined) {
      const up = p.change_pct > 0.05;
      const down = p.change_pct < -0.05;
      const chg = document.createElement("div");
      chg.className = "pp-mkt-change " + (up ? "pp-mkt-up" : down ? "pp-mkt-down" : "pp-mkt-flat");
      const arrow = up ? "▲" : down ? "▼" : "→";
      const chgAbs = p.change !== null && p.change !== undefined ? Math.abs(p.change) : null;
      const chgStr = chgAbs !== null ? `${chgAbs.toLocaleString(undefined, { maximumFractionDigits: 2 })} ` : "";
      chg.textContent = `${arrow} ${chgStr}(${Math.abs(p.change_pct).toFixed(2)}%) today`;
      card.appendChild(chg);
    }
  }

  /** Native sports scores card. Payload built by sports.build_card_payload
   * (see sports.py). Layout: league header (+ updated stamp) → up to three
   * sections — Live, Upcoming, Recent — each a list of game rows (matchup +
   * status/kickoff). Live rows get an accent dot. */
  function populateSportsCard(card: HTMLElement, event: ProcessEvent) {
    interface Side { name?: string; abbrev?: string; score?: string | null; }
    interface Game {
      matchup: string;
      state?: "pre" | "in" | "post" | string;
      detail?: string;
      venue?: string;
      home?: Side; away?: Side;
    }
    interface SportsPayload {
      league: string;
      live: Game[]; recent: Game[]; upcoming: Game[];
      updated_at?: string;
    }
    const p = (event.payload || {}) as unknown as SportsPayload;

    // Header
    const head = document.createElement("div");
    head.className = "pp-sports-head";
    const league = document.createElement("div");
    league.className = "pp-sports-league";
    league.textContent = p.league || "Scores";
    head.appendChild(league);
    if (p.updated_at) {
      const upd = document.createElement("div");
      upd.className = "pp-sports-updated";
      upd.textContent = p.updated_at;
      head.appendChild(upd);
    }
    card.appendChild(head);

    const section = (label: string, games: Game[]) => {
      if (!games || games.length === 0) return;
      const sec = document.createElement("div");
      sec.className = "pp-sports-section";
      const lbl = document.createElement("div");
      lbl.className = "pp-sports-section-label";
      lbl.textContent = label;
      sec.appendChild(lbl);
      for (const g of games) {
        const row = document.createElement("div");
        row.className = "pp-sports-row" + (g.state === "in" ? " pp-sports-live" : "");
        const matchup = document.createElement("div");
        matchup.className = "pp-sports-matchup";
        matchup.textContent = g.matchup || "";
        row.appendChild(matchup);
        const detail = document.createElement("div");
        detail.className = "pp-sports-detail";
        detail.textContent = g.detail || "";
        row.appendChild(detail);
        sec.appendChild(row);
      }
      card.appendChild(sec);
    };

    section("Live", p.live);
    section("Upcoming", p.upcoming);
    section("Final", p.recent);

    if ((!p.live || !p.live.length) && (!p.upcoming || !p.upcoming.length) && (!p.recent || !p.recent.length)) {
      const empty = document.createElement("div");
      empty.className = "pp-sports-detail";
      empty.textContent = "No games found.";
      card.appendChild(empty);
    }
  }

  /** Native weather card. Payload is built by weather.build_card_payload on
   * the backend; see weather.py. Layout: header (location + updated stamp)
   * → "now" block (big temp + condition + feels-like/wind/humidity) → optional
   * alert banner → 7-day horizontal strip. Compact phone-widget aesthetic. */
  function populateWeatherCard(card: HTMLElement, event: ProcessEvent) {
    interface DayCell {
      date: string; day_name: string;
      high: number | null; low: number | null;
      code_label: string; code_emoji: string;
      uv_max: number | null; precip_pct: number | null;
    }
    interface AlertBlock { level: "severe" | "uv" | "rain"; text: string; }
    interface WeatherPayload {
      location: string;
      current: {
        temp_f: number | null; feels_like_f: number | null;
        code_label: string; code_emoji: string;
        humidity: number | null; wind_mph: number | null;
      };
      alert: AlertBlock | null;
      daily: DayCell[];
      sunrise?: string; sunset?: string;
      updated_at?: string;
    }
    const p = (event.payload || {}) as unknown as WeatherPayload;
    const fmt = (n: number | null | undefined, suffix = "°") =>
      n === null || n === undefined || Number.isNaN(n) ? "—" : `${Math.round(n)}${suffix}`;

    // Sunrise/sunset arrive as Open-Meteo "naive local" ISO strings
    // (e.g. "2026-06-03T05:34") with no offset. Pull HH:MM straight from the
    // string rather than via Date(), which would reinterpret it in the
    // browser's timezone and skew the displayed time.
    const fmtTime = (iso: string | null | undefined): string => {
      if (!iso) return "—";
      const m = /T(\d{2}):(\d{2})/.exec(iso);
      if (!m) return "—";
      let h = parseInt(m[1], 10);
      const ampm = h >= 12 ? "PM" : "AM";
      h = h % 12 || 12;
      return `${h}:${m[2]} ${ampm}`;
    };

    // WHO/EPA UV bands → label + severity class (drives the value's accent).
    const uvInfo = (uv: number | null | undefined): { label: string; cls: string } | null => {
      if (uv === null || uv === undefined || Number.isNaN(uv)) return null;
      const v = Math.round(uv);
      if (v <= 2) return { label: "Low", cls: "uv-low" };
      if (v <= 5) return { label: "Moderate", cls: "uv-mod" };
      if (v <= 7) return { label: "High", cls: "uv-high" };
      if (v <= 10) return { label: "Very High", cls: "uv-vhigh" };
      return { label: "Extreme", cls: "uv-extreme" };
    };

    // --- Header
    const head = document.createElement("div");
    head.className = "weather-header";
    const loc = document.createElement("div");
    loc.className = "weather-location";
    loc.textContent = p.location || "Unknown";
    head.appendChild(loc);
    if (p.updated_at) {
      const stamp = document.createElement("div");
      stamp.className = "weather-updated";
      const diff = Math.max(0, (Date.now() - Date.parse(p.updated_at)) / 1000);
      stamp.textContent = diff < 60 ? "just now" : `${Math.floor(diff / 60)}m ago`;
      head.appendChild(stamp);
    }
    card.appendChild(head);

    // --- Now block
    const now = document.createElement("div");
    now.className = "weather-now";
    const temp = document.createElement("div");
    temp.className = "weather-temp";
    temp.textContent = fmt(p.current?.temp_f, "°");
    now.appendChild(temp);
    const cond = document.createElement("div");
    cond.className = "weather-cond";
    cond.innerHTML = `<span class="weather-emoji">${p.current?.code_emoji ?? ""}</span><span>${p.current?.code_label ?? ""}</span>`;
    now.appendChild(cond);
    const meta = document.createElement("div");
    meta.className = "weather-meta";
    const metaParts: string[] = [];
    if (p.current?.feels_like_f !== null && p.current?.feels_like_f !== undefined)
      metaParts.push(`feels ${fmt(p.current.feels_like_f, "°")}`);
    if (p.current?.wind_mph !== null && p.current?.wind_mph !== undefined)
      metaParts.push(`${Math.round(p.current.wind_mph)} mph wind`);
    if (p.current?.humidity !== null && p.current?.humidity !== undefined)
      metaParts.push(`${Math.round(p.current.humidity)}% humidity`);
    meta.textContent = metaParts.join(" · ");
    now.appendChild(meta);
    card.appendChild(now);

    // --- Today block — high/low, prominent UV index, sunrise/sunset. The
    // "now" block above is live conditions; this is the day's outlook, the
    // detail the spoken summary used to carry before weather went render-only.
    {
      const today = Array.isArray(p.daily) ? p.daily[0] : undefined;
      const stats = document.createElement("div");
      stats.className = "weather-today-stats";

      const statTile = (label: string, value: string, valueCls = "") => {
        const tile = document.createElement("div");
        tile.className = "weather-stat";
        const l = document.createElement("div");
        l.className = "weather-stat-label";
        l.textContent = label;
        const v = document.createElement("div");
        v.className = "weather-stat-value" + (valueCls ? ` ${valueCls}` : "");
        v.textContent = value;
        tile.appendChild(l);
        tile.appendChild(v);
        stats.appendChild(tile);
      };

      if (today) {
        statTile("High", fmt(today.high));
        statTile("Low", fmt(today.low));
        const uv = uvInfo(today.uv_max);
        if (uv) statTile("UV Index", `${Math.round(today.uv_max as number)} · ${uv.label}`, uv.cls);
      }
      if (p.sunrise) statTile("Sunrise", fmtTime(p.sunrise));
      if (p.sunset) statTile("Sunset", fmtTime(p.sunset));

      if (stats.children.length) {
        const todayBlock = document.createElement("div");
        todayBlock.className = "weather-today";
        const heading = document.createElement("div");
        heading.className = "weather-today-head";
        heading.textContent = "Today";
        todayBlock.appendChild(heading);
        todayBlock.appendChild(stats);
        card.appendChild(todayBlock);
      }
    }

    // --- Alert banner (only if present)
    if (p.alert && p.alert.text) {
      const banner = document.createElement("div");
      banner.className = `weather-alert weather-alert-${p.alert.level}`;
      banner.textContent = p.alert.text;
      card.appendChild(banner);
    }

    // --- 7-day strip
    if (Array.isArray(p.daily) && p.daily.length) {
      const strip = document.createElement("div");
      strip.className = "weather-strip";
      for (const day of p.daily) {
        const cell = document.createElement("div");
        cell.className = "weather-day";
        const name = document.createElement("div");
        name.className = "weather-day-name";
        name.textContent = day.day_name;
        const emoji = document.createElement("div");
        emoji.className = "weather-day-emoji";
        emoji.textContent = day.code_emoji;
        const hl = document.createElement("div");
        hl.className = "weather-day-hl";
        hl.textContent = `${fmt(day.high)}/${fmt(day.low)}`;
        cell.appendChild(name);
        cell.appendChild(emoji);
        cell.appendChild(hl);
        if (day.uv_max !== null && day.uv_max !== undefined && day.uv_max >= 7) {
          const uv = document.createElement("div");
          uv.className = "weather-day-badge weather-day-uv";
          uv.textContent = `UV ${Math.round(day.uv_max)}`;
          cell.appendChild(uv);
        }
        if (day.precip_pct !== null && day.precip_pct !== undefined && day.precip_pct >= 40) {
          const r = document.createElement("div");
          r.className = "weather-day-badge weather-day-rain";
          r.textContent = `💧 ${Math.round(day.precip_pct)}%`;
          cell.appendChild(r);
        }
        strip.appendChild(cell);
      }
      card.appendChild(strip);
    }
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

  function setDesignActive(active: boolean) {
    // While design mode is active the design panel is the sole surface — the
    // process panel hides entirely and ignores incoming events until design
    // ends. (Previously this just toggled a "· design" chip on the header.)
    designSuppressed = active;
    designIndicator.hidden = true;  // chip retired — no design surfacing here
    if (active) {
      closeAndClear();
    }
  }

  function setDictationActive(active: boolean) {
    // Mirror of setDesignActive for dictation. Amber chip rather than
    // cyan so the two modes are visually distinguishable at a glance.
    dictationIndicator.hidden = !active;
    if (active) {
      show();
    }
  }

  return {
    handleEvent,
    close: closeAndClear,
    destroy: () => {
      cancelDismiss();
      root.remove();
    },
    setDesignActive,
    setDictationActive,
    /** External-trigger auto-close. Honors the pin (so a user who clicked
     * pin keeps the panel up), and resets activeTaskCount so a stuck
     * task_start (e.g. one whose task_done was dropped on a WS reconnect)
     * can never wedge the panel open forever. Called from main.ts when
     * VALET returns to idle. */
    tryAutoClose: () => {
      if (pinned) return;
      activeTaskCount = 0;
      closeAndClear();
    },
    hasActiveTasks: () => activeTaskCount > 0,
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
