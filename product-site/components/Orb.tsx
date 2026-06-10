"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";

/**
 * Audio-reactive-style particle orb. A luminous spherical particle field that
 * breathes and rotates slowly, echoing the local app's Three.js orb.
 *
 * Performance + accessibility:
 * - Pauses the render loop when scrolled off-screen (IntersectionObserver).
 * - Respects prefers-reduced-motion: skips WebGL entirely and renders a static
 *   CSS/SVG fallback instead.
 */
export default function Orb() {
  const mountRef = useRef<HTMLDivElement>(null);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [webglFailed, setWebglFailed] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    if (reducedMotion) return;
    const mount = mountRef.current;
    if (!mount) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    } catch {
      setWebglFailed(true);
      return;
    }

    const size = Math.min(mount.clientWidth, 560) || 480;
    renderer.setSize(size, size);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
    camera.position.z = 6;

    // Particle sphere.
    const COUNT = 2600;
    const positions = new Float32Array(COUNT * 3);
    const radius = 2.2;
    for (let i = 0; i < COUNT; i++) {
      // Even-ish distribution on a sphere shell with slight jitter.
      const theta = Math.acos(2 * ((i + 0.5) / COUNT) - 1);
      const phi = Math.PI * (1 + Math.sqrt(5)) * i;
      const r = radius + (((i * 53) % 17) / 17 - 0.5) * 0.25;
      positions[i * 3] = r * Math.sin(theta) * Math.cos(phi);
      positions[i * 3 + 1] = r * Math.sin(theta) * Math.sin(phi);
      positions[i * 3 + 2] = r * Math.cos(theta);
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));

    const material = new THREE.PointsMaterial({
      color: new THREE.Color("#38e1ff"),
      size: 0.035,
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });

    const points = new THREE.Points(geometry, material);
    scene.add(points);

    // Inner glow core.
    const coreGeo = new THREE.SphereGeometry(1.1, 32, 32);
    const coreMat = new THREE.MeshBasicMaterial({
      color: new THREE.Color("#1488c8"),
      transparent: true,
      opacity: 0.12,
    });
    scene.add(new THREE.Mesh(coreGeo, coreMat));

    // Render loop, gated by visibility.
    let running = true;
    let frame = 0;
    let raf = 0;
    const animate = () => {
      if (!running) return;
      frame += 1;
      const t = frame * 0.005;
      points.rotation.y = t;
      points.rotation.x = Math.sin(t * 0.5) * 0.2;
      // Gentle "breathing" scale, the pseudo-reactive pulse.
      const pulse = 1 + Math.sin(t * 1.6) * 0.03;
      points.scale.setScalar(pulse);
      renderer.render(scene, camera);
      raf = requestAnimationFrame(animate);
    };

    const io = new IntersectionObserver(
      (entries) => {
        const visible = entries[0]?.isIntersecting ?? false;
        if (visible && !running) {
          running = true;
          animate();
        } else if (!visible) {
          running = false;
          cancelAnimationFrame(raf);
        }
      },
      { threshold: 0.05 },
    );
    io.observe(mount);
    animate();

    const onResize = () => {
      const s = Math.min(mount.clientWidth, 560) || 480;
      renderer.setSize(s, s);
    };
    window.addEventListener("resize", onResize);

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      io.disconnect();
      window.removeEventListener("resize", onResize);
      geometry.dispose();
      material.dispose();
      coreGeo.dispose();
      coreMat.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, [reducedMotion]);

  // Static fallback for reduced motion or WebGL failure.
  if (reducedMotion || webglFailed) {
    return (
      <div
        aria-hidden
        className="relative mx-auto aspect-square w-full max-w-[480px]"
      >
        <div className="absolute inset-[18%] rounded-full bg-[radial-gradient(circle_at_50%_45%,rgba(56,225,255,0.55),rgba(20,136,200,0.18)_45%,transparent_70%)] blur-[2px]" />
        <div className="absolute inset-[30%] rounded-full border border-accent/30" />
        <div className="absolute inset-[42%] rounded-full border border-accent/20" />
      </div>
    );
  }

  return (
    <div
      ref={mountRef}
      aria-hidden
      className="mx-auto flex aspect-square w-full max-w-[560px] items-center justify-center"
    />
  );
}
