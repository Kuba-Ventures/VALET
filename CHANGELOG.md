# Changelog

All notable changes to VALET are documented here. Versions are the app version
(`src-tauri/tauri.conf.json`) shipped as the signed, notarized macOS build.

## [0.2.29] — 2026-07-16

### Fixed
- **"Needs setup" on Input Monitoring you'd already granted.** The setup step
  asked the backend whether Input Monitoring was granted, but the ⌃⌥ chord runs
  in the VALET binary and macOS grants this per executable — so the backend was
  answering for a permission it neither holds nor needs. It reported "Needs
  setup" while the chord worked fine, and Re-check kept asking the same wrong
  process, so the step could never be satisfied. VALET now asks the binary that
  actually listens. The other permissions are genuinely used by the backend, so
  they still answer for themselves.

## [0.2.28] — 2026-07-16

### Fixed
- **The whole setup panel drags now, not just parts of it.** 0.2.27 made the
  card padding, the brand row, the actions row and each step's title draggable,
  but grabbing anything else — a permission row's label, its grey blurb — still
  selected text instead of moving the window. Tauri only starts a drag when the
  pressed element itself is a drag region, so hand-picking which elements to tag
  kept missing whatever people actually grabbed (this is the third pass at it).
  Inverted: every inert element in the card is tagged after each render, and only
  the exceptions are named — the scrolling step body (so its scrollbar still
  scrolls) and interactive controls (so buttons and inputs still work). Grabbing
  text no longer highlights it either.

## [0.2.27] — 2026-07-16

### Fixed
- **The setup wizard can be moved, and pushed behind other windows.** During
  first-run setup the panel couldn't be dragged and floated above everything,
  so granting a permission meant fighting the window that was asking for it.
  Only a 22px strip and the small "VALET" row were drag handles, while the card
  covers most of the 380x560 popover — grabbing the panel anywhere a person
  actually grabs it (the title, the blurb, the padding) did nothing. Those are
  all drag handles now. The scrolling step body is deliberately left alone so
  its scrollbar still scrolls instead of dragging the window. Setup also drops
  always-on-top for its duration and restores it when you finish, so the wizard
  can sit behind System Settings while you grant a permission. The orb itself is
  unchanged — it still floats.

## [0.2.26] — 2026-07-16

