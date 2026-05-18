/**
 * Floating result-card panels.
 *
 * Each result.* event (product / location / web / image / research_source)
 * becomes its own draggable, dismissable floating panel — not a row inside
 * the Process Panel. The Process Panel is still the live event log; this
 * layer holds the cards that come *out* of it.
 *
 * Layout: cards auto-tile into a right-side grid. Initial placement uses
 * a monotonic slot counter — slot 0 lands at top-right, subsequent slots
 * fill row-by-row to the left, then wrap to a new row. The grid sits to
 * the right of the orb area; orb width is reserved at the left of the
 * viewport. New cards land in the next free slot — they never reshuffle
 * existing cards. Once the user drags a panel, its slot is released and
 * the next new card lands in that vacated slot.
 *
 * No persistence — positions live in component state for the session and
 * vanish on reload. The user explicitly opted out of localStorage here.
 */

import type { ProcessEvent } from "./processPanel";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface FloatingPanelsLayer {
  /** Mount a new card for `event`. `buildContent` returns the card body
   *  (everything below the floating chrome — typically the pp-card div the
   *  Process Panel already builds today). */
  mountCard(event: ProcessEvent, buildContent: () => HTMLElement): void;

  /** Reset the grid slot counter so a new task starts with slot 0 at the
   *  top-right corner again. Called on each task_start. */
  resetLayout(): void;

  /** Remove every floating panel, e.g. when the panel closeAndClear runs. */
  clearAll(): void;

  /** Active panel count — used by the Process Panel to defer its
   *  auto-dismiss while cards are still up. */
  cardCount(): number;

  /** Subscribe to mount/unmount transitions. Callback receives the new
   *  count. Used by the Process Panel to re-evaluate auto-dismiss when
   *  the last card is closed. */
  onChange(cb: (count: number) => void): () => void;

  destroy(): void;
}

// ---------------------------------------------------------------------------
// Layout constants
// ---------------------------------------------------------------------------

const CARD_WIDTH = 340;                // matches .fp-panel width
const GUTTER = 16;                     // gap between cards horizontally + vertically
const RIGHT_MARGIN = 24;               // gap from the right edge of viewport
const TOP_MARGIN = 80;                 // below any top nav
const ROW_STRIDE = 290;                // approximate card height for row spacing
const ORB_BUFFER = 380;                // px reserved on the left for the orb

