"use client";

import { useEffect, useRef } from "react";

/**
 * Ambient voice orb (VALET signature): a golden-angle particle sphere on a
 * full-bleed <canvas>, cyan dots with depth-based size/opacity and a soft
 * radial core glow. Sits right-of-center on desktop, lower-center on mobile.
 * Freezes under prefers-reduced-motion.
 */
export default function Orb() {
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
      cx = W > 820 ? W * 0.72 : W * 0.5;
      cy = W > 820 ? H * 0.5 : H * 0.62;
      R = Math.min(W, H) * (W > 820 ? 0.3 : 0.34);
    }
    size();
    addEventListener("resize", size);

    const N = 520;
    const pts: { x: number; y: number; z: number; s: number }[] = [];
    for (let i = 0; i < N; i++) {
      const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), phi = i * 2.399963229728653;
      pts.push({ x: Math.cos(phi) * r, y, z: Math.sin(phi) * r, s: Math.random() });
    }

    let t = 0, raf = 0;
    function frame() {
      if (!ctx) return;
      ctx.clearRect(0, 0, W, H);
      t += reduce ? 0 : 0.0032;
      const cosA = Math.cos(t), sinA = Math.sin(t), cosB = Math.cos(t * 0.6), sinB = Math.sin(t * 0.6);
      for (const p of pts) {
        const x = p.x * cosA - p.z * sinA;
        let z = p.x * sinA + p.z * cosA;
        const y = p.y * cosB - z * sinB;
        z = p.y * sinB + z * cosB;
        const pulse = 1 + Math.sin(t * 1.6 + p.s * 6.28) * 0.04;
        const px = cx + x * R * pulse, py = cy + y * R * pulse, depth = (z + 1) / 2;
        ctx.beginPath();
        ctx.arc(px, py, depth * 1.7 + 0.3, 0, 6.283);
        ctx.fillStyle = "rgba(77,227,242," + (0.1 + depth * 0.55).toFixed(3) + ")";
        ctx.fill();
      }
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.1);
      g.addColorStop(0, "rgba(31,168,188,0.12)");
      g.addColorStop(1, "rgba(31,168,188,0)");
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(cx, cy, R * 1.1, 0, 6.283); ctx.fill();
      if (!reduce) raf = requestAnimationFrame(frame);
    }
    frame();

    return () => {
      removeEventListener("resize", size);
      cancelAnimationFrame(raf);
    };
  }, []);

  return <canvas ref={ref} aria-hidden className="absolute inset-0 h-full w-full" />;
}
