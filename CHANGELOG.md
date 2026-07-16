# Changelog

All notable changes to VALET are documented here. Versions are the app version
(`src-tauri/tauri.conf.json`) shipped as the signed, notarized macOS build.

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
