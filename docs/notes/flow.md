# Google Flow: how StudioWright drives it

Notes from StudioWright's own probes on a test profile (October 2026). They describe what
the code relies on, so a Flow update can be traced quickly. Flow changes often; re-measure
before trusting a detail here.

## Browser

- StudioWright starts the installed Chrome on a dedicated user-data directory with
  `--remote-debugging-port` on loopback (port `9300 + crc32(profile) % 90`) and attaches
  with Playwright's `connect_over_cdp`. Chrome is not launched by Playwright, so
  `navigator.webdriver` stays false and no automation switches are added.
- A minimized or occluded window stops rendering, which stalls Flow. Chrome gets
  `--disable-backgrounding-occluded-windows`, `--disable-renderer-backgrounding` and
  `--disable-background-timer-throttling`, and `sw window hide` moves the window
  off-screen (CDP `Browser.setWindowBounds`) instead of minimizing it.
- Chrome renders only the active tab of each window. A background tab reports
  `visibilityState: hidden` and runs no animation frames, so Playwright's clicks never see
  a stable element and time out. Flow and Flow Music therefore each get a window of their
  own (CDP `Target.createTarget` with `newWindow`, at the existing window's bounds).
- Playwright's download events do not fire on an attached browser. Downloads are captured
  with CDP `Browser.setDownloadBehavior` plus `downloadWillBegin` / `downloadProgress`.

## Safety decision

Flow protects generation and upload calls with reCAPTCHA Enterprise. A token minted from
the page was refused on the first try (`PUBLIC_ERROR_UNUSUAL_ACTIVITY`), and rewriting a
request the page signed would be circumvention. So:

- every token-bearing call is made by Flow's own UI, after StudioWright types and clicks;
- StudioWright never mints, replays, rewrites or forges such requests, never uses
  `page.route`, and never exports cookies;
- it only reads Flow's replies (`page.on("response")`) to learn ids and download URLs;
- token-free calls the page itself makes (project creation, `jHPbke`) may be sent from
  the page with its own session values.

## Wire facts StudioWright reads

- **batchexecute.** `POST /_/…/data/batchexecute` with
  `f.req=[[["<rpcid>", "<inner JSON as a string>", null, "generic"]]]&at=<csrf>`.
  Replies start with `)]}'` and carry rows `["wrb.fr", "<rpcid>", "<payload JSON>", …]`.
- **Session values.** `WIZ_global_data.FdrFJe` = `f.sid`, `cfb2h` = `bl`, `SNlM0e` = `at`.
- **Refusals arrive as HTTP 200.** The payload is null and an `ErrorInfo` reason is
  attached; code 7 with `PUBLIC_ERROR_UNUSUAL_ACTIVITY` starts a 30-minute cooldown.
- **RPCs seen.**

  | RPC | Role |
  | --- | --- |
  | `ogiZ0b` | image generation (one held reply, no polling) |
  | `maseQ` | upload |
  | `YhhmEf` / `eb1hJf` / `MZZa6b` / `nprQif` | video: text / start frame / ingredients / start+end |
  | `jwpduf` | video status poll (every ~5 s) |
  | `as29s` | fresh signed URLs (fetched by the edit view) |
  | `jHPbke` | project creation (token-free) |
  | `Zzl0ze` | the project's media list, loaded with the project page (2026-10-25) |
  | `SPrCad` | upscale behind a 2K/4K download; refused with code 7 / `PUBLIC_ERROR_UNUSUAL_ACTIVITY` after ~45 upscales in 37 min (2026-10-11) |

- **Image reply.** One tuple per image: `[media_id, _, workflow_id, …, [DETAILS, _, [w, h]]]`
  with a signed `flow-content.google` URL in DETAILS.
- **Video record.** `[media_id, project_id, workflow_id, …, DETAILS]`, status at
  `DETAILS[8][0]`: 6 queued, 2 running, 3 done. New clips are recognized by this shape in
  any reply, because ingredients mode never fetches the finished URL on its own.
- **Fresh URLs.** Opening `/project/<pid>/edit/<workflow_id>` makes the page fetch fresh
  signed URLs for the project's media.
- **Media list (`Zzl0ze`).** Workflow records
  `[workflow_id, null, null, [title, [s, ns], null, null, media_id, generation_id, …], project_id]`
  (uploads have no generation id) and media records
  `[media_id, project_id, workflow_id, null, null, DETAILS, IMAGE, VIDEO]` (a clip has the
  VIDEO block). The same reply also carries items of other projects. In a project of 68
  clips it was 272 KB.
- **Cancelled replies (2026-10-25).** The page cancels some replies once it has read them
  (`net::ERR_ABORTED` after the data arrived): the media list of a large project, and at
  times an image reply. Chrome keeps no body for a cancelled request, so reading it after
  the fact fails with "No data found for resource". StudioWright opens a DevTools session
  of its own and streams each wanted reply (`Network.streamResourceContent`), which keeps
  the data however the request ends. One request can also carry several RPCs
  (`rpcids=a,b`).

## UI facts

- Since 2026-10-11 the project page has a left sidebar (all media, images, videos,
  characters, scenes, uploads) and shows two large tiles per row. The grid is a virtual
  scroller (`.cdk-virtual-scrollable`): only ~16 tiles exist at a time, and a project of
  270 media is ~52,000 px tall. Tiles carry `img[data-media-id]` and their title as text.
  StudioWright scrolls that container itself (from where the medium should be, by its age
  among the project's media) rather than with the mouse wheel.
- The prompt box's own add button opens a picker with tabs and a search field. Search
  matches display names; uploads are named after the staged file (`sw-<sha256[:10]>.jpg`),
  so an old upload is attached by searching its name. Clicking a result attaches it.
- A medium's display name is the edit view's `input.editable-text-input`; typing a name and
  Enter saves it (the grid and `Zzl0ze` show it at once; the edit view may show the old name
  until the project is reopened). Generated media get an AI summary title by default (2026-10-26).
- `/project/<id>/edit/<media id>` opens a medium's edit view directly (a workflow id works
  too), so sized downloads and fresh URLs need no grid. Generated media offer
  `1K` / `2K` / `4K` there; uploaded images have no size menu and download at once.
- The interface follows the account's language (it switched to Turkish); selectors use
  icon ligatures, classes and tokens Flow does not translate.

- Icons are Material ligatures (`mat-icon` text), which do not change with the UI
  language, so selectors use them: `more_vert` → `add_2` attaches a reference,
  `motion_blur` (Animate) sets a start frame, `crop_free` / `chrome_extension` pick the
  frames / ingredients mode.
- Settings live in an overlay pane with radio groups (aspect, count, model, duration,
  resolution); the pane sometimes needs a second click to open.
- The end frame is set from the empty chip → the asset whose `/asb/<token>` matches the
  grid image → "add to prompt".
- Upload: Ctrl+U after blurring the composer, with the toolbar's add → upload as a
  fallback. Do not reload after an upload: a reloaded grid lags one upload behind.
- Sized downloads come from the edit view's download menu; entries are matched by their
  leading token (1K, 2K, 270p, 360p, 720p, 1080p, 4K). The button is disabled until the
  medium has loaded.

**Omni 1.2 Flash (2026-11-07).** The model menu lists it first. Each entry keeps the
model's name in `span.label`; Omni 1.1, Veo Fast and Veo Quality also carry a caption
(`span.caption`, "removed on 5 Dec"), so an entry's whole text no longer ends with the
name. Omni 1.2 picks its length with a slider (`flow-duration-slider`, a range input of
13 steps for 3-15 s, keyboard-operable) instead of the 4/6/8/10 s radios Omni 1.1 keeps.

## Prices and pacing

Upscales are paced on their own (60 s + jitter) and pause for an hour after a refusal;
the refusal is read from the `SPrCad` reply, because the page itself shows nothing.
Flow's credit balance is not shown as a number anywhere in the UI (only a "credits
running low" banner); StudioWright records it from video submit replies.

| Job | Flow credits |
| --- | --- |
| Image (any Nano Banana model) | 0 (daily quota) |
| Omni Flash, 360p, 4 s | 4 |
| Omni Flash, 720p, 4 / 6 / 8 / 10 s | 7 / 10 / 12 / 15 (not linear) |
| Omni 1.2 Flash, 720p, 3-10 s / 11-15 s | 3 a second / 6 a second (8 s = 24, 15 s = 60) |
| Omni 1.2 Flash, 360p, 3-10 s / 11-15 s | 1 a second / 2 a second (15 s = 20) |
| Veo Lite / Fast / Quality | 10 / 10 or 20 / 100 (Fast: 10 on one account, 20 on another, 25 Oct 2026; a start frame does not change the price) |

Prices are read from Flow's own settings pane before submitting (`--dry-run`,
`--max-credits`). Submits are spaced 20 s apart plus 0-6 s of jitter per profile, across
processes (SQLite).

## Measured times (warm tab)

| Job | Time |
| --- | --- |
| Image with one reference (upload, attach, generate, download) | 18-22 s |
| Nano Banana Pro 9:16 with a new upload | 27 s |
| 4 s clip with start and end frames | 30 s |
| Ingredients clip | 51 s |
| Veo Lite 9:16 | 61 s |
| 720p clip saved as 1080p | 70 s |
