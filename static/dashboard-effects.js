(() => {
  const root = document.getElementById("dashboard-three-root");
  if (!root) return;

  const runFallbackMotion = () => {
    document.body.classList.add("dash-effects-fallback");
  };

  const hasWebGL = () => {
    try {
      const canvas = document.createElement("canvas");
      return Boolean(
        window.WebGLRenderingContext &&
          (canvas.getContext("webgl") || canvas.getContext("experimental-webgl"))
      );
    } catch (error) {
      return false;
    }
  };

  const boot = async () => {
    try {
      if (!hasWebGL()) {
        runFallbackMotion();
        return;
      }

      const [
        ReactMod,
        ReactDomMod,
        ThreeMod,
        FiberMod,
        GsapMod,
        AnimeMod,
        FramerMod,
      ] = await Promise.all([
        import("react"),
        import("react-dom/client"),
        import("three"),
        import("@react-three/fiber"),
        import("gsap"),
        import("animejs"),
        import("framer-motion"),
      ]);

      const React = ReactMod.default || ReactMod;
      const { useMemo, useRef } = ReactMod;
      const { createRoot } = ReactDomMod;
      const THREE = ThreeMod;
      const { Canvas, useFrame } = FiberMod;
      const gsap = GsapMod.gsap || GsapMod.default || GsapMod;
      const anime = AnimeMod.default || AnimeMod;
      const { motion } = FramerMod;
      const h = React.createElement;

      document.body.classList.add("dash-enhanced");

      function DeckObject() {
        const group = useRef(null);
        const colors = useMemo(
          () => [
            new THREE.Color("#60a5fa"),
            new THREE.Color("#34d399"),
            new THREE.Color("#a78bfa"),
            new THREE.Color("#22d3ee"),
            new THREE.Color("#fbbf24"),
          ],
          []
        );

        useFrame(({ clock }) => {
          if (!group.current) return;
          const t = clock.getElapsedTime();
          group.current.rotation.y = t * 0.22;
          group.current.rotation.x = Math.sin(t * 0.42) * 0.12;
        });

        const cards = colors.map((color, index) =>
          h(
            "mesh",
            {
              key: `card-${index}`,
              position: [
                Math.cos((index / colors.length) * Math.PI * 2) * 1.15,
                Math.sin(index * 1.7) * 0.32,
                Math.sin((index / colors.length) * Math.PI * 2) * 1.15,
              ],
              rotation: [0.22, (index / colors.length) * Math.PI * 2, 0.08],
              scale: [0.74, 1.06, 0.052],
            },
            h("boxGeometry", { args: [1, 1, 1] }),
            h("meshStandardMaterial", {
              color,
              roughness: 0.34,
              metalness: 0.36,
              emissive: color,
              emissiveIntensity: 0.08,
            })
          )
        );

        const points = colors.map((color, index) =>
          h(
            "mesh",
            {
              key: `node-${index}`,
              position: [
                Math.cos((index / colors.length) * Math.PI * 2 + 0.6) * 1.72,
                Math.cos(index * 0.9) * 0.52,
                Math.sin((index / colors.length) * Math.PI * 2 + 0.6) * 1.72,
              ],
            },
            h("sphereGeometry", { args: [0.07, 24, 24] }),
            h("meshStandardMaterial", {
              color,
              roughness: 0.2,
              metalness: 0.5,
              emissive: color,
              emissiveIntensity: 0.22,
            })
          )
        );

        return h(
          "group",
          { ref: group },
          h(
            "mesh",
            { rotation: [0.7, 0.25, 0] },
            h("torusGeometry", { args: [1.28, 0.012, 12, 120] }),
            h("meshStandardMaterial", {
              color: "#7dd3fc",
              transparent: true,
              opacity: 0.58,
              roughness: 0.45,
              metalness: 0.5,
            })
          ),
          h(
            "mesh",
            { rotation: [1.22, -0.28, 0.35] },
            h("torusGeometry", { args: [1.82, 0.01, 12, 120] }),
            h("meshStandardMaterial", {
              color: "#34d399",
              transparent: true,
              opacity: 0.36,
              roughness: 0.45,
              metalness: 0.45,
            })
          ),
          h(
            "mesh",
            null,
            h("icosahedronGeometry", { args: [0.58, 1] }),
            h("meshStandardMaterial", {
              color: "#111827",
              roughness: 0.28,
              metalness: 0.62,
              emissive: "#2563eb",
              emissiveIntensity: 0.12,
            })
          ),
          ...cards,
          ...points
        );
      }

      function Scene() {
        return h(
          Canvas,
          {
            dpr: [1, 1.7],
            camera: { position: [0, 0.15, 5.2], fov: 42 },
            gl: { alpha: true, antialias: true },
          },
          h("ambientLight", { intensity: 0.76 }),
          h("directionalLight", { position: [3, 4, 5], intensity: 1.2 }),
          h("pointLight", { position: [-2.5, -1.5, 2.8], intensity: 0.82, color: "#60a5fa" }),
          h(DeckObject)
        );
      }

      function VisualApp() {
        return h(
          React.Fragment,
          null,
          h(Scene),
          h(
            motion.div,
            {
              className: "dash-motion-label",
              initial: { opacity: 0, y: 16 },
              animate: { opacity: 1, y: 0 },
              transition: { duration: 0.7, delay: 0.25, ease: [0.16, 1, 0.3, 1] },
            },
            h("span", null, "Live-сигналы бота"),
            h("span", null, "R3F + Three.js")
          )
        );
      }

      createRoot(root).render(h(VisualApp));

      if (gsap && typeof gsap.fromTo === "function") {
        gsap.fromTo(
          ".dash-hero-copy > *",
          { y: 18 },
          { y: 0, duration: 0.72, stagger: 0.08, ease: "power3.out", clearProps: "transform" }
        );
        gsap.fromTo(
          ".chart-wrap, .logs-section",
          { y: 14 },
          { y: 0, duration: 0.58, stagger: 0.08, delay: 0.18, ease: "power2.out", clearProps: "transform" }
        );
      }

      if (anime && typeof anime === "function") {
        const pulseStats = () => {
          anime({
            targets: ".cards .card",
            translateY: [8, 0],
            opacity: [0.82, 1],
            delay: anime.stagger(42),
            duration: 520,
            easing: "easeOutCubic",
          });
          anime({
            targets: ".cards .value",
            scale: [0.96, 1],
            duration: 560,
            delay: anime.stagger(36),
            easing: "easeOutElastic(1, .72)",
          });
        };
        window.addEventListener("dashboard:stats-rendered", pulseStats);
        window.addEventListener("dashboard:theme-changed", () => {
          anime({
            targets: ".dash-hero, .card, .chart-wrap, .logs-section",
            opacity: [0.92, 1],
            duration: 360,
            easing: "easeOutQuad",
          });
        });
      }
    } catch (error) {
      console.warn("Dashboard effects disabled:", error);
      runFallbackMotion();
    }
  };

  boot();
})();
