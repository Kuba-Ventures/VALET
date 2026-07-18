/**
 * VALET working hum — a subtle ambient bed that fades in while VALET is
 * actively working (a task is in flight) and fades back out when the work
 * settles. It's the audio companion to the Process Panel's live activity:
 * presence, not melody.
 *
 * Sound: "Pulsing Thrum" — a low E-minor drone (E2 · B2 · sub-E1) through a
 * lowpass, with a gentle ~2.6 Hz amplitude tremolo so it reads as an active
 * heartbeat rather than a static tone. Chosen from the option board; master
 * level is deliberately low (see TARGET_GAIN).
 *
 * Built on the app's existing shared AudioContext (see voice.ts / main.ts) so
 * it obeys the same autoplay-unlock lifecycle as TTS. The oscillator graph is
 * created lazily on first activation, so a session that never dispatches work
 * never spins up audio nodes.
 *
 * User control: the "Working hum" toggle in Console Settings, or set
 * localStorage["valet.hum.enabled"] = "0" to mute it.
 */

const ENABLED_KEY = "valet.hum.enabled";

// Base loudness of the drone when fully active. 0.015 == 15% on the option
// board's master slider (the chosen level). Low on purpose — this sits under
// everything and never competes with VALET's voice.
const TARGET_GAIN = 0.015;
const FADE_IN_S = 0.7;
const FADE_OUT_S = 1.4;

export interface WorkingHum {
  /** Fade the hum in (true) or out (false) to match active-work state. */
  setActive(active: boolean): void;
  /** Persisted on/off. When turned off while sounding, fades out immediately. */
  setEnabled(enabled: boolean): void;
  isEnabled(): boolean;
  dispose(): void;
}

function readEnabled(): boolean {
  try {
    return localStorage.getItem(ENABLED_KEY) !== "0";
  } catch {
    return true; // localStorage blocked → default on
  }
}

export function createWorkingHum(ctx: AudioContext): WorkingHum {
  let enabled = readEnabled();
  let active = false;
  let built = false;

  // Lazily-built graph. `master` carries only the fade envelope; the tremolo
  // lives on a separate gain node so fades stay clean.
  let master: GainNode | null = null;
  // Everything with a .stop(), tracked for dispose().
  let sources: Array<{ stop: () => void }> = [];

  function build() {
    if (built) return;
    built = true;

    master = ctx.createGain();
    master.gain.value = 0;
    master.connect(ctx.destination);

    // Tremolo stage — its gain oscillates so the drone "pulses".
    const trem = ctx.createGain();
    trem.gain.value = 1;
    trem.connect(master);

    // Gentle low-pass so only the warm low end comes through — no fizz.
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 420;
    lp.Q.value = 0.5;
    lp.connect(trem);

    // Low E-minor drone: E2, a fifth (B2), and a sub octave (E1), each slightly
    // detuned for a living, chorused body rather than a dead test tone.
    const voices: Array<{ freq: number; detune: number; level: number }> = [
      { freq: 82.4, detune: -3, level: 0.55 },   // E2
      { freq: 123.47, detune: +4, level: 0.3 },  // B2 (fifth)
      { freq: 41.2, detune: 0, level: 0.4 },     // E1 sub
    ];
    for (const v of voices) {
      const osc = ctx.createOscillator();
      osc.type = "sine";
      osc.frequency.value = v.freq;
      osc.detune.value = v.detune;
      const g = ctx.createGain();
      g.gain.value = v.level;
      osc.connect(g);
      g.connect(lp);
      osc.start();
      sources.push(osc);
    }

    // Tremolo LFO — nudges trem.gain around 0.68 (± 0.32), so the level dips
    // to ~0.36 and peaks at ~1.0: a clear pulse that never goes fully silent.
    const lfo = ctx.createOscillator();
    lfo.type = "sine";
    lfo.frequency.value = 2.6;
    const lfoDepth = ctx.createGain();
    lfoDepth.gain.value = 0.32;
    const dc = ctx.createConstantSource();
    dc.offset.value = 0.68;
    dc.connect(trem.gain);
    lfo.connect(lfoDepth);
    lfoDepth.connect(trem.gain);
    lfo.start();
    dc.start();
    sources.push(lfo, dc);
  }

  function rampTo(value: number, seconds: number) {
    if (!master) return;
    const now = ctx.currentTime;
    try {
      master.gain.cancelScheduledValues(now);
    } catch { /* no-op */ }
    master.gain.setValueAtTime(master.gain.value, now);
    master.gain.linearRampToValueAtTime(value, now + seconds);
  }

  function apply() {
    const shouldSound = enabled && active;
    if (shouldSound) {
      build();
      // Resume is best-effort; if the context is still locked the ramp is
      // scheduled and takes effect once it unlocks.
      if (ctx.state !== "running") ctx.resume().catch(() => {});
      rampTo(TARGET_GAIN, FADE_IN_S);
    } else if (built) {
      rampTo(0, FADE_OUT_S);
    }
  }

  return {
    setActive(next: boolean) {
      if (next === active) return;
      active = next;
      apply();
    },
    setEnabled(next: boolean) {
      enabled = next;
      try { localStorage.setItem(ENABLED_KEY, next ? "1" : "0"); } catch { /* ignore */ }
      apply();
    },
    isEnabled() {
      return enabled;
    },
    dispose() {
      for (const s of sources) {
        try { s.stop(); } catch { /* ignore */ }
      }
      sources = [];
      try { master?.disconnect(); } catch { /* ignore */ }
      master = null;
      built = false;
    },
  };
}
