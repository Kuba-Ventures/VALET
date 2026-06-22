# Showcase media

Drop real example **images or videos** here to replace the stylized CSS mockups
in the home "Speed is the product" carousel (`components/home/HomeShowcase.tsx`).

For each card, set its `media` field in `HomeShowcase.tsx`:

```ts
// image
media: { type: "image", src: "/showcase/talk.png" }

// video (autoplays, muted, looped — keep it short + silent, like Raycast)
media: { type: "video", src: "/showcase/talk.mp4", poster: "/showcase/talk.jpg" }
```

When `media` is `null`, the card falls back to its built-in CSS mockup.

Suggested files (one per card): `talk`, `instant`, `control`, `ship`, `teach`.
Record at ~16:10, ≥1280px wide; videos as H.264 `.mp4` (and optionally `.webm`),
a few seconds, no audio. They render edge-to-edge inside the window frame.
