---
name: capturd-autopilot
description: React to a plain-English order like "turn on Captur'd and grab me a video/pictures of X" and JUST DO IT — no clarifying questions. Launches Captur'd on the user's screen, drives the walkthrough autonomously (or turn-by-turn if they want to talk to it live), streams frames into chat, and delivers a Supademo-grade MP4/GIF/stills. Load this whenever the user says: turn on capturd / sunsponge, take pictures/screenshots, record a walkthrough, make a demo video, film this app/flow, or "go capture X".
---

# Captur'd Autopilot — the user says "go", the film crew shows up

The user will say something like: *"turn on Captur'd and take some pictures of the checkout
flow"* — and then walk away. Your job is to make a browser **pop up on their screen**, drive
the product, **stream what you're seeing into chat**, and hand back a finished video or a
folder of shots. You do all of it **without asking a single question first.**

This is also the product demoing itself: an agent filming a SaaS walkthrough, live, is
exactly the footage Captur'd exists to make.

## THE MANDATE: get the go, then go dark until done
"Go" authorizes the **whole** run. Do **not** come back with "which URL?", "what should I
name it?", "mp4 or gif?", "should I proceed?". Unspecified detail → **you decide** (defaults
below) and go. A wrong guess is cheap and editable; a chat sitting idle waiting on a nudge is
the failure. The only thing you may surface before the artifact is done: that a genuinely
long render is in flight — one line, then quiet.

Deciding defaults (never ask, just pick):
- **Target URL** — from what they named. A product you're both working on → use that URL from
  context. Truly nothing nameable → state the one fact you're missing in a single line *and
  still start* on your best guess.
- **Name** — a short human title ("Checkout walkthrough").
- **Format** — `mp4` for "video/film/record", `gif` for "gif", stills for "pictures/
  screenshots". Ambiguous → `mp4`.
- **Mode** — `agent` (self-driving) unless they say they want to steer it → then `live`.
- **Visible** — `true` for anything they're watching (that's the point). `false` only for a
  silent background render.

## STEP 0 — reach Captur'd (two rails; pick whichever is wired)

**Rail A — MCP (preferred; it streams frames):** if you have tools named `demo.record`,
`demo.act`, `demo.export`, `capture.crawl` (server "DemoForge"), use them.

