/**
 * Floating result-card panels.
 *
 * Each result.* event (product / location / web / image / research_source)
 * becomes its own draggable, dismissable floating panel — not a row inside
 * the Process Panel. The Process Panel is still the live event log; this
 * layer holds the cards that come *out* of it.
 *
 * Layout: cards cascade in from the top-right with a ~30px offset per
 * card so a fresh batch is fully visible (no piled-up un-clickable stack).
 * The cascade index resets at task_start so each new research run starts
 * its own staircase rather than continuing the previous one.
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

  /** Start a fresh cascade — called on each task_start so a new run's
   *  cards don't stack onto the previous run's staircase. */
  resetCascade(): void;

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

const CARD_WIDTH = 340;                // matches .fp-panel max-width
const CASCADE_STEP = 30;               // horizontal+vertical offset per card
const INITIAL_RIGHT_MARGIN = 24;       // gap from the right edge
const INITIAL_TOP = 80;                // below any top nav
const CASCADE_WRAP_AFTER = 8;          // restart staircase from origin after N

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function createFloatingPanelsLayer(
  rootId: string = "floating-panels-root",
): FloatingPanelsLayer {
  const root = ensureRoot(rootId);
  const panels = new Map<string, HTMLElement>();   // eventId → panel root
  const changeListeners = new Set<(count: number) => void>();
  let cascadeIndex = 0;
  let zCounter = 1000;

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

    // Position via cascade. Computed in pixels so the panel can be dragged
    // freely afterward without fighting `right:`/percentage anchors.
    const idx = cascadeIndex % CASCADE_WRAP_AFTER;
    const offset = idx * CASCADE_STEP;
    const left = clamp(
      window.innerWidth - CARD_WIDTH - INITIAL_RIGHT_MARGIN - offset,
      8,
      window.innerWidth - CARD_WIDTH - 8,
    );
    const top = clamp(INITIAL_TOP + offset, 8, window.innerHeight - 100);
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
    panel.style.zIndex = String(++zCounter);
    cascadeIndex++;

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
    attachDrag(panel, handle);

    root.appendChild(panel);
    panels.set(eventId, panel);
    emitChange();
  }

  function removePanel(eventId: string): void {
    const panel = panels.get(eventId);
    if (!panel) return;
    panel.remove();
    panels.delete(eventId);
    emitChange();
  }

  function resetCascade(): void {
    cascadeIndex = 0;
  }

  function clearAll(): void {
    const had = panels.size > 0;
    for (const panel of panels.values()) panel.remove();
    panels.clear();
    cascadeIndex = 0;
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

  return { mountCard, resetCascade, clearAll, cardCount, onChange, destroy };
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

function attachDrag(panel: HTMLElement, handle: HTMLElement): void {
  let from: { x: number; y: number; panelLeft: number; panelTop: number } | null = null;

  handle.addEventListener("pointerdown", (e) => {
    // Don't initiate drag if the close button (or any button) was hit —
    // it owns its own click handler.
    if ((e.target as HTMLElement).closest(".fp-close")) return;
    const r = panel.getBoundingClientRect();
    from = { x: e.clientX, y: e.clientY, panelLeft: r.left, panelTop: r.top };
    panel.classList.add("dragging");
    handle.setPointerCapture(e.pointerId);
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
