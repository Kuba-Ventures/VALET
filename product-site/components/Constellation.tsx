"use client";

import { useEffect, useRef } from "react";

/**
 * Visual 03: a dense golden-angle field of cyan dots drifting in slow rotation,
 * with five purple "surface" anchors (Calendar, Mail, Notes, Browser, Files)
 * lifted on top. No connecting lines. Central VALET core.
 */
export default function Constellation() {
  const ref = useRef<SVGSVGElement>(null);

  useEffect(() => {
    const cs = ref.current;
    if (!cs || cs.dataset.built) return;
    cs.dataset.built = "1";
    const NS = "http://www.w3.org/2000/svg";
    const CX = 220, CY = 190, GA = 2.399963229728653;

    const field = document.createElementNS(NS, "g");
    field.setAttribute("class", "field");
    const DOTS = 300, SCALE = 9.4;
    for (let i = 4; i < DOTS; i++) {
      const ang = i * GA, rad = SCALE * Math.sqrt(i);
      if (rad < 32) continue;
      const x = CX + rad * Math.cos(ang), y = CY + rad * Math.sin(ang);
      const c = document.createElementNS(NS, "circle");
      const fade = Math.max(0.12, 0.62 - (rad / 200) * 0.5);
      const o = (fade * (0.5 + Math.random() * 0.7)).toFixed(2);
      c.setAttribute("cx", x.toFixed(1)); c.setAttribute("cy", y.toFixed(1));
      c.setAttribute("r", (0.7 + Math.random() * 1.1).toFixed(2));
      c.setAttribute("class", "fdotx");
      c.style.setProperty("--o", o); c.style.opacity = o;
      c.style.animation = "tw " + (3.5 + Math.random() * 4).toFixed(2) + "s ease-in-out infinite";
      c.style.animationDelay = (-Math.random() * 6).toFixed(2) + "s";
      field.appendChild(c);
    }
    cs.appendChild(field);

    const surf = [
      { name: "Calendar", a: -90 }, { name: "Mail", a: -26 }, { name: "Notes", a: 48 },
      { name: "Browser", a: 132 }, { name: "Files", a: 214 },
    ];
    const AR = 150;
    surf.forEach((s, k) => {
      const ang = (s.a * Math.PI) / 180;
      const x = CX + AR * Math.cos(ang), y = CY + AR * Math.sin(ang);
      const halo = document.createElementNS(NS, "circle");
      halo.setAttribute("cx", x.toFixed(1)); halo.setAttribute("cy", y.toFixed(1));
      halo.setAttribute("r", "9"); halo.setAttribute("class", "ahalo");
      halo.style.animation = "apulse 3s ease-in-out infinite"; halo.style.animationDelay = k * 0.4 + "s";
      cs.appendChild(halo);
      const a = document.createElementNS(NS, "circle");
      a.setAttribute("cx", x.toFixed(1)); a.setAttribute("cy", y.toFixed(1));
      a.setAttribute("r", "4.2"); a.setAttribute("class", "anchor");
      cs.appendChild(a);
      const t = document.createElementNS(NS, "text");
      const lx = CX + (AR + 18) * Math.cos(ang), ly = CY + (AR + 18) * Math.sin(ang);
      let al = "middle";
      if (Math.cos(ang) > 0.3) al = "start";
      else if (Math.cos(ang) < -0.3) al = "end";
      t.setAttribute("x", lx.toFixed(1)); t.setAttribute("y", (ly + 3.5).toFixed(1));
      t.setAttribute("text-anchor", al); t.setAttribute("class", "alabel");
      t.textContent = s.name;
      cs.appendChild(t);
    });

    const core = document.createElementNS(NS, "circle");
    core.setAttribute("cx", String(CX)); core.setAttribute("cy", String(CY)); core.setAttribute("r", "30");
    core.setAttribute("class", "core"); cs.appendChild(core);
    const cl = document.createElementNS(NS, "text");
    cl.setAttribute("x", String(CX)); cl.setAttribute("y", String(CY + 4)); cl.setAttribute("text-anchor", "middle");
    cl.setAttribute("class", "core-label"); cl.textContent = "VALET"; cs.appendChild(cl);
  }, []);

  return <svg ref={ref} viewBox="0 0 440 380" aria-hidden className="mx-auto w-full max-w-[440px]" />;
}