### Fixed
- **Prices, index names and version numbers spoken correctly.** The 0.2.25
  number-spelling fix applies to every spoken response, and three cases came out
  wrong: `$150` stranded the symbol ("dollar one hundred fifty"), `S&P 500`
  became "S&P five hundred", and `0.2.25` became "0.2.twenty-five". Currency is
  now reordered into words ("one hundred fifty dollars", "$67,432" → "sixty-seven
  thousand four hundred thirty-two dollars"), and acronym-numbers and dotted
  versions are left verbatim. Bare digit runs (IDs, phone numbers) are untouched.

## [0.2.25] — 2026-07-16

### Fixed
- **VALET no longer argues with its own live stats.** The butler rephrase on
  the new STATS path was fact-checking the (current, correct) StatMuse line
  against its own out-of-date training and "correcting" it — reporting Man
  United's actual top scorers as *"Mbeumo plays for Brentford and Šeško for RB
  Leipzig, neither of whom are Manchester United players, sir"* after both had
  transferred. The rephrase is now a strict style transform, backed by a
  deterministic faithfulness check that discards any restatement which disputes
  the source or drops/invents a figure, falling back to the raw stat line.
- **Numbers spoken in the wrong language.** Fish's multilingual voice read bare
  digits in whichever language it guessed — "Argentina beat England, 2 to 1"
  came out *"dua to uno"*. Plain integers are now spelled out in English for the
  audio only (captions keep the digits); years, seasons, decimals, times and
  ordinals are left untouched so "2025-26" still reads correctly.
- **"Tell me more about [headline]" summarized the screen** instead of opening
  the article. Follow-ups about items VALET just surfaced are now always
  READ_ARTICLE — the screen actions are explicitly excluded, and topic-based
  references ("the Falklands banner issue") are matched, not just ordinals.

## [0.2.24] — 2026-07-16

### Added
- **Sports statistics via StatMuse (`[ACTION:STATS]`).** A new keyless source
  (`statmuse.py`) that answers player and team stat questions — "how many goals
  did Mbappé score for Real Madrid this season", "who's the Premier League top
  scorer", "LeBron's points per game" — with one correct spoken sentence in
  ~1–2 seconds from a single source, no stack of research panels. StatMuse
  auto-detects the sport and understands "this season"/"last season"; on any
  miss it falls back to the quick web LOOKUP, then deep RESEARCH.

### Fixed
- **Sports stat questions were answered wrong or slowly.** They previously hit
  either the SPORTS path (which only knows scores/schedules, so "leading scorer
  of the Premier League" returned a *fixture*) or the multi-page RESEARCH crawl
  (slow, many source cards, often punting on "conflicting figures"). They now
  route to the StatMuse fast-path.
- **"Show me the three best fishing poles"** (and other superlative/"options"
  research requests) no longer get grabbed by the on-screen point-and-teach
  handler ("I don't see a … to point at, sir") — they correctly reach RESEARCH.

## [0.2.23] — 2026-07-16

### Fixed
- **Player/team stat questions now answer in ~2 seconds.** "How many goals did
  Mbappé score for Real Madrid this season" and "how many goals has Mbappé scored
  this World Cup" were taking the wrong path — the first spun up the full
  multi-source RESEARCH crawl (fetching 3–5 pages), and the second hit the SPORTS
  fast-path (which only knows scores/schedules and answered with an unrelated
  match result). Any single-fact stat question ("how many goals/points/assists/…")
  now routes to the fast LOOKUP path (one search → one spoken number) instead of
  SPORTS (wrong) or RESEARCH (slow). Rich multi-item asks still use RESEARCH.

## [0.2.22] — 2026-07-15

A large release focused on reliability and dramatically expanding what VALET can
answer, fast. Highlights below are grouped by the increments that built up to it.

### Fixed
- **Microphone recovery after quit → relaunch.** The speech recognizer could get
  wedged (and audibly cycle the mic on/off) when macOS hadn't released the prior
  process's audio session. The recognizer is now rebuilt on backoff with
  escalating, spaced retries, so it recovers cleanly. (0.2.10)
- **"Who's playing in the World Cup final"** now names the actual final (Argentina
  vs Spain) vs the third-place match, using ESPN's round labels. (0.2.17)
- **Current role-holders always verified.** "Who's the coach of UVA basketball"
  no longer answers a stale name from memory — role/title/office questions always
  do a live check. (0.2.18)
- **Leadership transitions** are stated precisely ("Tim Cook until September 1st,
  then John Ternus"), never calling a future successor the current holder. (0.2.19)

### Added
- **Temporal grounding.** VALET defaults to the current instance of an event or
  season given today's date instead of asking which year. (0.2.11)
- **Universal live sports** (`[ACTION:SPORTS]`) via ESPN — any team/league/school
  resolved dynamically: scores, schedules, offseason "next game", season records,
  "who did X lose to", plus individual sports (golf/tennis/F1/UFC). (0.2.12–0.2.14)
- **Fast direct answers** for settled facts (no web round-trip), reserving deep
  research for what genuinely needs the live web. (0.2.15)
- **Fast web fact-check** (`[ACTION:LOOKUP]`) — a ~2s DuckDuckGo + synthesis path
  for current facts; VALET checks instead of saying "I'd need to check". (0.2.16)
- **Live market quotes** (`[ACTION:MARKETS]`) via Yahoo Finance — stocks, crypto,
  indices, commodities; any ticker/company, instant. (0.2.19)
- **News** (`[ACTION:NEWS]`) via Google News — top headlines and topic search. (0.2.20)
- **Cite what VALET mentioned.** After it reads headlines, "tell me more about the
  X one" / "open the second one" resolves the reference, opens that article in the
  browser, and speaks a summary. (0.2.21–0.2.22)

### Notes
- All new information sources are keyless. Answers land in ~1–3s; anything not
  covered by a dedicated source falls back to web research.
- macOS build is signed with Developer ID and notarized.

## [0.2.9] and earlier

See the GitHub release history at `Kuba-Ventures/valet-downloads` for prior
versions (menu-bar product, resizable window, admin dashboard, Langfuse tracing,
Universal Control, etc.).