interface PanelEntry {
  element: HTMLElement;
  slot: number | null;                 // null = user-dragged, no longer grid-bound
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function createFloatingPanelsLayer(
  rootId: string = "floating-panels-root",
): FloatingPanelsLayer {
  const root = ensureRoot(rootId);
  const panels = new Map<string, PanelEntry>();    // eventId → entry
  const changeListeners = new Set<(count: number) => void>();
  let zCounter = 1000;

  function computeColumns(): number {
    const usable = window.innerWidth - ORB_BUFFER - RIGHT_MARGIN;
    return Math.max(1, Math.floor((usable + GUTTER) / (CARD_WIDTH + GUTTER)));
  }

  function slotPosition(slot: number): { left: number; top: number } {
    const cols = computeColumns();
    const row = Math.floor(slot / cols);
    const col = slot % cols;                       // 0 = rightmost
    const rightAnchorLeft = window.innerWidth - RIGHT_MARGIN - CARD_WIDTH;
    const left = clamp(
      rightAnchorLeft - col * (CARD_WIDTH + GUTTER),
      8,
      window.innerWidth - CARD_WIDTH - 8,
    );
    const top = clamp(
      TOP_MARGIN + row * ROW_STRIDE,
      8,
      window.innerHeight - 120,
    );
    return { left, top };
  }

  function nextFreeSlot(): number {
    const used = new Set<number>();
    for (const e of panels.values()) {
      if (e.slot !== null) used.add(e.slot);
    }
    let i = 0;
    while (used.has(i)) i++;
    return i;
  }

  function emitChange(): void {
    const c = panels.size;
    for (const cb of changeListeners) {
      try { cb(c); } catch (e) { console.warn("floatingPanels onChange listener threw", e); }
    }
  }

  function mountCard(event: ProcessEvent, buildContent: () => HTMLElement): void {
    const eventId = event.id || `card-${Date.now()}-${Math.random()}`;
    if (panels.has(eventId)) return; // already mounted (defensive)

    const panel = document.createElement("div");
    panel.className = `fp-panel fp-panel-${cssTypeSuffix(event.type)}`;
    panel.dataset.eventId = eventId;
    panel.dataset.taskId = event.task_id;

    // Grid-slot placement. Next free slot fills any gap left by a
    // user-dragged-away panel; otherwise the slot counter increments.
    // Existing panels are never reshuffled when a new card arrives.
    const slot = nextFreeSlot();
    const { left, top } = slotPosition(slot);
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
    panel.style.zIndex = String(++zCounter);

    // Chrome: grab-handle + type label + close button.
    const handle = document.createElement("div");
    handle.className = "fp-handle";

    const grip = document.createElement("div");
    grip.className = "fp-handle-grip";
    handle.appendChild(grip);

    const label = document.createElement("span");
    label.className = "fp-type-label";
    label.textContent = typeLabel(event.type);
    handle.appendChild(label);

    const close = document.createElement("button");
    close.className = "fp-close";
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss");
    close.textContent = "×";
    close.addEventListener("click", (e) => {
      e.stopPropagation();
      removePanel(eventId);
    });
    handle.appendChild(close);

    panel.appendChild(handle);

    const body = document.createElement("div");
    body.className = "fp-body";
    try {
      body.appendChild(buildContent());
    } catch (err) {
      console.warn("floatingPanel: content build failed", err);
      const fallback = document.createElement("div");
      fallback.textContent = event.title || "(card content failed to render)";
      body.appendChild(fallback);
    }
    panel.appendChild(body);

    // Z-order: any pointerdown anywhere on the panel raises it to the top.
    panel.addEventListener("pointerdown", () => {
      panel.style.zIndex = String(++zCounter);
    });

    // Drag — mirrors the Process Panel's pattern (setPointerCapture so the
    // pointer is owned during the drag, even if it leaves the handle).
    // First drag releases the panel's grid slot so a new card can take it
    // without overlapping the dragged panel's new manual position.
    attachDrag(panel, handle, () => {
      const entry = panels.get(eventId);
      if (entry) entry.slot = null;
    });

    root.appendChild(panel);
    panels.set(eventId, { element: panel, slot });
    emitChange();
  }

  function removePanel(eventId: string): void {
    const entry = panels.get(eventId);
    if (!entry) return;
    entry.element.remove();
    panels.delete(eventId);
    emitChange();
  }

  function resetLayout(): void {
    // Drop any user-dragged "anchored" panels' slot-release state so the
    // next batch starts cleanly. Existing panels remain visible at their
    // current pixel positions — the user can still see prior research's
    // cards until they X them. nextFreeSlot() will pick slot 0 again
    // because no panel in `panels` currently claims it (the new batch's
    // cards haven't mounted yet, and previous-run cards from clearAll's
    // perspective are gone — but if the user kept them, their slots are
    // still tracked so the new run skips past them).
    //
    // Concretely: if a user kept 3 cards from run 1, those panels still
    // claim slots 0/1/2. resetLayout doesn't reset anything per-panel; it's
    // a no-op placeholder kept for symmetry with the cascade era so the
    // call site can stay readable. If a future change wants to force the
    // new batch onto slot 0 regardless of prior cards, this is where it
    // would clear those slots.
  }

  function clearAll(): void {
    const had = panels.size > 0;
    for (const entry of panels.values()) entry.element.remove();
    panels.clear();
    if (had) emitChange();
  }

  function cardCount(): number {
    return panels.size;
  }

  function onChange(cb: (count: number) => void): () => void {
    changeListeners.add(cb);
    return () => changeListeners.delete(cb);
  }

  function destroy(): void {
    clearAll();
    changeListeners.clear();
    root.remove();
  }

  return { mountCard, resetLayout, clearAll, cardCount, onChange, destroy };
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

function attachDrag(
  panel: HTMLElement,
  handle: HTMLElement,
  onDragStart?: () => void,
): void {
  let from: { x: number; y: number; panelLeft: number; panelTop: number } | null = null;
  let firedStartHook = false;

  handle.addEventListener("pointerdown", (e) => {
    // Don't initiate drag if the close button (or any button) was hit —
    // it owns its own click handler.
    if ((e.target as HTMLElement).closest(".fp-close")) return;
    const r = panel.getBoundingClientRect();
    from = { x: e.clientX, y: e.clientY, panelLeft: r.left, panelTop: r.top };
    panel.classList.add("dragging");
    handle.setPointerCapture(e.pointerId);
    if (!firedStartHook) {
      firedStartHook = true;
      try { onDragStart?.(); } catch (e) { console.warn("onDragStart threw", e); }
    }
  });

  handle.addEventListener("pointermove", (e) => {
    if (!from) return;
    const dx = e.clientX - from.x;
    const dy = e.clientY - from.y;
    const { width, height } = panel.getBoundingClientRect();
    panel.style.left = `${clamp(from.panelLeft + dx, 0, window.innerWidth - width)}px`;
    panel.style.top = `${clamp(from.panelTop + dy, 0, window.innerHeight - height)}px`;
  });

  handle.addEventListener("pointerup", (e) => {
    if (!from) return;
    from = null;
    panel.classList.remove("dragging");
    handle.releasePointerCapture(e.pointerId);
  });

  handle.addEventListener("pointercancel", () => {
    from = null;
    panel.classList.remove("dragging");
  });
}

function ensureRoot(id: string): HTMLElement {
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

function cssTypeSuffix(eventType: string): string {
  // Convert "result.product" → "result_product" for use in CSS class names.
  return eventType.replace(/\./g, "_");
}

function typeLabel(eventType: string): string {
  const t = eventType.replace(/^result\./, "");
  switch (t) {
    case "product":         return "PRODUCT";
    case "location":        return "LOCATION";
    case "web":             return "WEB";
    case "image":           return "IMAGE";
    case "research_source": return "SOURCE";
    default:                return t.toUpperCase();
  }
}
