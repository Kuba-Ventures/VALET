"use client";

import { useEffect, useRef } from "react";

/**
 * Hero orb: a golden-angle particle sphere on a full-bleed <canvas>, centered
 * behind the headline and recolored to VALET violet. It rotates slowly on two
 * axes with a gentle per-particle pulse so it reads as a living, flowing orb
 * rather than a static image. Freezes under prefers-reduced-motion.
 */
export default function HeroOrb() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;

    let W = 0, H = 0, DPR = 1, cx = 0, cy = 0, R = 0;
    const reduce = matchMedia("(prefers-reduced-motion:reduce)").matches;

    function size() {
      if (!c || !ctx) return;
      DPR = Math.min(devicePixelRatio || 1, 2);
      W = c.clientWidth; H = c.clientHeight;
      c.width = W * DPR; c.height = H * DPR; ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      // Centered behind the headline; scales with the smaller viewport edge.
      cx = W * 0.5;
      cy = H * 0.5;
      R = Math.min(W, H) * (W > 820 ? 0.32 : 0.4);
    }
    size();
    addEventListener("resize", size);

    const N = 620;
    const pts: { x: number; y: number; z: number; s: number }[] = [];
    for (let i = 0; i < N; i++) {
      const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), phi = i * 2.399963229728653;
      pts.push({ x: Math.cos(phi) * r, y, z: Math.sin(phi) * r, s: Math.random() });
    }

    let t = 0, raf = 0;
    function frame() {
      if (!ctx) return;
      ctx.clearRect(0, 0, W, H);
      t += reduce ? 0 : 0.0026;
      const cosA = Math.cos(t), sinA = Math.sin(t), cosB = Math.cos(t * 0.6), sinB = Math.sin(t * 0.6);
      for (const p of pts) {
        const x = p.x * cosA - p.z * sinA;
        let z = p.x * sinA + p.z * cosA;
        const y = p.y * cosB - z * sinB;
        z = p.y * sinB + z * cosB;
        const pulse = 1 + Math.sin(t * 1.6 + p.s * 6.28) * 0.045;
        const px = cx + x * R * pulse, py = cy + y * R * pulse, depth = (z + 1) / 2;
        ctx.beginPath();
        ctx.arc(px, py, depth * 1.9 + 0.3, 0, 6.283);
        // light-violet dots (#C4B8FA), brighter toward the viewer
        ctx.fillStyle = "rgba(196,184,250," + (0.1 + depth * 0.6).toFixed(3) + ")";
        ctx.fill();
      }
      // soft violet core glow (#7C68F0)
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.2);
      g.addColorStop(0, "rgba(124,104,240,0.18)");
      g.addColorStop(1, "rgba(124,104,240,0)");
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(cx, cy, R * 1.2, 0, 6.283); ctx.fill();
      if (!reduce) raf = requestAnimationFrame(frame);
    }
    frame();

    return () => {
      removeEventListener("resize", size);
      cancelAnimationFrame(raf);
    };
  }, []);

  return <canvas ref={ref} aria-hidden className="lp-hero-orb" />;
}
