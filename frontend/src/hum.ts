/**
 * VALET working hum — a subtle ambient drone that fades in while VALET is
 * actively working (a task is in flight) and fades back out when the work
 * settles. It's the audio companion to the Process Panel's live activity:
 * presence, not melody.
 *
 * Built on the app's existing shared AudioContext (see voice.ts / main.ts) so
 * it obeys the same autoplay-unlock lifecycle as TTS. The oscillator graph is
 * created lazily on first activation, so a session that never dispatches work
 * never spins up audio nodes. Volume is deliberately low and the fades are slow
 * enough that brief tasks barely swell.
 *
 * User control: set localStorage["valet.hum.enabled"] = "0" to mute it.
 */

const ENABLED_KEY = "valet.hum.enabled";

// Base loudness of the drone when fully active. Low on purpose — this should
// sit under everything, never compete with VALET's voice.
const TARGET_GAIN = 0.05;
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

  // Lazily-built graph.
  let master: GainNode | null = null;
  let oscillators: OscillatorNode[] = [];
  let lfo: OscillatorNode | null = null;

  function build() {
    if (built) return;
    built = true;

    master = ctx.createGain();
    master.gain.value = 0;

    // Gentle low-pass so only the warm low end comes through — no fizz.
    const filter = ctx.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.value = 480;
    filter.Q.value = 0.4;
    filter.connect(master);
    master.connect(ctx.destination);

    // A low fundamental plus a soft perfect-fifth above it, each slightly
    // detuned for a living, chorused drone rather than a dead test tone.
    const voices: Array<{ freq: number; detune: number; level: number; type: OscillatorType }> = [
      { freq: 110.0, detune: -4, level: 0.6, type: "sine" },     // A2
      { freq: 164.8, detune: +5, level: 0.35, type: "sine" },    // E3 (fifth)
      { freq: 55.0, detune: 0, level: 0.5, type: "sine" },       // A1 sub
    ];
    for (const v of voices) {
      const osc = ctx.createOscillator();
      osc.type = v.type;
      osc.frequency.value = v.freq;
      osc.detune.value = v.detune;
      const g = ctx.createGain();
      g.gain.value = v.level;
      osc.connect(g);
      g.connect(filter);
      osc.start();
      oscillators.push(osc);
    }

    // Slow "breathing" — an LFO nudging the master gain so the drone drifts
    // instead of sitting perfectly still.
    lfo = ctx.createOscillator();
    lfo.type = "sine";
    lfo.frequency.value = 0.13;
    const lfoGain = ctx.createGain();
    lfoGain.gain.value = 0.012;
    lfo.connect(lfoGain);
    lfoGain.connect(master.gain);
    lfo.start();
  }

  function rampTo(value: number, seconds: number) {
    if (!master) return;
    const now = ctx.currentTime;
    // cancelAndHold isn't in every engine; cancel + re-anchor at the current
    // value keeps the ramp continuous without a click.
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
      try { lfo?.stop(); } catch { /* ignore */ }
      for (const o of oscillators) {
        try { o.stop(); } catch { /* ignore */ }
      }
      oscillators = [];
      try { master?.disconnect(); } catch { /* ignore */ }
      master = null;
      built = false;
    },
  };
}