**Rail B — CLI (any harness with a shell):** if the MCP isn't wired, drive the same engine
through the `capturd` CLI (`capturd walk ...`, or `python -m capturd.cli walk ...` from the
Captur'd repo/venv). Agent mode + export work headlessly here; **live-drive is MCP-only**.

**Where the window appears:** the browser opens on **whatever machine runs the Captur'd
server**. For the "pops up on MY screen" experience the server must run on the user's box. If
your harness's MCP points at their machine, `visible:true` does it. If not, say so in one line
and fall back to a headless render they watch via the streamed frames + final file — don't
silently produce an off-screen video and imply it popped up.

To stand the server up (Rail A, if not connected): run `capturd serve` (or
`python -m capturd.mcp.server`, stdio) on the box with the display and register it as an MCP
server named `capturd`.

## PLAY 1 — Autonomous ("take a video of X"): agent mode

The agent drives itself to the goal; you narrate the stream.

**Rail A:**
1. `demo.record` → `{ url, name, goal, mode:"agent", visible:true }`. `goal` = a plain
   sentence of what to demonstrate ("Walk through adding an item to the cart and checking
   out"). Returns `sessionId`. The window is now up on their screen.
2. Tell them it's live in one line ("Captur'd is up on your other window, filming the
   checkout flow now"). While it runs you can poll `demo.status` and relay progress.
3. `demo.stop` → waits for the agent to finish, kicks off enrichment (annotations, voice,
   zoom camera). Poll `demo.status` until `enriched`.
4. `demo.export` → `{ demo_id, format:"mp4" }`. Blocks through the render; returns the path.
5. Deliver the file. Done.

**Rail B (CLI):**
```
capturd walk record --agent --visible --url <URL> --name "<Name>" --goal "<what to show>"
# prints sessionId + demoId; enrichment auto-kicks on stop
capturd walk export --demo-id <ID> --format mp4 --out <path>
```

Agent mode needs an OpenAI-compatible gateway on the host (`RHOBEAR_GW_API_KEY`, optional
`RHOBEAR_GW_BASE_URL`) to pick clicks and write narration. **No key?** Enrichment + export
still work keyless (deterministic zoom, selector-based captions, free Edge-TTS voice) — but
the *self-driving* needs it. If it's missing, switch to **Play 2** (you become the driver)
rather than stalling.

## PLAY 2 — Live-drive ("let me talk to it as it records") — the marquee one

The user types "click the house button top-left — that's the one — now type this", and you
make the video as they go. **MCP only.**

1. `demo.record` → `{ url, name, goal, mode:"live", visible:true }` → `sessionId`. The window
   pops up on their screen and waits for you.
2. For each thing they tell you, translate it to ONE `demo.act`:
   - "click the house button" → `{ session_id, action:"click", selector:"<the home link>",
     note:"This is the home button" }`
   - "type my email" → `{ action:"input", selector:"#email", value:"...", note:"Enter your
     email" }`
   - "scroll down" → `{ action:"scroll", value:"down" }`
   - "go to pricing" → `{ action:"navigate", value:"https://.../pricing" }`
   Each `demo.act` returns `{ stepIndex, url, pageTitle, frameBase64 }`. **Show that frame in
   chat** so they see the stream — that's the whole experience.
   - Resolving the selector is YOUR job, not theirs: they speak plain English ("the house
     button"); you read the returned frame + page title and pick a stable CSS selector (`#id`
     > `.class` > text). If a click misses, look at the next frame and try a better selector —
     don't bounce the question back to them.
3. `note` on an act, or a standalone `demo.narrate` `{ session_id, text }`, sets the on-screen
   caption + voiceover for that step ("That's the one — the home button").
4. When they're done ("that's it / render it") → `demo.stop`, wait for `enriched`,
   `demo.export mp4`, deliver.

## PLAY 3 — Just pictures (stills)

"take some pictures/screenshots of X" → `capture.crawl` `{ url }` (whole site, desktop+mobile,
light+dark) or `capture.rested` `{ urls:[...] }` for specific pages. CLI: `capturd shots ...`.
Returns an output folder. Deliver the shots (or the best few inline).

## STREAM INTO CHAT — don't go silent
They want to *watch it happen*, not get a wall of nothing then a file. As it runs:
- one line when the window comes up,
- the `frameBase64` images from `demo.act` (live), or a couple `demo.status` progress lines +
  a mid-run frame (agent),
- the finished MP4/GIF/stills at the end.
Keep the chatter tight — a picture and a short line beats a paragraph.

## VERIFY THE ARTIFACT — it's not done until you've seen it
Before saying "here's your video": confirm the file is real and right. Read a few frames of
the MP4 (ffmpeg extract at a couple timestamps) or open the export — is the zoom landing on
the thing they asked about, is it the flow they wanted? A path string is not proof. If the
agent wandered off the goal or a step is blank, fix it (`demo.regenerate` / `demo.trim` /
re-drive the bad step) before delivering. Report honestly: if a step broke, say so and say
what you did about it.

## Sellable by default
Nothing you generate bakes in the user's identity, keys, or payment info — anyone runs this
exact flow with their own env. Never hardcode a gateway key into a command you persist; it
lives in the host env.

## One-liners the user might say → what you do
- "turn on capturd and film the dashboard" → Play 1, agent mode, visible, mp4.
- "grab me pics of the pricing page" → Play 3, `capture.rested` on the pricing URL.
- "let me drive it — record while I click through onboarding" → Play 2, live mode.
- "make me a gif of the login" → Play 1, `format:"gif"`.
Never answer any of these with a question. Launch it.
