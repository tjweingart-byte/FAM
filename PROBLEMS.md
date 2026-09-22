# Problems encountered, and how each one is solved

This is the honest engineering log for the project: every problem that came up
while building it, the solution that shipped, and the ones that are still open.

---

## 1. "It converted the search to an MP3 file" — the problem you hit last time

**Why that happens.** The obvious design is: generate script → synthesise the
whole thing → encode → write `episode.mp3` → hand the browser a URL. Every step
is blocking, so the listener waits for the *entire* episode before hearing the
first word. A 10-minute podcast means a 10-minute-ish wait, plus disk writes,
plus an ffmpeg/LAME dependency.

**Solution — never materialise a file.** The pipeline is:

```
Claude tokens → sentence buffer → TTS subprocess → raw PCM → HTTP chunk → speakers
```

- The TTS engine writes **raw 16-bit PCM** (`piper --output_raw`; espeak's WAV
  header is stripped in `audio_utils.strip_wav_header`). No encoder is involved
  at any point — there is no ffmpeg, LAME or `pydub` dependency in this repo.
- Those bytes go straight into a `StreamingResponse` (`app.py`).
- The browser reads the response with the fetch streams API and schedules each
  chunk on a Web Audio clock (`static/app.js`). No `Blob`, no object URL, no
  file.

**Result:** audio starts as soon as the *first sentence* is written — about a
second or two — and the rest is produced while the listener is already
listening. Measured here at ~200x realtime for synthesis, so the model is the
only thing anyone waits on.

### "If that is not possible…" — it is, but here's the tradeoff you're buying

Uncompressed PCM is bulky: **~2.6 MB per minute** at 22.05 kHz mono 16-bit, so a
10-minute episode is ~26 MB on the wire (measured). That's the price of not
encoding. It is the right trade for a stream you play once and discard, and
poor for something you'd store or re-download. If bandwidth becomes the
constraint, see §9 — you can halve or better it *without* reintroducing a file.

---

## 2. "Write a 6-minute podcast" produces wildly variable lengths

**Problem.** Duration language in a prompt is close to useless as a length
control. The same prompt yields 400 or 1,300 words on different runs.

**Solution — three independent mechanisms**, because none is sufficient alone:

1. **Word budget** (`script_generator.plan_episode`). Minutes × 150 wpm becomes
   a hard word range, handed to the model with a per-section breakdown. Section
   *count* also scales with duration, so a 10-minute episode gets real structure
   instead of one padded monologue.
2. **Pacing controller** (`audio_utils.PaceController`). Before *every* sentence
   it re-plans: given the audio already emitted and the words still unspoken,
   what rate lands exactly on target? Small misses vanish invisibly. Corrections
   are clamped to 115–185 wpm so the voice never becomes a chipmunk.
3. **Trim / top-up** (`pipeline.PodcastPipeline`). Over-long scripts are cut at a
   sentence boundary once the remaining time can't fit the next sentence.
   Short scripts trigger a second, small Claude call (`ScriptGenerator.top_up`)
   that continues the episode — which arrives while the listener is still
   hearing earlier material, so the top-up is free in wall-clock terms.

**Measured result** (real espeak synthesis, `tests/test_pipeline.py` plus a
live end-to-end run):

| Requested | Produced | Drift |
|---|---|---|
| 1 min | 60.68 s | +0.68 s |
| 3 min | 180.00 s | 0.00 s |
| 10 min | 600.00 s | 0.00 s |

Tests also assert this holds when the model misses its budget by ±30%.

---

## 3. Naive pacing can't absorb a long script (found by a failing test)

The first implementation used only mechanisms 1 and 2 above. A script 30% over
budget needed 196 wpm to fit — past the 185 wpm clamp — so a 5-minute request
produced **6 min 18 s** of audio. The clamp is not negotiable (faster is
unlistenable), so the fix was the sentence-boundary trim. Cutting mid-sentence
sounds broken; cutting *between* sentences is why the whole pipeline works in
sentence units.

**Residual issue:** a trimmed episode can end slightly abruptly, since the
model's planned closing line may be the thing cut. `stats.truncated` records
when this happened. A future improvement is to reserve ~10 seconds of budget for
a pre-written sign-off sentence that is always spoken last.

---

## 4. Naive pacing can't absorb a *short* script either

Symmetric failure: a 30%-short script left **64 seconds of dead air**. Padding
that with silence is not an answer at that scale. Solution is the top-up call
(§2.3). Capped at `MAX_TOPUPS = 2` so a chronically under-writing model can't
fan out into unbounded API calls; any residual gap under 6 seconds is closed
with room tone, which reads as "the episode ended" rather than as a bug.

---

## 5. Sample-rate mismatch — the silent, nasty one

**Problem.** The app assumed 22,050 Hz. espeak-ng happens to emit exactly that,
so everything worked *by luck*. Piper voices ship at 16,000 **or** 22,050
depending on the model. Feed 16 kHz samples to a player told they're 22.05 kHz
and you get audio that is pitch-shifted and ~27% short — and no test catches it,
because the byte counts still look plausible.

**Solution.** The engine is authoritative about its own rate, never the config
file. `PiperEngine` reads the `sample_rate` from the voice's sidecar JSON;
`EspeakEngine` reads it from the WAV header of its first synthesis. That rate
flows into the WAV header, the `X-Sample-Rate` response header the player reads,
and the `PaceController`'s timing maths.

---

## 6. Streaming a WAV whose length isn't known yet

A normal WAV header must declare total byte count, which doesn't exist until the
episode is finished. `audio_utils.streaming_wav_header` writes `0xFFFFFFFF` for
both size fields — the conventional live-stream trick that tells players "read
until the socket closes". This is what lets `fmt=wav` work in a plain `<audio>`
tag. The `fmt=pcm` path used by the web player skips headers entirely.

---

## 7. Claude and the TTS engine run at wildly different speeds

The model produces text in bursts; the engine synthesises ~200x faster than
realtime. Coupling them directly means one repeatedly blocks the other.

**Solution.** A bounded `asyncio.Queue` (depth 4) between them. The model writes
ahead while the current sentence plays, and the small depth caps memory at a few
seconds of audio *regardless of episode length* — a 10-minute episode uses the
same memory as a 1-minute one.

---

## 8. Assorted smaller problems

| Problem | Solution |
|---|---|
| Model emits `**bold**`, `[intro music]`, `Host:` — all read aloud as literal noise | `clean_for_speech()` strips markdown, bracketed stage directions and speaker labels before synthesis |
| 16-bit samples straddle chunk boundaries; a split sample becomes a click | Player carries the odd byte over to the next chunk (`leftover` in `app.js`) |
| Network stall pushes the Web Audio play head into the past, silently dropping chunks | Schedule at `max(playHead, currentTime + 0.05)` |
| Browsers start `AudioContext` suspended | Created inside the click handler and explicitly `resume()`d |
| An error *after* streaming starts can't become an HTTP 500 | Logged and the stream closed cleanly; player keeps what it has. Errors *before* the first byte are still proper JSON errors |
| Listener closes the tab; generation keeps burning tokens | `request.is_disconnected()` checked between chunks, generation abandoned |
| One request holds a Claude stream + TTS process open for minutes — trivially abusable | Per-IP rate limit (`RATE_LIMIT_SECONDS`, default 3 s) |
| A model ignoring the budget entirely could emit an hour of audio | Hard safety valve at 1.35x the word budget in `stream_sentences` |
| `stop_reason: "refusal"` would otherwise yield silence | Detected and turned into a spoken explanation |
| Nothing works with no TTS installed, making the pipeline untestable | `DebugEngine` emits a duration-accurate tone, so timing, transport and player are all testable offline. `/api/health` reports it so it can't be mistaken for a real voice |

---

## 9. Reducing output time and compute further

Already in place: streaming-first architecture, `effort: "low"` (script writing
isn't a hard reasoning task, and effort costs time-to-first-token directly),
sentence-level pipelining, bounded memory, top-up calls that skip web search
since research is already done, and abandonment on disconnect.

If you need more:

**Where the time actually goes.** Measured: TTS is ~330x realtime (15 ms for a
5-second sentence) and the model outputs text ~16x faster than the listener
consumes it. So after the opening sentence, *nothing downstream is ever the
bottleneck* — the script finishes long before playback does. The only latency
that matters is **time to first audio**, and that is almost entirely the model:
web search (~2-6 s) + thinking tokens + the first sentence. Optimising anything
else is optimising ~2% of the wait. Two consequences:

- Parallelising script generation across sections would not help. The script is
  already far ahead of the listener.
- **Prompt caching is not worth it here** (correcting an earlier note): the
  stable prefix is ~172 tokens, well under the 512-4096 token minimum cacheable
  prefix, so it would never cache.

**Latency — the levers that actually move it**
- **Cold-open in parallel.** Have `claude-haiku-4-5` write a one-sentence opener
  with no tools while the main model researches. Speech starts in well under a
  second and the research latency disappears behind it. Biggest single win.
- **Skip web search when it isn't needed.** It is the largest share of
  time-to-first-audio. `ENABLE_WEB_SEARCH=0` for evergreen topics, or expose it
  as a "use live sources" toggle in the UI.
- **`claude-sonnet-5` or `claude-haiku-4-5`** for the script pass: cheaper and
  noticeably faster to first token. One env var (`MODEL`).
- **Pre-warm the TTS process.** Measured: subprocess spawn is 9.4 ms, 62% of
  espeak's per-sentence cost but only ~1.6 s spread across a whole 10-minute
  episode — so it is irrelevant for espeak. It is *essential* for piper, which
  reloads its ONNX voice on every spawn. Use a persistent worker fed over a pipe
  if you switch to piper.

**Throughput and cost per episode**
- **Shared script cache — implemented** (`cache.py`). See §11.
- **Batch API (50% cheaper)** for pre-generating popular or scheduled episodes
  ahead of time. Not for interactive requests.

**Server capacity.** At ~330x realtime, one CPU core covers roughly 300
concurrent listeners' worth of synthesis. The app is I/O-bound; the TTS
subprocesses are the only real CPU load, and queue depth caps memory per stream
regardless of episode length.

**Bandwidth / compute** — all of these keep the no-file architecture:
- Drop to 16 kHz mono: ~1.9 MB/min instead of 2.6, with little quality loss for
  speech.
- Opus over WebSocket or MediaSource: ~0.2 MB/min, a ~13x reduction. This is
  still a *stream*, not a file — but it does add an encoder dependency, which is
  exactly what you asked to avoid, so it's opt-in rather than default.
- Cache generated scripts by `(query, minutes)`. The expensive part is the model
  call; re-synthesising audio is nearly free at 200x realtime.

**Cost**
- Web search dominates token cost. `ENABLE_WEB_SEARCH=0` for evergreen topics.
- `MAX_WEB_SEARCHES` caps the research budget per episode.

---

## 10. Known limitations / not yet done

- **No live API key was available in the build environment**, so the Claude path
  is exercised against a scripted fake generator and the espeak path against
  real synthesis. The SDK call shape follows the current Anthropic Python SDK
  (streaming, `output_config.effort`, `web_search_20260209`), but the
  first real run should be watched.
- **espeak-ng sounds robotic.** It is the zero-dependency default. Install a
  Piper voice for a natural one — the code path is already there and selected
  automatically.
- **The rate limiter is in-process**, so it resets on restart and doesn't work
  across multiple workers. Use Redis for a real deployment.
- **No authentication.** Anyone who can reach the server can spend your tokens.
- **Trimmed episodes can end abruptly** (§3).
- **No seeking or pause.** The player is a live stream; adding transport
  controls means buffering the whole episode client-side, which is a deliberate
  tradeoff against the current instant-start design.


---

## 11. Sharing one output between different users

Implemented in `cache.py`. Two design decisions and three problems it raised.

**Cache the script, not the audio.** A 10-minute script is ~9 KB; the same
episode as PCM is ~26 MB — 2,900x larger. Re-synthesis runs at ~330x realtime,
so storing audio buys almost nothing and costs a great deal of disk. Audio also
can't be shared across durations, whereas the text is the part that cost money.

**SQLite, not an in-process dict.** The users are different people, so the hit
has to be visible to whichever worker serves the next request. SQLite in WAL
mode gives cross-process sharing and restart persistence with no new dependency.
Swap in Redis (same two methods) if you outgrow one machine.

### Problem: equivalent phrasings miss each other

"Give me a recap of week 5 of the NFL season" and "Week 5, NFL season — recap"
are the same request. Keys are therefore normalized: lowercased, punctuation
stripped, filler words removed, remaining tokens sorted.

This is lexical, so it has a real limit — "NFL week 5 recap" vs "NFL **season**
week 5 recap" differ by one meaningful word and still miss. There is a test
documenting exactly this. `CACHE_SEMANTIC_KEY=1` closes the gap with a small
model that canonicalises the topic first; the tradeoff is ~400 ms added to every
request, which is a win when traffic concentrates on popular topics and a waste
on a long tail of unique queries. Off by default.

### Problem: staleness is worse than slowness

Serving a cached "latest news" from six hours ago is a worse failure than making
someone wait. TTL is therefore query-dependent: queries containing volatile
markers ("latest", "today", "now", "breaking", "score") get 15 minutes; the rest
get 24 hours. A recap of a *completed* event is a perfect cache candidate; the
same query asked mid-event is not, which the volatile-word list only partly
catches. A classifier call would do better, and is the natural upgrade.

### Problem: an over-eager privacy filter silently disabled the cache

The first version treated "me", "I" and "we" as personal markers. That reads
sensibly and is badly wrong: "give **me** a recap of week 5" is not a personal
query, and the filter meant most real traffic bypassed the cache entirely. It
was caught by an end-to-end run showing two identical requests both missing, not
by the unit tests. The filter now matches only possessives ("my", "our"), email
addresses and phone numbers. There is a regression test for ordinary phrasing.

Anything matching is generated fresh and never stored — the bias is towards not
sharing.

### Measured

Three differently-worded requests for the same episode: **one model call, two
cache hits**. A fourth request containing "my" was regenerated and left out of
the store. On a hit the API cost is zero and time-to-first-audio drops from
seconds to the cost of one sentence of synthesis (~15 ms).

### Still open

- Cache keys include `minutes`, so a topic is generated up to 10 times. Serving
  shorter episodes by trimming a longer cached one would collapse that, at some
  cost to structure — a 3-minute episode is not a truncated 10-minute one.
- No cache warming. Pre-generating predictable episodes (this week's recap, the
  morning briefing) via the Batch API at 50% cost would make the common case a
  hit for everyone.
- `purge_expired()` exists but nothing calls it on a schedule.


---

## 12. "It generates an episode but I can't hear anything"

Reported from a real run, reproduced, and fixed. There were **two** ways the app
could produce a silent episode that every layer treated as a success.

**Cause 1: a failure before the first audio byte became a silent HTTP 200.**
The original code wrapped the whole stream in a catch-all whose comment read
"the response has already begun, so an error cannot become a 500". That is true
*after* the first byte — and false before it. So a rejected API key made the
Claude call raise, the handler logged it and closed the stream, and the browser
received `200 OK` with zero bytes. The player dutifully reported "Episode
complete" and played nothing.

Reproduced exactly:

```
$ curl -o /dev/null -w "%{http_code} %{size_download}\n" ".../api/audio?q=...&fmt=pcm"
200 0
```

**Fix.** The handler now pulls chunks until real audio exists *before* returning
a response. A failure during that window becomes a proper `502` with a message
naming the cause. The same request now answers:

```
502 {"error": "Claude rejected the credentials. Set ANTHROPIC_API_KEY in .env
     (or run `ant auth login`) and restart the server."}
```

`friendly_error()` maps auth, permission, model-not-found, rate-limit and
connection failures to something actionable rather than a stack trace.

**Cause 2: an empty script was padded into a silent "valid" episode.**
Found by a test written for cause 1. When the model returned nothing, the
end-of-episode room tone still ran, so the pipeline emitted a few seconds of
silence — enough bytes to look like a real episode to the priming check, the
`Content-Length`, and the player. The pipeline now refuses to pad an episode
containing no speech, and the endpoint rejects on `stats.sentences == 0` rather
than on a byte count, because silence is bytes but is not an episode.

**Also fixed, on the player side.** It now counts received bytes: zero bytes
raises a visible error instead of "Episode complete", and a stream ending under
half the expected length says so. The health check disables the Listen button
outright when the server reports no credentials, so the failure is visible
before anyone waits on a generation.

**Verified in a real browser** (Chromium, Web Audio instrumented): 106 buffers
scheduled, 180.2 s of audio, peak amplitude 0.84. The player was never the
problem — it was faithfully playing an empty stream.

**Regression tests** in `tests/test_app.py` cover both causes across both output
formats: a generator that raises, and a generator that yields nothing, must each
produce a 502 rather than a playable silence.

### Follow-up: the disabled button looked like a spinner

Reported with a screenshot. Two more UI problems, both mine:

1. **`button:disabled { cursor: progress }`** meant hovering the disabled Listen
   button showed a spinning wheel — which reads as "working on it" when it
   actually means "you cannot press this". Now `cursor: not-allowed`, with
   `progress` moved to a `.working` class applied only while an episode is
   genuinely being generated.
2. **Two health warnings overwrote each other.** `say()` replaces the status
   text, so when both the API key and the speech engine were missing, only the
   *second* message survived. The screenshot showed "No speech engine installed"
   while the button was disabled for an entirely different reason — the missing
   key. Notices are now collected and rendered as a list: blocking problems in
   red, warnings in amber, and the disabled button carries a `title` explaining
   itself on hover.

### If you still hear nothing

1. `curl localhost:8000/api/health` — check `api_key_configured` and
   `tts.selected`. If `selected` is `"debug"`, no speech engine is installed and
   you will hear a quiet placeholder tone, not a voice: install `espeak-ng`.
2. Watch the server log while you press Listen. Every episode logs a line with
   `words`, `sentences` and `cache`; `sentences: 0` means the script was empty.
3. Check the browser console and the status line under the form — real errors
   now surface there in red.
4. Check your system volume and that the tab is not muted. The stream is played
   through Web Audio, so it obeys the tab's mute state.


---

## 13. You cannot evaluate the audio approach without an API key

The most useful failure of the whole build. Every blocker reported so far — the
silent episode, the disabled button, the spinner — had the same root: the app
required Claude credentials before it would produce a single second of sound. So
the question "does instant streaming audio actually work?" could not be answered
without first solving an unrelated setup problem.

That is backwards. The audio pipeline is the risky, novel part; the model call is
the routine part. The risky part should be the easiest to try.

**Demo mode** (`demo_script.py`). With no credentials the server now runs on a
built-in sample script instead of refusing. Everything downstream of the writer
is real: the same sentence streaming, the same pacing controller, the same
raw-PCM transport, the same player. `/api/health` reports `"mode": "demo"` and
the interface says so plainly, so nothing is mistaken for real output.

Verified with no key present: a 1-minute request produced 60.00 s and a 3-minute
request 180.00 s, and in Chromium the button was enabled, 86 buffers were
scheduled, and peak amplitude was 0.80.

**macOS `say`** (`tts.SayEngine`). Requiring a Homebrew install of espeak-ng
before hearing anything was a second unnecessary gate; every Mac already ships a
speech engine, and it sounds considerably better. It is now auto-detected ahead
of the placeholder tone. `say` needs a seekable destination for a WAV container,
so each sentence goes to a scratch file that is read and deleted immediately —
a per-sentence temporary of a second or two, not an episode file. No encoding
happens and nothing is assembled on disk, so the architecture is unchanged.

**A footgun this exposed.** `PodcastPipeline(cache=None)` used to mean "build the
default cache", so a caller passing None to switch caching *off* silently turned
it on. It caused two separate test failures — the second time as false passes,
where a cached script from an earlier test made a deliberately failing generator
return 200. `cache` now takes a store, or the explicit `AUTO` sentinel to build
the configured one, or `None` to disable. Ambiguous defaults that read as their
own opposite are worth deleting the moment they mislead once.

Untested caveat: `say` could not be exercised here (this build machine is
Linux). The engine falls back automatically if it fails, and the failure would
be visible in `/api/health`.


---

## 14. Demo mode was mistaken for a broken engine

Reported after the prototype integration: "I typed in a search, it did not
generate an episode, all it did was talk about the process." Everything was
working exactly as designed, which is what made the report so useful.

The server had no `ANTHROPIC_API_KEY`, so it was in demo mode and playing the
built-in sample script — which happens to describe the audio pipeline. From the
listener's seat that is indistinguishable from "the generator was never wired
in": you type a question and hear something that is not an answer to it.

The signal existed (a banner above the phone) but was in the wrong place. The
listening happens *inside* the phone, on the player screen, and that is where
the state needed to be visible. Demo mode is now labelled in three places: the
banner, a `SAMPLE SCRIPT` badge beside "Now playing", and the loading overlay,
which reads "Playing the built-in sample script…" instead of the normal text.

**Proving the live path, without credentials.** The deeper problem was that the
live path had never actually run — no key had existed in any environment all
session, so "the engine is wired in" was an inference rather than an
observation. A stand-in server that speaks the real Anthropic streaming wire
protocol now makes that testable: pointing `ANTHROPIC_BASE_URL` at it exercises
the genuine `ScriptGenerator`, the real SDK, real SSE streaming and real
sentence assembly.

Result: a request for "the 1969 moon landing" produced a script about the 1969
moon landing, at 150 words against a 150-word budget, and in the browser the
banner read "Live — briefings written by claude-opus-5" with no sample-script
badge. The engine is wired in; only the credentials were missing.

The lesson worth keeping: a fallback that is *useful* is also a fallback that is
*confusable*, and the label belongs where the user's attention is, not where it
was convenient to put it.


---

## 15. Five seconds of audio, then five seconds of dead air

Reported from the first real generated episode. Playback started almost
instantly, ran for about five seconds, went silent for about five, then resumed
and completed normally.

**Cause: buffer underrun between the cold open and the main script.** The
opener (§9) exists to cover research latency, but it was a single sentence -
roughly five seconds of speech - while a researched Claude call can take ten
seconds or more to produce its first sentence. When the opener ended, there was
nothing left to play, so the listener heard the shortfall as silence. Audio,
gap, audio sounds broken in a way that simply waiting does not.

**Reproduced deterministically** with a stand-in API that stalls a configurable
number of seconds before responding, and a detector that compares audio arrival
against audio consumption - a listener hears a gap exactly when the audio
delivered so far is shorter than the time elapsed since playback began:

```
BEFORE  research takes 15s
  time to first audio : 0.20s
  GAP: 9.9s of dead air, 5.2s into playback
```

**First attempt, rejected.** Holding the opener until the main script was ready
removed the gap completely - and pushed time to first audio from 0.2s to 15.3s.
That trades away the property the whole architecture exists for.

**The fix: an adaptive opener.** The opener is now written as up to four short
framing sentences, released one at a time, with a check before each one for
whether the main script has arrived. The moment it has, the opener stops and the
briefing takes over seamlessly. Slow research gets more introduction; fast
research gets almost none; unused sentences are discarded. Each sentence is
prompted to stand alone, since playback may cut away after any of them.

```
AFTER   research takes 2s / 8s / 15s
  time to first audio : 0.51s / 0.49s / 0.48s
  no gaps in any case
```

Three guards around it: at least one sentence always plays (the main script is
written on the understanding that the episode has already been opened, so
without it the briefing starts mid-thought); nothing is spoken at all until the
script has had a moment to fail, so a bad key still produces a clean 502 rather
than an introduction to an episode that never comes; and the opener will not run
past `COLD_OPEN_MAX_SECONDS`, after which a gap is better than endless preamble.

**Still open.** There is no client-side jitter buffer. On localhost the server
now guarantees continuity, but across a real network a stalled connection can
still starve the player. A pre-roll of a few hundred milliseconds in
`fam-audio.js` would absorb that, at a small cost to time-to-first-audio.


---

## 16. Transport controls: why skip needed a different player

Requested: working pause, playback speed, skip forward/back 15 seconds, length
changes from inside the player, and a working Go Deeper.

**Skip was the one that forced a redesign.** The player scheduled each incoming
chunk onto the audio clock and then forgot it, which is the cheapest way to play
a live stream and makes seeking impossible: there is nothing behind you to go
back to, and nothing to re-schedule differently.

The player now keeps every sample it has received in a growing buffer and drives
playback from a position cursor. Skip, scrub and speed all become the same
operation - stop what is queued, move the cursor, re-schedule from there.
Samples are kept as Int16 (~2.6 MB per minute) and converted to float only for
the quarter-second slice being scheduled, which halves what a long episode
holds.

Position is derived from the audio clock rather than counted separately, so it
stays correct across pauses for free: suspending an `AudioContext` stops its
clock advancing.

**Length is not a playback setting.** Speed changes what is already playing;
length changes what the episode *is*. Changing it from the player therefore
regenerates rather than adjusting anything locally.

**Go Deeper was already carrying the typed prompt** - the pipeline was just
ignoring it in favour of the display title. It also needed the parent topic
attached: "focus on the economics" is not a briefing request on its own, so the
query sent is now `<follow-up> (following up on: <parent topic>)`.

**Forward skip has a real limit** and says so. The episode is still being
written, so you cannot skip into audio that does not exist yet; skipping past
the end of what has arrived reports "that is as far as the episode has been
written" rather than silently doing nothing.

Verified in Chromium against a live server: pause froze position at 7.27s and
resume advanced it; back-15 from 8.71s clamped to 0s and forward-15 landed on
14.98s with playback continuing; 2x advanced 4.02s of audio in 2s of wall clock
and 1x advanced 2.01s; a length change from 2 min to 1 min regenerated and the
timer showed 1:00; and Go Deeper sent
`focus on the economics of early radio (following up on: the history of radio)`.


---

## 17. Five issues from the 29/08 field notes

**Skipping forward past the written script froze the player.** Reproduced with a
slow-streaming stand-in server: skipping to the edge of received audio left the
cursor exactly where no audio existed, so playback stopped dead and further
skips appeared to do nothing - position pinned at 27.29s across three
consecutive presses.

The cursor now stops two seconds short of the written edge while an episode is
still streaming, so there is always audio left to keep playing. After the fix
the same test clamps at 25.76s and *keeps advancing* (25.76 -> 28.16 -> 30.56)
as more arrives. The progress bar also grew a faint second track showing how
much has been written, so the limit is visible rather than only felt, and the
message changed from "that is as far as the episode has been written" to
"caught up with the writing - it will keep going", which is what actually
happens.

**A gap could still open between the opener and the script.** The adaptive
opener (§15) covers only as much time as the sentences it was given. If the
script took longer than that, silence returned. The opener is now refilled -
up to twice - when it runs dry while the script is still being written.

**The opener was generic and repetitive across episodes.** It was prompted only
to "frame what is about to be covered", which produces the same shape every
time; a listener working through several episodes hears a template. It is now
prompted to be specific to the topic - what is unsettled, why someone would ask
now - and the server keeps the last few openers and instructs the model not to
reuse their wording, rhythm, or opening move. "Here is your briefing" and
similar are explicitly banned.

**No sense of time scope.** Asking for "the Tour Championship update" could not
distinguish this morning's state from this moment's, because *the prompt never
said what time it was*. The model had no clock. Requests now carry the current
date and time, with instructions to prefer the newest information, say out loud
what the picture is as of, describe what changed during the day in order, and
state plainly when something is still in progress rather than implying a result.

**Go Deeper had the wrong scope.** The follow-up was being glued onto the query
as one run-on string, so the model treated it as a fresh topic and re-explained
the ground the listener had just heard. What was already covered now travels as
a separate `context` parameter, and the prompt tells the model to treat it as
known, not re-introduce the subject, and spend the whole episode on the narrower
point. It is also part of the cache key, since a follow-up is a different
episode from the same words asked cold.

**A bug found while fixing these.** The opener refill called `cold_open(plan)`
from a method that did not take `plan`. It did not raise cleanly - it hung the
test suite - which is a good argument for passing dependencies as arguments
rather than stashing them on `self`, which is what the code now does.


---

## 18. Pinning the Anthropic client to HTTP/1.1

Reported: `APIConnectionError: Connection error` on every live request, with
`curl` succeeding only under `--http1.1`, so HTTP/2 was suspected.

**What the code does now.** All Anthropic clients are built by
`anthropic_client.build_async_client()`, which passes `http2=False` to
`anthropic.DefaultAsyncHttpxClient`. The SDK subclass is used rather than a raw
`httpx2.AsyncClient` so its timeouts, connection limits and TCP keep-alive
settings survive; only the protocol version is overridden. `/api/health` reports
the version in force, and `ANTHROPIC_HTTP2=1` opts back in - raising a named
error if the optional `h2` package is missing, rather than failing as another
opaque connection error.

**A finding worth stating plainly, because it affects the diagnosis.**
`anthropic` 1.x runs on **httpx2**, not httpx, and `httpx2.AsyncClient` already
defaults to `http2=False`. The SDK never sets it. And `h2` is not installed by
this project, without which httpx2 cannot negotiate HTTP/2 at all. So the Python
client was almost certainly *already* using HTTP/1.1 before this change, even
though `curl` was negotiating HTTP/2 by default and failing.

That means pinning is worth doing - it states the requirement in code, survives
a future default change, and is now asserted by tests - but it should not be
assumed to be the fix. If the error persists, the cause lies elsewhere.

**So the change ships with a diagnostic.** `diagnose_api.py` reports library
versions, whether `h2` is present, the pinned protocol, the proxy and CA-bundle
environment, and - on a real request - the full `__cause__` chain that
`APIConnectionError` hides, with the common causes named: DNS failure, TLS
interception (`SSLCertVerificationError`, fixed with `SSL_CERT_FILE`), a proxy
refusing CONNECT, or a timeout indicating traffic is dropped rather than
refused.

**Tests** (`tests/test_http_client.py`) assert on the real transport pool
(`client._transport._pool._http2 is False`), not merely on the flag passed in;
that the generator used in production is built through the pinned path; that SDK
defaults are preserved rather than replaced; and that enabling HTTP/2 without
`h2` fails with a clear message.

Also corrected: `requirements.txt` listed `httpx`, which the Anthropic SDK does
not use. It is needed only by `fastapi.testclient`, and now says so.


---

## 19. Voice selection

The first step towards a shippable v1: the espeak fallback sounds robotic, and
for an audio product that is the first thing anyone judges.

`GET /api/voices` enumerates what the machine can actually speak. Each engine
reports its own: espeak offers a curated six accents (it exposes hundreds of
variants, and offering all of them is worse for a listener than offering a few
that sound genuinely different), macOS `say` parses `say -v ?` and puts the
long-form-friendly voices first, Piper reports its configured model. Voice ids
carry their engine (`say:Samantha`), so an id alone routes the request.

**Voice is not part of the script cache key, on purpose.** A voice changes the
audio, not the words, so the same script serves every voice. Measured: two
voices for one query produced different audio, identical duration, and **one
model call** - the second request was a cache hit. Switching voice on an
existing episode takes ~90 ms against ~1.2 s for a fresh topic, and costs
nothing.

An unavailable voice falls back to the best engine present rather than failing:
a listener whose chosen voice was uninstalled should still hear their episode.

**Untested here:** the macOS `say` voice list could not be exercised on this
Linux build machine. The parsing is defensive and the engine falls back if it
returns nothing, but the first Mac run should be watched.


---

## 20. Making the voice part of the app rather than the host

Until now the voice was whatever the machine happened to have: macOS `say` on a
laptop, espeak-ng on Linux if someone apt-installed it, a placeholder tone
otherwise. That is fine for a prototype and wrong for a product, and it breaks
in a specific way at exactly the wrong moment: **deploying to a Linux container
would have made the app sound worse than it does locally**, because `say` does
not exist there.

Piper is now a real dependency (`piper-tts` in `requirements.txt`) with voice
models installed into `voices/` by `python setup_voices.py`. The engine ships
with the app; only the model files are fetched, and they are fetched by a
command in the project rather than assumed to exist.

**Two things the implementation has to get right.**

*Load the model once.* A Piper voice takes about a second to load. The old
implementation shelled out to a `piper` binary per sentence, which would have
reloaded the model roughly 170 times in a ten-minute episode. Loaded voices are
now cached for the life of the process. (§9 measured subprocess spawn as 62% of
espeak's per-sentence cost while being irrelevant in absolute terms; for Piper
the same mistake would have been fatal.)

*Do not block the event loop.* Inference is synchronous CPU work. Run directly
it would stall every other listener for the duration of every sentence, so it
runs via `asyncio.to_thread`. There is a test asserting synthesis does not
happen on the main thread.

**Hosted neural voices were considered and not adopted.** They sound best, but
bill per character, which can dwarf the model cost, and add a network dependency
to the one part of the pipeline that is currently free and instant.

### What is not verified

The build machine cannot reach huggingface.co, where the voice models are
hosted, so **the ONNX inference itself has never been run here.** Everything
around it is tested against a stub: discovery, naming, routing, model caching,
rate control, sample rate, thread offloading, and fallback when a voice is
missing. `python verify_voice.py` closes the gap on a real machine - it
synthesises a sentence with every installed voice and reports duration, sample
rate and realtime factor, with `--save` to write a WAV to listen to.

If the models cannot be downloaded on the target machine either, the app keeps
working on the existing fallbacks and says so; it does not fail.


---

## 21. The gap, properly understood

Reported as a consistent 5-7 second pause near the start - "appears to the
viewer as a glitch". §15 and §17 had each improved this without eliminating it,
because both fixes were aimed at the wrong variable.

### Finding the real mechanism

Two hypotheses were tested and killed first. Piper is far slower to synthesise
than espeak (~10x realtime against ~330x), so that looked likely - but sweeping
simulated synthesis speed from 10x down to 3x produced no gaps at all, because
anything faster than realtime keeps the stream ahead. And time-to-first-audio,
while it did rise from 0.5s to ~2.8s with a neural voice, is a delay before
playback, not a hole in it.

The pipeline was then made to report on itself: per-sentence synthesis time, and
a running comparison of audio produced against wall clock consumed - which is
exactly when a listener hears silence. With a realistically short opener and a
30-second researched call, it named the mechanism immediately:

```
opener ran dry after 6.8s; refilling
opener ran dry after 13.7s; refilling     <- the second and last refill
STARVED after 31.2s: only 29.5s of audio made in 31.2s of wall clock
GAP: 7.9s of dead air, 20.4s into playback
```

`MAX_OPENER_REFILLS = 2` covered about twenty seconds. Research took thirty. The
listener heard the difference - and because the cap was fixed, so was the gap,
which is why it was *consistent* at 5-7 seconds.

### The control law

Removing the cap fixed the gap and created a worse problem: the opener produced
**75 seconds of preamble to cover a 30 second wait**. Audio is synthesised many
times faster than it is heard, so any wall-clock budget lets the opener sprint
ahead of the listener.

The opener is now paced to the listener rather than the clock. It speaks only
while the audio produced is less than `OPENER_HEADROOM_TARGET` (8s) ahead of the
wall clock, then waits. That is precisely the buffer needed never to fall
silent, and it self-regulates to roughly real time. Top-ups are triggered by
seconds of speech held rather than sentence count, and fetched *before* the
buffer drains, so an API call never interrupts speech.

Measured, at 8x-realtime synthesis:

| Research takes | Gaps | Opener fetches | Opener spoken |
|---|---|---|---|
| 10s | none | 0 | one batch |
| 30s | none | 3 | 36s |
| 45s | none | 4 | 49s |

### What this does not fix

**A slow researched call still means a long introduction.** If research takes
thirty seconds the listener must hear thirty seconds of *something*, and that
something is preamble. The gap is now structurally impossible up to a 60 second
ceiling, but the cure for a long opener is a faster script, not a longer opener:

- `MAX_WEB_SEARCHES` is now 3 rather than 5. Each search costs seconds.
- `ENABLE_WEB_SEARCH=0` removes the dominant cost for evergreen topics.
- A faster model (`MODEL=claude-sonnet-5`) reduces time to first sentence.
- On the browse surfaces, prefetching removes the wait entirely (`CLAUDE.md`).

Every episode now logs `first_audio_at`, `opener_fills`, `min_headroom` and
`starved`, so this is diagnosable from a log line rather than by ear.


---

## 22. Voice models were being re-downloaded for every new version

Each release was handed over as a fresh `fam-podcast N` folder, and voice models
lived in `voices/` *inside* it. So moving from version 6 to version 7 looked
like a fresh install: no voices, download sixty megabytes again. The models are
large and change far less often than the code, so tying their lifetime to the
code's was simply the wrong choice.

They now live once per user in `~/.fam/voices`, resolved in one place
(`voice_store.py`) that every entry point uses - the server, `setup_voices.py`,
`verify_voice.py` and the tests - so they cannot drift apart. `FAM_VOICES_DIR`
overrides it for deployments and tests; the older `VOICES_DIR` is still honoured.

**Existing downloads are adopted, not re-fetched.** On startup, if the shared
store is empty and an older project folder has voices, they are copied across.
Three properties make that safe to run automatically on every launch:

* **Copy, not move.** The old folder keeps working if anything goes wrong, and
  can be deleted whenever the user chooses. `--move-legacy` moves instead.
* **Written via a `.partial` name, then renamed.** An interrupted copy can never
  leave a half-written model that looks installed.
* **Idempotent, and never overwrites.** It only runs when the shared store is
  empty, and skips any voice already present. A model whose `.onnx.json` sidecar
  is missing is skipped rather than half-adopted, since Piper needs both.

The result is the workflow that was asked for: unzip, install requirements, run.
No voice step at all after the first time.


---

## 23. The opener was filler, and the scripts were worse

Reported bluntly and correctly: the first thirty seconds were "I'm telling you
what I'm going to tell you about", and the briefings themselves were "poor,
sometimes inaccurate, and not interesting to listen to".

### The opener

It was filler *by construction*. Its prompt said: "State NO facts, figures,
dates, names, results or opinions... Frame the question; never answer it." Given
that instruction there was no good version of it - and §21, which made it longer
so it could cover slower research, made the experience worse rather than better.
It is now off by default. Nothing plays until the real briefing does.

A concrete bug was also found while investigating: when an opener refill fails,
the pipeline fired up to twelve rapid retries and then went silent - one
sentence, then nothing, which is exactly what was reported. The burst may have
been causing the failures itself by tripping a rate limit.

### The scripts

The real problem, and the one that had gone unexamined while the plumbing got
all the attention. Three lines of the brief were doing the damage:

* **"Length contract - this is the most important requirement."** The model was
  told, in as many words, that hitting a word count mattered more than being
  worth hearing. So it padded.
* **"a one-line hook (about 146 words)."** Incoherent: an instruction to inflate
  a single line into a paragraph.
* **A fixed five-beat template** applied to every topic. For a golf recap, "the
  main debate or open question" is a section with nothing in it, so the model
  invented something. That is where inaccuracy came from - not a hallucinating
  model, but a prompt demanding content that did not exist.

Both prompts are rewritten. The system prompt now describes what makes a
briefing good - open on the most concrete thing you know, prefer one exact
detail to three general statements, cut anything the listener could have
guessed, let the material choose the shape, never fill a gap with something
plausible. The brief gives a length as "the listener's time, not a quota", with
an explicit instruction to finish early rather than pad.

### The tension this exposes

**Duration control and content quality were fighting each other.** Enforcing a
word count in both directions guarantees padding whenever a topic has less to
say than the slider asks for. The length is now a ceiling: over-runs are still
trimmed, but a short script ends rather than being topped up (`ALLOW_TOPUPS=1`
restores the old behaviour). A three-minute briefing worth hearing beats a
five-minute one stretched to fill the slider.

### Still unverified

The rewrite cannot be judged from here - it needs reading against real queries,
and the judgement is editorial rather than technical. `write.py` prints a script
in seconds without generating audio, which is the loop for improving it.


---

## 24. Optimising the wait instead of removing it

The product is one sentence: type a question, hear the answer within about a
second. Sections 15, 17, 21 and 23 are all elaborate machinery for *disguising*
a twenty to thirty second wait - an adaptive opener, refills, a pacing control
law, a headroom target. None of them asked why the wait existed.

It existed because every query went through the slowest model with live web
search attached. Web search front-loads 10-25 seconds before the model writes a
single word. No buffering strategy can hide that, and every attempt to hide it
made the product worse: first a gap, then filler, then more filler.

**What changed**

* **Web search is off by default**, opt-in per request (`search=1`). Most
  questions do not need today's facts, and the ones that do can wait knowingly.
* **The default model is `claude-sonnet-5`**, which answers from what it knows
  almost immediately, rather than `claude-opus-5`.
* **The voice model is loaded at startup**, not on the first listener - it was
  costing 1.5-3 seconds on the first episode, exactly where it showed most.
* **A 1.5 second pre-roll** before playback begins. Models stream in bursts, so
  starting on the very first sentence turns any stall into an audible hole a
  second in. At many-times-realtime synthesis this costs almost nothing.
* **The prompt bans preamble outright**, by name: "Here's what I can tell you
  about...", "Let's talk about...", "There's a lot to unpack here...". If the
  first sentence would survive having a different topic substituted into it, it
  is wrong.

**Measured, end to end, with a real speech engine:**

```
time to first audio : 0.69s / 0.53s / 0.51s
no gaps in any run
```

Against 20-30 seconds and a gap before.

**The lesson worth keeping.** Every fix from §15 onward optimised the machinery
around a wait that should not have existed. The question "why is this slow?"
was never asked, only "what can we play while it is slow?" - and the answer to
that question is always filler.


---

## 25. Two things: narrated timestamps, and prefetching the wait away

**"Here is what I have as of Sunday, August 30..."** was mine. §17 told the
script to say what its picture was current as of "the first time it matters",
and the model turned that into an opening disclaimer. A listener does not want a
timestamp read to them. The prompt now bans announcing currency outright, and
permits timing only where it changes the meaning - "the count is still going" -
said in passing rather than as a preamble.

**Prefetch, which was the user's idea and a better one than anything above.**
The observation: a shop estimates delivery when an item goes in the basket, not
at checkout. It does the slow work during a pause the customer is taking anyway.

Applied here: the expensive part of an episode is the script; the audio is
nearly free. So 800ms after someone stops typing, `/api/prefetch` writes the
script into the cache. When they press play, `/api/audio` finds it there and the
model wait has already happened.

Measured against a stand-in API that takes 18 seconds to answer:

```
cold press   : time to first audio 18.30s
prefetched   : time to first audio  0.12s
```

**This corrects something stated wrongly earlier in this log.** §24 framed live
search as costing 10-25 seconds, full stop. That is only true if generation
starts when the button is pressed. It does not have to. The wait is a scheduling
choice, not a property of the work.

Guards: only after a real pause (800ms) and a real question (12+ characters);
never the same script twice; never a personal query; a failed prefetch is
silent and the listener simply takes the normal path. An unused prefetch costs
one script and no audio.


---

## 26. A prefetch that could vanish mid-flight

Reported from a clean install on Python 3.12.6: `test_prefetch_puts_a_script_in_the_cache`
failed while everything else passed. It passed here, which was luck - running it
twenty times in a row failed **seven**.

**Cause.** The endpoint started its work with `asyncio.create_task(build())` and
kept no reference to the result. The event loop holds only a *weak* reference to
a task, so a fire-and-forget coroutine can be garbage collected part-way through
and simply stop. Python's own documentation warns about exactly this.

**Why it mattered more than a flaky test.** A failed prefetch is deliberately
silent - the listener just takes the normal path - so in production this would
have shown up only as the wait occasionally not being removed, at random, with
nothing in the logs. The feature built specifically to make the app feel instant
would have worked most of the time and quietly not worked the rest.

**Fix.** Starlette's `BackgroundTasks`. It runs after the response is sent, so
the browser is never kept waiting, and the task is owned by the request rather
than floating free. Twenty consecutive runs of the test now pass, as do five
consecutive full-suite runs.

**The test was strengthened, not weakened.** It had been polling with a retry
loop - which existed only to paper over this bug. That is gone: the assertion is
now immediate, because the test client runs background tasks to completion. A
second test parses the endpoint's AST and fails if `asyncio.create_task`
reappears, and a third proves end to end that a prefetched episode plays without
touching the model.

Verified unaffected: the shared voice store still resolves to `~/.fam/voices`,
`setup_voices.py --list` reads it, startup reports it, and prefetch still turns
an 18-second model call into 0.11s to first audio.


---

## 27. Prefetch removed

Removed at the user's request. `/api/prefetch`, the typing-pause trigger in the
interface, its bookkeeping and its tests are all gone - not disabled behind a
flag, deleted, so there is no dead path to reason about later.

What it did is worth keeping in mind rather than in code: writing the script
during the pause before someone presses play turned an 18.30s wait into 0.12s,
because the script is the slow part and the audio is nearly free. What it cost
was a speculative model call for every abandoned query, which on a search box is
most of them.

That trade is much better on the browse surfaces, where what someone might tap
is known well in advance and the hit rate would be far higher. The reasoning is
recorded in `CLAUDE.md`; the mechanism is not in the codebase.

The ordinary script cache is untouched and still does the cheap half of the same
job: a repeat of the same query is a hit. Measured after removal - 0.70s to
first audio cold, 0.11s on the repeat, no gaps.


---

## 28. Teaching the voice by example rather than by rule

The user asked whether supplying sample scripts - one minute, two minutes, three
minutes - would help. It is the strongest lever available, and better than
anything attempted so far.

Every attempt to fix the writing until now has been a *rule*: "prefer one exact
detail to three general statements", "cut anything the listener could have
guessed". Models follow rules loosely and imitate examples closely. Two or three
briefings written the way they should sound will do more than another twenty
adjectives of instruction.

`examples/` is now read at import. Each file is `<minutes>-<slug>.txt`: first
line the query, blank line, then the script. They are shown to the model as the
house voice, explicitly framed as sound to imitate rather than facts to borrow,
since borrowing a fact from an example would be a hallucination.

Including the length in the filename is deliberate: how a briefing grows from
one minute to five is exactly where padding creeps in, and demonstrating that is
more reliable than describing it.

An empty folder changes the prompt not at all, and a malformed file is skipped
with a warning rather than breaking generation.

**Cost note.** Three examples add roughly 1,500-2,000 input tokens per request -
about a third of a cent on Sonnet. It also pushes the system prompt over the
minimum cacheable prefix, which §9 noted it was previously too short to reach,
so the examples may end up close to free on repeat traffic.


---

## 29. The thing that makes a FAM episode different

The user, arriving at the actual product thesis: an episode should be a *story*.
Even a simple explainer or a routine update. Entertaining, feeling like it comes
from somewhere - and crucially, not so obviously storytelling that a listener
thinks "get to the point". The immersion should be subconscious.

**The craft distinction that decides whether this works:**

* **Narrative as structure** - the facts arrive in an order that opens a
  question and closes it. Because / therefore / but rather than and-then.
  Invisible; the listener just does not want to stop.
* **Storytelling as decoration** - "picture this", scene-setting, atmosphere
  laid over the information. This is exactly what produces *get to the point*.

The prompt now asks for the first and bans the second by name.

**This corrected a rule I had introduced three sections earlier.** §23 said to
open with the answer immediately - the most concrete fact, straight away. That
is news-writing, the inverted pyramid, and it is the *opposite* of story
structure: state the conclusion in sentence one and there is nowhere left to go.
The opening should now be concrete and open a question - a small "wait, why?" -
without either spoiling the answer or delaying it.

The guard against the other failure is explicit: every sentence must carry
information, atmosphere alone is cut, and the point should be arriving
continuously from the first line. Story is the shape of the delivery, never a
delay before it.

Length now describes **story scope** rather than a word quota or a section
template - "one question, opened and answered" at a minute, "the full arc" at
ten - so the model picks something it can resolve in the time rather than
starting something too big and padding or truncating it.

**Rules can only get this so far.** Every failure in this log came from a rule
that was followed too literally. "A story, but not too much story" is precisely
the kind of instruction a model interprets badly and a human writer gets
instantly from one good example. `examples/` is where this is really settled;
its guide now says what an example needs to demonstrate - the specific opening,
the turn, causation over chronology, the landed ending.

## 30. Annexation: a good ending is still an exit

**The problem.** The prompt said *"Land it. The last line should give the
listener something to carry."* That is sound podcast advice and exactly wrong
for this product. A landed ending is a resolution, and a resolution is
permission to leave — politely, satisfied, and gone. If the aim is to absorb a
listener rather than entertain them for four minutes, the ending is where it is
won or lost.

**The first fix, and why it was not enough.** "Do not end. Widen." replaced it,
along with a ban on the explicit exit signals ("so, to sum up", "in
conclusion", "the bottom line is") and a rule to speak from inside rather than
orienting the listener. That is directionally right but *underspecified*, and
underspecified prompt instructions get filled in with atmosphere — which is the
failure mode this whole section of the prompt exists to prevent. "Widen" alone
invites a portentous closing mood: *"and what happens next will matter for all
of us."* That is a feeling, not a thread, and it is banned two paragraphs later
by the rule that every sentence must carry information.

**What widening actually has to mean.** One *specific* unresolved thing, named
concretely: a decision not yet taken, a figure that does not add up, a person
whose next move settles it, a rule about to be tested. Three constraints make it
work rather than tease:

- **It has to already be in the room.** Nobody can want to know more about
  something they first hear of in the final sentence. The thread has to be set
  up in passing while the story is being told, and left standing.
- **It has to be nameable in a handful of words** — small enough to be a
  request, big enough to be a whole episode.
- **Point at it; do not ask about it.** No rhetorical questions to the listener
  ("but will it hold?"), no promises about next time. Curiosity comes from the
  gap being real, not from being told to be curious.

**The half of the problem that was not in the prompt at all.** An episode that
ends pointed at something specific is only half the job while acting on it still
means composing a question into an empty text box. Go Deeper opened on a blank
field with a generic placeholder. The listener had the impulse and the interface
asked them to do the work.

So the thread is now carried out of the script rather than left in the
listener's head. The model writes it after the final sentence as
`<<NEXT: six to twelve words>>`, phrased as the follow-up someone would ask for.

**Making sure it is never spoken.** This is the risk the feature introduces, and
the project has been bitten before by things that fail quietly, so it is
defended in three places:

- `stream_sentences` partitions the buffer at `<<` and only ever hands the part
  *before* it to the sentence splitter — the marker can arrive split across
  stream events, and a half-written `<<NEX` must not be read out either.
- `clean_for_speech` strips both a complete marker and anything from an
  unmatched `<<` onwards, so no path can reach synthesis with one in it.
- It is stored in its own cache column, beside the sentences rather than inside
  them, so a replayed cache hit cannot speak it by accident.

**Getting it to the interface.** The thread is only known once the script is
finished — which is *after* the `/api/audio` response headers have gone out, so
it cannot be a header. It is written into the script cache with the script, and
`GET /api/next` reads it back: no second model call, no tokens, no added
latency. A cache hit keeps its thread, so a free replayed episode still offers
the same follow-up. The Go Deeper sheet shows it as a one-tap chip; the field
below it still takes anything the listener would rather ask. An absent thread is
normal — uncached, or the model named none — and degrades to exactly the blank
box that was there before.

**Still unverified.** As with every writing change in this project so far, this
is reasoning about a prompt rather than evidence about output: there are no
credentials on the build machine and no real script has ever been read from any
version of it. `python write.py "<query>" --minutes 3` prints one in seconds.
The question to ask of the output is not "is this a good ending" but "can I
name, in a few words, the thing I now want to hear about" — and whether that
thing was set up earlier in the piece or produced out of nowhere at the end.

The cache key version is bumped to 2 so nothing written under the old prompt is
served under the new one.

## 31. Satisfied first, curious second

**The risk that section 30 introduced.** "Leave one thread open" and "answer the
question" pull against each other, and nothing in the prompt said which wins. A
model resolving that tension the wrong way withholds the answer and calls it
momentum — it ends on the interesting unresolved thing by simply never closing
the question the listener actually asked. That reads as a tease, and a listener
spots it in one episode. It is also the most likely way for a set of ending
rules that elaborate to fail: they are the most specific, most recently stated
instructions in the prompt, so they attract weight the plain job does not.

**The ordering, stated as a rule.** Someone searched, tapped a tile, or decided
to keep listening — each is a want, and meeting it is the first duty. They must
finish knowing what they came to find out, well enough to say it back in their
own words. *Then* the curiosity. The order is load-bearing rather than a
pleasantry: curiosity is what makes someone want another episode, but
satisfaction is what makes them believe another one is worth having. Reversed,
the second episode never gets tapped, which also means the Go Deeper chip and
the browse surfaces are built on nothing.

**The fix, in three places.**

- A governing paragraph now sits directly under the opening of the system
  prompt, *above* all the craft: answer them, satisfied first and curious
  second, and the thing left open is never the answer held back.
- The thread rule is restated as "leave exactly one thread, **and never the main
  one**". The thread is second-order — something the answer itself raised, that
  the listener could not have known to ask about when they started. The test
  given to the model is concrete: if someone could hear the last line and think
  *"so you never actually told me"*, it withheld rather than widened.
- The per-episode brief — the thing the model actually acts on, and the more
  influential of the two — opens with "Answer them" and only then asks for the
  thread, explicitly "not the one they asked about. Close their question first,
  completely."

Tests assert both the wording and its *position*: the job has to appear before
the craft in the system prompt and before the thread instruction in the brief,
because an instruction's weight depends on where it sits.

**Note what this does not change.** The rule against opening with the conclusion
still stands — answering fully is not the same as front-loading. The answer
arrives across the piece and is complete by the end; it is neither withheld nor
delivered as a headline in sentence one.

Still unverified against real output, like everything else about the writing.
The check when reading a script from `write.py` is now two questions in order:
*could I say the answer back in my own words?*, and only then *can I name the
thing I want next?* A yes to the second and a no to the first is the failure
this section exists to catch.

## 32. myFAM: personalise the ordering, not the inventory

**The constraint that decided the design.** A per-user set of topics means a
per-user script for every tile, and a script is the only expensive thing in
this product. So every listener sees the **same bank** of ~28 topics and a
different **ordering** of it. Two people who tap the same tile share one script
through `cache.py`: the second tap costs nothing and starts instantly. That is
the whole cost argument, and it also happens to be what makes prefetching the
browse surfaces affordable later.

**Four sections have to run on four signals**, or they are one ranked list
wearing four headings - which is the standard way a feed like this fails:

    trending      global play counts, identical for everyone (cheapest to serve)
    might_like    adjacent to taste, strongest tag suppressed (exploration)
    followers     co-listener overlap (social proxy)
    from_history  closest match to what they played (exploitation)

A taste profile is computed from the event log on read, never stored: a stored
profile is a cache that can disagree with the log it came from. Completing an
episode counts 2.5x a play; a **skip counts negative**, because treating it as
a weak play means skipping something recommends more of it. Interests decay
with a fourteen-day half-life so the feed is not a museum.

**Three bugs the tests caught, all of them design errors rather than typos.**

*The personal sections were starved by the generic ones.* Filling in display
order, Trending and "might like" claim from the whole bank first, so by the
time the two sections the listener actually asked for are filled, every topic
they wanted is taken and they render empty. Sections are now **filled in
constraint order** (from_history, followers, might_like, trending) and
**displayed in product order**. Trending chooses last precisely because it can
fall back to anything.

*Four sections of six needs twenty-four topics.* The bank had twenty-two, so
the last section could not fill even when correctly ordered. Now twenty-eight,
with a test that fails if a future edit drops it below four times the section
size.

*A one-tag listener got an empty "might like".* Suppressing their strongest tag
to avoid a filter bubble leaves nothing at all for someone whose entire history
is one tag - and they are exactly who that section exists for. It now fills in
tiers that top each other up: their other interests, then bridges that keep the
familiar tag but pair it with a new one, then anything unseen.

**Honesty rules carried over from the rest of the project.** An empty section
says why it is empty rather than being padded with picks that pretend to be
personal - a new listener genuinely has no history and no co-listeners. A feed
that fails to load says so instead of rendering as an empty app. And the event
store is wrapped so that losing an interaction can never break playback: a feed
is a nicety, audio is the product.

**What is still wrong, and known.**

*"What your followers are listening to" is a label over data that does not
exist.* There are no accounts and no follow graph. It ranks co-listener overlap
- people who played what you played also played this - which is a real signal
and a standard one, but it is not followers. Either build follows or rename the
section; do not let the heading keep implying a social network.

*Tags come from keyword matching*, not a classifier. A search for "the fed" gets
`money`; "zzzz" gets nothing and contributes no signal. A model call per search
would cost more than the episode it is recommending, so this is the right trade
at this size - but it silently mis-tags anything phrased unusually, and there is
no way to notice from the outside.

*Identity is a random id in `localStorage`.* Clearing site data is a new person;
a second device is a second person. Real accounts replace it and nothing else
has to change.

*Cold start is real.* Trending falls back to a stable slice of the bank rather
than random picks - random would defeat the shared script cache and move tiles
under the listener between visits - but until people are actually playing
things, "trending" means "the front of the bank" and only the ordering is
honest.

**Interface: rails, not grids.** Each category is one horizontally scrolling
row. Four two-column grids stacked into a very long page - the fourth section
sat three screens down and would effectively never be seen, which defeats the
point of having four different signals. Each rail owns its own horizontal
overflow so the page itself never scrolls sideways (asserted in a real
browser), and a chevron closes each rail because a rail gives no hint that it
moves until you touch it.

## 33. playFAM: a mix holds topics, not audio

**The decision everything else follows from.** A mix is a *standing
subscription* to a handful of topics, not a saved recording. "At the gym" is
the same three subjects every day and a different three episodes. Saving audio
would make a mix stale the moment it was created, would break the no-files rule
the whole product rests on, and would cost storage per listener; saving topic
ids costs a row in SQLite and is fresh every morning.

**Membership is validated against the shared bank.** A mix that could hold
arbitrary free-text queries would quietly undo the myFAM cost design - two
people whose "Morning" mixes both contain the Fed episode share one script
through `cache.py`, and that only works while members are bank topics. An
unknown id is rejected with a message rather than dropped silently, because a
mix that loses a topic on save looks like the app forgot.

**Rules live on the server, not in both places.** Duplicate names, the topic
cap, empty names: the API decides and the interface shows what it says. The
alternative - validating in the browser too - means two implementations that
drift, and the browser's copy is the one that gets skipped.

**Two bugs found by driving a real browser, not by unit tests.**

*The topic picker died silently when `/api/topics` failed.* An error response
is still JSON, so `r.json().topics` was `undefined` and `renderMixPicker` threw
`Cannot read properties of undefined`. The listener saw an empty screen and a
console error they will never look at. Now the response status is checked, and
a failed bank renders a message with a retry rather than nothing. Worth noting
how it surfaced: the app's own rate limiter throttled the page during testing,
which is exactly the condition that would hit a real listener on a slow or
busy server.

*`openPlayFAM` both set the tab and navigated,* pushing the same screen onto
the back stack twice, so Back did nothing the first time it was pressed.
`setTab` already shows the screen and resets the stack.

**Removed with it:** the prototype's client-side playlists - `BRANCHES`-backed
grids, the create-playlist modal, the separate add-topics screen and the
hard-coded tile colours. Leaving them would have meant two playlist systems,
one real and one fake, with no way for a listener to tell which they were
using.

**Still open.** "Daily" is currently a promise about *content* (each day's
episode of a standing topic), not a scheduler - nothing wakes up in the morning
and generates the mix. That is the right next step and the right place for
prefetch: a mix names exactly which scripts are worth warming, per listener,
before they press play, which is the case `CLAUDE.md` has been arguing for all
along. Until then the mix generates on tap like everything else.

## 34. Explore: a surface defined by what it cannot do

**dailyFAM is now explore**, and its defining property is negative: it must
never cause a script to be written. Every card is a live entry in the shared
script cache - something another listener already paid to generate - and the
tab exists to get more value out of scripts that already exist rather than to
create new ones.

**The guarantee lives in the pipeline, not the interface.** `EpisodePlan` grew
a `cached_only` flag; a cache miss under that flag raises `NotCached` instead
of generating. If the rule lived in the frontend it would be one refactor from
being broken silently *and expensively* - the failure mode would be a feed that
quietly costs a model call per scroll, which is exactly the kind of invisible
loss this project has been bitten by. The test asserts it against a generator
that counts its calls and fails if it is ever asked to write.

Three cases the tests pin: a miss refuses; a hit replays without generating; and
a miss with caching switched off entirely still refuses rather than falling
through to "generate everything".

**Replaying needs the duration.** The cache stored the query and the script but
not the length it was written for, and a one-minute script replayed as a
five-minute episode is padded with silence. `minutes` is now a column (added by
the same ALTER TABLE migration pattern as `thread`), entries without one are
skipped rather than guessed at, and the player shows the episode's own length
rather than the listener's default - otherwise Explore looks like it ignored
the length setting.

**Privacy comes for free, and that is worth stating explicitly.** Only
shareable queries are ever written to the cache - the personal-query filter
runs before the write - so everything Explore can possibly show has already
passed it. That makes the filter load-bearing in a way it was not before: it is
now the thing standing between someone's private question and a public feed.
There is a test that a personal query never reaches `recent()`.

**A stale card is a 409, not a fault.** An entry can expire between the feed
loading and a tap. The API answers 409 with a message naming Explore, because
this is an expected outcome rather than a server error, and the interface can
tell the difference.

**Removed with it:** the prototype's hardcoded `DAILYFAM_TITLES`, its
eight-day bucketing and the stories-style stage. They were a demo of a feature
that now has real data behind it, and keeping both would have meant two feeds
with no way to tell which was real.

**Still open.** Explore ranks by recency alone. Popularity (`plays` is already
in the payload) or a myFAM-style taste signal would order it better, but
recency is honest and cheap and does not need a model. The feed is also
global - there is no "near you" or "people like you" - which is the same
missing follow graph noted for myFAM.

## 35. Typed topics, and Explore as a slot machine

**playFAM is now DailyFAM** (labels only - the ids stay `playfam`/mixes, so
the code and the URL do not have to be re-read to be understood).

**A mix can now hold a question the listener typed.** The bank was a hard
constraint before; it is now a shortcut. `MixItem` distinguishes the two, and
the distinction is kept because *the cost differs*: a bank topic is shared by
everyone who has it in a mix, so the second listener's copy is free, while a
typed one is only shared with people who phrase the same question the same way
- for a niche question, nobody. That is a script a day for one person, and it
is the right trade for "what my council is doing about the high street", which
no shared bank will ever contain. `custom_count` is on the wire so the
interface can eventually say so.

Storage moved to one JSON column so a mix keeps the order of mixed entry types,
with the old comma-separated `topic_ids` kept as the bank-only view an older
row would have written. `Mix.topic_ids` is now derived rather than stored,
which is why every existing test kept passing unchanged.

**Explore is now reels rather than a list.** One episode fills the screen, the
next is deliberately not visible, and a swipe up deals another. The point is
the absence: nothing to scan, nothing to skip past, no decision except whether
to stay. The queue is shuffled rather than ordered by recency - ordering makes
it a feed, shuffling makes it a deal - and it refills from what has already
been shown rather than ending, because an empty screen at the bottom of a reel
is a stop and nothing here expires from being heard twice. Swipe, wheel and
arrow keys all drive it; running out of audio advances without the swipe.

**Three bugs, all found by driving a browser.**

*Every failed card cascaded into an infinite auto-advance.* "Drop the dud and
deal the next" is right for one stale card and catastrophic when the whole
batch is stale: it blanked the screen and hammered the server in a loop. Three
failures in a row now stops and says the batch has aged out. A card that plays
clears the streak.

*Demo mode built the pipeline with `cache=None`,* which made Explore
structurally impossible without credentials - and Explore is the one surface
that needs none, because it only replays. Demo mode now swaps the model and
nothing else, which also means it exercises the real cache hit/miss path
rather than a path that only exists in demo.

*`FamAudio` has no `isPlaying`.* The reel's play/pause guarded on
`FamAudio.isPlaying && FamAudio.isPlaying()`, which is always false, so pause
resumed instead of pausing. The real signals are `isActive()` and `isPaused()`.

**Worth noting about the demo:** seeding the cache with arbitrary keys produced
a feed that listed episodes it could not play. Explore's cards are only real if
they are stored under the key the pipeline will compute - `cache_key(query,
minutes, ...)` - which is a useful reminder that the feed and the player must
agree on the key or the tab is a menu of dead links.

## 36. The Explore dead end, and a tab bar built around search

**Explore had no way out.** `.reel-stage` was `position:absolute; inset:0;
bottom:56px`, but `.screen` is a flex column with no `position`, so `inset:0`
resolved against an ancestor further up and the stage painted straight over
the tab bar. The `56px` was a guess at the bar's height that never applied to
anything.

The fix is to stop positioning it at all: the stage is now an ordinary
`flex:1` child of the flex-column screen, so the bar sits below it by
construction and the magic number is gone. Worth noting the class of bug -
an absolutely positioned element whose containing block is not what the author
assumed, hiding chrome the user needs. The browser check now clicks a tab from
inside Explore rather than only asserting the bar exists, because "present in
the DOM" was true the whole time it was unusable.

**The tab bar now has search in the middle, raised on a disc.** Five tabs, in
order: myFAM, DailyFAM, search, explore, Messages. Search is the one thing
someone opens the app to do, so it gets the easiest thumb position and a shape
nothing else in the bar has - findable without reading a label, which is why
it is the only tab without one.

All five bars are generated from one list in the build edit rather than hand-
edited in five places; they had already drifted once when playFAM was added.

## 37. myFAM rebuilt: unfinished business before recommendations

**Go Deeper moved to the top of the page**, and it holds two kinds of thing a
listener already has a foothold in:

*Part-heard episodes.* Resume positions live in `localStorage`, not on the
server - they are per-device by nature, worthless to anyone else, and not
worth a write on every tick. Written continuously rather than on pause, so
closing the tab mid-episode still leaves a way back in. Anything under twenty
seconds in or within thirty seconds of the end is dropped: a card offering the
last four seconds of something is clutter, not a way back in.

*Threads finished episodes left open.* This is the payoff for the widening
ending. The thread now rides along on the completion event (a new `thread`
column on `events`) rather than being joined back to the cache at read time -
the cache key depends on settings that may have moved on, and a thread the
listener was actually offered should not vanish because the model changed.
A thread they have since searched for is dropped, because it is only a thread
while it is still open.

**Sections lead with the personal one.** Display order is now from_history,
might_like, followers, trending - someone opening myFAM is likelier to want
what was chosen for them than what is popular, and should not scroll past the
crowd to reach it. Fill order stays the opposite (constrained sections choose
their topics first), which is why the two orders are separate constants.

**Headings speak rather than label.** "Made for you, Monday evening" instead
of "Based off what you've listened to". The old mono kickers described the
machinery; the reason each pick is there moved onto the card itself, where it
belongs - a recommendation that cannot say why it is there reads as arbitrary.

**One test was asserting by position** (`sections[0]["topics"]`) and broke the
moment the order changed, even though the behaviour it cared about - trending
still falling back to the bank when the event store is broken - was intact. It
now addresses the section by key. Worth noting as a category: a test that
encodes a presentation decision it does not care about will fail for the wrong
reason and tempt you to weaken it.

**The preview fixtures now derive their section order and titles from
`topics.SECTIONS`** instead of hardcoding them, after the preview kept showing
the old order following this change. A fixture that can disagree with the code
is worse than no fixture.

## 38. Profile takes the fifth tab; Messages becomes a sheet

**Messages was never a place, it was a detour.** It sat in the tab bar next to
the four surfaces the product is actually about, which gave it equal billing
and cost a slot. It now opens from the myFAM header - replacing the three dots,
which did nothing but toast "concept" - as a sheet pushed over whatever you
were doing, with an X that returns you there. That is a different promise from
a tab: you come back to where you were rather than having to navigate home.
The sheet carries no tab bar, because it is over the app rather than one of its
places, and a thread opened inside it returns to the sheet rather than skipping
past it.

**Profile is a scaffold and says so.** Everything on it comes from this
listener's own event log: started, finished, threads still open, and the
subjects they are positive about. A skipped subject is excluded, because a skip
is evidence against a tag and has no business on a list of what someone likes.

The temptation on a profile page is to fill it - followers, streaks, hours
saved, a rank. Every one of those would be invented here, and an invented
number is a promise the product has to keep later. So the account section says
the true thing instead: there are no accounts, this is one device, clearing
browser data starts you over, and signing in is what would join them up. That
is also the clearest statement anywhere in the app of what identity work is
still outstanding.

**The smoke test now opens and closes the sheet** rather than checking it
exists. Explore already shipped once as a screen with no way out; a sheet is
the same failure waiting to happen, and "present in the DOM" would have passed
that bug too.

## 39. Echoes: a social layer that generates nothing

**An echo is a row, not an episode.** Someone finishes something and pushes it
to other listeners. The episode may well have reached them anyway - scripts are
shared, so a popular question is already in the cache Explore reads from - but
the echo changes what the card *says*: "Rachel sent you this" instead of
"someone asked this", which is a different reason to press play.

That is the whole design, and it is the same argument as the rest of the
product: the expensive thing is the script, and an echo points at one that
already exists. The social layer costs a SQLite row. `social.py` also holds the
minimum identity an echo needs to make sense - a name and a handle - because
"posted by ___" needs a ___.

Echoing twice is one echo (the intent is "send this", not "send it twice"), an
echo can be taken back, the same question at a different length is a different
episode, and your own echoes are never labelled back to you.

**Mixes are private until they are not.** A mix is a routine, and a routine is
personal, so `public` defaults to false and publishing is a deliberate switch
inside the mix. Public mixes are what the profile shows.

**What the profile still refuses to invent.** There is no follow graph, so
there is no friends count, no friends row, and echoes are visible to everyone
rather than to a chosen few. The page says that in plain words instead of
showing a number with nothing behind it. `recent_echoes` is where the filter
goes when follows exist, and nothing else has to change.

**A bad edit duplicated 69,000 characters of the interface.** Replacing a
region with `s[:start] + new + s[end:]` when `start` came *after* `end` in the
file silently produced `A + B + new + B + C`. Two consequences worth recording:

- The duplicate was invisible to the page, because the second copy of every
  function simply shadowed the first - until it shadowed the *new* profile with
  the *old* one, which is how it surfaced.
- Repairing it needed a guard, not confidence. Deleting from the second marker
  to the next section would have removed fifteen functions that existed only
  once; the fix was to delete exactly the byte-identical prefix and assert that
  no function name disappeared.

The same edit also deleted the whole wordmark stylesheet, which broke all four
marks at once (603px misalignment). The lesson is not "be careful with
indices": it is that a large single-file interface has no compiler to catch
this, so every structural edit needs a check that runs afterwards. The browser
smoke test now covers the visibility switch and the echo controls for exactly
that reason.

## 40. The deleted CSS came back a second time, so a check now catches it

myFAM came back "all messed up": the Go Deeper grid was plain text, cards had
no tag or reason, and the now-playing bar was an empty slab sitting over the
tile rail. Nothing had failed. Tests passed, the interface parsed, the smoke
test passed - because every one of those checks asks whether the page *works*,
and the page did work. It just looked wrong.

**Cause: more collateral from §39's bad edit.** The overwritten region held the
whole myFAM style block, and its loss surfaced only when someone looked at the
page. Auditing the class names showed thirteen of them with no rule behind
them at all:

    gd-head gd-kicker gd-count gd-grid gd-card gd-bar gd-ask
    feed-title seed-tag seed-why nowbar nowbar-thumb nowbar-play

`.mix-vis`, `.mix-switch` and the echo controls had been lost the same way and
found the same way - one at a time, by noticing. That is the actual problem:
**the discovery method was "look at it", so each missing block cost a round
trip.**

**A second, quieter bug in the same block.** `.nowbar` sets `display:flex`,
which outranks the `hidden` attribute the markup relies on. So the bar was not
merely unstyled, it was *showing* with nothing in it whenever no episode was
playing. A class that sets `display` silently disables `hidden` on every
element carrying it, and nothing anywhere reports that.

**Fix: `tools/check_css.py`, in `./dev.sh check` and in CI.** It fails when a
class appears in the markup with no rule behind it, and when an element with
`hidden` wears a class that forces it visible. Structural hooks that genuinely
need no styling are named in `HOOKS` so the exemption has to be written down
rather than assumed. Both regressions above were reproduced against it before
it was wired in; it catches both.

The rule this settles: **a check that only asks whether the page works cannot
see a page that looks wrong.** In a single-file interface with no compiler,
appearance needs its own check or it is discovered by the person using it.

## 41. A maintenance pass: what was dead, what was actually broken

A deliberate sweep after §40, on the theory that the deleted stylesheet was
unlikely to be the only thing rotting. It was not. Recorded here because the
*findings* matter less than which check would have caught each one.

**First, the question that prompted it: did the bad edit take anything else?**
No. Comparing every CSS class, function name and element id between the commit
before it and now, the only losses are the five `pf-*` classes of the old
profile scaffold, which were replaced on purpose. No function and no id was
lost, and nothing is duplicated any more. That is now settled, not assumed.

**Two real bugs, both of them dormant rather than visible:**

* **The script cache never deleted anything.** `purge_expired` existed and
  nothing called it. Expired entries were filtered out on read, so the cache
  behaved correctly and `scripts.db` grew for the life of the deployment. The
  app now purges at startup - entries expire in days, so once per boot is
  enough. Two tests pin it: the method deletes only what is expired, and
  `lifespan` actually calls it. The second one fails if the wiring is removed.
* **A cold open would have opened the episode twice.** `build_prompt` computed
  an `already_opened` instruction and then never interpolated it - a leftover
  from the prompt rewrite. With `ENABLE_COLD_OPEN=1` the reserved words still
  shrank the budget, but the model was never told an opener had already been
  spoken, so it would write its own. Wired back in. Invisible today because
  the cold open is off, which is exactly why it survived.

**One fragility, in the spirit of "failures must be visible":** `showScreen`
removed `active` from every screen and *then* looked up the target, so an
unknown id left a blank white app and a thrown error. It now looks first and
says so.

**Dead code removed:** seven interface functions and, once they went, five more
functions and seven module-level tables that only they used - the whole
Branches/albums subsystem, including a `navigate("connect")` pointing at a
screen that no longer exists. Plus `/api/echoes` (no caller, no test, and
`/api/profile` already returned the same list), three unused Python helpers,
`full_script` (whose docstring claimed `/api/script` used it - it did not), and
76 CSS rules. About 13 KB.

**How the CSS deletion was made safe, which is the transferable part.** Given
§39 and §40, deleting 76 rules by static analysis alone was not good enough -
a false positive there is invisible until someone looks at a phone. So
`tools/shots.py` photographs all nine surfaces; the change was made between two
captures. Eight came back byte-identical. Explore and the player differed - and
capturing the *unchanged* build twice produced the identical difference, which
is what proved it was the rotating reel and the moving scrubber rather than the
edit. Verification by screenshot is now a tool, not a one-off.

**The new check had the same hole it was built to close.** `check_css.py` read
class names out of CSS *comments* as though they were rules, so a class
mentioned only in a note would have counted as defined and let a genuinely
missing rule through. Nothing had fallen through it, but it is fixed and the
masking case is verified to fail now.

**What was deliberately left alone.** `/api/next` returns an empty thread when
no speech engine is installed, even though it only reads the cache. It looks
like a bug and is not worth fixing: in that state `/api/audio` cannot play
anything at all, so missing Go Deeper chips are the smallest of the problems,
and the engine failure is already loud. `piper` and `h2` still look like unused
imports to a linter; both are availability probes. Churn in code that is
working is how the last three regressions got in.

## 42. Echo on every player, found by not naming them

The main player - the one a search lands on - had no echo control. playFAM and
the Explore reel had one; `screen-player` did not, so the most-used surface in
the app was the one you could not echo from.

The cause is worth more than the fix. `setEchoed` kept the two controls in step
by listing their ids: `["echoIcon", "reelEcho"]`. Adding a third player meant
remembering to edit a function somewhere else, and the smoke test asserted
those same two ids by name, so it agreed the app was fine. **A check written
against the things that exist cannot notice the thing that is missing.**

Now every echo control carries `data-echo`, `setEchoed` drives whatever it
finds, and the smoke test iterates the list of *player screens* asserting each
contains one. A fourth player without an echo button fails; both checks were
confirmed to fail before the button was put back.

Screenshots of all nine surfaces before and after: only the player's control
row changed, in the band where the button went.

## 43. myFAM loses a section, and Go Deeper loses half its height

Two interface changes, one with a consequence worth recording.

**"A little sideways from that" is gone.** It was the `might_like` section -
the exploration signal, which suppressed a listener's strongest tag so the page
offered something adjacent rather than more of the same. Removed from
`SECTIONS` *and* `FILL_ORDER`: leaving it in the fill order would have kept it
silently claiming six topics out of the shared bank for a section nobody sees,
starving the three that remain.

`rank_might_like` itself is kept, with its tests, and both the module docstring
and CLAUDE.md now say it is not wired in. The consequence is the part to keep
in view: **it was the only signal that offered anything outside an established
taste.** What is left - history, co-listeners, trending - all points inward, so
myFAM will now tend to narrow around what someone already plays. One line in
`SECTIONS` and one in `FILL_ORDER` puts it back.

**Go Deeper tiles: 96px to 48px.** The height came from the tallest title, not
from `min-height:76px` - a three-line headline set the row and the grid made
the other three match it. So the fix is a fixed `height`, plus a two-line clamp
and smaller type. Two lines rather than one on purpose: one line of a headline
at half a card's width says nothing useful.

The smoke test asserted four rails and the preview fixture supplied four
sections; both are now three. The one test that failed was asserting a section
that no longer exists, and its docstring - "the whole reason for four sections"
- was rewritten rather than deleted, because the signal it covers is still
there and a silently rotting one is worse than none.

## 44. The tiles were cut on a phone and green in the checks

Reported from a real device with a photo: the Go Deeper titles were being cut
mid-word - "Talking About AI...", "The World's Chips". Everything here was
green.

**What I had actually verified was the wrong thing.** After halving the tiles I
measured `.gd-card` height (48px, exactly half, correct) and looked at four
short fixture titles in a screenshot. Neither asks the only question that
mattered: *is any text being clipped?* Adding that check afterwards showed the
48px version clipped **28 of 28** titles in the bank, not just the long ones.
A measurement that confirms the number you set is not a test of the thing the
number was for.

Two causes, and the second is the interesting one:

* Three lines, not two. Two lines of a headline at half a card's width is not
  enough for the titles this bank actually contains.
* **`flex:none` on the title.** The card is a flex column with
  `justify-content:space-between`, so the title box was being *shrunk below*
  the three lines `-webkit-line-clamp` allowed. Text was cut with room still
  in the card - the clamp said three lines and the layout granted two. Height
  alone would not have fixed it.

Also: the preview fixture only ever showed bank titles, but a thread card's
title is the raw `<<NEXT: ...>>` text, six to twelve words - longer than
anything in the bank and the case most likely to clip. The new check measures
every bank title *and* a twelve-word thread.

Now in the smoke test: set a title into a real tile and compare `scrollHeight`
to `clientHeight`. It fails on the version that shipped, which is the only
reason to believe it.

Tiles are 68px - down from 96, up from the 48 that did not fit.

**"Ask FAM anything" is gone** from under the grid: the search field says the
same thing two rows above it. The empty state used to be that button alone
under a "Go deeper" heading, so with the button gone the whole block now
renders nothing rather than a heading over empty space.

## 45. Go Deeper always has four tiles, and stops saying "thread"

Asked what "4 threads" meant. It is a word from inside the codebase - the
unresolved thing an episode names on its `<<NEXT: ...>>` line - and it should
never have been on screen. Worse, the number counted *every* card, and half of
them are part-heard episodes rather than threads: the tile reading "2:41 LEFT"
was being counted as a thread.

The count is gone. The row now reads **"Pick up where you left off"** - but
only when at least one tile really is something they left off. On a first run
every tile is a starting point and the same line would be a small lie, so it
says "Somewhere to start" instead.

**And the section no longer comes up empty.** A listener with no threads and
nothing half-heard got *nothing at all* where the way in should be - a state
nobody developing this ever sees, because everyone testing has history. Go
Deeper now tops up from the shared topic bank.

The distinction that keeps this on the right side of §4's rule about not
inventing: **these are not placeholders.** They are real bank topics carrying a
real query, they generate a real episode when tapped, and they pass their
`bankTopicId` through so the tap is logged like any other - otherwise the tiles
a new listener taps most would teach the taste model nothing. Anything the
rails below already show is skipped, because the same title twice on one screen
reads as a bug.

Both cases are now in the smoke test, and both fail without the fix: the
first-run one reported "a new listener saw 0 Go Deeper tiles, not 4".

Still open, and pre-existing: a *resume* card can duplicate a rail pick, since
only starters are de-duplicated against the rails.

## 46. The gap was not pacing. Sentences were being written and thrown away

Reported from listening: "after the first line is read, there is a pause before
the story continues." §21 had declared silence "structurally impossible up to a
60s ceiling". That was wrong, and `tools/gap_probe.py` reproduces it with no API
key - a fake generator whose researched call takes a set time, measuring the one
thing that matters, audio produced against wall clock consumed:

    script took  5.0s -> worst silence  2.20s
    script took 10.0s -> worst silence  2.86s
    script took 30.0s -> worst silence 11.54s   (52 opener sentences written, 11 spoken)

**The cause was a rebound variable, not a budget.** Topping up did this:

    opener = self._start(cold_open(plan))

`opener` was the *only* handle on the call in flight. Reassigning it orphaned
every sentence the previous call had not yet queued - written, paid for, and
dropped. The buffer then ran dry, and the listener heard the hole while the
replacement was fetched. On a 30s script that was 52 sentences bought and 11
spoken, with 11.5 seconds of dead air in the middle.

Two changes:

* **Every fill in flight is drained, oldest first.** `live` is a list, so a
  refill *adds* a source of sentences rather than replacing one that is still
  delivering.
* **A refill waits until the calls in flight have finished.** The first attempt
  fired whenever no pump was instantaneously `ready()`, which for a streaming
  call is most of the time - it bought 52 sentences to speak 11. Gating on
  "nothing still delivering" cut that to 12 for the same episode.

Measured after: **0.00s of silence at every latency from 2s to 30s**, and
opener calls on a 30s script down from thirteen to three.

The regression test asserts what the docstring used to assert and could not
back: that sentences written are sentences spoken, within one fill plus one
buffer of legitimate cut-over waste. It fails on the shipped code - `assert 11
>= (52 - 8)`.

### The transition, which is a different problem

Also reported: the content of the opener and the script sometimes disagree, and
the seam shows. Two causes, and only one is fixable this way.

Fills were independent calls with an identical prompt, so a slow script
produced several *openings* in a row rather than one continuous run-up. Each
fill now receives what the listener has actually heard and is told to continue
from the last sentence rather than begin again.

**What this does not fix, and cannot.** The opener and the script are written
concurrently - that is the entire point - so the script cannot be shown the
opener. The opener is also forbidden from stating any fact, having done no
research. So the seam is structural: content-free framing meeting researched
prose. Making the opener run *longer* makes it worse, not better, because
everything it adds is by construction filler. The cure named in CLAUDE.md
still stands: remove the wait rather than fill it better - fewer web searches,
a faster script model, or prefetch on the browse surfaces.

## 47. Attachments: documents, photos and links as context

A search can now carry material the listener supplies. `attachments.py`
extracts it, `AttachmentStore` holds it for six hours, and the script prompt
gets it ahead of the question.

**Extraction happens on attach, never on generation.** Reading a PDF or
fetching a page is a round-trip, and seconds in front of the first word is the
one thing this product will not spend - it is the mistake CLAUDE.md records
about live web search. Doing it when the file is added puts the cost while
someone is still typing, and the generation path only ever resolves an id.

**Every failure is a sentence the listener can act on.** An unreadable PDF, a
scanned one with no text layer, a dead link, a .exe, an empty file - each names
what happened and what would work instead, and the chip stays on screen marked
failed rather than disappearing. Silently dropping it would produce a confident
episode about a document nobody read. `/api/audio` returns 410 rather than
generating if an id has expired, for the same reason.

One detail worth keeping: the pypdf import is guarded with `except
BaseException`, not `except Exception`. A missing package raises ImportError,
but pypdf's optional native crypto dependency can raise pyo3's
`PanicException`, which does not inherit from Exception - the narrower catch
let a broken install take down the request instead of explaining itself. This
container has exactly that broken install, which is how it was found.

**.docx needs no dependency.** It is a zip of XML, so the words come out with
`zipfile` and a regex. Only PDF needs a package, and it is optional.

**Nothing attached is ever cached.** `_cache_key` returns "" when a plan has
attachments, so there is no read, no write, and nothing that could be served to
another listener or surface in Explore. That was the answer to the design
question; the alternative was a content hash, which puts listener-supplied
material in a shared store.

An attachment with nothing typed is a summarise request, not an error. It is
cleared after the search, so it cannot ride along on the next, unrelated one.

**A checker bug found on the way.** `check_css.py` read `class="attach-chip' +
cls + '"` as using a class called `cls`. It now strips JavaScript splices out
of attribute values before splitting. It still tracks class *names* rather than
selector context, so `.share-contact .nm` counts as defining `.nm` anywhere -
a known coarseness, not worth a full CSS matcher.

## 48. Endings stop teasing; the follow-up becomes a prediction

Asked for twice: remove the tease at the end of a searched episode. §19's rule
was "endings widen, they do not conclude" - leave one named thread standing and
end pointed at it. On paper that is momentum. Heard episode after episode it is
a hook every single time, which is a tease, and no amount of wording in the
rule stopped it being one.

**The ending rules are reversed.** The episode lands on the most concrete thing
it has and stops, mid-stride. No hook, no "but that raises another question",
no rhetorical question, no forecasting. What has *not* changed: summarising and
recapping are still banned - that was never what the tease was, and bringing
back "so, to sum up" would fix nothing.

Anything genuinely unresolved now belongs *inside* the piece, stated plainly as
unresolved, with the piece then carrying on. That keeps the honesty the old
rule was reaching for without putting it in the last sentence, where it reads
as a hook whatever it says.

**Go Deeper did not lose its suggestion - it stopped coming from the script.**
The `<<NEXT: ...>>` line survives, still stripped before synthesis and still
never spoken, but its meaning is inverted: it was "the thread you actually left
open", and it is now a *prediction*. Having heard this episode, what is the
single most natural thing this listener would go on to ask - read off the
mechanism that has an obvious next step, the figure that invites "compared to
what", the decision someone still has to make. The likeliest follow-up, not the
most interesting one, and the script is explicitly barred from gesturing at it.

That is the whole point of the change: the suggestion is *waiting* for anyone
who wants it, and costs nothing to anyone who does not. A tease charges every
listener for it whether they want to go deeper or not.

**Six tests changed, and they were pinning the old doctrine deliberately.**
They are rewritten to pin the new contract rather than deleted, including one
that asserts the removed phrases are gone, so this cannot quietly revert.

**The known cost, recorded so it is a trade and not a surprise.** CLAUDE.md
argued the widening ending was what made the browse surfaces work - "the ending
of one is the entry to the next" - and dailyFAM's infinite swipe and myFAM's
tiles were leaning on it. They now have to earn the next tap themselves. The
predicted follow-up and the prefetch plan are what that leans on instead.

Not verified against real output: there is no API key here, so `python write.py
"<query>" --minutes 3` is the check that closes this one.

## 49. `./dev.sh check` could pass without ever opening a browser

Picking the project up in a fresh container, the first `./dev.sh check` died
with `No module named pytest`, which reads like a broken repo rather than a
missing `pip install -r requirements.txt`. That is a small annoyance. The one
underneath it is not.

`playwright` is not in `requirements.txt`, so on any machine that has the deps
but not playwright, the check ran the tests, the two interface checks and the
preview build, **skipped the browser smoke test in silence**, and stopped. The
guard was written as a convenience - don't fail on a machine without a browser -
but the last line a reader sees in that case is the preview file listing, and
the run looks complete. It is not: twelve behaviours (three rails, Go Deeper,
attachments, mixes, Explore, messages, profile, mix visibility, echo on every
player) went unchecked.

This is the constraint CLAUDE.md already states - **failures must be visible,
every fallback announces itself** - broken by a skip rather than a fallback,
which is why it survived. A skipped check is a fallback: it substitutes "we did
not look" for "we looked and it was fine", and says nothing.

Two changes to `dev.sh`, both of them announcements rather than new checks:

* Missing dependencies now stop the run with the exact command to fix it,
  instead of a traceback from pytest.
* A skipped smoke test prints **SKIPPED: the browser smoke test did not run**,
  says nothing below it was checked in a browser, and gives the install command.

Verified both paths deliberately: forcing a Python without the deps produces
the dependency message, and shadowing `playwright` with a module that raises on
import produces the skip banner. With playwright installed the full run ends in
`all checks passed` with all twelve smoke behaviours listed, which is the state
this branch is in.

Worth stating for the next fresh container, because neither is in the code:
`pip install -r requirements.txt` then `pip install playwright` is the setup,
and there is still **no API key here**, so writing quality - including the
untested ending rules from §48 - remains unverified.

## 50. A demo of the product needs a product that has been used

Asked for a demo where every page can be prompted and generate episodes, so the
writing could be judged in the app rather than through `write.py`. Starting the
server and opening it is not that, for a reason that is structural rather than a
bug: **three of the five tabs read state a fresh install does not have.**

* **explore** reads finished scripts out of the shared cache and refuses to
  generate - `cached_only` is enforced in the pipeline, not in the interface, so
  the guarantee holds however the card is tapped. On a fresh database the tab is
  empty and *cannot fill itself*. Tapping it forever changes nothing.
* **myFAM**'s Trending rail ranks a global event log. An empty log ranks
  nothing, so the rail falls back to the bank in bank order - which looks like a
  feed and is not one.
* **profile** reports only what the log holds, deliberately, so it reads as a
  scaffold until something has been played.

So the demo needed seeding, and seeding needed a rule about what may be faked.
**Listeners may be invented; episodes may not.** `tools/seed_demo.py` writes
real scripts from real model calls into the shared cache, then records three
invented listeners having played them at plausible times spread across the
trending window - all-at-once timestamps produce a feed that is technically
populated and behaves nothing like a used app, because trending counts a window
and every event decays. Two echoes go in as well, so an Explore card can say
"Rachel sent you this" rather than "someone asked this"; an echo points at a
script that already exists, so it costs nothing.

It seeds **scripts and not audio**, which is the same argument CLAUDE.md makes
for prefetch: the script is the expensive half (~$0.03, seconds, cacheable
text), the audio is ~330x realtime. Seeding audio would be storing the cheap
half.

**The refusal is the load-bearing part.** With no API key the app serves a
canned sample, and a cache seeded with *that* is indistinguishable from a cache
of real episodes until someone presses play - at which point every tab plays the
same script and the demo has been lying since it started. The seeder exits 2
rather than write it.

`demo.sh` reports before it serves: live model or canned script, a real voice or
the debug placeholder tone, how many episodes the cache holds. The voice check
had to be written twice - the first version counted `list_voices()`, which
includes `debug:tone`, and cheerfully reported "1 voice" on a machine that can
only beep. That is the silent success this project keeps paying for, reproduced
inside the tool built to detect it. It now counts only non-debug voices and
names the engine.

Verified without an API key, which is all that is possible here: all five
surfaces answer, `/api/audio` streams (2.6 MB for one minute, matching the
2.65 MB/min figure), Explore returns 409 for an episode that was never generated
and replays a seeded one, and after seeding, Trending ranks six tiles while "your
circle" stays empty until the listener plays something - then it fills from
co-listener overlap. The write path was exercised with a stub writer; the model
calls themselves are still unverified here, same as everything else that needs a
key.

## 51. Three faults from one real session: a canned script, and a 429 storm

First run on a real machine, and the two things that broke were both invisible
from this container. A DailyFAM tile played a script *about how episode
generation works* instead of an episode about habits, and ordinary navigation
came back "Slow down a moment, then try again."

**1. `python app.py` never read .env.** `run.sh` and `demo.sh` source it before
starting uvicorn, so for a long time nothing in Python needed to - and then
`app.py`'s own `__main__` block, which offers exactly that entry point, started
the server without it. The key sat in .env, `settings.anthropic_api_key` was
empty, `DEMO_MODE` came on, and the app served the built-in sample script. That
script opens "This is a demonstration briefing" and goes on to describe the
streaming pipeline - which is precisely what the listener heard, under a tile
about habit research. `config.py` now loads .env itself (no new dependency;
handles `export`, quotes and comments), so the key is found however the server
is started, and a real environment variable still wins over the file.

**2. Demo mode wrote the canned script into the shared cache.** `_make_pipeline`
deliberately kept the real cache in demo mode - the comment even explains why,
and the reasoning was right for *reads*: Explore only ever replays, so it is the
one surface that needs no credentials. But writes went through the same store,
so the sample script was cached under the listener's actual query, for the full
24h TTL, where Explore and every other listener would later be served it as a
real episode - including after a key was finally added. The log line
`cached 34 sentences for 'what habit research actually shows about lasting
change'` is that happening.

This is §50's rule - **listeners may be invented, episodes may not** - which
`seed_demo.py` was written to obey while the app itself broke it on every play.
`PodcastPipeline` now takes `cache_writes`; demo mode reads and never writes.

**3. One 3-second pace was on all eighteen endpoints.** `_rate_limit` exists to
bound model spend: each generation holds a Claude stream and a TTS subprocess
open. It was applied to every endpoint including `/api/topics`, `/api/mixes`,
`/api/myfam`, `/api/explore`, `/api/profile` and `/api/voices` - and the
interface fires several of those the moment a tab opens, so the second one 429'd.
A limiter that fires on correct use is not protecting anything; it is the
failure. Generation keeps the pace; cheap reads get a burst-tolerant ceiling
(60 per 10s per client, `READ_LIMIT_PER_WINDOW`). Replay-only requests move to
the read limiter too: `cached_only` provably cannot spend a model call, so
pacing it only stopped someone swiping Explore at a normal speed.

**A fourth thing fell out of fixing the first.** Once `config.py` read .env, the
test suite started reading it too, and a developer with a key in .env would run
a different suite from CI - demo mode off, a different model, a different cache
key. `tests/conftest.py` sets `FAM_IGNORE_DOTENV=1`, so the suite stays
hermetic. It was caught immediately because a stray test .env flipped
`test_the_default_model_is_a_fast_one` red.

**And the badge was too quiet.** Demo mode was announced - a `sample script`
chip on the player at 8.5px - and the listener still spent a session wondering
why the audio did not match the tile. It is now 11px and says **not your
episode**, which is the fact that matters. The label described what the audio
was; the warning has to say what it is not.

Ten tests pin all of it (`tests/test_demo_and_limits.py`), and the fixes were
verified against a running server: the six calls a tab opens all return 200,
ten rapid Explore swipes return 409 rather than 429, and an episode played in
demo mode leaves `scripts.db` with zero rows and Explore empty.

**Anyone who ran the broken build must delete `scripts.db` once.** Canned
scripts cached under real queries do not expire for 24h and will keep playing
until they do.

## 52. Why it kept happening: everything checked the configuration, nothing checked the thing

Fourth failure in a row on the same machine, and the first one worth a section
about the pattern rather than the bug. §51 fixed the key not being *found*.
This time it was found, the app said **Live — briefings written by
claude-sonnet-5**, and then every episode 502'd on
`authentication_error: invalid x-api-key`.

Four failures, four different mechanisms, one shape:

| what was checked | what mattered |
|---|---|
| is a key set? | does the key work? |
| is a cache configured? | should *this* generator be allowed to write to it? |
| is a limiter configured? | does it fire on correct use? |
| does `.env` exist? | which of its two `ANTHROPIC_API_KEY` lines wins? |

Every one of these validated a proxy and reported success. That is the same
failure CLAUDE.md already names - **failures must be visible** - arriving from
a direction the rule did not cover: not a fallback that stayed quiet, but a
*check* that answered a cheaper question than the one being asked and then said
OK.

**The self-inflicted one first.** §51's `_load_dotenv` took the **first**
occurrence of a duplicated name. `source .env`, which `run.sh` and `demo.sh`
use, takes the **last**. So the same file authenticated differently depending
on who read it - and `demo.sh` *appended* the pasted key rather than replacing
it, so a second paste (a corrected key, say) left the stale one winning under
Python and the new one winning under the shell. The loader now takes the last,
matching the shell, and `demo.sh` rewrites the line instead of accumulating.

**The class fix: verify, do not inspect.** `app.py` now asks Claude at startup
whether the key is accepted - `models.retrieve(settings.model)`, which bills
nothing and answers both "is this key accepted" and "can this account use this
model", the two ways this has actually failed. The result is logged loudly,
carried on `/api/health` as `credentials`, and shown by the interface on every
tab as **KEY REJECTED** with a safe fingerprint of the key in force
(`sk-ant-t...0000 (32 chars, looks like an API key)`), because a 401 looks
identical whichever wrong key produced it and the first question is always
whether the one being sent is the one you think.

`mode` stays `live` in that state on purpose: it reports the configuration.
`credentials.state` reports reality. Conflating them is what let the interface
say "Live" over a server that could not generate anything.

**The reason these cluster where they do.** There is no API key in the build
container and no real speech engine, so the credential path and the audio path
are precisely the two that cannot be exercised before shipping. Every check
written here runs green without them - which is why four consecutive failures
all landed in the untested half. The startup verification does not remove that
gap; it moves the discovery from a listener mid-episode to the server's first
ten seconds, which is the most that can be done from here.

Seven tests (`tests/test_credentials.py`) pin it, including that a check which
cannot run reports rather than raises - Explore needs no credentials at all and
must keep working when the key is dead.

## 53. The key kept being re-pasted because every new copy was a fresh folder

"Would it be embedded into the code so I don't have to paste it every time?"

The question is the finding. Nobody re-enters a credential four times because
they enjoy it - they do it because the app keeps losing it, and the obvious
place to put it next is wherever is easiest, which in this case was nearly a
chat window. **The re-pasting was a symptom of a storage decision, and the
storage decision was already solved elsewhere in this repo.**

`~/.fam/voices` exists precisely because voice models inside the project folder
get re-downloaded on every new copy. The key had exactly the same problem and
none of the same treatment: it lived in a project `.env`, and every bundle
shipped for testing was a new directory with no `.env` in it.

So the key now lives in `~/.fam/env`, written by `python setup_key.py`:

* **Outside the project**, next to the voice store, for the same reason.
* **Verified before it is stored** - `models.retrieve`, which bills nothing.
  A key that does not work is worse stored than not stored: the app starts,
  reports live, and fails on the first episode. Nothing is written on a reject.
* **chmod 600**, entered through `getpass` so it never reaches the terminal
  scrollback, and therefore never a screenshot.
* **Replaced, never appended** - §52's duplicate-key trap, closed at the writer.
* `--show` reports which file the key came from and whether Claude still takes
  it; `--remove` forgets it.

**Not embedded in source, and that is not a limitation.** Source is committed;
a key in a commit stays in the history after the line is deleted, so "embedded"
means "rotate this key later". A test asserts no key-shaped literal appears in
any module.

Project `.env` still wins over the machine-wide file, so a project can pin its
own key or model.

**A latent inconsistency fell out of the voice half.** `build_engine` preferred
`espeak` over `say` while `list_voices` (and therefore the picker and the
default voice) preferred `say` over `espeak`. On a Mac with espeak installed the
picker would offer Samantha and the audio would come out robotic. The orders now
match.

Still not verifiable here: `huggingface.co` is denied by this environment's
network policy (403 on CONNECT), so `setup_voices.py` cannot be exercised end to
end from the build container and no real Piper audio has been produced here.
That is the same gap CLAUDE.md records against voice quality; the download path
is unchanged and only the store location was ever in question.

## 54. The example config turned on the two things the product exists to avoid

Reported from a real run, as a consistent pattern: three to five seconds of
audio, then 30-45 seconds of nothing with the page unresponsive, then the rest
of the episode plays **continuing from where the burst stopped** - and the
episode itself is good.

The audio half of that is fully explained, and the cause is not in the pipeline.
`.env.example` disagreed with every settled default in `config.py`:

| `.env.example` shipped | `config.py` default |
|---|---|
| `ENABLE_COLD_OPEN=1` | `False` |
| `ENABLE_WEB_SEARCH=1`, `MAX_WEB_SEARCHES=5` | `False`, `3` |
| `MODEL=claude-opus-5` | `claude-sonnet-5` |

Those three settings compose into exactly the reported shape. The cold open is
a small fast model writing one framing sentence - about eighteen words, **three
to five seconds spoken** - and then the listener waits for the real script,
which with five web searches on the slower model is **30-45 seconds**. When it
arrives it continues from where the opener stopped, because that is what the
opener is for. This is the cold-open seam of PROBLEMS 21 and 46, which CLAUDE.md
already describes as structural rather than a bug: *"a 30s researched call means
30s of preamble... the cure is a faster script, not a longer opener."*

**The finding is not the settings, it is the file.** CLAUDE.md records all three
as settled decisions with the reasoning attached; `config.py` sets them
correctly and comments why. And the file people are told to copy set them the
other way. A comment saying "off by default" is not a defence when the thing
anyone actually copies says `1`. The product's one-sentence spec - *type a
question and within about a second audio starts giving the answer* - was
configured against itself, by following the documented setup.

Fixed at the file, and then at the class: `tests/test_env_example.py` walks every
name in `.env.example`, resolves it against the live `Settings` default, and
fails on any disagreement. The per-setting assertions catch the three that are
known to hurt; the general one catches whichever drifts next. The preflight also
now shouts when either latency switch is on, naming what the listener will hear.

**What is not explained: the page being unresponsive.** `tools/stall_probe.py`
was written to reproduce it - it plants a 50 ms heartbeat in a real Chromium,
plays an episode, and reports the largest gap between beats, needing no API key
because the stall would be a function of bytes and timing rather than words. It
does not reproduce here: first audio 0.70s, the whole three-minute episode
buffered, **longest main-thread stall 0.18s**. Three candidate mechanisms were
checked and ruled out against the code: the recursive promise chain in the
fetch pump (real, but one chunk per sentence means tens of chunks, not the
thousands needed to go quadratic), synthesis blocking the server's event loop
(`say` is awaited through `_run`, not run synchronously), and per-slice
scheduling (bounded at ~4 buffer sources per second).

So the honest position: the gap is explained and fixed, the unresponsiveness is
not reproduced. The most likely remaining explanation is host CPU contention -
`say` spawning a subprocess per sentence on the same laptop as the browser -
which would starve the renderer without any code path being at fault. The probe
is committed so the next occurrence can be measured rather than described.

## 55. The filler is deleted, and the question decides whether to research

Two decisions taken after hearing the product run, both reversing things this
file previously recorded as settled.

### The cold open is gone, not off

Its own premise was arithmetically impossible. It is eighteen words - three to
five seconds spoken - and it existed to cover a research wait of 30-45 seconds.
Five does not become forty-five by tuning. And the opener's prompt said, in
capitals, **"State NO facts, figures, dates, names, results or opinions about
the topic... Frame the question; never answer it"**, so the five seconds it did
cover carried nothing. A listener heard a few seconds of throat-clearing, then
silence, then the episode.

§21 and §46 both treated this as a bug in the implementation and fixed it
twice - a rebound variable orphaning sentences, then the fill queue. Both fixes
were correct and neither mattered, because the feature could not work at that
ratio. Roughly 45 lines of `pipeline.py`, a second model call, five settings,
twelve tests and an entire measuring tool (`tools/gap_probe.py`) served
something that was never going to do its job.

**Deleted rather than disabled, deliberately.** It was already off by default;
`.env.example` turned it back on and cost a session (§54). A setting left
behind is an invitation. There is now no `cold_open` method on any generator,
no `_run_cold_open`, no `ENABLE_COLD_OPEN`, and a test asserts that a generator
which still offers a `cold_open` method is ignored rather than duck-typed back
into service.

The trade is stated plainly, because it is a real cost: **on-demand latency is
now exposed.** A researched episode makes the listener wait with nothing
playing. That was judged better than covering it with words that say nothing.

### The honest wait

What replaces it is not audio. The interface names what it is waiting for -
"Checking recent sources — this one needs today's facts" against "Writing your
episode" - and counts seconds past three, so a wait is legible rather than a
dead screen. Which message it shows comes from `/api/health`, which serves the
same keyword set the server decides with; a second copy in JavaScript would
drift and the listener would be told one thing while the server did another.

### Search is opt-in, and the question opts in

`SEARCH_MODE=auto|never|always`, default `auto`. Auto reads the query with
`needs_fresh_information()` - the same `_VOLATILE` set `cache.py` already used
to decide that "latest news on X" goes stale in minutes. It is exactly the
question "does answering this honestly need something recent", so search reads
it too. `search=1`/`search=0` on a request still wins; `ENABLE_WEB_SEARCH=1`
maps to `always` so an existing `.env` does not silently change meaning.

Deliberately a keyword test rather than a model call: classifying the query
with a model puts a round trip in front of the first word, and being wrong
costs one episode answered from memory that could have been fresher - not a
broken episode.

### On `MAX_WEB_SEARCHES=5`

Asked why five, and whether more is better. **A cap is not a target.** It sets
`max_uses` on the web-search tool: the model takes as many as it judges it
needs up to that ceiling, so raising it does not buy more research, it raises
how much the model *may* do - and each search it takes costs seconds before the
first word plus a per-search charge on top of the tokens. Five was a guess; the
comment in `config.py` said "three is enough for a briefing" while
`.env.example` shipped five, which is the same disagreement as §54 in miniature.
Now 3 in both.

Whether even three earns its latency is unmeasured, so `tools/compare_search.py`
runs one query at several depths and reports time to first word, total time,
word count and the scripts themselves - because "did the extra searches change
the writing" is a reading judgement, not a number.

### What this cost

12 tests deleted, 20 added, and the suite dropped from 30s to 8s - most of that
time was the opener tests sleeping through simulated research latency. Still
unverified here, as ever: no API key, so the search comparison has never been
run and the latency of a `never`-mode episode has not been measured on a real
machine.

## 56. Answer first, research underneath

§55 removed the filler and accepted the wait it had been failing to cover. This
removes the wait as well, without putting the filler back.

**The shape is the cold open's; the content is the opposite.** On an episode
that is going to be researched, two calls start at once: one with no tools,
which begins writing immediately, and one with web search, which is still
reading. The first is spoken while the second works, and the researched half
takes over the moment it has a sentence ready.

The old opener could never work because it was eighteen words and was forbidden
from stating a fact. Here the cover *is* the answer, written by the same model
at full length - so a listener who quits before the handover has still been told
something true.

**The two halves are divided by content, not by text.** The obvious design -
show the researched call what the opening actually said - is impossible: both
start at the same moment, and the researched call's prompt is fixed before the
opening has written a word. So neither is told the other's text; each is told
which job is whose. The opening takes what does not change week to week (what
this is, how it works, the history). The continuation is told the episode is
already playing, not to re-introduce anything, to spend its length on what is
current, and - the part that makes the seam survivable - to correct the opening
in passing if its sources disagree: *"that figure has since moved to X"*, then
carry on. A correction stated calmly is better content than the seam it hides.

**The ceiling is not a tuning knob, it is the thing that makes it work.** The
first test written against this failed, and it was right to. Synthesis runs far
faster than research: given a 3-minute episode and a 20-second search, the
from-knowledge half can finish the *entire episode* before the research lands,
and the listener gets an unresearched answer to a question that was researched
precisely because it needed today's facts. The design defeats itself silently.
`ANSWER_FIRST_SHARE` (0.5) caps how much of the episode the instant half may
speak; past it the remainder is owed to the research. A test pins that faster
research means proportionally less of the known half - that the cover tracks the
wait rather than being a fixed preamble, which is what would make it filler
again.

Also caught by writing the tests: the ceiling was first checked against
`stats.audio_seconds`, which is only filled in at the end, so it never fired.
`pace.elapsed` is the live measure. A ceiling that reads a value written after
the loop it guards is not a ceiling.

**Costs.** One extra model call on researched episodes only - unresearched ones
still make exactly one, and a test pins that. `ANSWER_FIRST=0` returns to §55's
behaviour: one call, and an honest wait.

**The interface stopped saying it was waiting**, because it is not any more.
"Checking recent sources — this one needs today's facts" became "Answering now —
checking sources underneath". Leaving the old copy would have been a fresh lie
in the exact place the last one was removed from.

Unverified, as ever, without a key: how audible the handover actually is. The
tests prove no known-half sentence is spoken after the researched half starts
and that the halves never interleave; whether the join *sounds* like a join is
a listening judgement.

## 57. The heuristic widened, because the model's own knowledge has a date on it

§55 gated research on the cache's freshness keywords. That set was written to
answer a different question - "will this episode go stale in the cache" - and it
only catches questions that *say* they are about now. "Who runs OpenAI" contains
no time word at all and is exactly the kind of thing that has moved.

**The number that forced this.** From the model docs rather than memory:

| Model | Reliable knowledge cutoff |
|---|---|
| Claude Fable 5.1 | Jun 2026 |
| Claude Opus 5 | May 2026 |
| Claude Sonnet 5 | Jan 2026 |
| Claude Haiku 4.5 | Feb 2025 |

The app runs Sonnet 5, so "answer from what the model already knows" means
answering from January 2026 - eight months back as this is written. There is no
refresh cadence: a cutoff is fixed at training and only advances when a new
model ships.

**The asymmetry decides the tuning.** Since §56 a wrong "research this" costs a
background call the listener never waits for, while a wrong "don't" costs a
confidently dated answer with no signal that it is dated. So the set is now
deliberately broad: roles that change hands (ceo, coach, minister, owner,
resigned, appointed), numbers that move (price, valuation, score, standings,
inflation), things in progress (election, trial, merger, launch, playoffs),
superlatives - which are always claims about the present - and version words.
Plus three phrase rules: a year at or after last year, "who is/runs/leads X",
and "how many/much".

It returns a **reason** rather than a bool, logged on every researched episode,
because a heuristic nobody can see the workings of is a heuristic nobody can
tune.

**What it must still not do.** "Why is the sky blue", "how does a heat pump
work", "why the Roman republic fell", "what happened in 1789" - pinned as
answered from memory, because over-triggering is cheap rather than free.

**Two test bugs caught while doing this, both the project's own recurring kind.**
The cache-sharing test compared `gen.calls` to a variable read *after* the same
run, which would have passed whatever happened - a check answering a cheaper
question than the one asked, again. It now captures the count between the two
listeners. And the year rule was first asserted against "the biggest story of
2026", which trips on "biggest" long before the year is looked at; the test was
wrong, not the code.

## 58. Searching from home showed nothing at all, because the overlay it asked for did not exist

The report was about doubt rather than about a bug: "when i click search on a
searchFAM, it just shows the searchFAM page and the user questions whether or
not their search was actually received."

They were right, and it was not a perception problem. `generate()` picked its
overlay by building an id - `"gen-" + overlayScreen` - and **there was no
element with `id="gen-home"`**. Four screens had one (`gen-detail`,
`gen-player`, `gen-playall`, `gen-explore`); home, the screen the product's
one-sentence spec is about, did not. `document.getElementById` returned null,
the code went on without complaint, and a search from the home screen showed
the home screen for as long as generation took. The honest wait added in §55
was writing its status into overlays nobody could see.

**Why it survived.** Every id was correct in isolation, and the lookup could
not fail loudly: an overlay that does not exist and an overlay that is simply
not shown look identical from inside `generate()`. Nothing tied the set of
screens that can start an episode to the set of screens that own an overlay.

**The fix removes the coupling rather than adding the fifth overlay.** One
full-screen `#famLoading` now sits inside `.frame`, above the tab bar
(`z-index:60`), and covers every surface. There is no id to derive and no
per-screen element to forget, so a sixth surface that generates gets the
loading screen for free. `overlayScreen` and its `overlayMap` are gone from
`generate()` and from every call site.

It is the brand doing the waiting: the wordmark, three chevrons rising through
`famRise`, "FAMiliarizing…", five dots. The §55 status line is kept underneath
as `#famLoadingStatus` and still says what is being waited for and for how long
- the animation is not allowed to replace the honest wait, only to frame it.
Demo-mode and key-rejected notices write into that same line.

**And a second, smaller thing the smoke test caught.** On a cache hit the
screen appeared at 90 ms and was gone at 93 ms: a 3 ms flash, which reads as a
glitch rather than as speed. `GEN_MIN_VISIBLE_MS = 450` and `finishGenOverlay()`
hold it for 450 ms once shown, and `clearGenOverlay()` stays immediate for the
failure path. Worth noting that the fastest possible case is the one that
looked broken.

Two smoke behaviours now pin it: "Searching shows the loading screen" and
"One loading screen serves every surface". Fourteen behaviours pass.

**Explore is deliberately excluded.** Its reel calls `FamAudio.play` directly
and never routes through `generate()`, because it replays cached episodes and
refuses to generate. It has its own turn animation; a loading screen there
would promise work that is not happening.

## 59. WellSaid added as an alternate voice, and the two ways that could go wrong quietly

Two specific WellSaid voices - Chase J (speaker 35) and Kai M (speaker 32) -
wanted A/B testing against Piper inside the real product rather than in
isolation. The engine interface made this small: `TTSEngine.synth(text, wpm,
voice) -> PCM` plus a `voice:` id prefix that `engine_for_voice()` routes on,
so a new engine is a new class and nothing in the script pipeline changes.
`pipeline.py`, `script_generator.py`, search, myFAM and the player are all
untouched.

**Verified rather than assumed**, since the brief asked for the current API:
`POST https://api.wellsaidlabs.com/v1/tts/stream`, `X-Api-Key` header,
`{"text", "speaker_id"}` body, ~1000 characters per request. Their docs site
is unreachable from the build container, so this came from their published
reference and their own example repo - which is also where `WELLSAID_API_KEY`
comes from as the variable name. **Unconfirmed anywhere reachable: whether they
serve WAV as well as MP3.** So the request asks for WAV and accepts MP3, reads
whichever actually arrived, and logs it. WAV needs no decoder; MP3 goes through
ffmpeg in memory, and if ffmpeg is missing the error says so rather than
producing silence.

**Two failures this could have shipped with, both caught by writing the test
first:**

1. **A paid engine became the default.** `default_voice()` returned
   `list_voices()[0]`. On a machine with no Piper installed - which is most
   fresh containers - WellSaid was first, so every listener would have been
   billed per character without anyone choosing it. `default_voice()` now skips
   paid engines entirely and falls back to the placeholder tone, which
   announces itself as broken. A surprise bill does not.

2. **It would have silently become Piper.** `engine_for_voice()` falls back to
   the best available engine when a voice is unavailable, which is right for a
   Piper voice that was uninstalled - the listener still hears their episode.
   It is exactly wrong here: the only reason to select WellSaid is to hear
   WellSaid, so a fallback means judging one engine by another's output. It now
   raises with the reason and the command that fixes it.

**Sample rate had to be decided, not discovered.** `app.py` writes the stream
header from `engine.sample_rate` before the first synthesis, so the engine
cannot wait to be told what WellSaid sends. Everything is resampled on arrival
to the app's own rate, which is fixed and knowable.

**Chunking is mostly theoretical and still built.** The pipeline already
synthesises one sentence at a time, so the 1000-character ceiling is reached
only by an unusually long one. When it is, the split runs sentences, then
clauses, then word boundaries - never mid-word - and the near-silence at each
end of a chunk is trimmed before joining, because independently rendered chunks
each carry their own lead-in and tail and concatenating them untrimmed stacks
into a pause in the middle of a sentence. Single-chunk audio is not trimmed:
that would eat the gap the pipeline puts *between* sentences.

**What WellSaid cannot do:** there is no speaking-rate parameter, so the pacing
controller's wpm is advisory. Logged once per process rather than per sentence.
Duration is still a ceiling and over-runs are still trimmed.

**Two test bugs of my own, the project's recurring kind.** `test_empty_text`
had no key fixture and revealed a real ordering bug - the key was checked
before the empty-text check, so synthesising a blank line raised instead of
costing nothing. And patching `ws.asyncio.sleep` with a lambda that calls
`asyncio.sleep` patches the very function it then calls: the retry recursed
until the stack ended.

## 60. The WellSaid key could not be entered, and a good key was being thrown away

Reported as: "FAM starts without prompting me for the WellSaid API key, the
terminal says there are no WellSaid voices because the key is not set." Two
separate faults, and each on its own is enough to produce exactly that.

**1. The prompt answered itself.** `start.sh` asked "Paste your WellSaid key
now? [y/N]" and read the reply with `read -r answer`. `read` returns
immediately at end of input, so the moment stdin is not an interactive
terminal the question is printed and declined in the same breath. From the
outside that is indistinguishable from never being asked - which is how it was
reported, and correctly so.

The fix removes the question rather than fixing it. `setup_wellsaid.py`
already asks for the key itself and already treats an empty line as "nothing
changed", so the gate added a way to fail and nothing else. When stdin is not
a terminal the script now says so and prints the command to run, instead of
staging a conversation it cannot have.

**Worth naming: this passed its own test.** The previous session tested the
declined path by piping `n` into the script and confirmed it carried on. That
proves the branch works and says nothing about whether a person can reach the
other one. Testing a prompt without a terminal tests everything except the
prompt. It is now driven through a real pty, and `tests/test_start_script.py`
pins the shape - no `[y/N]`, no `read -r answer`, an explicit `[ -t 0 ]`.

**2. A working key was refused because ffmpeg was missing.** `works()`
validated a key by synthesising one line and treated *any* exception as
"REJECTED - nothing was stored". So on an account that returns MP3, with
ffmpeg not yet installed, WellSaid answered HTTP 200 - the key was fine - the
local decode failed, and the key was discarded with a message implying the key
was bad. Install ffmpeg afterwards and there is still no key, and the app
truthfully reports "no WellSaid voices - its key is not set".

This is `verify, do not inspect` (§52) overshooting in the opposite direction.
That rule says a check must perform the real action rather than confirm
configuration. It does not say a check may answer a *broader* question than
the one it was asked. "Does WellSaid accept this key" and "can this machine
decode what WellSaid sends" are two questions with opposite consequences for
whether the key is stored.

Three verdicts now, from three exception types rather than from words in a
message - `WellSaidError`, `WellSaidLocalError`, `WellSaidUnreachable`:

| What happened | The key |
|---|---|
| WellSaid returned audio | stored |
| WellSaid returned audio, this machine cannot decode it | **stored**, with the fix named |
| WellSaid refused the key | not stored |
| The request never arrived | not stored, and *not* called a rejection |

The last row matters as much as the ffmpeg one: a blocked network was
reporting "REJECTED", which sends someone off to regenerate a key that was
never the problem. `--no-verify` exists for a network that blocks the API.

Typed rather than string-matched on purpose. The first version of this fix
tested `if "ffmpeg" in message`, which is the same trap one level down: the
wording changes and the meaning silently flips.

Piper is untouched throughout, and a test says so.

## 61. WellSaid removed: two episodes exhausted a month's quota

Not a quality verdict. **Two episodes spent a month's allowance.** A 3-minute
FAM episode is about 2,610 characters, so the whole trial cost roughly 5,200
characters - which says the product was sold per seat with a monthly ceiling,
and was being used as if it were an API billed per character.

That is a category error rather than a vendor one, and it generalises: the
first question about any hosted voice is now "is it billed per character with
no monthly ceiling", asked before anyone listens to it. `VOICE_OPTIONS.md`
carries the shortlist and the arithmetic.

**Deleted, not switched off**, the same as the cold open in §55 and for the
same reason: a knob left behind is an invitation to turn it back on, and this
project has had exactly that happen before via an example file. Gone:
`wellsaid.py`, `setup_wellsaid.py`, its two test files, `WELLSAID_TESTING.md`,
the settings block, the `ffmpeg_binary` setting that only it used, the second
key prompt in `start.sh`, and the `var` parameter added to `write_key()` for a
second provider that no longer exists. 329 tests pass, down from 358 - the
difference is the WellSaid tests and nothing else.

**Two guards went with it, deliberately, and are worth re-adding by hand
rather than inheriting.** Both were real bugs found during the experiment, and
both are properties of *any* hosted engine:

* `default_voice()` returns the first offered voice, so merely registering a
  paid engine made it the default on any machine without Piper installed -
  every listener billed per character, nobody having chosen it.
* `engine_for_voice()` falls back to the best available engine, which is right
  for an uninstalled local voice and wrong for a hosted one: substituting
  Piper means judging one engine by another's output.

Leaving them as dead code with an empty set of paid engines would be worse
than writing them down, so they are written down in `VOICE_OPTIONS.md`.

**What the experiment did establish, and is worth keeping:** the `TTSEngine`
interface is the right shape. A new voice was a new class plus a `voice:` id
prefix, and touched nothing in `pipeline.py`, `script_generator.py`, the
cache, search or the player. Whatever replaces Piper plugs in the same way.

**And the process lesson.** Integration came first and listening came second,
so a week of plumbing was spent before the thing that killed it - the billing
model - was visible at all. It would have been visible in five minutes on the
pricing page. Next time: synthesise the same script through each candidate
offline, listen, decide, and only then write code.

## 62. Search never ran, because an omitted parameter arrived as an explicit "no"

Reported as "the model doesn't seem to be searching the web no matter what the
prompt is". It wasn't. Not for any prompt, ever, in the app.

`plan_episode` consults the freshness heuristic **only when `search is None`**,
because an explicit `True`/`False` is the listener's own choice and has to win
- that is what "opt in" means (§55). But `/api/audio` declared:

    search: bool = Query(False)

so FastAPI turned an *omitted* parameter into an explicit `False` before the
planner ever saw it. And the browser never sends `search=` at all. Every
episode the app has ever produced therefore said "the listener asked for no
research", and `SEARCH_MODE=auto` - along with the whole widened heuristic of
§57 - was dead code in production.

`/api/next` had the same default, which is worse than it looks: it reads the
cache entry `/api/audio` wrote, so if the two ever disagreed about whether an
episode was researched they would disagree about the key and Go Deeper would
silently never find a thread. And `/api/script` accepted a `search` field and
then dropped it on the floor - it never passed it to `_validated_plan` at all.

**Why every test passed.** They called `plan_episode(...)` directly with the
argument left off, which produces `None` and exercises the heuristic
beautifully. That is not what the app does. **A default that is correct inside
a function and wrong at its boundary is invisible to any test that starts
inside the boundary.** `tests/test_search_reaches_the_app.py` starts outside
it, driving real requests through `TestClient`; on the old code six of its
thirteen fail, while all 37 of the older search tests still pass. That gap is
the whole lesson.

Same shape as §52 ("verify, do not inspect") one level out: the tests answered
"does the heuristic work" when the question was "does the heuristic run".

**And the decision was silent when it went the wrong way.** `plan_episode`
logged only when it *did* research, so the state the product was actually
stuck in produced no output at all. It now prints `SEARCH yes` / `SEARCH no`
with the reason on every episode, so watching the terminal answers the
question that took a user report to surface.

Unrelated but worth recording: `tools/compare_search.py` passes `search=`
explicitly, so it was never affected. The measurements it produces were always
going to be right; it is the app that was not searching.

## 63. Two pieces of state, and the schema that was nearly adopted instead

A schema was proposed for the app - `USERS`, `EPISODES`, `EPISODE_HISTORY`,
`LIKES`, `TAGS`/`EPISODE_TAGS`, `SAVED_SEARCHES`, `DAILY_FEED`, `MESSAGES`, on
Postgres. It is a competent schema for a podcast catalogue and the wrong one
for this app, and the reasons are worth recording because they will be
proposed again.

**There is no episode to have a table.** An episode here is generated from a
query at the moment of the tap and never stored; what persists is the *script*,
keyed on `(normalized query, minutes)` in `cache.py`, expiring. `podcast_name`
has nothing to point at. Worse, some episodes must never become rows at all:
`_VOLATILE` queries refuse caching, `_PERSONAL` ones refuse sharing, and an
episode carrying attachments is never cached. A foreign key to `episodes.id`
would have to reference things the design forbids storing.

**`EPISODE_HISTORY` as one mutable row per (user, episode) is the log inverted.**
`topics.py` is append-only with time decay - a 14-day half-life and a 3-day
trending window - and none of that is computable from a row that only remembers
the latest state. The proposal offered session-level rows as an optional child
table; here that is the primary and the collapsed view is a query.

**`pause_reason` would have been invented data.** `skip` already carries the
negative signal at -1.5. Inferring "distracted" from an app going to background
is a guess stored as a fact, which is exactly what `summary()` exists to avoid.

**`LIKES` duplicates signal, and the explicit-endorsement primitive already
exists** - an echo, which points at a *query* rather than an episode id, which
is the shape this app actually has.

Two things in the proposal were real, and both are now built.

**Identity (`social.py`).** `people` had a row only once someone chose a display
name, so the app could not answer "who is out there" or "when were they last
here" for nearly all of its listeners - they had a taste profile and no record.
`seen()` makes every listener id a row with `joined` (first sighting, already
there) and `last_seen`; `active_since()` is the recency signal a daily feed
needs, because a decayed event log tells you what someone liked, not whether
they came back. No email, no fingerprint, no counters - `known` distinguishes
"never been here" from "here, unnamed", which the profile page could not do.
When accounts arrive the work is attaching credentials to rows that exist,
rather than inventing a user table underneath live data. `seen()` is called
from `/api/myfam`, `/api/event` and `/api/profile`, and on `/api/audio` only
after the pipeline is built and beside the existing play - never in front of
the first word.

**Impressions with an algo version (`topics.py`).** The one genuinely good idea
in the proposal was `DAILY_FEED.algo_version`: one row per recommendation, so
"why did we show this" is answerable and two rankings are comparable. It is
built as an event kind rather than its own table, so decay and windowing stay
uniform, with `section` and `algo` columns and `ALGO_VERSION` stamped on each.

**The trap in it, which is the load-bearing part.** A feed load shows ~18 tiles.
`for_user` is capped at 400 rows and is what `taste()` reads. Logged naively,
roughly twenty visits to myFAM would push every play, completion and search out
of the window, and the taste model would be trained on what the feed showed
instead of on what the listener did - a recommender learning its own output.
So impressions are excluded from `for_user`, carry no `EVENT_WEIGHT`, and are
read only by `impressions_for`, which nothing in the ranking path calls. Four
tests pin that from both ends. They are also the only writer that grows the
table quickly, so `IMPRESSION_TTL` prunes them at 30 days - at most hourly, and
never touching a behavioural event.

`build_feed` stays a pure function of the log; the write happens at the request
boundary in `app.py`. A ranker that writes cannot be called from a test.

**Both migrations widen tables that already hold data.** The script cache can be
dropped and regenerated; the event log cannot, so the `ALTER TABLE` path is
tested against the exact legacy schemas rather than assumed - including opening
the same store twice, which every worker on the machine does at startup.

**On Postgres.** Right eventually, wrong now, and for a different reason than
the proposal gave. The trigger is not row counts - this will not see tens of
millions of rows - it is process count. The five SQLite files are shared by
every worker *on one machine*; the moment there are two machines they are not
shared, cache hit rate collapses and every miss costs ~$0.03 again. That is the
migration signal, and it moves the same five schemas into one database rather
than re-modelling them.

Nothing here needs an API key, and none of it touches generation, the player,
or the script.

## 64. The databases followed the working directory, and two never reached the disk

Every store defaulted to a bare filename - `myfam.db`, `social.db`, `mixes.db`,
`scripts.db`, `attachments.db`. A bare filename is resolved against the
**current working directory**, which is not a property of the app: it is a
property of wherever the person starting the process happened to be standing.

**The failure pointed the wrong way.** Nothing raised. SQLite created a second,
empty set of files, so the app came up with a cold script cache (every episode
paying ~$0.03 again), an empty feed, no echoes and no mixes - and it read as a
broken feature rather than a wrong path. `tools/seed_demo.py` was caught by
exactly this: it constructs `EventStore()` and `SocialStore()` with no
arguments, so seeding from one directory and serving from another leaves
Explore empty however much you tap it, which is precisely the symptom §50 built
`demo.sh` to prevent.

The env vars already existed and were already read in `app.py` and `config.py`.
What did not exist was a **default that did not move**, so the fix is one
resolver rather than five call sites. `paths.py` derives `PROJECT_ROOT` from
`__file__`, and `data_path(env_var, filename)` gives:

* nothing set - the file sits in the project root, the same place however the
  process was started;
* an absolute env var - used exactly as given, which is what a deployment does;
* a relative env var - resolved against the project root too, **and logged**.
  Quietly reinterpreting it would put back the ambiguity this removes.

Each store now takes `path: str | None = None` and resolves its own variable,
so the mapping lives with the store instead of being restated in `app.py`, and
anything constructing a store directly gets the right file for free.

**Two variables were missing from the Dockerfile, and that one was not
theoretical.** It pinned `CACHE_PATH`, `MYFAM_DB` and `MIXES_DB` to the mounted
`/data` disk but not `SOCIAL_DB` or `ATTACHMENTS_PATH`, so in a deployed
container those two were written to the image's WORKDIR - **every redeploy
silently discarded every listener's name, handle and echo.** The comment above
them said "mixes and listening history survive a redeploy", which was true and
was the reason nobody read further. All five are named now, and a test fails if
a sixth store is ever added without one.

**`.env.example` ships no value for any of them, deliberately.** A default that
named a directory would be wrong on every machine but the one it was written
on, and `tests/test_env_example.py` compares the example against live settings,
so a literal there would now be a permanent disagreement. They are documented
as commented lines that say to use absolute paths.

**The static mount had the same bug and was hiding this one.** `app.py` mounted
`StaticFiles(directory="static")`, also cwd-relative. That one failed loudly -
starting the server anywhere else raised `Directory 'static' does not exist` -
which meant the *server* never got far enough to demonstrate the quiet database
version. It is resolved from `PROJECT_ROOT` too, and a test rejects any bare
`directory="..."` in `app.py`.

Verified rather than inspected, per §52: the server was started from an
unrelated directory with no path variables set. It served the interface (200),
answered `/api/myfam` (200), wrote the listener and six impressions into the
**project-root** databases, and created **zero** files in the directory it was
started from. 23 new tests; 388 pass.

## 65. A missing directory said nothing useful, and health reported one store of five

Two gaps left over from §64, both about a database saying what it is doing.

**A missing directory failed in sqlite's words, not the app's.** Pointing a
variable at a directory that did not exist yet raised `unable to open database
file` from inside `sqlite3`, at import time - naming neither the setting that
was wrong nor the directory that was missing, and killing the app before it
logged anything of its own. Since §64 put five path variables in
`.env.example`, that was a foreseeable first experience. `paths._ensure_parent`
now creates the directory (announced, and matching the `RUN mkdir -p /data` the
Dockerfile already did by hand) and, when it cannot, raises `DataPathError`
naming the variable, the path and the directory.

**`/api/health` reported the script cache and nothing else.** It returned
`"status": "ok"` while the other four could be pointed anywhere or be
unwritable, and no runtime surface named a single path. That is exactly the
§52 failure - confirming configuration instead of verifying readiness - inside
the endpoint whose job is to report readiness. It now lists all five with the
path each store is *actually* holding, whether the variable was set, size, and
a real read.

**The first version of that check was the same mistake again, and a test caught
it.** It used `SELECT 1`, which is a constant expression: sqlite answers it
without touching the file, so a path containing nothing but rubbish came back
`readable: true`. The check is now `SELECT count(*) FROM sqlite_master`, which
forces the header and schema to be parsed, and there is a test that writes
garbage to a file and asserts health calls it broken. Worth recording plainly:
§52 was written after four consecutive failures of this shape, and the fifth
happened while writing the fix for it.

`writable` is `os.access`, and is labelled a permission check rather than
dressed up as a performed action - writing on every health poll would cost more
than it tells anyone. 392 pass.

## 66. Accounts, and the shape that kept the app usable without one

A listener was whatever id the browser sent. `famUserId()` made one up with
`Math.random()`, kept it in localStorage and put it in the query string of
every request; nothing checked it. So `?user=<someone else>` read their
profile, renamed them, deleted their mixes and echoed as them. That was
defensible while everything was stateless. §49 keyed a durable listener table
on that id, which made it a real hole rather than a theoretical one.

**The shape of the fix mattered more than the fix.** "Add accounts" reads as
"put a login in front of the app", and that is wrong twice: it puts a form in
front of the first word, which the one-sentence spec forbids, and it discards
the history of everyone who has used the app so far. So:

> An identity is a **session**. An account is **credentials attached to a
> session's identity**.

Every request resolves a session from an HttpOnly cookie. With no cookie the
server *mints* one - `secrets.token_urlsafe`, high entropy, never chosen by the
client. That listener is anonymous and everything works for them: search,
myFAM, Go Deeper, mixes, echoes. Signing up attaches an email and password to
the id they already have, so there is **no migration and nothing to claim** -
it is the same `user_id`, and their history is simply theirs now, on any device
they log in from. Logging out drops the session and the next request mints a
fresh anonymous one.

That gets the property that actually matters - an id can no longer be forged -
without a login screen in front of anybody.

**Its own database.** `accounts.db` rather than a table in social.db, because
it is the only store holding secrets; mixing password hashes into the file that
holds echoes would give the whole thing the strictest of those properties by
accident. The §64 guard earned its keep here: adding a sixth store made
`test_the_dockerfile_puts_every_database_on_the_mounted_disk` fail until the
Dockerfile pinned it to the mounted disk, which is exactly the redeploy
data-loss bug §64 found, caught before it happened this time.

Details worth keeping:

* `hashlib.scrypt`, standard library, no new dependency. ~47 ms per attempt.
  Parameters travel with each hash so the cost can be raised later.
* **The session token is never stored** - only its SHA-256 - so a leaked
  database yields no live sessions.
* Login mints a *fresh* token rather than repointing the current one, or a
  token captured before login would keep working after it (session fixation).
* Wrong password and unknown address return the identical message, and the
  unknown-address path still pays the hashing cost. Otherwise the form is an
  account-enumeration oracle.
* Changing a password ends every session, including the one that changed it.

**Two mistakes worth recording, both caught by tests.**

The first was mine in the test rather than the code: an expiry test asserted a
session was dead past its TTL, but the earlier assertion in the same test had
*used* the session, which slides the expiry forward by design. Split into two
tests - one for an unused session expiring, one documenting the slide.

The second was a real gap in §65's own fix. `_ensure_parent` covered the env
var and the default but not a path passed directly to a store, so the moment a
fixture pointed at a new subdirectory it failed with the exact `unable to open
database file` that §65 exists to prevent. `data_path` now takes the explicit
path as an `override` and gives it the same guarantee, so there is one function
that owns every path decision instead of two routes with different promises.

**Known gaps, named rather than discovered later.** There is no password reset,
because it needs email delivery the app has no route to - a forgotten password
today is a lost account, and that must be said before anyone relies on it.
There is no admin, no email verification, and nothing is *gated* on having an
account: it buys you your data on a second device, and nothing else yet.

Verified on a running server, not just in tests: an anonymous listener finished
an episode, signed up, and kept the same id and their history; a second cookie
jar logged in and saw that history; `?user=<their id>` returned an empty
profile and failed to rename them; and the cookie came back
`HttpOnly; SameSite=lax`. 426 pass.

## 67. The preview proves layout; this one proves storage

`build_preview.py` ships `static/index.html` with every `fetch` answered from
fixtures. That is the right tool for layout, flow and interaction on a phone,
and it proves nothing about state: tap the same tile twice and the second tap
is the same canned JSON as the first, so nothing about the event log, the
cache or identity is exercised.

`build_live_preview.py` takes the **same interface** and swaps only that one
layer. The fixture shim becomes a small API implemented against the Artifact
`db` capability, mirroring the response shape of every route `app.py` serves,
so `static/index.html` runs **unmodified** - which is the whole claim: what you
click is the shipped frontend, not a mock of it.

Real: the append-only event log, impressions carrying `section` and
`ALGO_VERSION` (read from `topics.py` at build time rather than retyped),
`for_user` excluding them, the normalised cache key with its hit counter,
sessions, accounts attaching to the id a listener already has, mixes, echoes,
the listener table. Synthetic: `/api/audio` returns silence of the right
length, and `/api/topics`, `/api/voices` and `/api/health` are reference data.
There is no model and no speech engine in a published page.

**The verification is the repo's own smoke test.** `tools/smoke_preview.py`
takes a path, so it drives this build exactly as it drives the fixture one -
all fourteen named behaviours pass against the database. Five of them failed
first, every one for the same reason: a fresh store is empty where the fixture
preview ships pre-populated. That is §50's point again - Explore replays other
listeners' episodes and cannot generate one - so the shim seeds on first boot
only when the store is genuinely empty, the browser equivalent of
`tools/seed_demo.py`.

Two things the browser run corrected. The health fixture has no `mode`, so the
app fell through to "Audio server unreachable - start it with ./run.sh": true
of a server and useless advice inside a published page. Health now reports
`demo`, and the shim replaces that one sentence with what is actually true of
this build. And the session token lives in `localStorage` rather than the
HttpOnly cookie the server sets, because a published page has no cookie of its
own - the single divergence, stated in the badge the shim renders.

## 68. The near-match cache, and the measurement that says the vector is not earning it

**The problem.** The script cache keys on a SHA-256 of `normalize_query`, which
is an exact token set. It collapses punctuation, word order and filler, and
nothing else - it does not stem, and it has no idea that "the fall of the roman
empire" and "why did the roman empire fall" are one episode. Measured on a
corpus of 20 questions asked three ways each, **9 of 41 re-phrasings found the
episode already sitting in the cache**. The other 32 paid ~$0.0096 and several
seconds to write a script that existed.

`cache.canonical_key` was the existing answer and it is off by default for a
good reason: it puts a model call (~300-500 ms, ~$0.0002) in front of *every*
request, which is pure overhead on a miss. That is the wrong side of the
one-sentence spec to spend on.

**The idea, and why it belongs at write time.** Embed the question once when
its script is stored, and compare locally when the next question arrives. The
lookup then costs a scan, not a round trip. This is the same trade the whole
prefetch plan rests on - do the expensive part before the listener is waiting -
applied to matching rather than generating.

**What was built.** `embeddings.py` (one vector per query, two backends),
a `vector BLOB` and a `bucket TEXT` column added to `scripts` by the existing
additive-`ALTER` pattern, `cache.best_match` / `cache.comparable`, a
`nearest()` on both cache backends, `CACHE_VECTOR` off by default, and
`tools/bench_vector_cache.py` to measure it.

`bucket` is everything in the cache key **except** the question - duration,
context, whether it was researched, the model, and the vector space - so a scan
only ever compares entries that were interchangeable to begin with. Putting the
vector space in it means switching embedding backend retires the old vectors
instead of comparing coordinates that no longer mean the same thing.

**sqlite-vec was the obvious thing to reach for and is premature.** Benchmarked
here, exact cosine KNN over 384-dim vectors takes 0.06 ms at 1,000 vectors,
0.39 ms at 10,000 and 2.35 ms at 100,000, against a live cache bounded by a
24-hour TTL. An extension means a loadable binary on every deployment to save a
number nobody can perceive. The scan is brute force, capped at
`CACHE_VECTOR_SCAN` rows, and measures 5.5 ms over 400 vectors in pure Python -
which is the entire miss-path overhead, against 3-5 seconds of generation.

**The guards matter more than the threshold.** A cache miss costs a cent; a
false hit plays a fluent, confident answer to a question nobody asked, which
fails the first duty of an episode - *satisfy the thing that brought them* -
before a single word of it is wrong. So a score has to clear a bar and then
survive three checks a vector is known to be bad at: numbers must be identical,
the questions must share a set fraction of their actual words, and they must
agree about needing today's facts. `comparable()` returns the *reason* rather
than a bool, for the same cause `research_reason` does.

Two of those guards were written because the bench found the failure, not
because they seemed wise:

* **"the causes of world war one" and "the causes of world war two" score
  0.756.** With the threshold at 0.76, four thousandths of cosine were the only
  thing between a listener and the wrong war. The digit guard could not see it
  because the numbers were spelled. `embeddings._NUMERALS` folds spelled
  numbers to digits, which closes it - and pays for itself on the other side,
  since "week five" now reaches the episode cached for "week 5".
* **The two halves of the decision disagreed with each other.** The vector
  folded numerals and the overlap guard did not, so "week five" and "week 5"
  were one question to one half and two to the other, and a perfect match was
  refused for having half its words in common. `_token_set` now goes through
  `embeddings.tokens` too.

**The result, and it is not the one the idea predicted.**

| | re-phrasings found, of 41 | wrong episodes served, of 20 |
|---|---|---|
| exact keys only (today) | 9 | 0 |
| + near matching, shipped defaults | 23 | 0 |
| the guards alone, cosine ignored | **23** | 0 |

Near matching is worth **+14 of the 32 missed re-phrasings** - a 22% hit rate
becomes 56% on this corpus. But the third row is the finding: at the safe
operating point **the vector contributes nothing**. Everything the cosine finds,
the free token-overlap guard finds too. The cosine only adds recall (26 of 41)
at an overlap floor of 0.5, and there the margin to the nearest wrong answer
collapses to 0.024 - a false hit waiting for a question phrased slightly
differently, which is not a trade worth making for three matches.

That is what a **lexical** embedding is worth here, and the bench says so in
its own output rather than leaving it to be inferred. The shipped backend is
signed hashing over words and character 3-grams: no dependency, microseconds,
deterministic across workers - and permanently unable to know that "car" and
"automobile" are the same thing. The mechanism is not the problem. The
embedding is.

**So the defaults are conservative on purpose.** `CACHE_VECTOR=0`.
`CACHE_VECTOR_THRESHOLD=0.68` and `CACHE_VECTOR_OVERLAP=0.6` are the
highest-recall setting at which *every* must-not-collapse pair is refused by a
guard rather than by a threshold - so there is no near miss waiting for a query
slightly unlike the ones measured. The bench prints a `margin` column for
exactly that distinction: zero false hits is not the same as being safe.

**What is untested.** `embeddings._OnnxEmbedder` loads a real sentence model
from `~/.fam/embed`, on the same reasoning as the voice models - it ships with
the app rather than billing per call. **No model exists on this machine, so
that path has never produced a vector.** It is in the same position the Piper
ONNX path was in before `verify_voice.py`: written, plausible, unproven. The
honest next step is not more tuning of the lexical backend - it is installing a
model, re-running the bench, and watching whether the "guards alone" row stops
matching the row above it. That single line is how anyone will know the
embedding started earning its place.

`describe()` reports which backend is live, `/api/health` carries it whenever
near matching is on, and the bench refuses to print numbers without a header
saying whether they measure meaning or spelling. Reporting the setting without
the backend would be §52 in a new place: "vector matching is on" reads like
semantics, and with no model installed it is not.

## 69. numpy was needed and undeclared, and only a clean install could tell

**The problem.** The first CI run on PR #2 failed six tests, all with
`ModuleNotFoundError: No module named 'numpy'` at `tts.py:477`. `./dev.sh
check` had passed 852 tests locally minutes earlier.

**The cause.** `tts.pcm_from_float` and `ChatterboxEngine._synth_blocking`
import numpy, and `requirements.txt` did not list it. Every machine anyone had
developed on already had numpy sitting there as somebody else's transitive
dependency - piper pulled it in when piper was still a dependency, torch pulls
it on a GPU box. CI installs `requirements.txt` and `pytest` and nothing else,
so CI is the only environment that is a genuinely clean install, and therefore
the only one that could see it.

Reproduced before fixing, rather than assumed: hiding numpy behind a
`sys.meta_path` blocker locally produces the identical six failures.

**Why the fix is a declaration and not a skip.** Marking those tests
`importorskip("numpy")` would have turned CI green in one line. It would also
have deleted the only coverage the production voice has on a machine that
cannot run it - the engine contract tests exist precisely to check the
Chatterbox adapter with `chatterbox` and `torch` stubbed out, which is the one
configuration CI can exercise. Green by removing the check is the §51 failure
in a new costume.

numpy is also not filed under the deep-learning stack, tempting as that is.
`pcm_from_float` runs on the synthesis path with torch absent, and the stdlib
is not a substitute: a 3-minute episode at 24 kHz is ~4.3M samples, and
converting those one at a time in Python would put seconds on the one path this
product refuses to spend seconds on.

**`pydantic` went in at the same time**, for the same reason one step earlier.
`app.py` imports `BaseModel` and `Field` directly, and it has always worked
because fastapi happens to require pydantic. A dependency present by luck is
one nobody notices losing.

**The general fix: `tests/test_requirements.py`.** Every third-party import in
the shipped root modules must either be declared in `requirements.txt` or be
named in `DELIBERATELY_OPTIONAL` with the reason it is safe to be missing.
Seven are legitimately optional - chatterbox and torch belong in
`requirements-chatterbox.txt`; onnxruntime and tokenizers are the embedding
model `embeddings.py` falls back from and says so; h2 and httpx2 are imports
whose *absence* is what `diagnose_api.py` reports. The list makes an eighth a
deliberate two-line change instead of an invisible one, and a second test
fails when a name outlives the import it was written for.

This is the same shape as §54 (`.env.example` disagreeing with `config.py`) and
§64 (a path resolved against the working directory): the thing was true where
it was written and false where it ran, and nothing compared the two. It is
worth noticing that all three were caught by an environment that was *poorer*
than the developer's, not richer.

## 64. A smoke pass over everything forked in today

Trunk had taken a lot of merges - Phase 6 as the default pipeline, Chatterbox,
the database, accounts, and Exa as the default research backend. This is what a
full pass over it found.

**CI was red, and the whole loop was blocked behind it.** One test,
`test_health_flags_a_deployment_that_asked_for_exa_and_cannot_run_it`, failed on
any machine without `exa_py`. `diagnose()` reports the *first* missing
prerequisite, so without the package it says "exa_py is not installed" and never
reaches the key check the test asserted on. CI installs `requirements.txt` only,
so it never had the package. Worse than one red test: `dev.sh` runs under
`set -e`, so the interface checks, both preview builds and the browser smoke
tests never ran at all.

The test was wrong, not the code - `research.py`'s own docstring says "the test
suite must run without it". It is now split in three, each deterministic
whatever the machine has (a `None` entry in `sys.modules` makes the import
raise; a bare module makes it succeed): the package missing, the key missing
once the package is there, and both present. A fourth pins that the `claude`
backend never reports unavailable for a missing Exa key. Confirmed each fails
when the behaviour it covers is broken.

**A listener was told to read a server log.** `friendly_error` reduced
`ResearchUnavailable` to "Generation failed: ResearchUnavailable. See the server
log for details." The exception already carries the remedy - "exa_py is not
installed. `pip install -r requirements-exa.txt`" - and that is the one sentence
that would let someone fix it. The browser is exactly where a server log cannot
be read. Now surfaced verbatim.

**The preflight had a fourth blind spot.** `demo_preflight.py` names three ways
the demo can look like it is working - no key, no voice, empty cache. Research
is now a fourth: Exa is the default, it needs an optional package and a second
key, and without them a time-sensitive question *fails* rather than answering
from memory. That is deliberate, but discovering it by asking about today's news
is not. It now reports the backend, and the per-tab summary says search will
fail on a time-sensitive question.

**`dev.sh` never built the demo it ships.** There are two preview builders -
`build_preview.py` (fixtures) and `build_live_preview.py` (the app beside a real
database). Only the first was in "the whole loop in one command", and the second
is the one that gets published. That is how the wrong file nearly got published
today. Both are now built, and both are smoke-tested.

**A stale comment said the opposite of the code.** `requirements-exa.txt` still
claimed "The default backend is `claude`". It also now states the consequence
plainly rather than leaving it to be discovered: a fresh
`pip install -r requirements.txt` produces a deployment whose *default* research
backend cannot run. Nothing about that is silent - health reports it and a
researched episode fails rather than quietly searching another way - but a
researched episode does fail.

**What was checked and found sound.** End-to-end through a running server: 60.0s
of audio for a one-minute request in 0.26s. `stall_probe`: first audio 0.66s,
longest main-thread stall 0.16s, no stall. The app primes before it responds, so
a failure before the first byte becomes a proper 502 rather than a silent empty
episode. `answer_first` correctly follows the backend - off for Exa because
there is no wait to cover, on for `claude` where the model's own search costs
10-25s. The vector cache reports honestly that at the safe operating point the
embedding adds nothing the token-overlap guard did not already find.

989 passing, 3 skipped, both previews green in a browser.

## 65. The validated configuration and the shipping default had diverged

The Phase 6 validation on an RTX 4090 - production `app.py` through
`tools/pod_production_test.sh`, Chatterbox cloning `reference_3.wav`, warm
resident model, `CACHE_ENABLED=0` - produced episodes judged good, with first
audio at roughly 4.5s and 2.992s and research handing off mid-episode. Later,
the same harness on the same code would not reproduce it. The harness was not
the variable.

**One commit, one line.** `91d9dad` ("Make the direct research path the default")
replaced

    default_factory=lambda: os.environ.get("ANSWER_FIRST", "1") not in (...)

with `_answer_first_default()`, which derives the value from the research
backend via `SLOW_RESEARCH_BACKENDS = ("claude",)`. Evaluated rather than read:

    validation-era (14360d4), ANSWER_FIRST unset -> answer_first True
    today          (d2089b9), ANSWER_FIRST unset -> answer_first False

The harness never set `ANSWER_FIRST`. It inherited the default, so the default
changing changed the harness too - which is why re-running it did not reproduce
the result and why "harness versus production" was the wrong place to look. The
effective-config diff between the harness and a plain request today is only
`CACHE_ENABLED` (harness-only and deliberate: a cached script would make the
comparison void) and `chatterbox_reference` (packaging). Everything else -
pipeline, backend, model, effort, search mode, share - is identical.

The log line that settles which behaviour ran is
`"research took over after %.1fs of answering from knowledge"`. It is emitted
inside `_answer_first()`, called only when `plan.search and settings.answer_first`,
so its presence in the good run's logs is proof the cover was on.

**What was done about it, deliberately narrow.** `Dockerfile.gpu` pins
`ANSWER_FIRST=1` so the image reproduces the configuration that was listened
to. `config.py` is untouched: the derivation has a real measurement behind it -
the same RunPod run recorded the cover speaking 85.8 seconds before research
took over, which is most of an episode spent on what the model already knew -
and overriding that on the strength of a listening impression would be trading
one unmeasured default for another. A plain `app.py` still resolves
`answer_first=False`.

So there are now two configurations on purpose, and the pin says why in the
Dockerfile and in DEPLOY.md. The reproduction that collapses them back into one
is `ANSWER_FIRST=1 bash tools/pod_production_test.sh` against the same harness
without it. Whichever way the numbers go, one of the two lines is deleted.

## 70. Seven interface changes, and the three that were product decisions

An interface packet asked for seven things. Five were UI work; the other two
were questions wearing UI clothes, and the packet said so - it asked for the
account boundary to be "made explicit before implementation, not left implicit
in the code", and refused to guess between two readings of the DailyFAM item.
Both were put to the product owner before anything was built. Recording the
answers here rather than only in the diff, because each one is a rule the next
change has to obey.

**1. What plays next.** An episode used to end in one of two ways on the
player: silence, or - if it had not come from search - a jump straight into a
*random* myFAM topic (`playerTapAdvance`, on the `onEnd` path). The random jump
is the worse of the two: it is a recommendation nobody made and nobody could
decline. Both are now the same popup, four tiles in a 2x2 grid, the first
starting itself after five seconds unless something else is tapped.

The tiles are `topics.rank_next_up`, which is deliberately **not** a second
recommender: it is `build_feed`'s three signals, in the same order, over a
profile seeded with the episode that just finished (`JUST_HEARD_WEIGHT = 3.0`,
above a completion, because "what next" is a question about this episode first
and the history second). `test_it_agrees_with_the_shelf_it_came_from` is the
anti-drift check - a listener must not be told one thing by myFAM and another
by the popup an hour later.

Three things it is careful about. The countdown tile prefers the *album's* next
episode when there is one, so album continuity survives the change; failing
that it prefers the follow-up the model already named on the trailing
`<<NEXT:>>` line, which is free and is a better answer than any ranking because
it read what was actually covered. It never offers back the episode that just
ended, or anything already played - except as a last resort, where it drops the
"not already played" rule rather than the grid, since two empty squares are
worse than hearing something twice. And it does not fire on Explore or Explore
New: both are already continuous exploration, and a popup between cards there
interrupts rather than helps. The first version of that fallback was wrong and a
test caught it - it filtered against the same `taken` set that carried the
played-ids exclusion, so the fallback could never add anything and a listener
who had heard most of the bank got an empty grid.

**2. What "Skip for now" costs.** The answer chosen: *anything the server keeps
for you needs an account; anything you can hear does not.* So saved mixes,
chosen interests and language, and the weekly recap are gated (401, with a
message that offers signup rather than reporting a fault); search, myFAM,
DailyFAM's episodes, Explore, Go Deeper and the whole audio path are not.

The interaction log is deliberately outside the gate, and that line is the
load-bearing part: it is ambient personalisation rather than a thing the
listener made and can point at, and gating it would mean an anonymous
listener's feed could never be ranked - which is the product, not an account
perk. `ACCOUNT_REQUIRED` in app.py holds the reasoning next to the code that
enforces it.

This is the first time this app has taken something away from an anonymous
listener, so the cost is worth naming: mixes made before the gate are still
there, under the same `user_id`, and appear the moment that listener signs up.
That is `accounts.py`'s original design paying off rather than a migration -
signing up attaches credentials to the identity you already have. Five existing
tests failed on the change and were updated rather than relaxed; `test_mixes`
now signs up in its fixture, and the gate has its own tests.

**3. Google and Apple, drawn but not connected.** The packet asked for three
sign-in methods. There is no OAuth in this codebase and no client credentials
in any environment it runs in, so the honest options were to omit the buttons or
to show them saying what they are. They are shown, tapping one says it is not
connected and offers the email path, and nothing pretends to work. Same rule as
the language picker below, and the same rule that deleted the cold open: a
control that silently does nothing is the failure this project has lost the most
time to.

The email path captures a phone number at signup, and the interface says
plainly what that is: a contact detail, not an authentication factor. There is
no SMS delivery behind it, so it cannot sign anybody in or recover an account -
and password reset is still the honest gap named in §66.

**4. Interests, and the reason to ask at all.** The intro's first page takes up
to six facets. The cap is enforced in three places on purpose - the chip
disables past six, `clean_interests` refuses a seventh, and the endpoint turns
that into a message - because a cap only the client applies is not a cap.

The important decision is that they are **tags, not topics**. Six of the
twenty-eight bank topics would have seeded six tiles and taught the ranker
nothing; the eight facets in `TAG_WORDS` are exactly what `taste` scores, so
choosing them ranks the whole bank. They enter `taste` flat, at
`INTEREST_WEIGHT = 1.0` - a play, well under a completion, and without decay -
so they carry the first feed and are quietly outvoted once there is real
behaviour. That is tested in both directions: a declared interest fills the
"Made for you" shelf that was honestly empty for every new listener before
this, and three completed tech episodes beat a declared interest in sport.

An anonymous listener's answers stay in their own browser and arrive as
`?interests=` on the feed request. That is not the thing "a listener id is
never accepted from the client" forbids: an id is an identity and grants
access to somebody's data, while this is a hint validated against a fixed
eight-word vocabulary, used for one response and never written down. It is
also the only route by which the ranker can honour them at all.

**5. Language: stored, and inert, and saying so.** The packet scoped this
narrowly - capture and persist the preference, do not build per-language
generation - which leaves a setting that changes nothing. `LANGUAGE_ACTIVE`
is `False`, `/api/preferences` returns it, and the intro prints "Stored, but
not yet acted on: every episode is still written and spoken in English. This is
the setting, not the feature." The flag exists so that the day generation
learns about languages, the claim in the interface flips with it rather than
having to be remembered.

**6. Why the recap is a stored date and not a flag.** "The first time they open
on or after Sunday" cannot be a boolean, because nothing would ever clear it: a
listener who does not open the app until Wednesday must still get Sunday's
recap and must not then get it again on Thursday. `preferences.week_start`
names a week by the Sunday that began it (UTC - the server has no idea where
the listener is, and a recap a few hours early is a smaller wrong than one that
arrives twice), and the recap is due whenever the stored week differs from the
current one. It needs no scheduled job, which this app has no way to run.

It is marked seen on display rather than on dismissal: someone who swipes the
app away has still had their recap, and showing it again on Thursday would read
as a bug. The content is only what the event log holds - and a listener with
nothing to recap is told so, in a sentence, rather than shown a tile of
invented numbers. Same rule as `/api/profile` in §66.

**7. Explore New reuses a ranking that had been written and hidden.**
`rank_might_like` - the exploration signal, which suppresses the listener's
strongest tag on purpose - has existed and been tested since myFAM was built
and has been shown nowhere since its shelf came off that page. It was the only
signal in the app offering anything outside an established taste. Explore New
is its surface, which makes the two-shelf loss recorded in CLAUDE.md a
one-shelf loss.

**8. DailyFAM had two of the same button.** The packet's literal request was to
move the "add new" control to the top right. The reading it was worried about -
that DailyFAM might need a folder concept it does not have - turned out not to
be the situation: the screen already had a "+" in its header *and* an inline
"New mix" button above the list, so the same action sat twice on one page and
the second copy pushed every mix card down. Confirmed as reading (a) and the
inline one is gone. Nothing about Explore changed.

**9. The label question, asked rather than guessed.** The sketch said "Your
FAM" where the app said "Messages". Confirmed as an in-page header only: the
sheet's heading changed, the envelope entry point and everything else did not.
Two tiles sit above the thread list - Weekly Recap, which opens the same
content the Sunday popup shows, and Explore New.

**A note on where this could still be wrong.** The packet's names for two
screens are the reverse of the code's. It calls DailyFAM "a single continuous
stories feed with no folder concept" - which is the app's **explore** tab - and
describes PlayFAM as the grid of playlist tiles, which is the app's **DailyFAM**
tab. The exclusions in item 1 were therefore read by description rather than by
name: "already has its own auto-advance flow" and "already a continuous
exploration space" are the reel and Explore New, and those are the two surfaces
where the popup does not fire. If the intent was the other way round, the change
is one condition in `nextUpAllowedHere`.

## 71. The bar was a readout; now it is a control

The progress bar showed where you were and could not be used to change it.
Getting to the middle of a five-minute episode meant twenty presses of the
back-15 button, and there was no way at all to go to a point you could see.

**Both gestures stay, because they are not the same gesture.** The buttons are
the precise move — go back to the sentence that was missed — and the drag is
the coarse one, get to roughly there. A scrubber does not replace a fifteen
second button any more than a scrollbar replaces a page-down key, and the
smoke test asserts the buttons still work after a drag for exactly that reason.

Four things this had to get right, each of which is a way scrubbers usually
feel broken:

* **The seek lands on release, not on every pointermove.** `FamAudio.seek`
  goes through `rescheduleFrom`, which stops every queued audio source and
  starts the schedule again. Seeking continuously through a drag would stutter
  the audio for the whole length of the gesture and do it a hundred times over
  to arrive at one position. So the drag paints, and one seek happens when the
  finger lifts.
* **The ticker has to let go of the bar.** `refreshProgressNow` runs every
  200ms from the real playback position, so without a guard the knob crawls
  back to where the audio actually is while the listener is still holding it
  somewhere else. `scrubbingBar` is that guard, and it is one variable rather
  than one per bar because FamAudio plays one episode at a time.
* **The knob stops at what has been written.** The episode is still being
  generated, so `seekLimit()` is the end of the audio that exists, and the
  drag clamps there. That is legible only because the buffered edge is already
  drawn on the bar — a knob that followed the finger past it and then sprang
  back on release would not be. Overshooting gets the same sentence the
  forward button gives: a limit, not a stall.
* **A 4px bar is not a touch target.** The hit area is grown to ±14px with a
  `::before`, so the line stays a line. `touch-action: none` stops the browser
  claiming the gesture as a scroll halfway through, and pointer capture keeps
  the scrub alive when the finger slides off the bar.

**Explore needed one more thing.** Its bar sits inside a stage that listens for
swipes, so a drag along it was both a seek and a swipe to the next episode —
the card you were aiming at, gone. The scrubber stops touch events at the bar,
and `explores_bar_scrubs_without_swiping` is the check that it stays that way.

**And it turned up a placeholder.** The segmented player's bar was never wired
to anything: a fixed 44% fill from the CSS and a time set to a fifth of the
episode's length, neither of which moved. It survived because it looks correct
in a screenshot and nobody watches that screen for thirty seconds. It runs on
the same FamAudio as everything else, so it now shares the same ticker and the
same scrubber. The alternative was worse than leaving it: the shared
`.progress-bar` styles would have made a decorative bar look draggable.
## 72. The key was set once per machine, and the demo runs on a new machine every time

§53 fixed this, and it kept happening.

That section is right about the cause — "nobody re-enters a credential four
times because they enjoy it; they do it because the app keeps losing it" — and
right about the fix: `~/.fam/env`, outside the project, next to the voice store,
so unpacking a new copy of the app finds the key already there.

**It is scoped to one machine, and the demo is not.** A rented GPU pod, a fresh
container, a colleague's laptop, a CI runner: every one of those is a machine
with an empty `~/.fam`, so every one of them asks for the key again. §53 moved
the store out of the project folder. The same argument moves it out of the
machine.

### What the resolution order is now

    1. the process environment    a platform dashboard, a CI secret, docker -e
    2. FAM_SECRETS                fetched at runtime by the app itself
    3. the project .env           a project pinning its own key
    4. ~/.fam/env                 the per-machine store from §53

An explicit environment variable still wins, and a refresh will not overwrite
one: somebody who exported a key by hand did it to test that specific key
against a specific bug, and having it silently replaced by a secrets manager is
the version of this feature that costs a day.

### The provider is a shell command, and that is the whole design

Two schemes — `file:` and `cmd:`, each optionally `NAME=` — parsed as a JSON
object or as dotenv lines. That covers AWS Secrets Manager, Google Secret
Manager, Vault, Doppler, 1Password and Docker/Kubernetes secret files with **no
new dependency and no vendor picked on the app's behalf**, because every one of
them already ships a CLI that authenticates as the machine (an IAM role, a
service account) rather than as another stored password. The escape hatch for a
manager nobody has thought of is that it is already a shell command.

`FAM_SECRETS` is not itself a secret. It says *where* the secrets are, which is
why it can sit in a host template, a Dockerfile, this repository's docs, and
`render.yaml`.

### Four things that were nearly wrong

**`demo.sh` was asking the wrong question.** It tested `$ANTHROPIC_API_KEY` —
which is only what *that shell* can see, the project `.env` it had just sourced.
Three of the four sources are resolved in Python, so a machine that was already
set up correctly still got asked to paste its key in. It now asks the app
whether a key will be found, and prints a yes or a no and never the key: a value
echoed there would reach the scrollback, a screenshot and `bash -x` output.

**A pool-only configuration had keys and sent none.** `ANTHROPIC_API_KEYS`
(plural) is a form only `credentials.py` reads; the SDK reads the singular. A
deployment that set the pool and nothing else would have had two perfectly good
keys in its environment and reported demo mode. `prime()` publishes the head of
each pool once resolution is finished. Caught by a test, not by a person.

**`reset()` shrank the pool it was restoring.** `demote` writes the key in force
into the environment so the SDK follows a failover without knowing this module
exists — which means rebuilding the pool from the environment afterwards
rediscovers only the key that was failed over *to*. The list is kept, not
recomputed.

**A failing provider must not publish what it printed.** Found by the security
review of this branch, before it merged. The failure detail was built from the
subprocess's own output - last line of stderr, falling back to *stdout* - and
that string reaches `report()`, `CREDENTIALS["secrets"]` and therefore
`GET /api/health`, which is deliberately unauthenticated: the one `/api/` path
excluded from session handling and the platform's `healthCheckPath`. Two things
travelled that far. Reliably, a secrets manager's stderr, which names account
ids, role ARNs, Vault paths and internal hosts. Occasionally, through the
stdout fallback, the credential itself - a wrapper that echoes the value and
then fails a post-step puts the secret on stdout and exits non-zero.

It contradicted this module's own rule, stated twice within it: `describe_spec`
strips command arguments because "arguments have carried tokens before now",
and `report` says "names and counts only". One line bypassed both, and it
landed on the single endpoint with no auth in front of it. The classification
now crosses the HTTP boundary and the diagnostic stays in the log.

**A failed provider must not also be a quiet one.** Returning `{}` from a broken
fetch is demo mode with no reason given, which is §51's canned script served
under a real question all over again. It raises; startup, `/api/health` and
`tools/demo_preflight.py` all say so, and they say it **even when a key was
found some other way** — because that means the next machine will find nothing.

### Rotation, which is the part that was actually missing

`refresh()` re-runs the provider in the running process, and a rejection at
startup now re-reads the manager *before* it reports a bad key. The commonest
reason a key that worked yesterday is refused today is that somebody rotated it
and the replacement is already sitting in the manager. So rotating is now
"change it in the manager" — no redeploy, no restart. A test writes a new value
under a running server and asserts it is picked up.

`describe_key` and `ScriptGenerator` were both reading `settings.anthropic_api_key`,
captured once at import. After a rotation or a failover that is a different
string from the one being sent, and a fingerprint that names the wrong key is
worse than no fingerprint.

### What this deliberately does not buy, said before somebody assumes it

**A pool of Anthropic keys is failover, not headroom.** Anthropic's rate limits
are per *organisation*, so a second key from the same org shares one bucket.
Real headroom is a higher usage tier or separate workspaces. Exa's limits *are*
per key, so there a pool is genuine. Getting this backwards would have produced
a "scaling fix" that scales nothing.

**CI never needed a key**, and none was added. The suite is hermetic by
construction (`FAM_IGNORE_DOTENV`, which now skips the provider too), so the
tempting move — put `ANTHROPIC_API_KEY` in the repository's GitHub secrets —
would have bought nothing and added a key to rotate.

**The credential was never the ceiling.** Chatterbox runs in-process, so
concurrency is bounded by GPU capacity, and bandwidth is 2.65 MB/min per
listener uncompressed. No number of keys moves either. **Per-listener metering
still does not exist**: with one key behind everyone the provider sees only this
account, so per-user limits, billing and abuse detection need a row in our own
database tagged with the id from `_listener(request)` at the moment each model
call is made. That is the next piece, and it is not built.

Twenty-five tests in `tests/test_credential_chain.py` and four more in
`tests/test_credentials.py` pin all of it, none of them touching a network or a
real secrets manager — `cmd:` is a shell command, so a shell command is exactly
what a test can supply. `CREDENTIALS.md` is the operator-facing half.

**Still unverified here, and it is the same gap as always:** this container has
no API key and no secrets manager, so every provider in `CREDENTIALS.md` is
exercised against a local `echo`, a file and a failing command rather than
against AWS, Vault or Doppler. The parsing, the ordering, the failover and the
rotation are tested; *that `aws secretsmanager get-secret-value` prints what
this expects* is read from its documentation, not observed. The first run on a
real machine should be `python setup_key.py --show`, which names the provider,
its state, and whether Claude still accepts what it returned.

## 73. Per-listener metering, and the three costs that behave differently

§72 ended "per-listener metering still does not exist", and named why it has to:
**the provider only ever sees one account.** Anthropic bills this organisation,
Exa bills this key, the GPU bills by the hour, and none of them can say which
listener produced which request. Every question about pricing, per-user limits
and abuse is that question wearing a different hat, so if it is not answered at
the moment of spend it cannot be answered later at all.

`metering.py` is the ledger: one row per episode, appended and never updated,
written in `app.py` where `_listener(request)` is known - from the session
cookie, never a parameter, which is the settled rule and which metering is the
most tempting place in the app to break, because `?user=` is right there and
the ledger wants an id.

### The distinction the whole design rests on

A single "cost per user" number is the thing everyone asks for and the thing
most likely to be wrong, because it averages three costs that do not behave
alike:

* **Claude tokens and Exa searches are marginal.** Nobody listens, nobody is
  billed. This is what a price per listener has to cover.
* **The GPU is fixed.** Chatterbox runs in-process on a card that costs the same
  idle. Its *marginal* cost is almost nothing - synthesis at ~330x realtime
  makes a three-minute episode about $0.0001 of card, which is the arithmetic
  the prefetch plan rests on and which now has a number behind it - and its
  *real* cost is a floor that exists before the first listener arrives.
* **The shared cache is a discount that grows with listeners.** Two people
  asking the same thing pay for one script.

So the report gives marginal cost, the fixed floor, and the two combined at a
stated listener count, and **never folds the floor into a per-listener
average** - an average that includes it says more about how many listeners there
are than about what a listener costs. On a synthetic 120-listener month the
marginal total was $4.25 and the GPU floor $430: reported as one number, the
product looks a hundred times more expensive than it is, and the conclusion
("we cannot afford this") would have been drawn from an artefact of arithmetic.

### The mean was the wrong summary, so the shape is reported

Same run: the median listener cost $0.009 and the worst cost $0.64 - **68x**.
That ratio, not the mean, is what decides whether a flat price needs a usage cap
behind it, so `report` carries median, p90, p99 and max side by side and the
tool prints the multiple. A mean alone cannot tell a population where everyone
costs the same from one where 1% cost a hundred times the rest, and only the
second breaks a flat price.

`_percentile` is written out rather than reached for in `statistics`, because
`quantiles(n=100)` raises on a single data point and the first day of a
deployment is a single data point.

### Billed, priced, assumed - and the row that says so

Every number is one of three things and the report says which: **billed**
(the provider's own usage figures), **priced** (billed quantities times a
published rate), **assumed** (the GPU allocation and the cache saving, which
rest on configuration rather than an invoice). A model with no entry in
`PRICES` is counted as **unpriced and named**, never costed at zero - a silent
$0 is the same failure shape as everything else in this log: a number that
looks like an answer.

Costs are **stored at write time**, not recomputed on read. Prices change, and
a row that repriced itself would make "what did March cost" depend on when you
ask. The plan is stamped the same way: someone who upgrades on the 20th did not
cost paid-plan money on the 5th.

### Four things that were nearly wrong

**The cover half would have been free.** A researched episode runs two model
calls at once (`_answer_first`), and the instant half was deliberately given a
throwaway `ScriptNotes()` so its predicted follow-up could not beat the
researched one's. Give it a throwaway `Usage` as well and every researched
episode reports at roughly half price - on exactly the episodes that cost most.
It now gets `ScriptNotes(usage=notes.usage)`: separate thread, shared total.

**Tokens read from the model, not counted from the script.** Output tokens
could be guessed from the words. Input tokens could not - the prompt, the
examples and any evidence packet are invisible from the text - and cache reads
are invisible from both. The estimate would have been wrong in the direction
that flatters the bill. A test asserts the recorded count is *not* the word
count.

**`top_up` took a new parameter, and 23 test doubles implement it.** Top-ups are
off by default (`ALLOW_TOPUPS=1`), so the tempting move was to skip them. A
deployment that turned them on would then have had a silent under-count on its
longest, most expensive episodes. The doubles were updated.

**The ledger was not on the mounted disk.** `tests/test_data_paths.py` exists
because two stores were once written into the image's WORKDIR and discarded on
every redeploy. A new store must be added to its `STORES` list and to both
Dockerfiles; this one is the billing record, so losing it loses the answer to
what anything cost. Caught by adding it to the list, which is the test doing
its job.

### Abuse reports and does not act

Two thresholds over a window, because the two abuses look different: volume
catches a script hammering the endpoint (many cheap requests), spend catches
10-minute researched episodes all afternoon (few requests, a lot of money).
Either alone misses the other.

`suspects()` returns names and reasons. It never blocks: an automatic block on
a metering heuristic eventually locks out a real listener who has no way to tell
anyone, and `_rate_limit` is already the thing that paces requests. A test
asserts the module never grows a `ban`/`block`/`suspend` verb.

### Reading it back is the sensitive half

`/api/usage` is every listener's history of what they asked for and what it
cost - the most sensitive thing this app stores after the password hashes, and
unlike those it is meant to be read. It is gated on `FAM_ADMIN_TOKEN` with a
constant-time compare, and **unset means the endpoint does not exist**: 404
rather than 401, so an unconfigured deployment does not advertise that it has a
billing endpoint and a wrong token does not confirm the path. It is also not
paced by `_rate_limit` - it spends no model call, and an operator pulling a
report should not compete with listeners for the generation budget.

### What it deliberately does not do

**No enforcement.** No quota, no per-plan limit, no cutoff. The data to build
one now exists; the policy does not, and inventing one here would be inventing a
product decision. **No billing** - `plan` is a label, there is no payment route,
and `set_plan` is the whole interface. **No bandwidth**, which at 2.65 MB/min
uncompressed is a real cost at scale and is not in these numbers;
`audio_seconds` is the quantity to compute it from when there is a CDN bill to
compare against.

36 tests across `tests/test_metering.py` and `tests/test_usage_endpoint.py`.

**Unverified here, and it is the same gap as always:** no API key and no
invoice, so every figure quoted above comes from the published rate card and a
synthetic ledger, and the cost model has never been reconciled against a real
bill. `PRICES` was checked against the card on 2026-09-09. The first real month
should be compared line by line against the Anthropic and Exa invoices, and
`METERING.md` corrected wherever they disagree - a metering system nobody has
reconciled is a metering system that is confidently wrong.

## 74. The memoised key pool outranked a newly read key

**Found while answering a question, not while chasing a bug.** Asked to confirm
the automatic credential plugin works, the check ran `tests/test_key_store.py`
next to `tests/test_credentials.py` and two tests failed that pass on their own.
Order-dependent tests are usually a dirty fixture. This one was not.

Reproduced with no pytest anywhere, in one process, on `~/.fam/env` alone:

    write ANTHROPIC_API_KEY=sk-ant-FIRST ; _load_dotenv()   -> sk-ant-FIRST
    rewrite it to sk-ant-SECOND ; _load_dotenv()            -> sk-ant-FIRST

`pool()` memoised its list into `_POOL` on first read and never looked at the
environment again; only `load()` cleared it, and `load()` returns early when no
`FAM_SECRETS` provider is configured - which is every machine that uses the
`~/.fam/env` file the plural pool was built alongside. So `prime()`, whose whole
job is to make the environment agree with the configuration, republished the
*stale* key over the fresh one. Rotating the machine-wide key and re-reading it
left the process still sending the key that had just been replaced.

That is the precedence error this module exists to prevent, made by the module
itself, and it failed in the direction this project dislikes most: silently, and
looking fine. `key_source()` still named `~/.fam/env`, because the file really
was read - it was the value that got overwritten afterwards.

**Why the memo exists, and why deleting it is the wrong fix.** `demote()`
writes the key it failed over *to* into the environment. Rebuilding the pool
from the environment after that would rediscover only that key and lose every
one behind it, which is exactly what `reset()`'s docstring warns about. The memo
is load-bearing for failover.

**The fix distinguishes the two reasons the environment can disagree with the
memo.** `_SOURCED` records the raw `(NAMES, NAME)` strings the pool was built
from and `_PUBLISHED` records what `_publish` last wrote. The memo is served
when nothing moved, or when the only thing that moved is the single value this
module published itself - a failover. Anything else is somebody supplying a
different key, and the pool is rebuilt.

Both halves are pinned by test, and the first one was checked against the
unfixed code to confirm it actually fails there: a rotated key is not undone,
and a failover still keeps the keys behind it.

**What this says about the shape of the class of bug.** The module was reasoned
about as a chain - environment, then provider, then files - and the chain is
right. The cache in front of it was not part of that reasoning, and a cache is a
precedence decision whether or not anyone wrote it down as one.

## 75. The GPU was 99% idle, so the voice moved off the app's machine

Not a bug. A cost shape that the architecture had already named and nobody had
acted on, and it is written down here because the *reasoning* is the part worth
keeping — the code is small.

`DEPLOY.md` said it plainly: **Chatterbox runs in-process, so every replica
needs a GPU, and a GPU left running is the expensive kind.** At the volume this
product is actually at, that is the whole problem. Thirty minutes of audio a day
is **2% of the month**, and at Chatterbox's measured ~4.6x realtime the card is
genuinely working **0.45%** of the hours it is rented for. A pod billed 24/7 is
therefore about 99.5% idle, and the bill does not know that.

**What was measured before deciding.** Resemble's hosted API was priced first,
since it is the same company that publishes Chatterbox: Flex is $0.0005 per
second of audio with no monthly ceiling, so a 3-minute episode is $0.09 against
a ~$0.03 script — four times the writing, and about $34.50 per million
characters, which puts it in the *expensive* third of `VOICE_OPTIONS.md`'s
shortlist rather than the cheap end. It would also have meant a different model
and a different voice, and a fresh consent recording from the person whose voice
`reference_3` is. Renting the same card by the second is cheaper than renting
somebody's inference by the second, and it keeps the voice.

### The shape of the change

One JSON contract, two envelopes, one worker image:

    remote_voice.py           the app's side; owns the transport, nothing else
    voice_worker/synth.py     the card's side; owns the audio, nothing else
    voice_worker/handler.py   RunPod Serverless envelope
    voice_worker/server.py    plain-HTTP envelope, for an always-on pod

The worker **imports the real `ChatterboxEngine`** rather than reimplementing
`generate()`. That was the single most load-bearing decision here. The six
numbers in `CHATTERBOX_GENERATION` *are* the voice — the file says changing one
invalidates every listening judgement made on it — so a second copy on the
worker would be a second place for them to drift, and the drift would be
inaudible until someone compared two episodes side by side. Importing also
inherits the rights gate and the CPU refusal for free, which are two more things
that must not exist twice.

`speech_assembly.py` already batches sentences into ~33-word chunks, so an
episode is about fifteen requests rather than one per sentence. Each chunk is
~10 seconds of audio, which is ~10 seconds of playback headroom for the next
round trip. The split is affordable because that batching already happened.

### Four things that had to be got right, none of them obvious

* **The sample rate is known before the first call.** `app.py` writes
  `X-Sample-Rate` from `engine.sample_rate` before the response body runs, so
  the engine cannot wait to be told. It is configured, asked for in every
  request, and the reply is *checked* against it. A worker answering at a
  different rate is refused rather than played — a wrong rate is a failure the
  listener hears and nothing anywhere explains.
* **Base64 over JSON is not an audio file.** "No MP3, no audio files" is a rule
  about what reaches the listener and what is written to disk, not about what
  two servers say to each other — the same reasoning §61's WellSaid work
  established. Nothing is transcoded, and a worker offering `mp3` is refused
  rather than decoded.
* **Configured is not reachable.** `available()` reads configuration and never
  touches the network, because `/api/health` calls it and a health check that
  makes a billed third-party request is one nobody can afford to poll. What
  *does* perform the real action is `warm_up()`, and its answer is kept rather
  than only logged, so `/api/health` reports `reachable: {state: ok|failed|
  unknown}` beside `configured`. §52 again: "a credential is set" is not "the
  credential works", and one report must not let either stand in for the other.
* **The cold start is answered by starting earlier.** A serverless worker at
  zero pays boot plus a ~10s model load. `app.py` fires `remote_voice.wake()`
  when a request arrives — before Claude has written a word — so the worker
  boots while the script is being written. It never raises, never blocks and
  will not stampede; a miss costs only the cold start it was trying to hide.
  This is the same move as prefetching scripts for the browse surfaces, and it
  is emphatically *not* the cold open: nothing is played to cover the wait.

### §61's two guards, re-added by hand as it said to

`VOICE_OPTIONS.md` wrote both down rather than leaving them as dead code, on the
grounds that the next hosted engine should add them deliberately. It did.

* **A rented voice is never the default.** `VOICE_BACKEND` defaults to
  `chatterbox` and nothing auto-detects. `default_voice()` returning the first
  offered voice is how WellSaid silently became what every listener got on any
  machine without Piper; here the remote backend is unreachable without an
  explicit decision, and a test fails if that default ever drifts.
* **A hosted engine never falls back to a local one.** Every failure raises
  with the real reason — network error, 401, empty audio, wrong rate. There is
  no substitution, because substituting means judging one backend by another's
  output, and an operator debugging a GPU that was never being asked to speak.

### Deliberately a knob, which is the exception

This project deletes rather than disables — the cold open, Piper, WellSaid —
because a knob left behind is an invitation to turn it back on, and this one
turned itself once already. That rule is suspended here **on purpose**: a card
of our own is where this is going, and the split exists only while the volume
does not justify one. So `Dockerfile.gpu`, `RUNPOD_PRODUCTION.md`,
`pack_for_pod.py`, `pod_production_test.sh`, `requirements-chatterbox.txt` and
`ChatterboxEngine` are all untouched, and going back is one variable.

The difference from the cases the rule is for: those knobs led *away* from the
product's spec. This one leads back to it.

### Still unheard

Nobody has heard a FAM episode through this path, or through any Chatterbox
path — that is still open problem #1 in CLAUDE.md and this does not close it.
What the tests establish is that the transport is correct, that failures fail,
and that the contract survives a worker answering wrongly. **Whether it sounds
like the in-process engine is the thing to check first**, and it is checkable
cheaply: the same script through both backends, back to back.

The other unmeasured number is the wake. Whether it actually covers a cold
start on a real endpoint has not been observed — only reasoned about from the
script-generation time on one side and the model-load time on the other. If it
does not, `REMOTE_VOICE.md` lists the three fixes in order of cost.

## 76. The question decided whether to research, and it decided wrong

Render, in production, on a question about a game played the previous evening:

    SEARCH no '49ers game last night' - nothing in it reads as time-sensitive;
    answering from what the model knows

The episode was written from model memory. Nothing was broken: that log line
is `script_generator.plan_episode` reporting the designed behaviour of
`SEARCH_MODE=auto`, which was the shipped default.

### The cause is a price that changed, not a bug

`auto` was the settled constraint — "search is opt-in, and the question opts
in" — and it was right when it was written. Research then meant Anthropic's
server-side `web_search` tool running inside the model's turn, which
front-loads **10-25 seconds** before the first word. Against that, a keyword
guess is worth making: the questions it gets right save half a minute each, and
the one-sentence spec says a wait in front of the first word is the one cost
this product refuses.

`RESEARCH_BACKEND=exa` (§57, `research.py`) changed the number the guess was
priced against. Exa retrieves in about **half a second**. At that price the
arithmetic inverts completely:

* a question guessed **wrong** is answered from memory that may be a year
  stale — the whole failure, and invisible, because a confident wrong answer
  sounds exactly like a right one;
* a question guessed **right** saves half a second, which no listener can hear.

So the guess had stopped buying anything and was still capable of losing
everything. That is not a heuristic that needs widening. `research_reason` had
already been widened once (§58 added "who runs X", "how many", bare years) and
`last night` still missed — which is the general shape: a keyword list can
always be widened by one more word, and the next question it misses is already
written somewhere.

### The fix

One default, not a deleted code path. `config.search_mode` now defaults to
`always`, and `.env.example` moves with it (§54: a setting is settled only
where it is copied, and `tests/test_env_example.py` fails on any disagreement).
`auto` and `never` are kept and are explicitly **not production** — offline
`write.py`, `tools/compare_search.py`, a deployment with no Exa key. An
explicit `search=1` / `search=0` on a request still wins, because opt-out has
to mean something or the API documents a lie.

Three things moved with it, because a decision that is only changed in one
place is §54 again:

* **The log line says which mode it is in** in all three branches. The old one
  reported a reason without saying whose rule produced it, so the Render log
  looked like a heuristic misfiring rather than a default being wrong.
* **The interface asks the server which mode it is in** (`/api/health` already
  reported `search_mode`; `static/index.html` now reads it). It was mirroring
  the keyword list to choose a loading caption, so with research always on it
  would have told the listener "answering from what I know" while the server
  retrieved — a new version of the lie the honest wait was built to remove.
* **`demo_preflight` warns on the opposite condition.** It used to flag
  `always` as the mistake. It now flags anything that is *not* `always`, and —
  "verify, do not inspect" (§52) — asks `research.diagnose()` whether the
  backend can actually run, because with every episode researched a missing
  `EXA_API_KEY` is not a degraded demo, it is a demo where nothing generates.

### What is deliberately not done

**No fallback to model knowledge when research fails.** `ScriptGenerator.
research` lets `ResearchUnavailable` propagate, `app.friendly_error` turns it
into the sentence that names the remedy, and that is the whole of it.
Substituting memory for a failed retrieval would restore exactly this bug,
invisibly, on the days the backend is down — and an episode nobody can tell
was unresearched is unattributable.

The empty-packet path is different and stays: when Exa returns nothing usable,
`research` returns the plan without evidence, and since the plan still says
`search`, `_request_kwargs` leaves the `web_search` tool attached — so the
model searches after all rather than being handed an empty packet and told it
is research.

### The test that would have caught it

`tests/test_research_is_the_default.py`, and the shape worth copying: it
asserts the symptom (`"answering from what the model knows"` can no longer be
logged) as well as the cause, it goes through `/api/audio` rather than calling
`plan_episode` directly (§58's lesson — a default that is correct in the
function and wrong at the boundary is invisible to any test that starts inside
the boundary), and it follows the flag to the call by stubbing
`research.retrieve` and asserting it actually ran. Checked against the old
default, 17 of its 34 cases fail — including `49ers game last night`.

### Still open

Nobody has listened to an always-researched episode end to end with a key in
place. What is measured is that research is planned, invoked and attributed;
what is not is whether time-to-first-audio holds at the half-second Exa is
credited with under production concurrency. If it does not, the answer is
`ANSWER_FIRST=1` — which exists for exactly this and currently follows the
backend — and not a return to guessing.

## 77. The search tool was attached to every episode and never asked for

§76 made every episode researched. Production then answered `49ers game last
night`, and `Rams game last night`, with:

> I don't have any information on the 49ers game last night. I can't confirm
> the score, the opponent, or the plays.

Which reads like §76 not being deployed. It was deployed, and it was working:
the episode *was* routed for research. The bug is one layer further in, and it
was already there before §76 - reachable only on the few questions the keyword
list happened to flag, which is why nobody had seen it.

### Cause

`_request_kwargs` attaches Anthropic's `web_search` tool whenever an episode is
researched and no evidence packet came back:

    if plan.search and not plan.evidence:
        kwargs["tools"] = [{"type": "web_search_20260209", ...}]

and `build_prompt` said **nothing about it**. The model was handed a capability
it was never asked to use, in a prompt whose every other line is about writing a
story of a certain length. So it wrote one, from memory, and reported honestly
that its memory was empty.

**A tool is not an instruction.** Attaching one says the model *may* search; it
never says it *must*, and a prompt that spends three hundred words on narrative
structure and none on research is a prompt about narrative structure.

That path is reached in two ways, and §76 widened both from "the questions a
keyword list flagged" to "every question":

* `RESEARCH_BACKEND=claude` - `research()` returns the plan untouched by
  design, because the model does its own looking. *Every* episode lands here.
* `RESEARCH_BACKEND=exa` where retrieval returned nothing usable. `retrieve`
  logs `exa returned N result(s) but no usable evidence` and hands back an
  empty packet, deliberately, so the tool stays attached - which only works if
  something then asks the model to use it.

### Fix

`build_prompt` grows the block that was missing: when an episode is researched
and carries no evidence, it says there is a search tool, that the question was
routed for research, to search before writing, and that `"I don't have that
information"` and `"I can't confirm"` are the one pair of answers not available
- naming the exact sentences production produced. The packet and the tool stay
alternatives, never both: an episode with evidence is told to read it and is
given no tool, because searching on top of a packet pays the 10-25 seconds the
packet exists to avoid and makes the episode unattributable.

### The other half: the server could not say what it was running

Diagnosing this took a round of "is the fix even deployed?", and from outside
the server that question had no answer - which is PROBLEMS.md 52 with the
deployment as the subject. `/api/health` now reports:

* `build` - the running commit, from `RENDER_GIT_COMMIT` (Render injects it),
  `FAM_COMMIT`, or `git rev-parse` in a checkout, and `"unknown"` rather than a
  guess;
* `search_mode_source` - whether the mode came from `SEARCH_MODE`,
  `ENABLE_WEB_SEARCH`, or the code default. An env var beats the default
  silently and outlives any number of pushes, so "the default was changed" and
  "this server researches" are different claims and only the second one matters.

Neither is a feature. They are the two facts that had to be inferred, and
inferring them is how a session gets spent.

### What this does not fix

Whether Exa's `type="fast"` retrieval is any good on a question like `49ers
game last night` is still unmeasured - `tools/compare_search.py` is the harness
and nobody has pointed it at a sports recap. If the packets come back thin,
this change is what stops thin evidence becoming a confident shrug: the tool is
there, and now the model is told to use it.

## 78. The 404 that could have been three different things

Production, with the research half finally working:

    SEARCH yes '49ers game vs Rams'
    POST https://t6x3rlixn0tz18-8002.proxy.runpod.net/synth "HTTP/1.1 404 Not Found"
    remote voice synth returned HTTP 404

The episode was researched and written, and then nothing spoke.

### Both sides already agreed, which is what made it hard to read

`voice_worker/server.py` serves exactly two routes - `GET /health` and
`POST /synth` - plus the three FastAPI adds for free (`/openapi.json`, `/docs`,
`/redoc`). `remote_voice._call_http` posted to `f"{config.url}/synth"`. The log
line shows `/synth` at the origin, so the request the app sent was the request
the worker is written to answer. **The two halves of the repo were not in
disagreement; the address was.**

That is the part worth writing down: a 404 is the one voice failure that says
nothing about the voice. Every other guard in `remote_voice.py` is about a
worker that *answered* - empty audio, the wrong sample rate, an odd byte count,
`mp3`. A 404 means nobody was home, and an address has three halves that can be
wrong independently:

1. **the route** - the running image may be a different version of this repo
   than the checkout you are reading;
2. **the port** - the container serves `${PORT:-8001}`, and the live URL names
   **8002**, while `.env.example` documents `-8001`. A proxied port with nothing
   behind it returns `404 page not found` from RunPod's proxy, which is
   indistinguishable in a log from a worker with no such route;
3. **the mode** - `Dockerfile.voice` defaults to `VOICE_WORKER_MODE=serverless`,
   which runs `handler.py` and opens no port at all. A pod started without that
   variable set to `http` 404s every path, `/health` included.

Nothing in the app's log separated them, and the build container cannot reach
the pod to ask (its egress policy denies `proxy.runpod.net`, and there is no GPU
here either), so the fix had to make the *deployment* answer the question
instead of a guess deciding it.

### What changed

* **`tools/probe_remote_voice.py`** asks the live worker and prints the answer:
  `/health`, then **every route from `/openapi.json`** - the authority on the
  route name, since the image outranks the checkout - then a real sentence,
  decoded through the same checks the app applies. Exit 0 only if audio came
  back. §52's rule, applied to an address: reading a Dockerfile answers a
  cheaper question than asking the machine.
* **A 404 is now diagnosed rather than reported.** `_call_http` follows one with
  a single `GET /openapi.json`. If the worker names a POST route, the request is
  retried there and the route is remembered for the process, so a renamed route
  costs one extra request per deployment instead of a dead episode. If nothing
  answers, the error names the port and the mode - the two causes above - in the
  sentence that reaches the log.
* **A `REMOTE_VOICE_URL` that already ends in `/synth` is honoured** instead of
  becoming `/synth/synth`. It is the URL an operator tests the pod with by hand,
  and doubling it produced a 404 identical to the one being debugged.
* **The worker announces itself at boot**: `serving on port 8001: GET /health,
  POST /synth, ...`. The pod's log is the only place all three halves of the
  address are visible at once, and it was saying none of them.

### Three things this deliberately does not do

**No fallback to another voice.** The retry is to the same worker, at a route
that worker named. §61's guard holds: a remote voice that cannot speak raises
with the reason. `test_a_404_never_becomes_a_different_voice` says so.

**No guessed paths.** The retry uses the schema the worker publishes, never a
list of likely names - a POST to a guessed route on a machine that is not this
worker is a request to somebody else's service.

**No extra request on the happy path.** The probe only ever follows a 404;
`test_the_happy_path_costs_no_extra_request` fails if that drifts.

### Verified, and what is still unverified

On a real socket, with the card stubbed and `voice_worker.server:app` itself
serving: `/health` 200, the route table exactly `GET /health` and `POST /synth`,
`POST /synth` 401 without the token and 200 with it, and the app's own engine
getting PCM back through a base URL, through a URL ending in `/synth`, and
through a worker whose route is renamed. A port answering 404 to everything
produces the sentence naming `PORT` and `VOICE_WORKER_MODE`.

What that does **not** prove is which of the three it is on `t6x3rlixn0tz18`,
because this container cannot reach it, and it is not a listening test - the
audio in that loop came from a stub, not from Chatterbox. CLAUDE.md's open
problem #1 is untouched: nobody has heard a FAM episode in this voice.

## 79. The 429 in production was the allowance, and it was counting requests

Render logs showed the same question - *"which longevity interventions have
real evidence"* - asked over and over, and every `GET /api/audio` answering
`429 Too Many Requests`. Playback was effectively down.

### What it was not

**Not the frontend.** Established first, by driving the real interface in a
real browser and counting: one search sends exactly one `/api/audio`, plus one
`/api/event` and one `/api/next`. Nothing retries.

**Not the rate limiter, which §77's session-keyed fix had already put right.**
Reproduced against the running server before changing anything: six taps on one
question, one every four seconds - twice the three-second pace, so the pace
could not be involved. The first five were served. The sixth and every one
after it were refused, and would stay refused until midnight UTC.

### What it was

`FREE_EPISODES_PER_DAY=5`, and the allowance was counting **requests**.

Five taps on **one** question spent a free listener's entire day. That is not
an edge case: it is what the interface asks people to do. The player's own
topic row says *"tap to generate new episode"*, switching voice deliberately
re-requests the same script, and the honest wait invites a second tap from
anyone who is not sure the first one registered. None of those is a second
episode - the listener heard one episode - and none of them costs a model call,
because the script is already in the shared cache after the first.

The rule in `quotas.py` was *"a cache hit still counts: the listener heard an
episode and the GPU produced it"*. That is right about **somebody else's** cache
hit, and the same words read as "every request counts" are what took production
down. So a spend now carries an **episode key** - the cache key, meaning the
same question, length, context and research setting - and the first reservation
for that key in a window takes a unit while the repeats ride on it. Scoped to
the window, so tomorrow's replay is tomorrow's episode; scoped to the listener,
so nothing here makes an episode cheaper for the next person to ask for it.

A repeat is granted `charged=False`, and a refund gives back only what was
taken. Without that, failing on a replay would have refunded a unit nobody
spent - allowance earned by failing.

### The second charge for nothing at all

The empty-episode `502` - *"the episode came back with no speech in it"* - was
the one failure path with **no refund on it at all**. On a server whose voice
has gone away, which is exactly when it fires, that is five silent failures and
then a lockout until midnight, to a listener who has heard nothing. The
docstring beside it already said this was "a bug to fix rather than a quota to
soften"; fixing the empty episode is still the real answer, and charging for it
was a second fault sitting on top of the first.

### Two refusals, one status code

`429` in an access log is the same three digits whether the pace or the
allowance said no, and the two have opposite remedies: wait three seconds, or
wait until tomorrow. Nothing distinguished them, which is why the rate limiter
was the first suspect for a refusal the allowance was making. Both now carry
`X-FAM-Refused-By`, both log a line naming themselves, and the pacing refusal
carries `Retry-After` - without which a client's only strategy is to retry
immediately, turning a throttle into the storm it exists to prevent.

### And the pace, while it was in hand

Two things were wrong with it in the same way the allowance was wrong:

* **It priced a replay as a generation.** `/api/audio` paced everything that
  was not `cached_only`, but a request whose script is in the cache spends no
  model call either. It now asks the cache first - one local SQLite read, the
  same lookup the pipeline is about to do anyway - and with
  `CACHE_SEMANTIC_KEY` on, where the key itself needs a model call, it declines
  to answer and the request is paced: the safe way round.
* **It was a hard gate.** A gate refuses the second of two taps a person
  genuinely makes. It is a three-token bucket now, refilling one per
  `RATE_LIMIT_SECONDS`, so the sustained rate is exactly what it always was
  while a burst is forgiven.

### What was measured, after

One listener, one episode, ten taps: all served, **one** unit of allowance
spent, where before the fourth tap was refused and the day was gone. Ten rapid
taps plus a voice switch on an episode already written: eleven served, one unit.
Five different questions: served, and the sixth refused by the quota, naming
itself and saying when it comes back.

### Coverage

`tests/test_rate_limit_production.py` - fifteen tests, nine of which fail
against the code before this change. The same episode is charged once and a
different one still costs a unit; a follow-up, a different length and an
attachment are all correctly *different* episodes, so the key cannot quietly
make real episodes free; an empty episode and an unavailable voice are not
charged, and a refunded episode is forgotten so the retry that works is charged;
a replay is not paced and an uncached question still is; and the two refusals
are told apart.

The interface's half is the smoke behaviour **"One tap sends one request"**,
which fails against the interface as it was: three taps on an episode still
loading sent three requests. Once audio is playing a re-tap goes through, since
by then it is a replay - the check asserts that boundary too, so the guard
cannot quietly become "one episode, ever".

## 80. The recommender was ranking twenty-eight topics with twenty answers

**Asked:** what algorithm picks the four tiles after an episode, and how
could it be better.

`rank_next_up` is not a second recommender - by design it reuses the feed's
rankings, so the popup and the shelves cannot disagree. It builds a taste
profile (`taste`: recency-weighted tag affinity, 14-day half-life, a skip
genuinely negative), seeds it with the tags of the episode that just ended,
excludes everything already played, and fills four slots in cascade order:
closest-to-taste, then co-listener overlap, then the crowd.

**The finding, which was not where it was expected to be.** The scoring
function was fine. The *vocabulary underneath it* was the ceiling: eight tags
over twenty-eight topics, twelve of them carrying a single tag, and only
**twenty distinct tag signatures in the whole bank**. Measured before changing
anything - a listener whose entire history was `tech` had exactly four topics
with any positive affinity and **three of them scored identically** (0.7071),
so `scored.sort(key=lambda p: (-p[0], p[1].id))` decided the grid on
`topic.id`. Alphabetical order wearing a recommender's hat:
`attention-economy` beat `chip-supply` because *a* sorts before *c*.

No weight, half-life or tie-break fixes that. A scoring function cannot
express a preference it has no vocabulary for, and tuning `EVENT_WEIGHT`
would have been adjusting the precision of a score whose inputs took twenty
values.

**What was done.**

*A second level of tags, not a bigger flat list.* Twenty-nine subtags under
the same eight facets (`TAG_PARENT`, `SUBTAG_WORDS`). The bank went from 20
distinct signatures to **27 of a possible 28**. The facets are untouched and
still the only pickable vocabulary, because the intro's interest picker is
built from `TAG_LABELS` and a twenty-seven button intro is a worse question
than an eight button one - the resolution is for the ranker, not the listener.
A subtag never replaces its facet, it refines it: `tags_for_text` adds the
parent to every subtag it matches and every bank topic carries both, so a
listener who ticked "Technology" and nothing else matches exactly what they
matched before. The change is additive by construction, and a test asserts it.

*Impressions, which were being written and never read.* `record_impressions`
has always logged every tile shown, with its section and `ALGO_VERSION`, and
`impressions_for` had no caller outside tests. So "shown six times, never
tapped" was indistinguishable from "never shown" - and a tile nobody ever taps
has no other negative signal, because a skip needs a play first.

The existing note on `IMPRESSION` says it deliberately has no `EVENT_WEIGHT`
entry, and that is still right and still enforced: an impression must never
become **taste**, or the feed teaches itself its own preferences and calls the
echo a signal. Fatigue is a different thing and the boundary is exact - it is
**per-topic, never per-tag, and strictly negative**. It can only push a tile
down, so it has no positive feedback loop to close. `FATIGUE_WEIGHT` carries
the whole argument beside the constant.

Two details that decide whether it helps or hurts:

* **Occasions, not rows.** A feed load writes ~18 impressions; someone opening
  myFAM four times before breakfast has not rejected anything four times.
  `impression_occasions` counts distinct `FATIGUE_BUCKET` (1h) buckets, so
  refreshing cannot look like disinterest. Without this the ranking would
  have punished the most engaged listeners hardest.
* **It never reaches zero** (`FATIGUE_FLOOR`), and a played topic is dropped
  rather than damped - the impressions before a play are the opposite of
  disinterest.

`rank_trending` is deliberately **not** damped: it is the same list for
everyone, which is what makes it the cheapest section to serve.

**One real bug the tests caught, and it was in the change.** `rank_might_like`
breaks the filter bubble by muting the listener's strongest tag. With two
levels, muting `sports-performance` leaves `sports` at full strength and the
section quietly becomes history with a new heading. It now mutes the whole
**family** - the facet and every subtag under it. `test_might_like_suppresses_
their_strongest_tag` failed on exactly this and is the reason it was found.

**What this did not fix, deliberately.** A listener who ticked "Technology" in
the intro and has listened to nothing still gets tied scores. That tie is
honest: with no behaviour there is genuinely no basis to rank three tech
episodes against each other, and the answer is one episode of history, not
more vocabulary.

**Still open, and now cheap** - each was considered and left out rather than
missed. **IDF in `_affinity`**: a match on `money` (8 topics) scores like a
match on `world` (4), and there is no correction; one line, worth more now
that document frequency actually varies. **Position-aware skip**: `−1.5`
whether they left at ten seconds or ninety percent, which are opposite pieces
of evidence - `Event` has no progress field, so this is a schema, API and
client change, and under the iOS rule it is an API before it is a screen.
**A diversity cap** on the grid of four: deliberately *not* done yet, because
consuming impressions is the instrument that says whether same-tag grids
actually underperform, and shipping the fix before the measurement is guessing.

## 81. The tier system is switched off, and the refusal it will make is built

Two changes, one temporary and one for the day the temporary one is undone.

### Switched off, not removed

`ENFORCE_QUOTAS` now defaults to **0**. Nothing else moved: the tiers, the
limits, the counters, the reservation, the refund, the episode key and the
refusal all still exist and are still tested, and `ENFORCE_QUOTAS=1` turns the
whole of it back on in one place.

The reason is not that the limits are wrong. It is that **nothing sells a
listener a way past one.** There is no checkout (`ACCOUNTS.md`: no payment, and
`set_plan` moves an account between tiers but nothing calls it), so an enforced
free tier is a wall with no door - five episodes and then nothing until
midnight UTC, with no action available that changes the answer. §79 found that
shape in production the hard way: the refusal was correct, and correct was
still the worst possible experience because there was nowhere to go from it.

What that costs, stated rather than discovered later: per-listener spend is
**visible and not capped**. `metering.py` still records every episode and what
it cost, and `python tools/usage_report.py` still prints the median and the
p99, so the exposure is measurable at any moment - it is simply not bounded.
`_rate_limit` still paces every generation per listener, so nothing can be spun
faster than before. This is a deliberate position for a beta with no checkout
and the thing to revisit before the URL is handed to strangers at scale.

The old default had a good argument behind it - "a ceiling that has to be
switched on is a ceiling that is off on the machine nobody checked" - and it is
answered by making the state visible rather than by leaving it on: `/api/health`
now reports `tiers.enforced` and whether that came from the environment or the
code default. An unenforced tier system looks *exactly* like an enforced one
from outside until somebody reaches a limit, which is the §52 shape, so the
server says which it is instead of being inferred from.

### A refusal has to name the thing they pressed

The old sentence was *"That is all 5 of your episodes for today."* A resource is
an accounting word. `episode` is what the ledger counts and what the GPU makes;
it is not what anybody thinks they did. Somebody who typed a question into the
search box and was refused reads "episodes" and goes looking for the episodes
they apparently spent.

So `entitlements.service_label` maps the surface `app._surface` already derives
- search, myfam, godeeper, explore - onto the word a listener would use, the
verdict carries it, and both the headline and the sentence are composed
**server-side**:

    You've reached your daily limit for searches
    That is all 5 of your searches for today. You get more at 00:00 UTC.
    A bigger plan lifts the limit.

Server-side because there are two interfaces now (CLAUDE.md's iOS section), and
a sentence written twice is a sentence that will disagree with itself. The
adjective comes from the window, so a Plus listener is told "weekly".

### The numbers travel where they cannot be lost

The verdict was already in `X-FAM-Quota`. It is now in the response **body**
too, because a header is the one part of a response a client routinely cannot
reach: `fetch` hides it cross-origin without `expose_headers`, and every
wrapper that turns a failed response into an exception keeps the body and drops
the rest - which is exactly what `fam-audio.js` did, so the interface could say
"no" and nothing else.

### The screen

A refusal raises a modal rather than a toast, because a toast vanishes, cannot
be acted on, and does not say when the allowance comes back. It shows the
server's headline and sentence, a meter for how much is gone (drawn only where
both numbers are real - an unlimited tier has no proportion to show), the reset
**in the reader's own clock**, and two buttons: *Not now*, and *See plans*.

The reset line says "in your time" out loud. Windows are counted in UTC on
purpose and the server's sentence says so; without the label the card shows one
fact as two different times and reads as a contradiction.

*See plans* opens a sheet built from `/api/plans` - the real tier table, the
real limits, the current plan marked. It ends in a line saying that **upgrading
is not switched on yet, there is no checkout behind these plans, and nothing on
this server charges anybody.** That is the seam where checkout goes, named as
one. A plan list that looks buyable and is not would be the "quietly worse than
intended" failure with money attached, and the listener would find out after
tapping.

Explore raises the same screen rather than dealing the next card: its allowance
is separate and looser, and swiping on would spend the rest of the feed
discovering the same refusal one card at a time.

### Coverage

`tests/test_tiers_off_and_the_limit_screen.py` - fifteen tests over both
halves. Enforcement is off by the *default* (read from a freshly reloaded
`config` with the environment cleared) and `.env.example` agrees (§54); six
episodes against a five-episode tier are all served; the tiers, limits and
counters all still answer; health says whether limits are live; and one
environment variable switches the whole thing on. Then: each surface is named
in its own words, Explore is named as Explore, a weekly limit says weekly, a
granted verdict has no headline, the noun is never blank, and the verdict
reaches the body with every field the screen draws itself from. A pacing 429
carries no verdict, so the two refusals stay distinguishable and the limit
screen cannot be raised by the wrong one.

The smoke behaviour **"A limit leads to the plans"** drives the screen in a
real browser from the verdict a refused request carries - the honest way to
check a screen that cannot be reached while the system is switched off - and
asserts it names the service, says the number and the reset, opens the plans,
marks the current one, and says out loud that upgrading is unavailable.

## 82. The episodes were researched, and nothing decided what to research

### The symptom

Quality, consistency and relevance, reported from use rather than from a trace:
a bare entity like `Nvidia` produced a generic company explainer on a day the
company had done something; a finished game was described as "last night" when
it had ended two days earlier; a match still to be played was narrated as
though its result were known; and a five-minute episode was a three-minute one
with more words in it rather than a deeper one.

Four different-looking complaints, one cause underneath three of them.

### The cause

`plan_episode` took what was typed, `ScriptGenerator.research` handed that
string to Exa unchanged, and `build_prompt` put the result in front of the
model. Nothing between the keyboard and the search decided *what the question
was*. So:

* **`Nvidia` was searched as "Nvidia".** There was no step that could ask why
  someone types a company name on a particular morning, so the retrieval came
  back with the company's own description of itself and the episode was
  correct, current and useless.
* **The evidence packet had no dates in it.** `build_packet` wrote
  `SOURCE n / Title: / Key evidence:` and discarded `published_date` and the
  URL, both of which Exa was already returning. The only time the model had was
  `now_line()` - the current moment - and nothing to subtract from it. It was
  being asked to choose between "last night" and "two days ago" with no
  information that could distinguish them. **That is not a writing failure, and
  a year of prompt work would not have fixed it.**
* **The window was never narrowed**, so a well-linked two-year-old explainer
  competed on equal terms with last night's report of the thing actually asked
  about.
* **Duration was a word count.** `plan_episode` multiplied minutes by
  `TARGET_WPM` and attached a one-line scope note. Nothing said what the extra
  minutes were *for*, so the model spent them the only way a word budget
  suggests: more words on the same material.

The fourth complaint - a preview narrated in the past tense - is a different
failure with the same root. Nothing in the system held the idea that an event
has a *status*, so nothing could refuse to write a result for something that
had not happened.

### The fix

A layer between the typed question and the search - `episode_intelligence.py`.
One model call, before Exa, producing a `Brief`: intent, resolved subject, a
why-now hypothesis with a confidence attached, the query to actually search,
what the evidence must establish, how fresh it has to be, which story shape
fits, and what the writer must not assume. It feeds two consumers from one
call - `research` reads the retrieval half, `build_prompt` reads the rest.

**Why it is before retrieval and not after.** A critique downstream of Exa can
only judge an episode built on whatever the packet happened to contain. If the
query that produced the packet was wrong, the evidence is already the wrong
evidence and no amount of checking recovers it. The gate has to be where it can
still change the outcome.

**What it costs, and that this was chosen.** One model call in front of the
first word, on the search path. It breaks CLAUDE.md's one-sentence spec, which
had not been broken before and was not broken accidentally here: the writing is
the product, and a fast episode about the wrong thing is worth less than a
slower one about the right thing. The browse surfaces pay none of it - there
the brief is built before the tap, which is what prefetch is for. `write.py`
prints the brief above the script, so a weak episode can be attributed to a
weak brief or to a weak script written from a good one, which have fixes in
different files.

Then the retrieval, which is where three of the four symptoms actually die:

* **The packet carries a publication date and a source grade.** The relative
  phrase - "yesterday", "2 days ago" - is computed in `age_phrase` from the
  dates rather than left to the model, so the blueprint's rule that relative
  labels come from normalized time is enforced rather than requested. An
  undated source says `date not stated` and is never given today's date.
* **Recency filters; credibility sorts.** The window
  (`start_published_date`, from `Brief.recency_days`) decides what is eligible;
  `rank_results` orders what survives by publisher grade, newest first within a
  grade. So "prioritise recency without giving up source quality" is two
  mechanisms doing two jobs rather than a weighted sum nobody can reason about.
* **One retry when the packet is thin.** `packet_covers` is a token test - not
  a model call, for the same reason `research_reason` is not one - and a packet
  missing what the brief asked for buys exactly one more search, on the
  resolved subject with the window dropped. A retry that also misses still
  returns its evidence, and the writer is told which parts are thin so the gap
  is named rather than filled from memory in the same confident voice.

And the two that are not about retrieval at all:

* **Duration buys depth.** `DEPTH_BANDS` says what each band of minutes is
  *for* - orientation, understanding, depth, the full arc - in content rather
  than in words, and the band reaches the prompt.
* **A story shape per episode type**, offered as a shape and never as boxes.
  This is the one change here that could reintroduce a failure this project has
  already paid for: the prompt before the rewrite imposed the same five beats
  on every topic, so a golf recap had to invent something for "the main debate
  or open question". `build_structure_note` names the shape and, in the same
  breath, says a beat with nothing real behind it is dropped rather than
  filled. `test_a_structure_is_offered_as_a_shape_and_never_as_boxes_to_fill`
  pins that wording, because losing it turns a structure back into a template.

### The seam that is built and not filled

`live_facts.py`. Exa retrieves *writing about* the world, and for two kinds of
question that is structurally wrong however good the query is: a game ends and
the scoreboard knows instantly, while the recap saying so is written, published
and indexed later - so in between, a search returns the preview. Same shape,
shorter fuse, for a price.

So there is a registry: a source declares a domain, diagnoses why it can or
cannot serve, and fetches. Registering a real provider is one line and nothing
else changes, because `prepare` already asks and `build_prompt` already knows
how to put the answer in front of the writer - a live fact outranks the packet
and says so, carrying the timestamp it was true at.

**Nothing is configured, and that is deliberately not the same as nothing being
here.** Both domains are declared and both report exactly what they would need.
`/api/health` names them. The failure this avoids is the one the project keeps
paying for: a capability that is absent and quiet gets shipped, and a
scoreboard question answered from an article index is wrong in a way nobody
sees until a listener hears it.

### What is deliberately *not* fixed

**A pre-retrieval gate cannot verify a fact.** Nothing has been retrieved when
it runs and the model's own knowledge is months old, so the brief never asserts
what happened - it states what must be established and hands the writer
cautions. Event status is settled downstream, from dated evidence. Anyone
reading the brief as a source of facts will be wrong.

**The drift check is weak on purpose, and the reason is worth keeping.** The
gate requires the rewritten search to share a content word with the question,
by prefix rather than by equality. Equality fails on exactly the rewrite this
layer exists to make: "the fed" becomes "US Federal Reserve rate decision" and
shares no token with what was typed. Any test strict enough to catch a subject
being *replaced* also rejects one being *resolved* - the two are identical to a
token comparison. So it catches wholesale replacement only, and semantic drift
is caught downstream where there is evidence to catch it with: the packet comes
back without what `must_establish` asked for, and the retry goes to `subject`.

**There is still no critic between the finished script and TTS**, and there
cannot be one that blocks: the script is spoken as it is written, so there is
no complete script to gate. The checking that exists is all upstream of the
first word.

**Nothing here has been heard.** There is no API key in the build container, so
every claim above about *writing* is unverified - the same standing caveat as
the rest of this file. `tools/ei_eval.py` is the twenty-prompt milestone from
the packet, and it refuses to run without keys rather than reporting a green
run on no data.

### The rule this adds

**A layer that adds quality must not be able to subtract availability.** Every
failure path in `episode_intelligence` - no key, a timeout, a refusal,
unreadable JSON, a gate that trips - falls back to a brief built from the raw
query, which is exactly what FAM did before the module existed. And it degrades
*loudly*: `Brief.degraded`, the log line, and `/api/health`. An EI that had
quietly stopped running would look identical from outside to one that was
working; the episodes would merely be less relevant, which is the slowest
possible way to notice.

One consequence worth stating, because it reverses a rule rather than extending
one: `research.retrieve` is pinned by a test forbidding it to catch anything,
so that a failed backend reaches the caller rather than becoming a different
kind of research. `_second_look` is the single deliberate exemption, and the
test now pins that too - a *first* retrieval failing means research never
happened and must propagate; a *second* failing means research happened and an
optional improvement did not, and throwing the first packet away for that would
make the episode worse for nobody's benefit.

### Coverage

`tests/test_episode_intelligence.py` - thirty-two tests. The call is made with
a closed schema at low effort; the brief reaches every field the rest of the
system reads; what it cost lands in the episode's own total. Then the floor:
a raise, a timeout, a refusal, unparseable JSON and the off switch each fall
back to the raw query and say why. Then the gate: a replaced subject is
reverted and a *resolved* one is not, an unknown intent is mapped back,
confidence without a hypothesis is downgraded, a question about a moment
acquires a window and an evergreen one does not, a recap always carries the
not-yet-confirmed caution, and the gate never refuses whatever it is handed.
Then the prompt: each duration asks for something different, depth is described
as content rather than as a word count, a structure is offered as a shape,
a degraded brief tells the writer nothing, and the cover half of an
answer-first episode never waits for a brief.

`tests/test_retrieval_quality.py` - twenty-three tests. Dates are read and
turned into the words a person uses; a future-dated source is flagged rather
than trusted; a missing date is never filled in. Publishers are graded and an
unknown one is never graded high; the best source goes first and the newest
wins within a grade; an undated source sorts last. A question about a moment is
windowed and an evergreen one is not. A thin packet buys exactly one more
search, on the resolved subject with the window dropped; the better packet wins
and the cost is the sum of both; a retry that also misses still returns its
evidence; a failed second look keeps the first packet. And, last, that without
a brief retrieval behaves exactly as it did before - one search, no window, no
retry, which is the floor.

`tests/test_live_facts.py` - seventeen tests, most of them about absence. A
question routes to its domain and only its domain; a source that fails is
skipped rather than ending the episode; nothing configured returns nothing and
pretends nothing; and health names every domain with no provider *and what it
would take to have one*. Then the block itself: it says when it was true, it
says it outranks the packet, and event status travels with it.

## 83. Prefetch: the framework, with the policy left to a number nobody has yet

### The problem

CLAUDE.md has said from the first page that **latency is answered by starting
earlier, never by filling the gap**, and that the browse surfaces are where
that is actually possible - myFAM and DailyFAM know what somebody might tap
before they tap it. Nothing had been built for it.

§82 made it more valuable and more urgent at the same time. Episode
intelligence - contextual relevance - put a model call in front of the first
word on search, deliberately and at the owner's direction. On search that is a
trade. On a browse surface it does not have to be a trade at all: the brief is
the expensive half, the tap is predictable, so the brief can be paid for before
the finger lands.

### The design, and the one detail everything else hangs off

**Prefetch writes into the same script cache, under the same key, as a live
episode.** Nothing on the tap path changes. A tap on a warmed tile is an
ordinary cache hit, and the pipeline has served those since long before this
existed.

Which makes the cache key the whole ball game. If prefetch computes it one way
and the tap another, they agree today and drift the first time one of them
gains a field - and the failure is **silent and total**: every speculative
script is paid for and never read, while the feed looks exactly as it did
before. So `pipeline.key_for` and `pipeline.bucket_for` were extracted to
module level and `PodcastPipeline` now delegates to them. One function, two
callers, no possibility of disagreement, and a test that reads the pipeline's
own source to keep it that way.

That extraction immediately found a second bug of its own making: reading
`self.generator.client` as an argument made the pipeline require an attribute
it had previously only touched when `CACHE_SEMANTIC_KEY` was on. Six tests with
fake generators failed. `getattr(..., None)` restores the old tolerance, and
`key_for` already guards on `client is not None`.

### Two warm levels, because "how much to prefetch" is a real question

CLAUDE.md lists it as open: *every speculative script costs money; every one
not fetched costs a wait*. The two levels are the honest shape of that:

* **brief** - run contextual relevance only and keep the result. One small
  model call. Removes the seconds EI costs; the tap still pays retrieval and
  writing. **This is the default** on the reasoning that it buys most of the
  felt improvement for a fraction of the waste.
* **script** - write the whole episode. The tap pays nothing at all, and a
  wrong guess costs a full episode.

A warmed brief is **time-bounded**, and that is not a tidiness rule: a brief is
a claim about *now*. A why-now hypothesis and a recency window built this
morning are wrong by this evening, and a stale brief is worse than none because
it would make the episode confidently about the wrong day. A **degraded** brief
is never kept at all - keeping one would mean a tap skips contextual relevance
and gets the pre-EI behaviour, having already paid for a call that failed.

### Where the guesses come from

`prefetch_sources.py`, separate from the machinery so that adding a surface is
a new class there rather than an edit here, and so the machinery can be tested
without a topic bank. Four are built, and the ordering is a **cost design, not
a ranking one** - CLAUDE.md's "one bank for everyone, personalisation in the
ordering, not the inventory" is what makes some guesses structurally cheaper:

* **trending** - identical for every listener by construction, so one warmed
  script is taken by everybody who taps that tile. Best value per dollar in the
  app, and the only source worth running with nobody signed in.
* **mixes** - the strongest prediction FAM has: somebody wrote down that they
  want this subject every day, and a mix holds topic ids rather than audio
  precisely so it is generated fresh each morning. Bank members outrank typed
  ones because a bank member is shared and a typed one is a script a day for
  exactly one person - which `mixes.MixItem` already said, and which the
  candidate's reason now says out loud.
* **feed** - the same `build_feed` the page draws itself from, so a warmed tile
  and a shown tile cannot disagree. A second implementation of "what next" is
  what CLAUDE.md already refused once for `rank_next_up`.
* **threads** - the `<<NEXT:>>` follow-up, which is already written and already
  offered as a chip. Warming it is the difference between that chip being
  instant and being an ordinary episode.

Every candidate carries a **reason**, and that is the contextual-relevance
claim being made out loud. It survives to the report, because the only way to
judge a prefetcher is to see which kinds of guess get taken.

Sources are **forbidden to call a model or the network** - a source that costs
money to *ask* turns a speculative saving into a certain spend - and a test
reads the module to enforce it rather than trusting the rule.

### The four things it must never do

1. **Never compete with a live listener.** `stream_pcm` calls
   `note_live_generation()`; prefetch reads that clock and stands aside for
   `PREFETCH_QUIET_SECONDS`. One warm at a time, under a lock. A speculative
   episode that delays a real one has inverted the entire point.
2. **Never spend past a ceiling**, in two currencies because they fail
   differently: the episode count stops a runaway loop, the dollar figure stops
   a *correct* loop being expensive. A 10-minute researched episode costs
   several times a 1-minute one, so counting episodes alone does not bound the
   bill. An unpriced model is *named* in the log rather than silently costing
   nothing - a budget that stops counting is a budget that no longer caps.
3. **Never warm something personal.** `is_shareable` and the attachment rule,
   the same as a live episode. A warmed private question is a script nobody can
   be served, paid for in advance.
4. **Never pretend it is paying.** Warmed and taken are counted separately, per
   source, and `note_consumed` is called from the serving path with the key
   that actually hit - a hit rate inferred anywhere else is one nobody should
   trust. The rate is `None` rather than `0` when nothing has been warmed,
   because zero out of zero reads as a failing prefetcher and is actually no
   data at all.

### Shipped off, and why

`PREFETCH=0`. The same reasoning as the tier system (§81): the mechanism is
worth having ready and the policy is worth deciding with numbers. The hit rate
that answers "how much to prefetch" does not exist yet, and switching this on
is what starts producing it. `/api/health` reports which state a deploy is in,
because a prefetcher that is off looks exactly like one that is on and missing
everything - and `prefetch_sources.report()` names every surface with no source
installed, because a prefetcher running on one out of four looks identical from
outside to one running on all of them.

`python tools/prefetch_report.py` prints what a deployment *would* warm without
spending anything (safe against production data, since sources cannot call
anything), and `--live` reads the hit rate off a running server. There is
deliberately no command that warms: spending is the server's job under its
budget, and a tool that could spend outside it would be a second place the
ceiling has to be enforced.

### A bug the tests found, which was real

`install()` runs at startup and registered its sources unconditionally, while
`register` refuses a duplicate name - correctly, since two sources with one
name double-count the hit rate the whole thing is judged on. Any *second*
startup in one process therefore raised at boot: every test that opens a
`TestClient`, and any in-process reload. Ninety-three errors said so at once.
`install` now replaces the sources it manages and leaves anything else alone,
because startup is the statement "these are this deployment's sources" and a
statement has to be re-makeable.

### What is deliberately not here

**Nothing schedules a cycle.** `run_once` exists and nothing calls it on a
timer. That is the next decision rather than an oversight: when to run, how
often, and per-listener or globally, are policy questions that want the same
hit-rate evidence as the level does - and a scheduler added now would spend
money on a schedule nobody has justified.

**The brief store is in-process.** It does not survive a restart and is not
shared between workers, so a multi-worker deploy warms per worker. Scripts -
the expensive half - go in the real shared cache. This is the stated limit of
the seam, and moving it to a shared store is a change to `BriefStore` alone.

**No hit rate exists.** Everything above is machinery. There is no API key in
the build container and prefetch has never run against a real model, so which
sources pay for themselves is unknown and is exactly what turning it on is for.

### Coverage

`tests/test_prefetch.py` - thirty-seven tests, organised around the four
properties that have to hold before anybody turns it on. The key one first: the
pipeline is read to confirm it delegates to `key_for`, and a warmed episode is
then looked up with the key a tap would compute. Then standing aside (a live
generation blocks a warm; the serving path really does send the signal; two
concurrent warms serialise), the ceiling (episodes, dollars, the daily roll,
and nothing at all while off), and the ledger (a hit counted once and only
once, never claimed for a key nobody warmed, `None` rather than zero with no
data, and per source). Then the plan: sources interleaved so one cannot take
the whole budget, the same question warmed once, an unshareable one never, and
a cycle that stops the moment the answer can only be the same. Then the brief:
handed to the writing path before it pays for one, degraded ones never kept,
stale ones expired, and one warmed at three minutes never used at ten.

`tests/test_prefetch_sources.py` - sixteen tests against the real bank and the
real stores. Trending answers with no listener and is never tagged to one; the
candidate is the tile's `query` and not its label, because warming the label
writes an episode nobody asks for. A bank mix member outranks a typed one and
the typed one says it is shared with nobody. The feed source warms the rails
the page actually draws and does not warm trending a second time. A thread
source whose store falls over returns nothing rather than taking the cycle
down. Every candidate has a reason and the length the cache key expects. And,
last, the module is read to prove no source reaches for a model or the network.

> **§84 to §87 are not missing — they were reverted.** They recorded the
> continuous-line illustration subsystem, which was removed from `Main` in
> its entirety. The numbering keeps the gap rather than closing it, so that
> the code comments referring to §88 and §89 stay correct and so that
> restoring the visual work would not collide with these.

## 88. It wrote a final score for a game that was in its third quarter

Someone asked FAM "Chiefs game" on a Monday night in week 1, with about ten
minutes left in the third quarter and Denver trailing 21-7. The episode opened
on the 2017 draft, said *"Kansas City beat Denver 27-16"*, spent its middle
explaining how well Mahomes had played, and finished by saying it did not know
who the Chiefs play in week 2 - a fixture that had been public since May.

The same question asked twelve hours later produced a good episode. That is the
most useful fact in the report, because it rules out almost everything: the
prompt was fine, the model was fine, the retrieval worked. What failed was
confined to **the window between an event starting and a report about it
existing**, and in that window FAM had no way to say what was happening and
several reasons to say something that was not.

### Five separate faults, and only one of them is a prompt

**1. EI asserted that the game had finished, using a label.** `INTENTS` read:

    "recap",  # something finished; tell me what happened

That is a claim about the world, made by the one layer in FAM whose own module
docstring says it *never asserts a fact*, about the one thing it cannot possibly
know: nothing has been retrieved when EI runs and its own knowledge is months
old. The label looked like a classification of the request and was in fact a
statement that the event was over, and everything downstream read it as one.

The fix is not a better guess. It is to ask a question EI can actually answer.
"Has the game finished" is about the world; **"is the answer they want a
result"** is about the request, and is decidable with no evidence at all - "who
won" has no answer until something concludes, whether it concluded an hour ago
or is still going. That is `Brief.outcome_dependent`, and `recap` now reads
"tell me what happened, if it has happened".

**2. The story shape required a result, so the writer produced one.**
`sports_recap` is *"what was at stake, then the turns the game actually hinged
on, **then the result**, then who decided it, then what it changes"*.

`build_structure_note` already says a beat with nothing behind it is dropped
rather than filled, and that was written for exactly this class of failure (§82,
the golf recap invented "the main debate" to fill a slot). It was not enough
here, and the reason generalises: **dropping the result from a recap leaves
nothing**. It is not a beat of the shape, it is what the shape is for. A rule
that says "drop it" is asking the model to delete the episode, so it filled it
instead.

So the alternative has to be *named*, not deduced. `in_progress` is a new shape
- what is at stake, how it stands now, what has already been settled, what is
still open - and when a brief is outcome-dependent the structure note points at
it explicitly: if the evidence does not report the result as final, this is not
the shape, write that one. **It is a real episode, not a consolation.** What is
at stake and what has happened so far is most of what someone asking mid-event
actually wants, which is the part the whole failure obscures.

**3. The temporal block had two states and the world has three.** It covered
*not started* ("if something has not happened yet, it has no result") and
*finished but unclear* ("if the sources do not establish how something ended").
A game in its third quarter is neither, and there was no sentence anywhere in
FAM for it.

Worse, the packet was **entirely pregame** and read as evidence for a recap. The
script's own sentences give it away: "the favorite side of a spread that had
them at two and a half points", "his *projected* starting left tackle", a pass
rush "*expected to be* one of the tougher tests". Those are preview sentences,
retrieved because the recency window was correct and the recap did not exist
yet. Nobody had ever told the writer what a packet of previews *means*: it is
not thin evidence of an outcome, it is evidence that there is no outcome.

**4. It read a contradiction and talked itself out of it.** This is the tell
that it knew:

> "That result puts Kansas City at one win, no losses, though the standing
> snapshot from right after the game still shows them listed second in the AFC
> West. That's just a rounding artifact of how early it is in the season, not a
> sign anything's wrong."

The packet contained a standings source that its own invented result would have
changed. Given direct evidence against the fabrication, the episode **invented a
second fact to reconcile the first**. So the temporal block now says that a
contradiction is information rather than a problem: if something you believe
implies a result and a standing, record or table says otherwise, you do not have
a result - you have something that has not finished - and the smaller true
reading wins every time.

**5. A missing search result was reported as the world being silent.** The week
2 line came from `thin_on`. `packet_covers` found the brief's "next opponent"
missing, and `build_prompt` said:

    Say plainly that that part is not yet reported

One search missing something is a fact about *the search*. "Not yet reported" is
a claim about *the world*. For a volatile fact the two nearly coincide, and that
is why the wording survived. For a settled one they do not coincide at all:
nobody writes a news story about a fixture that has not changed since May, so
the search misses it and the instruction converts that into a false claim - and
then puts it in the last line, which also broke three separate system-prompt
rules about not narrating sourcing, not forecasting, and never ending on an open
thread. **The block was fighting the prompt it lives in.**

It now splits the two cases by hand, because they genuinely differ: something
that *changes* is not supplied from memory (a search that missed it is real
evidence it is unsettled), something *already settled* may be, and if you are
not sure enough to say it plainly you leave it out of the episode entirely.
Either way the gap is never announced and is never the last thing heard.

### The smallest bug, and the most expensive

    if brief.intent in ("recap", "update") and not brief.cautions:
        brief.cautions.append("nothing has been confirmed yet: ...")

The single most important caution in FAM was appended **only when the model
produced none of its own**. A model that has just been asked for cautions
produces some, so in production this fired almost never - while the source read
as though the guard were present, and the test that covered it passed, because
it was written with an empty list.

That shape is worth naming because nothing about it looks wrong on the page: a
guard whose precondition is *"nothing else happened"* is off exactly when the
system is working normally. It is now appended unconditionally and **prepended**,
which also puts it out of reach of the `[:6]` truncation two lines below that
could otherwise have dropped it. And its wording gained the state it had no
words for: *"an event that has started is not an event that has finished"*.

### The seam this lands on, which was predicted and is still empty

§82 wrote down exactly this failure in advance:

> A game ends and the scoreboard knows instantly; the recap saying so is
> written, published and indexed later, so in between a search returns the
> *preview*.

`live_facts.py` is the seam for it and has a provider for neither of its two
declared domains, so `lookup` returns `None` and the episode was written as
though nothing were missing. A capability that is absent and quiet gets shipped.

Registering a real scores provider is still one line and still the actual fix.
Until somebody does it, `build_prompt` now says the gap out loud whenever a
brief names a live domain and nothing answered: articles are written after the
fact and indexed after that, so the newest thing you have is older than the
thing being asked about, and the absence of a report is the report not existing
yet - never licence to supply the state yourself.

### The opening was vague, and it was following the rules

The episode opened on a 2017 draft decision. That is what "start inside
something already in motion", "no orienting", and "do not state your conclusion
in sentence one" produce when taken literally: a vivid concrete detail that
could have opened any Mahomes episode from any of eight seasons, and which left
the listener thirty seconds from knowing what they were listening to.

Nothing in the prompt said the opening has to be about **this** episode. It does
now, and the distinction it turns on is worth keeping straight because the two
sound alike:

* **Orienting** is telling someone why a subject matters or what they are about
  to hear. Still banned, for the reason it always was.
* **Situating** is telling them where they are standing. Who, what, when, in
  particulars - "the Chiefs play Denver tonight to open the season, and Mahomes
  is nine months off a torn ACL". Required, inside two sentences.

With the corollary that history earns its place by explaining the present rather
than preceding it. A cold open years back reads as stalling, because it is.

### What this cost the prompt budget, and how

`test_the_prompt_stays_lean` caught the addition at 8,407 characters against a
7,600 bound, which is the test doing its job. The two new rules were then cut to
about a third of their first draft and paid for by a dedup pass in the same
change - the `<<NEXT:>>` mechanics no longer explained in both prompts, the
orienting contrast folded into the bullet it restates. The bound moved 7,600 ->
7,800, by the residue rather than by the addition. Creep is addition with no
pass for duplication; the way past that test is to do the pass.

### What is fixed, and what is not

Fixed: EI no longer asserts that anything finished; a question whose answer is a
result is marked as one and cannot lose its caution; there is a story shape for
an event under way and the writer is told when to switch to it; pregame evidence
is named as evidence of no result; a contradiction is not to be reconciled by
invention; a thin packet is no longer reported as the world being silent; the
missing live feed is stated rather than absent; and the opening has to land the
listener in the actual situation.

Not fixed, and not fixable here: **none of this has been heard.** There is no
API key in the build container, so every one of these is a prompt and a code
path proven by tests rather than by an episode. The thing that would actually
settle it is one run of

    python write.py "chiefs game" --minutes 3

during a game, with a key, reading the EI block above the script - which is
precisely the split `write.py` prints for, and which now shows
`answer is a RESULT` and the missing live feed on the same screen as the words
they produced.

And still the real fix for the whole class: **register a scores provider.** Every
change above makes FAM honest about not knowing the score. None of them makes it
know the score.

## 89. Not knowing is not the same as nothing having happened

§88 stopped the writer inventing a result. It did not stop the *system* losing
the difference between "we have no view of this" and "there is nothing to see",
and that difference turns out to be the whole subsystem.

Four things were wrong, and only the first was the one being looked for.

### The cache was serving a game in progress for twenty-four hours

`cache.ttl_for` took a query string and matched it against a keyword list.
Measured on the exact reported case:

    'Chiefs game'                    -> 86400s
    'Tell me about the Chiefs game'  -> 86400s
    'how is the match going'         -> 86400s

Twenty-four hours, for an episode about a game being played. And `recent()` is
the Explore feed, so such an episode is not merely re-served - it is
**published** as a finished one, into a surface whose entire promise is that it
replays episodes that already exist.

**The fix is not another keyword, and this needs saying because it is the
obvious move.** §76 already settled it for research: a keyword list can always
be widened by one more word, and the next question it misses is already
written. "Chiefs", "game" and "score" would every one of them have missed "how
is the match going". The list is not under-tuned; it is answering the wrong
question.

So `ttl_for` now asks **how long what this episode says stays true**, which is
answerable, because by the time anything is written we know what it was built
from. Live status first (`in_progress` → uncacheable, `final` → the ordinary
ceiling, since a result does not move again), then `outcome_dependent`, then
the evidence window, then the keyword list as the floor for paths that have
none of those. Ordinary static content returns exactly what it always did.

### The information could not reach the decision

This was the part that made it a real change rather than a one-liner.
`pipeline.py` computes the TTL holding the **unprepared** plan:
`stream_sentences` rebinds it (`plan = await self.prepare(plan, notes)`) and
`_answer_first` derives two more the caller never sees. So `plan.brief` and
`plan.live` are `None` at the write site and always would be.

`ScriptNotes` is the channel that already crosses that boundary - it is how
`thread` and `research` get home - so the cache policy rides back the same way.

### Six different failures were being reported as one

`live_facts.lookup` returned `Optional[LiveFacts]`. No provider configured, the
provider broke, the provider timed out, the provider has no such game, the
provider is reporting nothing, the data came back too old - all `None`, and the
writer was told the same nothing by every one of them. In practice it was told
nothing at all.

`LiveLookup` replaces it with seven named outcomes, each rendering a different
block. The one that matters most is `not_configured`, because it is the state
FAM actually ships in, and the sentence it produces is the thesis of the whole
subsystem: *what is missing here is our view of it, not the event.*

### A stale score is worse than no score

Nothing bounded age. `as_of` was rendered for the model to judge, and a
timestamp a model is asked to judge is one it judges generously. A fifteen-
minute-delayed market feed - which is what free tiers are - would have been
rendered under a block that says *"this is what is true now"*.

Freshness is now a deterministic check in code, per domain (sports 120s,
markets 300s, elections 1800s - one threshold would be wrong for two of the
three), and data past it is **withheld** rather than annotated. `delayed` is
separate and is about design rather than age: nonzero means the prompt says
delayed, never current.

### What else came out of the audit

**Prefetch was warming live facts.** `_warm` called `live_lookup`, so warming
would spend a provider call speculatively *and* bake a score into a script
served hours later. It no longer calls it at all, and a script is never warmed
for an outcome-dependent question - the brief is kept, because that is a claim
about what is being *asked* and it keeps.

**The live lookup ran in series with research**, adding its whole latency in
front of the first word. Its own docstring said "runs alongside retrieval",
which was aspiration rather than description. They read the same brief and
neither reads the other's output, so they are now gathered.

**`examples/README.md` still taught the ending doctrine §48 reversed** - "don't
conclude, widen; leave one thing unresolved and stop pointed at it". CLAUDE.md
records that an example file has turned deleted behaviour back on in this
project before, and this was the same shape waiting to happen: the rule was
removed from the prompt and left in the file people copy.

**The hermetic test caught the new settings in both directions**, which is what
it is for - once for not clearing them, and once for listing a name `config.py`
did not read, because `live_sources` was reading it from `os.environ` directly.
Every knob belongs in `config.py`; one read elsewhere is one a developer's
shell can leak into a suite.

### The line this settles

EI may decide that the **request** wants a result. It may not decide that the
**event** is scheduled, under way or finished - it has retrieved nothing and
its knowledge is months old. There is no `status` field in `BRIEF_SCHEMA`, and
a test asserts there never is, because a field that does not exist cannot be
filled in by a persuasive model. `in_progress` is likewise absent from the
structures EI may pick, for the same reason pointing the other way.

### What is still not true

**No real provider is connected.** Everything above is the architecture for
one, verified against a fake that is selected explicitly and names itself
`NOT REAL DATA`. FAM does not have live scores; it now knows that it does not,
says so to the writer on every live question, and refuses to cache an episode
built in that state.

`LIVE_FACTS.md` is the whole of it.

## 90. "Trending" meant two different things, and only one of them was built

myFAM's crowd row was keyed `trending` and titled *"What FAM can't stop
playing"*. Those are not the same claim. The key said the world; the title said
this app; and what the code actually did was count FAM's own plays across a
hand-written bank of twenty-eight topics - so on a young deployment it mostly
ran its `filler` branch and showed a stable slice of the bank.

Nobody was misled yet, because the title was the honest one. But asking for "a
trending row" got the answer "you have one", and that was wrong.

### Two rows, because they are two questions

The crowd row keeps its title and becomes `most_played`, which is what it was
always doing. `world_trending` is new, titled "Trending", and comes from
outside.

They can disagree - the world can be consumed by something nobody on FAM has
played, and FAM can have a runaway hit the world has not heard of - and a
listener reads them differently. Blending them into one ranking would lose both
signals.

### It is not the live-facts subsystem, and reusing it would have been wrong

The obvious move was to route trending through `LiveSource.resolve/fetch`. It
does not fit, and the misfit is informative:

    live_facts   resolve an entity -> fetch its state
                 seconds of freshness, a closed status vocabulary
                 changes what an episode SAYS

    trending     no entity to resolve, no status
                 minutes of freshness
                 changes what is OFFERED

Reusing it would have meant inventing a fake entity for "the world" and
bending a contract built around scoreboards around something that is not one.

They compose instead, and better than they would have coupled: a trending tile
about a game becomes an ordinary FAM question when tapped, EI marks it
`live_domain=sports`, and `live_facts` answers it. **Trending feeds the bank;
live facts feed the evidence.**

### The cost design is the inverse of live facts', and that is the point

`live_facts` costs per entity and per episode. Trending costs **one fetch for
every listener** - one upstream call per window, one warmed script per tile,
everybody. That makes it the cheapest place in FAM to add live data rather than
the most expensive, and it is the same economics CLAUDE.md already relies on
for the crowd row.

Three things fall out of it rather than being chosen separately: the cache is
global rather than per listener; `build_feed` reads it **synchronously**, so
the ranker stays a pure function of the log plus the cache and is still
callable in a test with no network; and `/api/myfam` **schedules** a refresh
instead of awaiting one, because the browse surfaces are the one place the wait
has to be zero and a news feed is not worth spending it on.

### What a tile may carry

**A question, never a headline.** "Chiefs 21 Broncos 7" has a shelf life of
seconds and belongs to `live_facts`; "why the Chiefs' offensive line is
suddenly the story of their season" keeps for hours and is what a tile is for.
A tile whose query is a headline produces an episode that restates the
headline.

**`why_now` is a subtitle and never evidence.** Tapping a tile runs the
ordinary pipeline, which researches from scratch - which is what stops a stale
blurb becoming a stale episode. A test reads `build_prompt` and asserts it does
not mention trending at all.

**The id is hashed from the subject, not the query.** Impressions, fatigue and
the already-seen set are all keyed on it, so an id that churned on every
rephrasing would show one listener the same tile forever and fatigue could
never damp it.

### The empty state, which is §89 again on a different surface

Four ways to come up empty - not configured, the source failed, it timed out,
it had nothing - and four different sentences, because they are four different
things to fix. **None of them says "nothing is trending".** An empty row is a
fact about this deployment; reading it as a statement about the world is
exactly the mistake §89 settles for live facts, and it is easier to make here
because an empty row *looks* like a quiet day.

### What is deliberately not decided

**Connecting a source means the bank stops being entirely hand-written**, and
CLAUDE.md treats "one bank for everyone" as settled. The twenty-eight topics
have taste in them that a generated row will not.

That is a change to a settled constraint, so the seam ships with no source and
the decision is left where it can be made with a real row in front of you:
`TRENDING_SOURCE=fake`, look at the page, and judge whether generated tiles
belong beside the written ones.

`TRENDING.md` is the whole of it, including the candidate feeds and why
NewsAPI is ruled out.

## 91. Sources were collected on every episode and thrown away

FAM extracted every publisher, graded it, dated it and stored it on
`notes.research` - and nothing ever read it back. `ScriptNotes` said so in its
own docstring: *"Written to and never read back by the writing path."*

So the first half of this was not a feature, it was reconnecting a wire that
had never been plugged in at the far end.

### The rule that makes displaying it safe

CLAUDE.md is emphatic that the evidence packet carries source **grades** and
never hostnames - a domain in the packet is a domain the voice can read out,
and the model needs to know it is reading a wire service in order to weigh it,
not a way to say "reuters dot com" aloud.

**That rule is about the prompt. The panel is a different channel.** Nothing in
the provenance path reaches a prompt, and `research.domains()` has always
existed "for a person to judge". What this adds is somewhere for that output to
go.

The obvious next step is the one to refuse: *"we show sources now, so let the
model cite them"* would put hostnames back in front of the voice. A test reads
`build_prompt` and asserts it mentions neither provenance nor hostnames,
because a rule nothing enforces lasts until the next refactor.

### Two things that would have gone wrong quietly

**A cache hit had no sources.** A replayed episode has no `notes` to rebuild
them from, so a shared or Explore episode would have shown an empty panel while
a freshly generated one showed a full list. Same shape as the problem the
`thread` column already solved, fixed the same way - a `sources` column by
additive migration, and an accessor on both backends.

**An attachment title is the listener's own document.** The script cache is
shared and feeds Explore, so a cached title would have shown one listener the
name of another listener's file. `Provenance.shareable` drops anything private
before storage. An attached episode is already uncacheable, so this is belt and
braces - but the belt is the one that would have been noticed too late.

### Credit only where something contributed

A live provider is credited **only on `facts`**. One that failed, timed out,
found no matching entity or returned data too stale to use did not contribute,
and listing it would claim corroboration that did not happen. Likewise only the
articles that reached the packet are credited, not everything retrieval
returned - the writer never saw the rest.

And `known` on the API response separates *"we recorded no sources"* from
*"there were none"*, because an episode answered from knowledge is a real case
and rendering it as "no sources" is §89's mistake on a third surface.

### GDELT, in two jobs on two clocks

A second retrieval index beside Exa (per episode, `GDELT_CROSS_CHECK`) and the
Trending row's feed (shared 15-minute clock, `TRENDING_SOURCE=gdelt`). One
upstream, two adapters, because the two clocks want different things from it.

Keyless, so a cross-check costs nothing per episode - which is what makes "not
from only one source" affordable rather than aspirational. Additive only: it
never replaces the primary packet and `gdelt.retrieve` returns `[]` on any
failure by contract, because a cross-check that could break an episode would be
worse than no cross-check.

The results are **duck-typed to the Exa shape** so `rank_results`,
`credibility`, `published_at` and `provenance.from_results` all work unchanged.
A second retriever needing its own branch in each would be four places to
forget.

**The limitation worth writing down**: GDELT's DOC API is query-driven. It
measures coverage of a query you *name*; it does not hand back a ranked list of
everything hot. So the Trending source sweeps a fixed theme vocabulary and
ranks by measured volume - real measurement over a fixed list, not open-ended
discovery. Open-ended needs the bulk GKG exports, deliberately not taken.

### The providers, and the one that is a trap

API-Sports and SportsDataIO for sports, Finnhub and Alpha Vantage for markets,
Polymarket for forecasts. AP Elections and Decision Desk HQ are **declared and
unimplemented**: both are sales-gated with no public pricing and no open
endpoint, so there is nothing to write against and guessing would be worse than
nothing. Naming the gap with what it would take to close it is the point.

**Polymarket is the trap.** A prediction market returns what people are
*betting*, and a live in-game win-probability line moves with the score - so it
reads like the score. *"Chiefs at 94%, so they must be winning"* is §88 coming
back through a side door. Every fact it produces carries
`kind="prediction-market"` and `status=UNKNOWN`, always, and `unknown` is the
status in which no result may be spoken. Structural, not a request.

Every adapter maps its vendor's status vocabulary at the boundary, and anything
unrecognised becomes `unknown` rather than a guess - so a provider that changes
its codes degrades to silence rather than to a confident wrong tense.

### What this build cannot prove

**Not one of these has made a live request.** The container's egress proxy
blocks `api.gdeltproject.org`, `gamma-api.polymarket.com`, the API-Sports
hosts, `sportsdata.io`, `finnhub.io` and `alphavantage.co`. Every shape is
written from vendor documentation and pinned against recorded payloads.

That is the §52 gap exactly, and it is open: the tests prove the parsing and
prove nothing about whether the endpoints still answer in that shape.
`tools/gdelt_probe.py` and `tools/verify_live.py` are what close it, somewhere
with network. The probe was run here and failed honestly with 403s, which is
the only result it could have given and the right one.

`PROVENANCE.md` is the panel; the provider table is in `LIVE_FACTS.md`; the
GDELT notes are in `TRENDING.md`.

## 92. The sports adapter was pointed at the wrong sport

§91 shipped an API-Sports adapter with `BASE = "https://v3.football.api-sports.io"`.
That is **soccer**. The motivating case for this entire line of work is an NFL
game, which lives on `v1.american-football.api-sports.io` - a different host, a
different response shape and a different status vocabulary.

Nobody would have seen it from the tests, because the tests fed it a soccer
payload and it read a soccer payload correctly.

### API-Sports is four APIs wearing one brand

| sport | host | path | score field |
|---|---|---|---|
| american-football | `v1.american-football.…` | `games` | `scores.home.total` |
| football (soccer) | `v3.football.…` | `fixtures` | `goals.home` |
| basketball | `v1.basketball.…` | `games` | `scores.home.total` |
| baseball | `v1.baseball.…` | `games` | `scores.home.total` |

So `live_sources.SPORTS` is a table rather than a constant, and the shape
differences are **data rather than branching** - one `to_facts` that reads both
score shapes, with the status vocabulary looked up per sport. A test asserts no
two sports share a host, that both score shapes are read, and that a code
borrowed from another sport does **not** map: `1H` is in-progress in soccer and
`unknown` in gridiron, and cross-wiring those is the failure this catches.

The entity id carries its sport (`american-football:9`), because a bare game id
is meaningless without knowing which API issued it and `fetch` receives only
the entity.

### The limitation that is left, deliberately

**"Chiefs game" names no sport.** It falls back to `API_SPORTS_SPORT`, which a
deployment sets to whatever it mostly serves.

Team-name routing was considered and rejected: it needs a maintained roster of
every team in every league, and a stale roster sends an NFL question to a
soccer endpoint - worse than a default somebody chose on purpose. Resolving
sport from team properly wants the provider's own cross-sport team search,
which is N requests rather than one. That is the next step and is written down
rather than guessed at.

### SportsDataIO was subclassing the wrong thing

It inherited the API-Sports adapter for its sentence shaping, which meant it
also inherited API-Sports' endpoints and status codes. It is a different vendor
with flat PascalCase rows and its own vocabulary, so it now stands alone.

Also named, because it is a trap: **its free key returns deliberately scrambled
data**. A trial key looks like it works and `verify()` cannot tell the
difference, so `diagnose()` carries the warning whenever a key is set.

### Finnhub did not know whether the market was open

A quote pulled at three in the morning is not what something "is trading at" -
it is where it closed. Saying the first when you mean the second is a small lie
a listener catches instantly, and it is exactly the class this project keeps
paying for.

One extra call to `/stock/market-status`, and the sentence follows the fact:

    open     "is trading at"
    closed   "closed at"
    unknown  "was most recently at"

`None` for an unknown session rather than a guess, reported as "most recently"
rather than asserted either way.

Its symbol resolution also now prefers ordinary common stock over the warrants,
units and foreign listings sharing a prefix, so "Apple" finds `AAPL` rather
than `AAPL.SW` - a wrong ticker does not fail, it returns somebody else's
price.

### The proxy, settled

Every provider host is blocked from the build container, **including
`api.exa.ai`, which FAM already uses in production**. That is what proves it is
a blanket container egress policy rather than anything provider-specific, and
the proxy README is explicit: a 403 on CONNECT is an organization policy denial
and must be reported rather than routed around.

`PROVIDER_ROLLOUT.md` is the runbook for turning each one on somewhere with
network.

## 93. Polymarket was configured, healthy and never once asked anything

Connecting the four live providers to production turned up a provider that
could be switched on completely and still do nothing. `LIVE_ELECTIONS_PROVIDER=polymarket`
registers the source, `/api/health` reports it registered, `diagnose()` passes,
`tools/verify_live.py --domain elections` resolves a market and fetches a price.
Every check said yes. No episode ever reached it.

### The cause: one vocabulary kept in two places

`live_facts.LIVE_DOMAINS` is the routing vocabulary — a source declares one of
those and `lookup` dispatches on `brief.live_domain`. `BRIEF_SCHEMA` held a
second, hand-written copy:

    "live_domain": {"type": "string", "enum": ["", "sports", "markets"]},

`elections` was added to the tuple and never to the enum. The schema is passed
as a strict `json_schema` output format, so this was not a model that tended not
to say `elections` — it was a model that **could not**. `lookup` returns `None`
for any domain the brief does not name, so the elections branch was dead code
reachable only from the verification tool.

Nothing failed. There was no error, no log line, no degraded flag. The
difference between "configured and serving" and "configured and structurally
unreachable" was invisible from every surface built to make exactly that
difference visible — because those surfaces all report on the registry, and the
registry was fine. §89 built `LiveLookup` so the writer could tell "no provider"
from "provider broke"; this was a third thing, "provider that is never asked",
and it looked like neither.

**The general rule: two vocabularies that must agree are one vocabulary.** This
is §83's `key_for` argument arriving in a different file — there the failure was
two cache-key implementations drifting and every prefetched script being paid
for and never read, silently and totally. Same shape here. So the schema now
reads the tuple:

    "live_domain": {"type": "string",
                    "enum": [""] + list(live_facts.LIVE_DOMAINS)},

and a test asserts the *reading* rather than the values, because a test listing
the domains would be the third copy.

### The half a schema fix does not cover

An enum value with no instruction behind it is one the model never picks. The
EI prompt described `sports` and `markets` and stopped, so adding `elections` to
the enum alone would have left it technically selectable and practically unused.
A second test walks `LIVE_DOMAINS` and fails if any domain is routable but never
explained to the model.

### And a live bug that the blocker was hiding

`LiveFacts.as_prompt_block` ended every block with:

> This is the most authoritative thing you have been given. Where it and the
> articles below disagree, this is what is true and the articles are older.

That is earned for every other source here: a scoreboard was *observed*, and the
article about the game was written later and from further away. A prediction
market was observed too, but what it observed is what people **expect** — so it
is the newest thing in the prompt and the least authoritative thing in it, a
combination nothing else in this module has.

Handing a forecast that paragraph is §88's side door standing open with a
welcome mat: an article reporting the actual result would be explicitly
overruled by a price, and "trading at 94 percent" would be written up as the
outcome. `status=unknown` forbids *stating* a result; it says nothing about
which source wins a disagreement.

So `PREDICTION_MARKET` is now a named constant — a closed vocabulary, for the
same reason `STATUSES` is one, because `as_prompt_block` switches on it and a
switch on a free string misses silently — and that one kind is told the
opposite: it is a forecast, it is not evidence of an outcome, and **where it
and the articles disagree the articles win**. A market that has not caught up
with a reported result is a market that is wrong.

The bug was unreachable while the schema blocked elections, which is the part
worth noticing: unblocking a path is also unblocking whatever was wrong on it.

### Still true

Nothing here has made a real request to `gamma-api.polymarket.com` — the
container's egress blocks it, as it blocks every provider host including Exa's.
`python tools/verify_live.py --domain elections` on a machine with network is
what closes that, and the offline proof is that routing now reaches the source
with the network stubbed.

## 94. A perfect episode that opened by apologising for itself

`"Dodgers game last night"`, three minutes, researched, generated at 3:15 the
following afternoon. From its fifth sentence on it is the best episode this
project has produced: seven innings and one hit for Yamamoto, the two home
runs and who they came off, Freeman's three hits, the twelfth shutout, the
magic number down to two and what closes it out, Cincinnati shut out a league
high seventeen times. Every word of it checks out.

It opened like this:

> I don't have anything reliable on last night's specific Dodgers score or box
> score to hand you, and I'm not going to guess a result and dress it up as
> fact. That would be worse than useless if you're about to repeat it to a
> friend. Here's what's actually true and worth knowing, regardless of which
> game you mean.

Four sentences later it gave the score.

**Nobody hears the fifth sentence.** The first ten seconds are the whole
audition, and this one spends them saying the episode cannot do the thing the
episode then does. The reported verdict is the right one: as good as it was,
it was all for nothing.

### It is not a prompt lapse, it is the order of events

The system prompt already said "never narrate your own process, sourcing or
uncertainty". The opening role brief already said "no hedging about not having
looked anything up". Both were in the prompt that wrote that paragraph.

They lost because the model was not being unreasonable. On a researched
episode the opening words are written **before the sources land**:

* `_answer_first` starts two calls at once. The cover half runs with
  `search=False`, no packet, no brief (`understand` skips `role == "opening"`
  by design), and it is the half that is *spoken first*.
* The `research_now` path attaches the `web_search` tool with no packet, and a
  model can emit text before it calls a tool.

In both, something is asked "what happened last night" while holding nothing
about last night. A lone answerer in that position should say so - it is the
honest move, and §88 and §89 are two whole sections of this log insisting on
it. **It is not a lone answerer.** The rest of the episode is already being
retrieved underneath it. Nothing had ever told it that.

So the diagnosis in the report is exactly right: it needs to know, and trust,
that the information is coming, and that its job is the runway.

### Three changes, in the order they act

**1. Tell it the shape of the system.** `ROLE_BRIEFS["opening"]` now says that
a second half of this same episode is reading sources right now, will take
over mid-flow within seconds, and will give the listener the specifics - so
write as the first minute of a piece that is about to have everything, not as
the whole of a piece that is missing something.

**2. Give it something to write instead**, which is the part a ban alone could
never supply. "Cover what this is, why it works and the history that explains
it" is fine for a heat pump and useless for `Dodgers game last night`: there is
no durable explainer under it, which is *why* it reached for a disclaimer. The
brief now names the alternative for exactly that case - put them in the
situation: who, where it sits, what was at stake going in, what the run-up
was, what a result either way would mean. All of it is true whatever the
result was, and it is precisely what the specifics need in front of them. That
is §88's "situate, never orient", applied to the half that cannot yet know.

**3. Draw the line the ban has to respect.** "Never hedge" cannot be allowed
to become "never say a game is still going" - §88 bought that rule at the cost
of a final score for a game in its third quarter. So the house rules now state
both halves in one place: **where something stands in the world is the episode
("the game is in the seventh"); where it stands in your notes never is.**

### And a guard, because a prompt rule that fails silently is not a fix

This rule *was already written* when the Dodgers episode broke it. Writing it
more forcefully is worth doing and is not worth trusting, so `OpeningGuard`
holds a disclaimer back before it can reach the voice, in `stream_sentences` -
the one path the pipeline, the cover half and `write.py` all go through.

What keeps it safe is how little it is allowed to do:

* It looks only at the **head** of a stream, and switches off for good the
  moment one real sentence gets through. A piece naming something unresolved
  in the world halfway down is untouched.
* It is bounded by sentence count as well, so it can never reach a body.
* It drops a sentence about **our own access** - and then any sentence
  immediately after it that cannot stand alone ("That would be worse than
  useless...", "Here's what's actually true...", "Because it's the stuff
  that..."). Dropping the disclaimer and leaving its justification behind is
  worse than leaving both.
* Quoted speech is stripped before matching, so a manager saying "I don't know
  yet" is reporting rather than FAM disclaiming.
* If a half turns out to be **nothing but** disclaimer, the text is released
  rather than replaced with silence. An ugly opening is recoverable; dead air
  is the one failure this product never accepts.
* It is visible: a warning per drop, the sentences on `ScriptNotes.meta_openings`,
  and `write.py` printing them under the script. The guard firing means the
  prompt did not hold, which is a thing to fix rather than a thing to absorb.

### Considered and not done: skipping the cover on a result question

The cleanest-sounding fix is to not run the from-knowledge half at all when
the question turns on an outcome - it has, by construction, nothing to say
about the only thing being asked. It is not the fix, for two reasons. The
cover only runs where research is slow (`SLOW_RESEARCH_BACKENDS`; on Exa it is
already off), so it is not what the production path does most of the time. And
it would leave the `research_now` path - which has no cover at all - opening
exactly the same way. The thing to fix was the writing before the facts, not
the second call.

### The house rules paid for it themselves

`test_the_prompt_stays_lean` caught the addition at 8,736 characters against a
7,800 bound, and its own docstring says the way past it is the dedup pass
rather than a higher number. The pass found four rules stated twice: "never
narrate your own process" and "do not announce your own currency" are both
what the new rule says; the banned-openings list was split across two bullets;
"never survey many perspectives" and "neutral survey reads as generated" are
one rule; and the episode-closes paragraph restated "Never tease" in full. The
`<<NEXT:>>` block also kept a second worked example that `build_prompt` makes
unnecessary.

One sentence of the new rule moved rather than shrank, and that was a
correctness fix as much as a size one: "other parts of this episode may be
written from sources you cannot see" is *true of the cover half and false of a
single-call episode*, so it belongs in the role brief that only the cover half
reads, not in the rules every call is sent.

Net: **7,767 characters, below the bound it was already under**, with a rule
added. Which is what the bound is for.

**Still unheard.** There is no API key in the build container, so as with every
prompt change in this log, what is verified here is that the instructions and
the guard are in the prompt and the path. Whether the new opening *sounds*
right on "Dodgers game last night" needs a key and a listen - `python write.py
"dodgers game last night" --minutes 3` prints it in seconds.

---

## 95. The interface packet: nine surfaces, and three real bugs underneath them

A packet of action items arrived covering every surface of the app — the
player, myFAM, Explore, the profile, the first run. Most of it was interface
work. Three items were not, and those are the ones worth writing down, because
each was a place where the *interface* was fine and the thing underneath it was
wrong.

### Explore was showing people their own episodes

`/api/explore` reads `cache.recent()`, and the shared script cache had no idea
who had generated anything. So the feed whose entire premise is *other
people's questions* was handing a listener back their own, and there was no
way to tell — a question you asked yesterday looks exactly like a question
somebody else asked yesterday.

The fix is one nullable column, `scripts.author`, written at the one moment the
answer is knowable: `/api/audio` knows whose tap paid for the episode, so
`_make_pipeline` takes an `author` and the pipeline stamps it on anything it
writes. `recent(exclude_author=...)` drops them from that listener's own feed
and nobody else's.

Three things are load-bearing about where it lives.

**It is not on `EpisodePlan`.** The plan is what an episode *is*, and
`pipeline.key_for` is built from it. A listener id one field away from the key
is a listener id one careless refactor away from *being* in the key — at which
point every listener has their own cache, the shared-cost design the whole app
rests on is gone, and nothing fails. `test_the_author_is_not_part_of_the_cache_key`
reads `key_for`'s own source and fails if either word appears in its body.

**It is the first writer, never the most recent.** A second listener asking the
same question is served from the entry and never rewrites it; a re-write that
extends a TTL uses `CASE WHEN scripts.author != ''` so it cannot hand
authorship to whoever happened to trigger it. Authorship means "who paid for
this", not "who asked last".

**Prefetch writes no author at all**, and that is right: a speculatively warmed
script was nobody's tap, so it belongs to everybody. Rows written before the
migration have none either, and are shown to everyone — which is exactly what
they were already doing.

### Every share ended on the clipboard

`sharing.py` has written per-destination wording since it shipped — a LinkedIn
post and a text message are not the same message, and it knew that. What
nothing had was a *destination*. `shareTo` put the text on the clipboard and
left the listener to go and find the app themselves, which is not a share
sheet; it is a note saying the share sheet was not built.

Each target now carries a URL template (`sms:&body=`, `mailto:`,
`wa.me`, the intent URLs), rendered with every value percent-encoded —
including into the `sms:` and `mailto:` bodies, because those split their
parameters on `&` and a question containing one arrived as half a sentence.

Two details are deliberate. **LinkedIn takes only the URL** and reads the page
for its own preview, so the composed words go to the clipboard alongside with
one sentence saying so, rather than into a query string that drops them in
silence. And **the two story formats still have no URL**, which is not an
omission: a story is an image handed to the platform's SDK, `needs_image` is
what says so, and the web build can only open the card.

It lives in `sharing.py` rather than in the web app for IOS_APP.md's first
rule: every feature is an API before it is a screen, and a share sheet written
twice is a share sheet that behaves differently on two clients.

### Attaching two files told you to slow down

`/api/attach` was paced with `_rate_limit`, the generation limiter. A search
can carry several attachments and the interface invites them — so the second
file was answered with *"Slow down a moment, then try again"*, from a button
that had just asked for another one.

This file already has the rule: pace what can spend a model call, and nothing
else (`/api/audio` asks the cache before pacing at all). A document or a photo
is read locally and spends nothing, so it takes the reader's limit. A **link**
is an outbound fetch of whatever address was typed — the one thing on this
endpoint somebody else pays for — and stays paced.

### The interface work, and what was removed rather than fixed

Two controls were deleted instead of repaired, on the standing rule that a
control with nothing behind it is worse than no control:

* **The Audio / Transcript toggle** in the share sheet only ever changed a word
  in a toast. With sending made real — one row pointing at a question whose
  script already exists — there is nothing behind "transcript" at all.
* **Three invented contacts** with invented replies lived in `index.html`, and
  "sent" was a toast over a push into a local variable. Messaging and the share
  sheet's people row now read `/api/friends` and `/api/messages`, which have
  existed and been tested since SHARING.md was written and were shown nowhere.
  A social surface that fabricates people is the same failure as a profile that
  fabricates numbers, and worse, because it says a message was sent when none
  was.

The rest, briefly: speed defaults to **1x** and is remembered across episodes,
with 0.5x and 0.8x added — and `fam-audio.js` now **time-stretches** rather
than resampling, so changing speed leaves the voice's pitch alone. The
accounting above the stretcher is untouched, which is the point: WSOLA advances
its read pointer by exactly `rate * sampleRate` per second of output, so
`positionSamples`, seek, the scrub bar and `TAIL_MARGIN` all keep working
without knowing it exists, and at exactly 1x it is bypassed entirely.

`ECHO` is **VIBE!** in the interface, and an alias on the server: `/api/vibe`
and `/api/echo` are one handler over one table, because a phone that has not
updated is still calling the old one and a rename that breaks it turns a copy
change into an outage. `data-echo` and the `echoed` class keep their names for
the same reason — a hook renamed for a copy change is a button that quietly
stops being found, which is the bug the attribute was introduced to fix.

myFAM's rails end in a chevron until there is nothing left to scroll, and then
in **View more**, which opens the whole of that section: the same ranking, at
full length, with the tiles whose script is already written marked *ready* and
sorted to the front. It generates nothing — the rail was showing six of
something that already had twenty-eight — and `test_opening_a_section_costs_no_model_call`
fails if that ever stops being true.

**Still unheard and unseen on a real machine.** As with every entry in this
log: there is no API key and no GPU here, so what is verified is that the
checks pass, the twenty-six smoke behaviours pass, and the surfaces photograph
correctly. Whether the pitch-preserved 1.5x *sounds* right needs a machine that
can speak.

---

## 96. The second pass: a crop that guessed, a back button that lied, and a folder nobody made

A round of notes on what §95 shipped. Most of it is wording and layout. Four
were real, and three of those had the same shape: **the interface presenting a
guess, or a fixture, as if it were the listener's own.**

### The crop was a decision made silently, on the one photo where it matters

A picture went through `avatarChosen`, got centre-cropped to a square, and was
uploaded. A centre crop is a guess about where the subject is, and the subject
of a profile picture is a person's face - which is very often not in the
middle. There was no way to say otherwise, and no sign that a choice had been
made at all.

There is a **move-and-scale step** now: drag to reposition, a slider to zoom,
a circle showing exactly what will be kept.

The load-bearing part is not the UI, it is that **the preview and the export
read the same numbers**. `base` is the scale at which the image just covers
the square, `zoom` is what the slider adds, and `ox,oy` is the top-left, always
clamped so the square is never uncovered. `paintPhoto` and `savePhotoCrop`
both compute from those three. A preview derived one way and an export derived
another is a crop that lies, and nobody finds out until afterwards - which is
precisely the failure this replaced.

Two smaller decisions:

* **The original is kept on the device**, downscaled to 1024px, in
  localStorage. The server still only ever holds the 256px crop - which is
  what everyone else sees - and the device holds what is being edited. So
  "Move and scale" works on a photo set here, and a photo set on *another*
  device offers "choose a different one" instead and says why, rather than
  showing a menu item that quietly does nothing.
* **Clamping uses `Math.min(0, minX)` on the lower bound.** An image smaller
  than the stage cannot cover it, and without that the clamp inverts and the
  picture snaps to a corner.

### Back from the shelf went to search

Opening Save for Later from the profile and pressing back landed on SearchFAM.
`openSaved` called `closeMessages()` unconditionally - and `closeMessages` is a
`goBack()`. From the profile that popped the profile off the stack *before*
pushing the shelf, so back had nothing to return to and fell through to home.

It now unwinds the Your FAM sheet only when that sheet is the active screen.
One line, and the kind of bug that only ever appears on one of the two routes
into a screen.

### "Commute" was a fixture on a real screen

The shelf had a folder chip row, and the folder in it was a demo fixture. On a
real deployment it showed a folder nobody had made; in the preview it looked
like something the listener had.

The chips are gone. A shelf of a dozen episodes does not need filing, and
Downloads is a **switch inside Save for Later** rather than a second list -
because a download is a *state* of a saved episode, and two lists would put
the same row in two places and make removing it from one of them ambiguous.
`/api/saved/folders` and `saved.py`'s filing are untouched, so nothing anybody
filed is lost; what went is the row of chips.

### Every share opened a door onto a broken link

`§95` gave each destination a hand-off URL. Testing all nine end to end - which
is what the notes asked for - found that without `PUBLIC_BASE_URL` the share
link is **relative** (`/s/abc123`), and Facebook and LinkedIn were being handed
`?u=%2Fs%2Fabc123`. The composer opens, and fails there.

A link that is not absolute now produces **no hand-off at all**, for any
target. The sheet already says the link is not public; this stops it opening a
door onto that. The clipboard still works, so a deployment being tested is not
blocked - it just never pretends. `is_public_link` is the whole check and a
test walks every target through it.

Also found by testing them: **Facebook drops the composed wording**, exactly as
LinkedIn does - its `quote` parameter has not been honoured for years. It now
gets the same treatment: the words go to the clipboard alongside, with one
sentence saying so. A test pins *which two* destinations that is, so a third
joining them is noticed rather than silently losing its wording.

### And the rest

**Trending is second, in the slot Explore New held**; Explore New is off the
page. That is the second time that rail has come off, so `topics.UNSHELVED`
names it and a test asserts the absence is deliberate - the ranker, the
endpoint and the screen all stay, which keeps putting it back a one-line
change. `FeedSource` stopped warming it in the same breath: warming a rail
nobody is shown is paying for a tile that cannot be tapped.

**The first run's catalogue is the designs' list** - seventy-three named
interests with their own icon set. The distinction that lets it exist is in
`topics.INTEREST_CATALOGUE`: these are *interests*, not tags. The eight facets
are still the only pickable tag vocabulary and the chips above the catalogue
are still exactly `TAG_LABELS`; "Formula 1" is not something those eight can
say, and it reaches the ranker as `sports` plus whatever subtag it really
carries, without a listener ever reading a tag name. One entry is flagged in
the source rather than quietly kept: `Iran Conflict` is a live news event and
will go stale, and it is in the designs.

The download popup says **"Saved. Keep it on this phone?"** - the save already
happened, on the tap that opened it, and "Download?" left that ambiguous enough
that both buttons had to carry the word "save" to make up for it. It has an X,
because both buttons were commitments and the only way out was a backdrop
nothing said was tappable. Explore's actions are centred over its transport.
The 15-second arrows have solid heads, because at 24px a hairline chevron beside
a hairline arc read as a stray tick rather than as one arrow.

### The live preview keeps its own copy of the page, and it disagreed

Moving the rails passed every test and every fixture-preview behaviour, then
failed the *live* preview's smoke run: `expected 4 sections, saw 5`.
`build_live_preview.py` reimplements the feed in JavaScript, because the
published page has no server - so `topics.SECTIONS` exists twice, and only one
of them had been edited.

That is the same failure `pipeline.key_for` is written the way it is to avoid,
and it is worth naming because the preview is the thing the phone actually
opens: a second implementation drifts the first time one side gains a field.
The smoke run is what catches it, which is the argument for those behaviours
being *behaviours* rather than assertions about markup. The JS list now carries
a comment pointing at `topics.SECTIONS`, and `might_like` is still *filled*
there even though nothing draws it - it takes tiles out of the bank in
`FILL_ORDER` position, so deleting the fill would quietly change what the other
three rails contain.

**And "DailyFAM folders" is now "DailyFAM mixes".** The note asked what a
folder was; the shelves were the answer, but the profile was calling public
mixes folders too, which is the same word for a third thing.

**Unheard and unseen on a real machine, as always**: no API key and no GPU
here, so this is checks, smoke behaviours and photographs.

## 97. Four players, four ideas of whether it was playing

The third pass. Four notes, and the one underneath two of them is the same
bug: **the same episode had four transports and no shared state.**

### The mini bar's button was dead exactly when it was the only one on screen

"On mini player, be able to pause/play with the button there." It had a
button, wired to `toggleNowBar`, which opened with:

    if(!FamAudio.isActive()) return;

`active` goes false when the stream finishes - and a finished episode is the
state that bar is in most often, sitting above the tabs after the listener has
wandered off to another tab. So the one control still on screen did nothing at
all, silently.

Behind it was the larger version: the search player, DailyFAM's play-all,
Explore's reel and the mini bar each kept **their own boolean** (`isPlaying`,
`paIsPlaying`, and two derived from `FamAudio.isPaused()`) and each redrew
**only its own icon** over one shared `FamAudio`. Pause on the reel, open the
player, and the player showed a pause button over stopped audio.

`setPlayState` is now the only thing that moves the audio. It sets the state,
redraws all four, and every other entry point delegates to it - `paTogglePlay`,
`reelTogglePlay` (after its one genuinely different case: nothing loaded yet,
where the button *starts* the card rather than resuming it) and
`toggleNowBar`. A smoke behaviour pauses through one and reads all four.

The rule this is a case of, and it is the same one `pipeline.key_for` is
written for: **one fact, one place.** Four copies of "is it playing" is four
copies that agree until they don't, and nothing fails when they stop.

### VIBE comes off the mini bar, reversing an earlier rule

§95 put VIBE! on *every* player, the mini bar included, and the smoke check
listed `nowBar` by name so nobody could quietly drop it. The note reverses
that, and the reason holds up: the bar is a strip with three things competing
for one thumb - open, pause, close - and the only irreversible one of them was
the one that posts to your friends. It is still on all three real players,
where there is room to see what is being vibed before vibing it.

The check now asserts the *absence*, so this is a decision rather than a
regression waiting to be "fixed".

### The arrowheads were on the top of the ring; they belong on the side

Drawn at the top, over the "15", a hairline head reads as a stray tick - which
is what §96 had already tried to fix by making it solid. The note says plainly
where it goes: the ring opens **at the side**, and the head sits in the gap.
Both arcs are now ~280° with the gap centred on 9 and 3 o'clock.

One CSS trap on the way: `.skip-num` centres itself with `left/top:50%` plus a
`translate(-50%,-50%)`, and Explore's more specific rule replaces the offsets
with `inset:0` and centres by flex. It had been overriding the transform with
a `translateY(6%)` nudge, which existed only because the old gap was at the
top. Removing the nudge left the translate in force against a full-size box,
and the number walked out of the circle. `transform:none` is the fix, and the
lesson is that `inset` and `transform` are two centring mechanisms and using
one does not switch the other off.

### The play triangle was centred by its bounding box

"Increase arrow size and pause size - also center it on the circle." A play
arrow centred by its **bounding box** reads as sitting to the right, because
the eye weights the mass and the mass is on the flat side. It is positioned by
its **centroid** now, a shade left of the geometric middle, and both glyphs
are larger. The same triangle is used everywhere, including the small tile
glyphs, because two shapes for one idea is how the transports drifted in the
first place.

### The settings icon was a sun

Spokes around a circle is brightness on every phone this will run on. It is a
gear now. The first hand-drawn gear path was not symmetric about (12,12) and
the render showed it: the hub sat low and right of the teeth. Replaced with a
known-good one.

### Closing the topic catalogue ended the first run

"When setting up topics and clicked X, it should go back to topics setup page.
Right now it goes to SearchFAM, skipping the language selection step."

`closeTopicCatalog` called `goBack()`, which pops `stack`. But **the intro is
drawn with `showScreen` and never joins the stack** - `afterAccount` and both
settings entry points call `showScreen("intro")` directly. So from the intro,
`navigate("catalog")` pushed onto a stack whose top was still `home`, and
popping landed on SearchFAM with the language page never shown.

It returns by *name* now, recorded at open time, which also answers the other
route in (Settings → More topics). And not via `goBack`, which stops the
audio: closing a list of topics is not a reason to end an episode. The smoke
behaviour follows the close with `introNext()` and asserts the language page
is reachable, because the symptom the note describes is a missing *step*
rather than a wrong screen.

**Unheard and unseen on a real machine, as always**: no API key and no GPU
here, so this is checks, smoke behaviours and photographs - and this round the
photographs earned their place twice, once for the escaped "15" and once for
the lopsided gear.

## 98. A settings row that ran the first run again

Three notes. Two are the interests page; one is a routing bug with a shape
this log has now seen three times.

### The picker shows six, and they are the six being played

"Remove the paragraph after 'Your interests'. Then pick the six most popular
topics and display them as shown."

The paragraph is gone. It explained the cap and said interests stop mattering
once somebody has listened to something - both true, both read at the one
moment neither is useful yet, above a grid with nothing chosen in it.

The grid is six, three across and two down, and the six are **the most played
facets across FAM** (`topics.popular_facets`) rather than the first six of a
`dict`. Global, like `rank_most_played` and for the same reason: this is asked
on the first run, when this listener has no history and the only honest signal
is everybody else's. One count serves every listener.

Two things that had to be got right rather than assumed:

* **`PICKER_DEFAULT_ORDER`, and `interests_source` saying which is in use.** A
  fresh deployment has an empty log, which is the normal state on the screen
  this exists for - so there is a declared order, written down in one place,
  and the response says `"default"` rather than `"played"`. A declared order
  and a measurement look identical on screen, and calling the first one "most
  popular" would be inventing a number.
* **The picker narrowed; the vocabulary did not.** CLAUDE.md's constraint is
  that the eight facets are the only *pickable* vocabulary, and six of eight
  does not widen anything - but two facets would have become unreachable if
  the catalogue did not carry every one of them on its interests, which it
  does. `interests_all` is served beside `interests_available` because
  Settings has to be able to *read back* a stored interest that did not make
  this week's grid.

One honest consequence, recorded rather than papered over: `MAX_INTERESTS` is
six and the picker is now six, so the cap can no longer be shown as a dimmed
seventh chip. It is still enforced and still said out loud in the count line;
the smoke behaviour asserts that sentence instead of the dimming.

"View more" is a plain centred underlined link. It was a dashed pill, which
put a seventh chip-shaped thing under six chips and made the grid read as
uneven.

### Clicking a settings row ran the first run again

"When you click on one of those Settings tabs and then hit save, I want it to
go back to the Settings page. Right now when you click on some of them, it
reroutes it to perform like the initial setup."

Exactly right, and literally so. Interests and Language have no editor of
their own - they reuse the intro screen - and reusing the *screen* meant
reusing the *flow*:

    openInterestsFromSettings() -> showScreen("intro")
    Next -> introNext()   -> the language page
    Start listening -> finishIntro() -> intro: "done" -> finishEntry() -> myFAM

So editing one setting walked the listener through the other one and then put
them on myFAM. There was no way back and no X.

`introMode` is `"first-run"` or `"settings"`, and it decides three things:
whether there is an X, what the docked button says (Next / Start listening
versus **Save**), and where saving goes. `saveIntroFromSettings` writes the
preference and returns; it is deliberately not `finishIntro`, which is the
first run *ending* and has no business running when somebody changed their
language.

This is **the third time** the same trap has been paid for (§96 the shelf,
§97 the catalogue, now this): **the intro is drawn with `showScreen` and never
joins the navigation stack**, so `goBack()` from anything opened on top of it
pops to whatever was underneath. Every one of these returns by *name* now. If
a fourth screen is ever shown that way, this is the paragraph to read first.

### And an X on everything a settings row opens

The note asks for one on "every single one". The intro gets `.sheet-close`,
the same X as the catalogue and the sources panel; the shared modal (Name and
handle, Change password) and the photo editor get `.modal-x`, which is the
`.dl-x` rule from §96 generalised rather than a second X drawn a pixel
differently. The action sheets keep their Cancel row, which is already an
explicit way out.

Cancel was already on both modals, and an X is still worth having: it is
where a thumb goes to leave something, and the only other way out was a
backdrop tap nothing documents.

**Unheard and unseen on a real machine, as always**: no API key and no GPU
here, so this is checks, smoke behaviours and photographs. The photographs
earned their place again - the first grid had three unequal columns and two
unequal rows, because `1fr` is `minmax(auto, 1fr)` and a long label grows its
column past the equal share. `minmax(0, 1fr)` and `grid-auto-rows: 1fr` are
what make six pills actually six of the same pill.

## 99. The interests wheel, and a cap that was never buying anything

Two things: a first run that turns, and a number that goes away.

### Six discs orbiting a hub

The design is a wheel - six circles going slowly counter-clockwise around
"View more", which sits still in the middle. They are still selectable while
they move, which is the whole trick.

**How the turning works, because it is the part that breaks if it is touched
carelessly.** The ring rotates; each disc counter-rotates by exactly the same
amount over exactly the same duration, so positions orbit while labels stay
upright. Each disc's *placement* is one static transform - centre on the hub,
swing out to the radius, turn back upright - set once when the wheel is drawn
and never again.

Both halves are CSS animations on `transform`, and that is a decision rather
than a default:

* they run on the compositor, so the whole first run costs nothing per frame;
* they stay in lockstep with no code, because they are the same duration and
  the same easing started at the same moment - two `requestAnimationFrame`
  loops would drift, and one loop driving both would stop dead every time the
  main thread went off to fetch something;
* and redrawing a disc on every tap is therefore safe. The rotation lives on
  the ring, which is untouched, so a replaced disc reappears exactly where its
  slot already was.

**`prefers-reduced-motion` stops it.** The wheel stays a wheel - the discs keep
their positions - it simply stops turning. Continuous motion on a *selection*
control is the case that rule exists for.

Three things this got wrong first and a screenshot caught:

* **`vw` is the window, not the app.** The first version sized the radius with
  `clamp(86px, 29vw, 112px)` and overflowed the moment the app was not the
  whole window - which is exactly what the phone preview is, and what a
  desktop browser is. It is `cqw` now, a percentage of the wheel's own
  container, so it is correct at every width the app is ever drawn at.
* **The ring was swallowing taps meant for the hub.** It is `inset:0`, so its
  empty middle sits on top of "View more". `pointer-events:none` on the ring,
  put back on the discs.
* **"Money & markets" does not fit in a 78px circle.** `topics.TAG_SHORT` is
  the eight facets short enough to sit inside one - and every entry is a
  *prefix* of its `TAG_LABELS` value, so this is a shortening rather than a
  second name for the same thing. Settings, the recap and the catalogue all
  still read the full label.

**The smoke behaviour asks `elementFromPoint`, not `page.click`.** Playwright
waits for an element to stop moving before it will click one, and this one
never stops. That is a fact about the harness rather than about the interface;
asking the browser what is under the disc's centre answers the real question,
which is whether a thumb landing there hits it. It checks the same thing again
four seconds later, after everything has moved.

### The six-interest cap is gone, and is not replaced

"Remove the whole six topic limit - don't acknowledge that at all."

It is out of `preferences.clean_interests`, out of `/api/preferences`, out of
the interface, out of the preview's shim, and there is no counter on the page
saying how many have been chosen. The number was never doing anything a
listener wanted: it made somebody with seven interests pick which one to lie
about, and the ranker is perfectly happy to weigh eight.

**No cap is not no validation**, and the distinction is worth keeping straight
because unbounded input reaching a store is how this kind of removal usually
goes wrong. Every value still has to be one of the eight facets and duplicates
still collapse, so eight is the most that can ever be stored - a fact about
the vocabulary rather than a rule anybody is told about. A test says so.

The smoke behaviour now asserts the *absence*: no counter element, and the
words "limit", "up to six" and "at most" do not appear on the page. A cap is
the kind of thing that grows back as a helpful sentence.

**Unheard and unseen on a real machine, as always**: no API key and no GPU
here, so this is checks, smoke behaviours and photographs - and the wheel is
the first thing in this log that a photograph genuinely cannot check, which is
why the behaviour above measures it instead.

## 100. The wheel tilted when you tapped it, and the language page goes

Three things, and the first is a bug I wrote a comment claiming was impossible
one section ago.

### Tapping a disc tilted every label

§99 said, in the source:

> Redrawing on every tap is safe [...] the rotation lives in a CSS animation on
> the ring, which is untouched, so a chip that is replaced mid-revolution
> reappears exactly where its slot already was.

The *position* claim was right and the *orientation* claim was wrong, and they
are not the same claim. The ring keeps its animation when its children are
replaced - but a **new element's animation starts at zero**. The two
animations only cancel while they are at the same point in their cycle, so a
disc drawn eight seconds into a sixty-second revolution counter-rotated from
the wrong place and sat at 48 degrees for the rest of the turn. Every tap
rebuilt all six, so one tap tilted all six.

Two fixes, because they cover different halves:

* **`toggleInterest` no longer rebuilds.** Selecting is a class on a button,
  not a reason to redraw the other five. This is the tap path and the reported
  symptom.
* **`syncWheelPhase` re-phases any rebuild that does happen**, by reading the
  ring's `Animation.currentTime` and writing it onto each disc's. That covers
  every *other* way the wheel can be redrawn - opening it from Settings, the
  preferences fetch landing late - which the first fix alone would not.

Where `getAnimations` is missing the wheel simply turns un-rephased, which is
the behaviour that shipped in §99 rather than a broken one.

**The lesson is about the comment, not the code.** "Nothing here runs per
frame" was true and I let it stand in for "nothing here can desynchronise",
which is a different sentence. The smoke behaviour now taps a disc four
seconds in and reads every label's net angle, so the claim is measured rather
than asserted.

### The language page is gone

It was the second page of the first run, and it was never wired to anything -
every episode is written and spoken in English whatever is chosen, which is
why the only other thing on that screen was a note saying so. A question in
front of the product that answers to nothing is worse than no question, and
this one was between a new listener and their first episode.

Out: the page, its dock, the Settings row, `openLanguageFromSettings`,
`renderLanguageList`, `chooseLanguage`, `introLanguage`, `introNext`, the
`.intro-lang` styling and the "stored and not yet acted on" notes in both
places. Interests is now the last step, so its one button says **Start
listening** and finishes the run.

**The stored field stays**, and the distinction is the point: `LANGUAGES` is
still the vocabulary `clean_language` validates against, the field is still
accepted on `/api/preferences`, and nothing anybody saved is dropped. Deleting
a column is a migration with a real cost and no benefit, and this is what
per-language generation reads on the day it exists. What went is the screen.

### Settings' wheel is the listener's own, and keeps changing

"The page for Your interests in the settings page should be different than the
one they see when they are first setting up their account."

Two wheels, two questions, and `topics.my_facets` is the second one. The first
run asks somebody with **no history** what they like, so the only honest
answer is what everybody plays (`popular_facets`, §98). Settings is opened by
somebody who **has been using the app**, where their own listening is the
better answer and keeps changing - which is what makes that wheel worth
opening twice.

Three sources, in order, and the order is the design: what they played, then
what they chose, then `PICKER_DEFAULT_ORDER` as filler. The filler is not
decoration - **a wheel is six discs or it is a broken wheel** - and
`interests_yours_source` says which of the three actually decided it, for the
same reason `interests_source` does: a declared order and a measurement look
identical on a screen full of circles.

One line of copy, only in Settings: *"What you listen to most, kept up to date
as you listen."* The first run's page has no such line and does not need one,
but a wheel whose contents change on their own without a word reads as the app
having lost somebody's answer.

**Unheard and unseen on a real machine, as always**: no API key and no GPU
here, so this is checks, smoke behaviours and photographs.

One harness note worth keeping, because it cost a cycle: the first-run flow
can be walked **once** per smoke session - a second `startEntry()` lands on
myFAM - and the catalogue behaviour already spends it. The new two-wheel check
therefore drives `renderIntro` in each mode rather than restarting the run,
which is also the more direct question: that function is the thing that
decides which list is drawn.

## 101. Getting the branch ready to merge

Not a bug report - an audit before handing six commits to `Main`. Recorded
because two of the four findings were things a merge would have shipped
quietly, and both are the same shape: **a check that exists is not a check
that runs.**

### The branch itself

Six commits, `origin/Main` is an ancestor, so it fast-forwards: no conflicts
are possible and no merge commit is needed. Working tree clean, no untracked
files, nothing secret-shaped in the diff, no `console.log`, no `pdb`, no
hardcoded hosts, no model identifiers in anything pushed.

### The two schema migrations were proved rather than read

The branch adds `scripts.author` and `people.avatar`. Both follow the existing
idempotent `ALTER TABLE ... ADD COLUMN ... DEFAULT` pattern, which is easy to
*read* as correct and is exactly the sort of thing that is wrong in
production. So each was run against a database built by the **pre-branch
code**: the old module writes rows, the new module opens the same file.

Both widen in place. A script written before the migration still serves, and
still appears in everybody's Explore because it has no author - which is what
it was already doing. The follow graph survives the `people` widening. This is
"verify, do not inspect" applied to the one part of a merge that cannot be
undone by reverting a commit.

### CI was setting up node and never using it

`dev.sh` gained `tools/check_stretch.js` in §95 - the check that says changing
speed still leaves the pitch alone, which guards a settled constraint. CI
installs node 20 and then never runs it, so that guard was enforced only on
machines that happened to have node locally. Added to `.github/workflows/ci.yml`.

The general form, and it is worth keeping: **a check added to the local loop
is not added to the gate.** `dev.sh` and `ci.yml` are two lists of the same
intentions and nothing keeps them in step.

### A count written in prose does not fail

CLAUDE.md told a new session that a complete run ends with "twenty-six named
smoke behaviours; anything less means something was skipped". There are
thirty-four. The number was right when it was written and no longer is,
because a sentence cannot fail when somebody adds a behaviour - and it is
load-bearing advice, since it is what a fresh session compares against to
decide whether its baseline is honest. Corrected, and it now says where to get
the number (`grep -c '^        check(' tools/smoke_preview.py`) rather than
asking anybody to trust the prose.

### Known and deliberately not fixed here

**The preview build is not reproducible.** `preview/build_preview.py` bakes
`time.time()` into the profile fixture's `since` and `joined`, so a rebuild
with no source change still rewrites `preview/fam-live.html`. That makes the
committed build artifact impossible to verify against its source, and it
pre-dates this branch (it is the same on `Main`). Left alone rather than
widened into an interface branch; the fix is to freeze those two fields to a
fixed timestamp.


## 102. myFAM had four rankings of a bank that never changes

The browse page was a shared bank of twenty-eight evergreen topics, ranked four
ways. Every ranking was real and none of them could answer the thing a browse
page is actually asked: **what should I hear about today.** A bank that never
changes says the same thing on the morning a war starts as it did the morning
before, and no amount of re-sorting it fixes that - which is why this was a
missing inventory rather than a tuning problem.

The packet that prompted the work asked for four rails, live data behind the
two that face outward, variety, a judgement about how hard and how long to push
a story, zero queue on page load, and the cheapest possible way to do all of
it. Those turn out to be one design rather than six requirements.

### A tile is a title and an angle; the script is written on the tap

This is the whole cost argument and everything else follows from it. Writing an
episode costs a model call and several seconds; deciding one is worth writing
costs almost nothing. So myFAM decides - **one small model call per refresh
window, shared by every listener** - and the script happens when somebody taps,
or is replayed from the shared cache when somebody else already did.

Offering forty tiles therefore costs one call and not forty. `stories.py` holds
the pool; `story_sources.py` holds the providers; `MYFAM.md` is the whole of it.

Only the stories that are **new since the last window** are composed.
Everything already in the pool keeps the title and angle it was written with,
so a steady state composes two or three tiles per window rather than twenty. At
the shipped fifteen-minute window that is under a hundred small calls a day for
an entire deployment.

### The four sources, and what each may say

GDELT (keyless) measures how much the world's press is writing about a theme,
then reads the leading headlines under the hot ones. Finnhub quotes a named
watchlist and reports what actually moved. Polymarket returns the most-traded
open markets. API-Sports returns today's card. The `trending` registry became a
fifth source rather than being deprecated, so `TRENDING_SOURCE=fake` still
works and a configured feed still produces the row it always did - it now
carries its own question and reason through as `suggested_query` and
`suggested_angle`, because putting a layer in front of a configured source must
not make that source's output worse.

**Every one of them reports a measurement and never a result.** That is
§88 moved onto the surface more people see: a tile is written before anything
is researched, so a result on one is a claim nobody checked. The case that
makes the rule concrete is API-Sports, which *knows* the score and deliberately
does not pass it on - the tile says "what decided it" and the episode finds
out, from dated evidence, on the path built for that.

Three things back it up, in order of how much they are trusted: the composer's
system prompt says it has researched nothing and must write a tile that is true
whichever way the thing turns out; `_RESULT_WORDS` catches the case where the
prompt did not hold and sends that one tile back to its template **with a log
line**, because a guard that fires quietly is a prompt nobody fixes; and the
templates cannot say a result either, so the rule survives a total outage.

### How hard to push, and for how long, is now written down

The packet asked for judgement about this. Judgement left implicit in a sort
order is judgement nobody can argue with, so:

* **How hard**: `DOMAIN_WEIGHT` times the provider's own `strength`, halving
  every half-shelf-life. Loudest the hour it appears.
* **How long**: `DOMAIN_SHELF_LIFE` - eight hours for a fixture, eighteen for
  a market move, twenty for a wave of coverage, four days for an election
  market - as a **hard expiry** rather than a fade to nothing.
* **And then it stops**: `SUBJECT_COOLDOWN` keeps the subject out for 36 hours
  afterwards, however hot its signal still reads.

The load-bearing line is that `first_seen` is the clock and `last_seen` is not:
**a story that keeps being reported does not get to be new again.** Without it,
a subject the world talks about all week is the top tile all week - which is
the "shown the same thing forever" failure that `TrendingItem.id` was hashed
from the subject to avoid, arriving from the other direction.

### Variety is a cap on what is available, never a quota on what is not

Enforced twice - five per facet in a pool of twenty-four, two per facet in a
rail of six - so a busy Sunday in sport cannot take over the page before a
ranker sees it.

The part worth writing down is what happens when the cap has nothing to reach
for. A listener whose entire history is sport has nothing else with any
affinity, so `diversify` tops the rail up rather than returning a short one: a
two-tile rail reads as broken where a samey six-tile one reads as a taste.
There is a test pinning that trade, because it is the sort of thing that gets
"fixed" into an empty rail by somebody reading the cap as a rule.

### Zero queue is structural, not fast

`/api/myfam` schedules a refresh when the pool is stale and renders from
whatever the pool holds. Nothing on the page-load path awaits anything, so
there is no path from opening the page to generating anything - which is a
stronger claim than "it is quick" and is checkable. A test reads `build_feed`'s
own source and fails on `await`, `pipeline`, `generate` or `refresh(`.

A warming sweep runs at startup so the first listener usually does not see a
cold pool, and it is scheduled rather than awaited: a browse page that waited
on a news sweep to boot would be a server that fails to start when somebody
else's API is slow.

### Two rails were quietly dishonest and one of them has been for a while

**"Your circle is on this"** ranked co-listener overlap - people who played
what you played also played this - and the card under it said "People you
follow played this" about strangers. `topics.py`'s own docstring admitted it.
The follow graph has existed since SHARING.md, so the rail now reads it
(`social.circle_of`: friends first, then everyone else they follow) and is
named for what it does. An empty circle returns nothing, deliberately: a rail
called "what your friends are listening to" that quietly showed strangers would
be the original problem again with better wording. The empty state names the
two taps that fix it.

The overlap ranking was not deleted - it is a good standard signal - it just
has no rail. It tops up the post-episode popup, where the question is "what
next" and nothing claims those people are anybody's friends.

**"What FAM can't stop playing"** ranked the bank by play count and could not
show a live story somebody had tapped fifty times, because the row was a
ranking of the evergreen bank wearing a heading about the whole app. It now
ranks anything with plays, and leads with whatever is already written - a
written tile starts instantly and costs nothing to serve. Preferring cached is
a **sort and never a filter**: a deployment whose cache has just expired would
otherwise show an empty row, which is a fact about the cache told as a fact
about what people are playing.

### What is not connected, and why saying so is the point

**Nothing live is configured by default.** Every source is registered, each
reports exactly what it needs, `/api/health` distinguishes ready from not
configured, and `python tools/stories_report.py` makes a real sweep rather than
confirming a credential exists. `GDELT=1` is the one line that makes the
attention half live, because GDELT needs no credential.

Polymarket is keyless and therefore had to be given a switch of its own, off:
without one it would turn itself on, and on a fresh deployment it would be the
*only* live source, which would make the browse page a betting slip.

None of the four has made a real request from the build container - its egress
proxy blocks all four hosts - so every shape is written from the documented API
and tested against recorded payloads. **Do not say FAM's browse page is live
until `tools/stories_report.py` has printed real tiles on a real machine.**

### One thing found on the way that was nobody's feature

A source with a free tier measured in requests *per day* cannot be swept on a
fifteen-minute clock: API-Sports allows a hundred, and the pool would have
spent the lot on a page nobody opened. So a source declares
`min_interval_seconds` and a sweep it is not due for reports `skipped` - a
sixth outcome, and the only one that is not a fault. Reporting it as `empty`
would have made a working source look broken, which is the same "three
sentences collapsed into one" mistake the outcome vocabulary exists to prevent.

## 103. The pre-merge review of §102, and a clock that reset itself

An audit before handing §102 to `Main`, in the shape §101 set. The branch is
one commit, `origin/Main` is an ancestor, so it fast-forwards and no merge
commit is needed. Working tree clean, nothing secret-shaped in the diff, no
`console.log`, no `pdb`, **no schema change at all** - and the new code was
still run against a database written by the pre-branch code, because "there is
no migration" is exactly the sort of claim that is true right up until it is
not. Old rows read, ranked and served.

Seven findings. Four were real bugs, and the first one would have quietly
undone the feature it was part of.

### A story evicted by the variety cap came back as a brand new one

`_diversified` ran when the pool was **written**, so a story it passed over was
discarded. The next sweep saw that subject again, found no record of it, and
admitted it as new: `first_seen = now`. Which means it never aged, never
expired, never reached `SUBJECT_COOLDOWN`, and was **composed and paid for
again every window**.

Reproduced: five sports tiles admitted, three evicted; six hours later all
three were back reporting an age of 0.0 hours. So the subjects most likely to
be evicted - the ones from a busy facet - were exactly the ones that would
have been shown forever, which is the failure `DOMAIN_SHELF_LIFE` and the
cooldown exist to prevent, arriving through the one door the push model was
not watching.

Two changes, because one of them alone would leave the other hole open:

* **The pool keeps more than it offers.** `POOL_STORE` holds everything
  unexpired; `Pool.live` applies the variety cap on the way *out*. An
  over-served story is now hidden rather than forgotten.
* **`_FIRST_SEEN` remembers the clock regardless of pool membership**, so even
  a story that falls out of the store entirely comes back with the age it had.
  `_admissible` knows three states - never seen, seen and still inside its
  shelf life, seen and past it - and only the first may start a clock.

The general form, and it is the one worth keeping: **an eviction that forgets
is a creation.** Anything with a lifecycle needs its clock kept somewhere that
outlives its membership of the thing that displays it.

### The composition budget counted the trolley and not the shelf

`_worth_composing` capped per facet from an **empty** counter, so a window
whose pool already held five sports tiles would happily buy five more - and
`Pool.live` then showed the same five as yesterday. Money spent on tiles that
could not be reached. Seeded from what is already held.

### The outcome guard watched the two fields that are read, not the one that is used

`_RESULT_WORDS` checked `title` and `angle` and not `query`. The title is what
a listener reads; **the query is what the pipeline researches from**, so a
result asserted there is one the episode inherits and then elaborates. It was
the field the guard could least afford to miss and the only one it did.

### An empty Trending rail blamed the sources for our own ordering

Made for you fills first, so on a thin day it can take the whole pool and leave
Trending empty - and the rail reported `stories.Pool.empty_reason`, which says
things like "the live sources had nothing new this time". That is our own
page's arrangement stated as a fact about the world: §89 with a new way in.
`_world_empty_reason` now distinguishes the two, and says where the stories
went.

### Three smaller ones

**A provider that raises in `diagnose()` stopped the pool forever.** `diagnose`
ran outside the per-source `try`, and the refresh is scheduled and never
awaited, so the exception became one log line and the pool never refreshed
again with the page still looking normal. Guarded, and the endpoint's
`create_task` now goes through the same wrapper the boot sweep uses.

**API-Sports ordering was the provider's.** Three strength values over a whole
day's card means most fixtures tie, and a stable sort hands a tie to whatever
the API listed first - so the tiles changed between sweeps for no visible
reason. Tie broken on the subject. The *bigger* problem underneath it is named
rather than fixed: there is **no league filter**, so a day's card is every
league on earth. The parameter wants a real key in front of the real API,
because a wrong one does not fail - it returns somebody else's fixtures.

**`tools/prefetch_report.py` under-reported.** It never got the `social_store`
the feed source now needs, so its friends rail was always empty and the report
claimed the server would warm less than it would.

### And the check that was not failing

`dev.sh check` printed `1 check(s) failed` and **exited 0**, because the smoke
tests were run with `|| echo` and the mode exited zero unconditionally. It hid
a real failure during §102 itself - the run said nothing was wrong and the
shell agreed. The exit code carries the result now.

Same finding one layer out, which is §101's exactly: **a check added to the
local loop is not added to the gate.** `dev.sh` has built and smoke-tested
*both* previews for a while; CI built and smoke-tested only the fixture one -
and the live build is the one that gets published, and carries its own copy of
`topics.SECTIONS`. Both are in `ci.yml` now.

## 104. Twenty-two items off a review of the running app

A list from the owner after using the build, covering everything from a
one-word plural to two features that were drawn and had never worked. It is
recorded here in the order the causes fall into rather than the order the list
was written, because several of the items turned out to be the same mistake.

### Two features were drawn, wired to nothing, and looked finished

**Live captions showed a sentence about captions.** The panel rendered
`t.caption` — a line of prototype copy ending in an em dash, written when the
whole app was a clickable mock. Nothing was ever connected to the episode, so
turning captions on produced *"Live captions will appear here as this
plays—"*, indefinitely.

`/api/transcript` is `/api/sources`' sibling: `pipeline.script_for` reads the
sentences from the cache under the key the script is already stored under. The
rule that makes it affordable is that **it never generates** — captions that
could trigger a write would be a second full Claude call for every episode
somebody chose to read along with, which is the expensive half of an episode
paid twice for one listen. So there are three honest states and the panel says
which: sentences, still catching up, or none at all for an attachment episode,
which is deliberately uncacheable.

Which sentence is highlighted is **estimated from character count, not
measured**, and that is worth naming rather than hiding: the audio is one PCM
stream with no sentence marks in it, so there is nothing to measure against.
The denominator is the *planned* length rather than `FamAudio.duration()`,
which grows as the stream arrives and would pin the highlight near the end for
the whole episode.

**The sources strip was invisible, and one missing call was why.**
`fetchEpisodeSources` ran in `onEnd` — when the episode *finished* — and when
Go Deeper opened, and nowhere else. The one moment the panel is for, somebody
listening and wondering where this came from, was the one moment nothing asked
for it. It is fetched on the same clock as `/api/next` now, twice, because
provenance lands with the finished script and a 10-minute script takes longer
to write than a 2-minute one.

Two smaller faults went with it. An answer that comes back empty no longer
clears a strip that is already showing — the first try often lands before the
script has finished, and a panel that disappeared halfway through an episode is
worse than one that arrived late. And `toggleCC` called `clearSources()`
**twice**, one of the calls mis-indented under the line above it, so switching
captions off wiped a different control on a different row about a different
thing.

Both had a third cause in common: **neither preview had a fixture for either
endpoint**, so a preview with no sources and no transcript previewed neither.
Anybody reviewing the app through the published link could not have seen either
feature working even if the code had been right.

### Three controls said something that was not true

**"Suggested topics"** over the bank in `topic.id` order — alphabetical by
slug. `/api/topics?ranked=1` orders it with `topics.rank_bank`, the same taste
model every personal rail uses. Two rules it does *not* share with a rail: it
is a **sort and never a filter** (a rail is one of five and somebody who
dislikes its picks can scroll on; the picker *is* the list), and it **does not
exclude what they have played** — wanting a mix of subjects you already like is
the entire point of a mix. The heading reads `personalised` off the response
rather than asserting, because a declared order and a measurement look
identical on screen.

**The privacy switch meant public**, so the lit position was the one where
other people could see your mix — read backwards by everybody who has ever
used a phone. It means private now, with a closed padlock on the knob.

**The copy button on the captions panel** toasted "Transcript copied ✓" and
copied nothing. The control-with-nothing-behind-it failure in its worst form,
because it said the thing had happened.

### One bug with four symptoms, and one shared container

The reported navigation bug: Friends → a friend → back → back should reach
your own profile, and instead went to the friend again, then to searchFAM, and
then the Profile tab flashed the friend for a moment.

Four symptoms, one cause: **somebody else's profile was drawn into
`screen-profile`** with a variable deciding whose it was. So back from the
friend popped to Friends; back again showed `screen-profile` still holding the
*friend's* DOM; back again fell through to search; and the tab flashed them
before `loadProfile` replaced it.

Two pages sharing one container is one bug, not a routing bug with four fixes.
`screen-person` is its own screen, and `viewingProfile` now decides only what
is drawn there and never which screen is on.

### Things the strongest signal was being thrown away by

**Eighty-five per cent through is finished.** The completion event fired on the
last sample only. The ending is the one part a listener skips, so an episode
heard to the ninety-fifth percentile and then closed was recorded as a *play* —
worth 1.0 against a completion's 2.5. The strongest signal the taste model has
was being discarded by exactly the behaviour it should reward, and the
profile's "episodes finished" undercounted for the same reason. A share rather
than a number of seconds, because "the last thirty seconds" is most of a
one-minute episode and nothing of a ten-minute one.

### A tap that cost a model call

`.mini-stage` is `flex:1`, so it is most of the player, and it carried a tap
handler: with an album it jumped to the next episode, and **with no album it
generated a random myFAM topic**. An episode nobody asked for, costing a Claude
call and a GPU, in place of whatever was playing. Removed with nothing in its
place — the swipe-up gesture still moves through an album and is the one the
`next-hint` label actually advertises.

### The episode title was the question

Somebody who asked `what happened with the fed yesterday` got an episode called
*What Happened With The Fed Yesterday*: their own words handed back with
capital letters.

The model writes the title itself, on a `<<TITLE: ...>>` line beside the
`<<NEXT:>>` one, stripped before synthesis and never spoken. That is what makes
it affordable — naming an episode with a model call of its own would be the
expensive half of an episode spent on a label. It is cached in its own column
beside the script for the same reason `thread` is, and a re-write keeps the
title it has.

Getting it into the system prompt meant **fitting inside the lean-prompt
test**, which is the right way round: three rules each said the same thing
twice ("never tease" listed four ways of teasing, "never summarise" five
phrases, and the never-say-what-you-do-not-have rule restated its own line).
Deduplicating them paid for the addition. No rule changed.

### "Iansolomon" was a placeholder, and the setup had no picture

The reported "recommended name" was two things at once. One was genuine browser
autofill: **one shared `<input>` serves every modal in the app**, so a handle
typed into it once came back as a suggestion over the field for somebody's
name. The other was the app's own doing — `placeholder: "e.g. Ian Solomon"` and
`placeholder: "iansolomon"`, a real-looking name and handle offered to every
listener.

Every text field says `autocomplete="off"` now, with one exception: the login
screen's address, which may still be remembered on a device that account has
signed in on. And the two chained modals that asked for a name and then a
handle — with no way back between them and no picture at all — are one screen,
used both as a first-run step and as the Edit profile editor.

The picture needed one fix to work *during* setup: `saveAvatar` read the name
and handle out of storage, and nothing is stored yet at that point, so it
refused the photo and sent somebody back to the screen they were already on.
It reads the fields when that screen is up.

### The weekly recap was an interruption about nothing

It was one episode *about* somebody's week, fired as a popup on the first open
on or after Sunday. Two things were wrong and neither was fixable by tuning it:
a thin week produced an episode about having had a thin week, and whatever it
produced arrived in front of somebody who had opened the app to listen to
something else.

**What you missed last week** is a myFAM rail instead: what FAM put in front of
this listener in the last seven days and they did not take. Three rules hold it
honest, and they generalise. It is **what was actually offered** — the
impression log minus everything they played — so there is no top-up from the
bank and a short rail is short, because the heading is a claim about what this
app did. **An impression still never becomes taste**: it decides membership,
which is a fact about the feed, and `_affinity` decides the order. And **it can
only offer what it can still resolve**, which is the bank plus what the story
pool still holds — a tile invented to stand in for an expired story is §102
with a heading on it.

Its empty sentence claims neither of the two nothings: somebody who was not
here last week was offered nothing, somebody who played everything missed
nothing, and the rail cannot tell them apart.

### Download made saving a two-tap action with a decision in the middle

Pressing save raised a popup asking whether to download the audio to the
device as well. Save is a toggle now — green, press again to take it off —
and the download feature is **removed rather than switched off**: three
endpoints, four store methods, the tier field and its three environment
variables, the offline IndexedDB layer, the second view of the shelf, and the
Downloads tile. A route or a knob left behind is an invitation to turn it back
on, which is the lesson Piper cost.

### The rest, and what each one was

* **Sign-up refused the field it offered.** `_one_identifier` was shared
  between login and sign-up, so filling in the phone number came back as "send
  either an email address or a phone number, not both" — a rule about how the
  server stores things told to somebody answering the fields in front of them.
  Both are stored on one account row now, and the number formats as it is
  typed with the country code picked from a select, because the server stores
  exactly one form and refuses to guess a country.
* **A new follower was never announced.** "___ started following you", their
  picture, Follow back, an X. `new_followers` is a query over the follow
  graph's own timestamps against one column saying when the listener last
  looked — not a second table with its own read state — and it is cleared by
  the **Friends tab**, never by the popup being drawn.
* **A friend's vibe was not named on an Explore card,** and a stranger's was.
  The tag needs two conditions because it claims a friendship: a friend
  generated the episode *and* that same friend vibed it.
* **A friend's profile described people with nothing behind it.**
  `/api/person` returns only what they chose to publish — public mixes, vibes,
  and the interests they have not hidden. No play count, no completions, no
  inferred subjects, no history, and no listener id.
* **The post-episode popup was bank-only,** so somebody who had just heard
  about today's news was offered four standing explainers. It draws on the
  live story pool as well now, exactly as Made for you does.
* **Logging out left the listener on the Settings screen of an app that was
  now somebody else's,** with a toast. Half a second of the FAM mark, then the
  Sign up / Sign in screen, and everything of theirs cleared out of memory on
  the way.
* **The default episode was three minutes in about twenty places** — every
  endpoint's `Query` default, both client length controls, prefetch, the
  tools. `config.DEFAULT_MINUTES` is two, and a test reads `app.py`'s own
  source so no endpoint can grow a second opinion.
* **"1 vibed".** The counts are worded for the numbers they hold.
* **Your FAM was set in type** while myFAM, DailyFAM and exploreFAM all use
  the mark.
* **The player's four icons were unlabelled.** A bookmark, a two-way arrow and
  a speech rectangle are three guesses; the play-all sidebar and Explore's rail
  had always carried labels, so this row was the odd one out.
* **Passwords needed ten characters.** Eight.

### What is still unverified, and it is the same thing as always

None of the writing changes have been heard. There is no API key in this
container, so the `<<TITLE:>>` line has never produced a title, the captions
panel has never shown a real script, and the sources cluster has never drawn a
real publisher. Everything above is verified against tests, the interface
checks and both browser smoke runs; the parts that need a key need a key.

## 105. The prefetch framework was built, shipped, reported on — and called by nothing

myFAM's tap path was the same as searchFAM's, which meant it paid what search
pays. Every uncached tile ran episode intelligence in front of its first word:
one model call between the tap and the retrieval, on the one surface CLAUDE.md
says must wait for nothing. And the machinery for avoiding that had existed
since §83 — `prefetch.py`, four candidate sources, a budget, a ledger, a brief
store, `understand()` already asking for a warmed brief before paying for one.

The thing missing was the caller. `Prefetcher.run_once` was referenced by its
own tests, by `tools/prefetch_report.py`, and by nothing else in the app. So
the seam was right, the mechanism worked, and no listener had ever been half a
second better off for it. §83 said "nothing schedules a cycle yet - that is the
next decision, not an oversight". This is that decision, taken at the owner's
direction, for the browse surfaces only.

### What was turned on, and what deliberately was not

**`PREFETCH=1`, at `brief` level.** That split is the whole of why switching it
on is a small decision rather than a large one:

* a **brief** is one small model call. It resolves the subject, works out the
  why-now, decides the retrieval and the freshness window - the part that costs
  a browse tap its seconds. Warming one that nobody taps costs a fraction of a
  cent.
* a **script** is a whole episode, written speculatively. Warming one nobody
  taps costs a full episode, and the hit rate that says how often that happens
  still does not exist.

So the cheap half is on by default and the expensive half is still opt-in
(`PREFETCH_LEVEL=script`), which is exactly the shape of the request: warm what
makes the tap fast, don't write the episodes before anybody has asked for one.
A tap on a warmed tile still pays retrieval and writing, and still gets a
*fresh* episode - which is the other reason not to warm scripts on a browse
page whose top rails are today's news.

### The four things that had to be built, not just switched on

**1. Something has to schedule a cycle.** `prefetch.schedule_cycle` is called
by `/api/myfam` when the page is drawn, and never awaited - the same shape as
the story sweep two lines above it, and for the same reason: a page that waited
for speculation would have spent the latency the speculation was buying. It
refuses itself in a dictionary lookup when prefetch is off, when there is no
prefetcher, or when this listener was warmed recently, because it runs on every
draw. Neither the scheduling nor the cycle can raise into the request: somebody
who asked for a browse page gets a browse page, and a failed guess goes to the
log.

**2. A browse page is drawn far more often than it is acted on.** Opening the
tab, coming back from a player, a pull to refresh - without a clock on it, one
listener flicking between tabs would spend the daily ceiling on the same six
tiles. `PREFETCH_CYCLE_SECONDS` is 300 and the clock is **per listener**, so
the busiest person on the server does not become the only one whose guesses get
warmed.

**3. A warm at the wrong length is a warm nobody finds.** A brief is keyed by
`(query, minutes, context)` and a script by `pipeline.key_for`, which carries
minutes too - and every source warmed at `config.DEFAULT_MINUTES` while myFAM
has had a **length control of its own** since §95. A listener browsing at five
minutes would have had briefs warmed at two: paid for, held for an hour, and
never looked up once. `Prefetcher.plan(minutes=...)` overrides every source's
default with the length the surface is actually showing, and `/api/myfam`
passes the one it already receives.

**4. The inventory with most to gain had no source at all.** `FeedSource`
warms the personal rails, `TrendingSource` warms FAM's own most-played row -
and the live story pool (§102), which fills the top two rails of the page, was
warmed by nothing. It is the inventory where a brief buys most, because a story
tile is a title and an angle: the subject still has to be resolved on the tap,
which is the call EI makes. `StoriesSource` reads `stories.pool()`, which is
already in memory, so asking costs nothing - the rule every source here keeps.
Its candidates carry no listener, like trending's: the pool is composed once
for everybody, so a warmed brief is used by whoever taps that tile.

### And one thing that would have quietly eaten the saving

A brief keeps for an hour; a browse can schedule a cycle every five minutes. So
`_warm` would have re-bought the same six briefs twelve times over - a
prefetcher whose entire saving went on re-buying its own work, visible from
outside only as a bill. A held, unexpired brief is now a `cached` skip, counted
like any other. The `script` level is unaffected: there the cache check above it
is the one that matters, and a held brief makes that warm *cheaper*, because
`understand()` reads the same store.

### The ledger had to learn to count the cheap half

"Never pretend it is paying" was written when a warm meant a script, so the
ledger counted scripts: keys warmed, keys taken, per source. Shipping `brief`
as the level would have made every deployment report `hit rate: no data yet`
forever - the same failure with the sign flipped, a prefetcher spending money
and reporting nothing about whether it was worth it. Briefs are now counted
beside scripts, warmed and taken, per source.

Two differences from the script counters, both deliberate. A brief is counted
**every time a tap uses one**, where a script key is counted once: the brief
store is in-process rather than the shared cache, so each tap that finds one is
a separate several seconds nobody waited, and the brief rate can exceed 1. And a
**degraded brief is never counted as warmed**, because the store drops it -
counting it would report a saving no tap can collect.

### The ceiling was denominated in the wrong thing

Found in the pre-merge review, and it would have switched the feature off
within the hour rather than breaking it - which is worse, because nothing
looks wrong. `Budget` counted every warm as one "episode" against
`PREFETCH_DAILY_EPISODES=50`, a number chosen when a warm *was* an episode at
about three cents. A brief costs a fraction of that, and myFAM warms six per
cycle, one cycle per browse: a handful of listeners exhausts fifty inside an
hour, warming stops for the rest of the day, and the dollar ceiling it was
really meant to respect has five cents of two dollars gone.

Briefs are counted apart from episodes now, against `PREFETCH_DAILY_BRIEFS`
(400), and **the dollars still bound both** - that is the currency the two
kinds of warm actually share, and the per-kind counts are the backstop for the
one thing dollars cannot see, a model with no price in `metering.PRICES`. A
`Budget` built without a brief ceiling falls back to the episode one, so
nothing that existed before this bounds less than it used to.

### Two things the review named rather than changed

**Speculative spend is in no listener's ledger, and that is correct.** A
warmed brief was nobody's tap - attributing it to whoever happened to be
browsing would be the same mistake as putting a listener id near the cache
key. It is bounded by `PREFETCH_DAILY_DOLLARS` and reported by the budget
block on `/api/health`, and `METERING.md` now says that a month reconciled
against the invoice has to add the two. **And a warm can start while somebody
is about to tap**: `quiet_enough` looks backwards, so the first cycle after a
page draw begins immediately. That is the accepted shape - the check runs
before *every* candidate, so a listener who taps two seconds later stops the
rest of the cycle, and the one small call already in flight is the price of
not delaying the warming itself by a fixed wait.

### What this does not change

The tap path. Nothing was added to it, which is the property §83 built the
whole design around: prefetch writes into the same store, under the same key,
and a warmed tap is an ordinary lookup. `EPISODE_INTELLIGENCE=0` and
`PREFETCH=0` each still restore exactly what they restored before.

And the numbers still have to be read. `/api/health` now reports
`listeners_cycled` beside the hit rate, because sources installed with nothing
scheduling a cycle looks identical from outside to sources installed and
warming every browse - which is precisely the state this had been in since it
was written.

## 106. A share link handed a stranger the whole app instead of the episode

The nine share destinations have existed since §96 and all nine work: the
wording, the hand-off URL and the story card are composed server-side and
tested end to end. What nothing had looked at was **the other end of the
link**.

`/s/<id>` did this:

    return RedirectResponse(url=f"/?q={quote(record['query'])}&minutes=...")

So somebody sent one episode by a friend landed on the **front door of the
whole product** - the search box, myFAM, Explore, a sign-up screen - with
their episode reduced to a query string that the app may or may not act on.
Everything the share was for was the one thing hardest to find once they
arrived. This was listed under "what is not built" as app-side work, which it
is not: the redirect is the server's, and so is the page that replaced it.

### What was actually wrong, in three parts

**1. The destination was the app.** Fixed by serving a page instead:
`static/listen.html`, one episode, and the rule that the only control which
works is play. The wordmark, "Ask your own question", "Browse episodes" and Get
FAM are all `data-door`, routed by one delegated listener to the App Store - so
a control added later is a door by default rather than by somebody remembering
to wire it.

**2. Every FAM link posted anywhere previewed identically.** This file already
records that Facebook and LinkedIn drop everything except the URL and read the
page for their own preview (§96, found by pressing the button rather than by
reading the code). The page they were reading was the app's shell. A crawler
does not run JavaScript, so this cannot be fixed on the client: the title, the
question and the story card are substituted into the HTML the server sends.
The same substitution carries the payload the player needs, so the page spends
no round trip working out what it is - the one-sentence spec applies to a
stranger's first second of FAM more than to anybody else's.

`og:image` is claimed **only when the card URL is absolute**. A crawler fetches
it from its own servers, so a relative one advertises a picture that never
loads - which is the refusal `destination_for` already makes about the link.

**3. The open count would have become a count of robots.** Those same crawlers
*fetch* `/s/<id>` to build the preview, and opens were counted on the serve.
Opens are the only number sharing produces, so a wrong one is worse than none.
The count moved to `POST /api/share/<id>/open`, which the page calls once it is
running in front of a person. A crawler never gets there, and this needs no
list of user agents - which is the shape §76 settled against, because such a
list can always be widened by one more entry and the next miss is already
written.

### The part that turned out to need no new machinery at all

The question was how a link traces back to "the current episode", and the
answer is that it already did. **There is no episode id in this product and
this did not add one.** An episode is identified by its cache key; `key_for`
builds that key from the question and the length; a share row holds exactly
those two. So the landing page asking `/api/audio?q=...&minutes=...` computes
the same key and gets the sharer's own script out of the shared cache - same
words, no second model call, nothing new stored, ten opens against one script.

Adding a share-specific episode id would have been a second identity for a
thing that already has one, and the two would have drifted the first time
`key_for` gained a field - §83's failure with a wider blast radius. A test
asserts the two keys are equal instead, so that drift fails loudly.

### Two things deliberately not done

**No `cached_only` on the landing page.** It would make a share whose script
had aged out refuse to play, and a broken link is worse than an episode that
costs a model call. The exposure is stated rather than hidden: a publicly
posted link can be opened by strangers, and the first one after an expiry pays
for a script the rest then share.

**No invented App Store URL.** `APP_STORE_URL` is unset on every deployment
until the app ships. Empty, the page draws no non-listening control at all -
not one that 404s, and not one quietly rerouted into the web app, which is the
thing the page exists to not be. A control with nothing behind it is worse than
no control, and a stranger arriving from LinkedIn is the worst possible
audience for a dead button. `/api/health` reports both, because from inside the
app the configured and unconfigured states look the same.

And the payload carries **no `user_id`**. The share row has the listener id
sitting next to the question, and this is the one response in the app handed to
people who are not listeners - so authorship stays provenance and never
identity (§95), asserted in two tests rather than left to review.

### Getting §106 ready to merge

The §101 audit, run again on this branch. One commit, `origin/Main` is an
ancestor so it fast-forwards, working tree clean. No secrets, no `console.log`,
no `pdb`, no hardcoded hosts, no model identifiers in anything pushed. **No
schema migration at all** - the share row already held the question and the
length, which is the whole reason the landing page needed no new concept, so
§101's riskiest category does not arise here.

Two things it did turn up.

**§101's own finding, repeating.** `dev.sh` gained a preview build and a
browser driver for the landing page; `.github/workflows/ci.yml` gained neither,
so the thirteen behaviours that prove the page's *restriction* - play works and
nothing else does - would have been enforced only on whoever happened to run
the local loop. Added to the gate. The general form is worth restating because
it has now cost two branches: **a check added to the local loop is not added to
the gate**, and nothing keeps the two lists in step but somebody remembering.

**A smoke behaviour was racing, and it was the test.** "The first run asks,
then lets you in" failed once, during a run with three browsers going, on the
handle field not cleaning what was typed into it. It was not the app:
`cleanHandleInput` is a synchronous `oninput` handler with no debounce, and
nothing re-enters `openIdentity` to clobber the field. It was `openIdentity`'s
own autofocus - `setTimeout(... focus(), 80)` on the *name* field - landing in
the middle of Playwright's fill of the *handle* field, which types into
whatever holds focus. So the handle went into the name box and the assertion
read an empty field.

The fix waits for the autofocus to have landed before typing, which removes the
race without weakening anything. Nine runs, three of them with all three
browsers concurrent, are clean. Worth recording for the shape rather than the
bug: **a screen that focuses something on a timer is a race against any test
that types into it**, and the failure does not look like a focus problem - it
looks like the field under test not doing its job.

**Known and deliberately not fixed here.** The preview builds are still not
reproducible (§101), because `build_preview.py` bakes `time.time()` into a
fixture. `build_share_preview.py` does not, and rebuilding it is byte-identical
- so the new artifact can be verified against its source even though the older
two cannot.

### The gate was red on `Main`, and had been for days

The audit above was written believing CI was green. It was not: **every CI run
on `Main` for at least ten merges had failed**, always on the same assertion -
`tests/test_trending.py::test_one_refresh_serves_every_listener`, `assert 2 ==
1`, "the feed was fetched per listener". This branch inherited it, so its first
two runs were red for a reason that had nothing to do with sharing.

Worth recording for how nearly it was dismissed. It passed locally - the whole
suite, 1946 tests - and failed on CI every time, which is the exact shape of a
thing you write off as an environment difference and stop looking at. The
difference was the Python version: **3.11 locally, 3.12 in the gate**. Under
3.12 the whole suite reproduces it, and `tests/test_trending.py` on its own
passes under both. So it was order-dependent state, not a version bug.

**The cause was `stories.reset()`, whose docstring said "drop everything" and
did not drop the source registry.** The helper in `test_trending.py` resets and
then registers `TrendingRegistrySignals`, so a second call through it was
*appending* rather than replacing: two copies of the same source in `_SOURCES`,
`stories.refresh()` collecting from both, and therefore two upstream fetches.
Reproduced in eight lines outside pytest - one helper call gives one fetch, two
give two.

Three things in it are worth keeping:

* **The failing test was right, and it was the right test.** It exists to guard
  this row's economics - one fetch serves every listener, which is why Trending
  is the cheapest place in FAM to put live data rather than the most expensive.
  It reported one refresh becoming two per listener. That is precisely its job;
  what it was catching was test pollution rather than a regression in the row,
  but the number it printed was true either way.
* **A partial reset is worse than no reset**, because it leaves the caller
  believing they are starting clean. `reset` now clears `_SOURCES`, which is
  safe because nothing in production calls it - sources are installed by
  `story_sources.install`, and `reset` has only ever had test callers.
* **A red gate protects nothing, and stops being read.** Ten merges went in
  over a failing check. The cost is not the one test; it is that the next real
  failure arrives on a board that is already red, and nobody looks.

Fixed, with a guard in `test_stories.py` pinning that `reset` empties the
registry, and the full suite verified green on **3.12** - the version the gate
actually runs - as well as on 3.11.

### And a second failure was hiding behind the first

Fixing the trending pollution got CI past the **Tests** step for the first time
in this branch's history - and the run promptly failed again, on a *different*
check:

    FAIL  Go Deeper titles are not cut off: Go Deeper tile cuts these titles
          off: ['What happens to the grid operators when the subsidy expires
          next year']

Stated precisely, because the distinction matters: this was **not introduced
here and was also not previously known**. Every recent CI run died at the Tests
step, so **no run had ever reached the browser smoke tests at all**. The check
has presumably been failing in the gate for as long as the trending one has;
nobody could see it. That is the second-order cost of a red board, and it is
worse than the first: a red check hides the checks behind it.

It does not reproduce here, and the diagnostic added below is what said why.
The gate reports:

    worst: {'text': 'What happens to the grid operators...',
            'scrollHeight': 53, 'clientHeight': 39}
    rendered with: Fraunces, serif at 10.5px/13.125px (webfonts: loaded)

39px is three lines at that line height and 53px is four. So on the runner the
longest title wraps one line further than the tile allows, and it is **not
marginal** - it is a whole line over.

`Fraunces, serif` is the CSS stack, not proof that Fraunces rendered;
`document.fonts.status` says loading *settled*, not that it succeeded. The
most likely reading is therefore that the runner falls back to its own serif,
which is wider than the one this container falls back to - which is also why
blocking Google Fonts locally does **not** reproduce it. The other half of the
difference is the browser: CI downloads Chromium 1243 (Chrome 153), this
container has 1194, and it cannot be fetched from here.

Note what that implies beyond the test, because it is the more interesting
half: if the fallback really is what the runner draws, then **any listener
whose browser does not get Fraunces sees these titles clipped too.** The check
would then be reporting a real product fact rather than a CI quirk.

**Deliberately not "fixed".** The ways to make it pass are to install the font
on the runner, to pin the browser, to give the tile a fourth line, or to loosen
the tolerance - and the last would weaken a guard that exists because a clipped
headline actually shipped once. Given the reading above, the honest candidates
are the third and a real decision about fallback fonts, neither of which is a
thing to quietly patch in a branch about sharing. It belongs to whoever owns
that guard.

What was done instead is the part that is safe and was missing: **the failure
now says what it measured** - the worst offender's scroll and client heights,
the computed font, size and line height, and whether webfonts had settled.
Nothing about what passes or fails changed. A layout assertion that can differ
between machines has to report its measurement, or a failure on a machine you
do not have is unactionable - which is exactly the position this one is in.

---

## 107. Eighteen items off a second review of the running app

An implementation list, worked through in order. Five of the eighteen turned
out to be the *same* class of bug wearing different clothes, and that is the
part worth carrying forward rather than the individual fixes.

### The class: a list maintained by hand, beside a guard that reads the same list

**The reported symptom** was "when I deploy render with the latest updates,
the accounts that were created are erased". Accounts were fine. `Dockerfile`
pins every database to the mounted disk so it survives a redeploy, and
`ACCOUNTS_DB=/data/accounts.db` was in there.

What was *not* in there: `MESSAGES_DB`, `SAVED_DB`, `SHARES_DB`, `QUOTAS_DB` —
four stores added after that list was written. So every push discarded every
conversation, every saved episode and every share link, while the accounts
beside them survived. From outside, a wiped database and a new install look
identical, so nothing said so.

The guard that exists precisely to catch this did not, and the reason is the
finding. `tests/test_data_paths.py` compared the Dockerfile against its own
`STORES` list — **also maintained by hand**. A store missing from both looked
perfectly consistent, and the test passed while asserting nothing about four
of the twelve databases. Two hand-written lists agreeing with each other is
not a check; it is the same mistake made twice and then compared to itself.

`ALL_VARS` is now derived, by reading every `data_path("VAR", "file")` call out
of the modules. A store is discovered the moment it calls that function,
whether or not anybody remembered this file. `STORES` stays by hand because a
class cannot be discovered from a string, and a second test asserts it has not
fallen behind the derived set.

**Generalises to**: any guard whose subject is enumerated. If the check and
the thing checked are both lists somebody types, the check is decorative.

### The corollary: the server now answers the question itself

Reading the Dockerfile is how anybody would have answered "will a redeploy
erase this", and the Dockerfile was wrong twice. Worse, it can be right and
still wrong: a store pointed at `/data` on a host with no disk actually
attached is ephemeral, and no amount of reading configuration reveals that.

`/api/health` reports `storage` now, and it is **measured rather than
configured** — §52's rule applied to durability. A mounted volume is a
different filesystem, so `st_dev` answers it: a database on the same device as
the application code is inside the container image and goes when the image is
replaced. Three states, not two, because "could not tell" is real and
reporting it as either of the others is the confident wrong answer this
project keeps paying for.

    "storage": {
      "durable": [...], "ephemeral": [...], "unknown": [...],
      "note": "These are inside the application filesystem and a redeploy
               replaces them: messages, saved, shares, quotas. ..."
    }

The note names the stores rather than counting them. A number nobody can act
on is the shape of report this project keeps replacing.

### The same shape again: polling a place the answer is not in

**"The captions/transcript is still not working or loading."** The panel
polled `/api/transcript` six times over twelve seconds and then said *"No
transcript for this one — an episode you attached a file to is never stored"*.
Every word of that sentence was wrong about the episode in front of it.

`/api/transcript` reads the script **cache**, which is written once, at the
end. On a first listen the sentences do not exist under that key until after
the last word has been spoken — which is the one moment a caption panel is no
use. Twelve seconds is roughly a two-minute episode's script and nothing like
a researched ten-minute one's, so *every* long episode read back as having no
transcript.

**The bug was not the poll count.** Polling a place the answer is not yet in
cannot be fixed by polling it more, and this is the trap: the failure looks
like a tuning problem and has a tempting one-line non-fix (raise the count),
which would have made it rarer and no less wrong.

`live_captions.py` is the other half. Sentences are published as they are
handed to the voice, under the cache key, so the transcript builds up while
the episode is spoken — which is what was asked for, and cheaper than what was
there: it holds text already paid for, on its way to the engine.

Three things make it small. It is **in-process with no store behind it**,
because a live track describes a generation happening in *this* worker now and
is worthless to another one — by the time a second worker could read it, the
episode is in the cache, which is the thing to read instead. It is keyed on
the **cache key**, so a live track and the cached script are the same episode
by construction and an episode with no key (an attachment) gets no track,
which keeps "an attachment episode has no captions" one rule rather than a
special case. And it says **`done`**, because "still being written" and "that
is the whole thing" are different answers and the poll count was a guess at
which.

### And a third time, in the sources panel

**"The sources icons are still not showing up on the audioplayer."** The panel
was fine. Provenance was built from the Exa packet, from live facts and from
attachments — and from nothing else.

But FAM has a second, entirely ordinary way of researching an episode: when no
evidence packet comes back, `_request_kwargs` attaches Anthropic's
`web_search` tool and the model does the looking itself. That is not an edge
case. It is **every episode on a deployment with no Exa key**, which is what
the Render service has always been — `render.yaml` has never carried one. So
the panel correctly hid an empty list, on every episode, and the cause looked
like a rendering bug.

`provenance.from_web_search` reads the results off the final message, which
`stream_sentences` already fetches for the usage accounting. It claims **no
grade and no date**: `research.credibility` reads an Exa result's own fields
and a web_search result carries none, and `page_age` is prose ("2 days ago")
rather than the ISO date `at` is documented as. Inventing either would put a
confidence on the panel that nothing measured.

The second half is the same fix as the captions: sources are published to the
live track as soon as they are known. On the retrieval path the packet exists
*before the first sentence*, and storing it only in the cache meant a panel
that could not appear until the episode had finished — forty seconds after
"where is this coming from?" stopped being interesting.

### A chat that did not work, described as a chat that was slow

**"When I stay in the chat with someone, the messages they send don't
automatically appear."** They did not appear at all. The thread was fetched
once, when the screen opened, so two people talking had to leave and come back
to see each other.

The cursor is the **row id**, not a timestamp. Two messages can share a
`time.time()`, and a `> at` cursor silently drops the second of any such pair —
which on a chat is a lost message.

The drop-down notifications are one endpoint for both kinds, because the
interface asks both questions from the same timer. The rule worth keeping is
about **whose cursor it is**: a message's *read* state is a fact about a
conversation somebody opened, and whether it has been *announced* is a fact
about a banner this client raised. Deriving the second from the first would
mean opening one chat silenced the notifications for every other — so the
client holds the cursor and the server answers only what it was asked. A
`bootstrap` call establishes where "new" starts and announces nothing, without
which opening the app raises a banner for every message ever sent to you.

And the "few seconds of latency when I send a message" was two round trips: the
POST, and then a full re-fetch of the conversation. The message is drawn when
it is typed and the written row swaps in for it, carrying the id the poll
de-duplicates on.

### A count that was missing rather than wrong

**"0 friends, 1 following, 1 follower"** — three numbers contradicting each
other on the one page with a rule against inventing any. `follow_counts`
returned `{following, followers}` and two screens read `follows.friends`. A
missing key is `undefined`, and `undefined || 0` prints a zero that looks
exactly like a measurement.

Counted in SQL by the same intersection `friends()` uses, with a test that the
number and the list can never disagree — two definitions of what a friend is
would be the bug this module already avoids by never storing mutuality.

### A search that could only fail

The topic catalogue's search filtered the list and nothing else, so searching
for something absent produced an empty screen and a sentence telling the
listener to go and ask for it somewhere else — on the one screen whose whole
job is collecting what somebody is interested in. Seventy-three strings
somebody wrote down is not the set of things a person can be interested in,
and refusing anything outside it is the app telling them their interest is
invalid.

Typed topics are stored now, which required noticing that **nothing was**:
adding one wrote a `pick` event into the append-only log and kept no list. The
log is what the ranker reads and is the right home for behaviour, but it
cannot be read back as "here is what you chose" — so there was nothing to
show, nothing to remove, and nothing for a wheel meant to reflect somebody's
choices to draw from. Both now happen: the list is a statement, the log is
behaviour, and `topics.py` stays a pure query over the second.

### Two reversals, both deliberate

**The Settings wheel no longer fills itself.** §100 gave it three sources —
what they played, then what they chose, then a declared order as filler — so
that it always had six discs, on the reasoning that "a wheel is six discs or
it is a broken wheel". Three of those are the app's answer rather than the
listener's, and a screen called *Your interests* that shows a recommendation
is answering a question nobody asked. The filler is gone, an empty wheel is
possible, and what fills it is the hub in the middle — which now says
"Edit/add topics" and is the only route to the catalogue, the second door in
Settings having come off.

**The app opens on sign-up rather than myFAM.** myFAM was the screen marked
`active` in the markup, so it was on screen from the moment the page parsed —
and who the listener is is not known until `/api/auth/me` answers. A default
screen is a guess at an answer that has not come back, and it was the wrong
guess for everybody without an account: they saw myFAM flash and be replaced.
There is no default now; the splash is held open until the answer arrives.

This sits against a settled constraint and is worth naming as a trade rather
than a fix. CLAUDE.md says listening needs no account, and that a login screen
in front of the product is exactly what this app avoided becoming. What keeps
that true is the door: "Skip for now" is on this screen, one tap, and
everything behind it still works with nothing signed in. What changed is which
side of the door the app opens on. **If that starts costing listeners, this is
the line to revisit** — it is one branch in `bootToFirstScreen`.

### Near-match caching, measured rather than argued

"Is there a way to efficiently reuse/cache the topics/titles across the
platform… so that it is not generating a brand new myFAM page every time?"

The browse page already cost no model call: both inventories are shared, the
evergreen bank is fixed, the live story pool is composed once per refresh
window *for the whole deployment*, and two listeners tapping a tile share one
script through `cache.py`. That half needed no change.

The half that did was the cache's exactness. `config.py` said to turn near
matching on "once `tools/bench_vector_cache.py` has been run on traffic that
looks like yours". It has now been run:

    without it   9 of 41 re-phrasings found an existing episode
    with it     23 of 41, with no false match at any threshold
    cost        8.93 ms scanning 400 vectors, miss path only, no model call

Fourteen episodes not written, at roughly a cent each, for nine milliseconds
spent only when the exact key already missed. It ships on; `CACHE_VECTOR=0`
restores the old behaviour exactly.

The bench's control line is the part to keep reading: **guards alone, with the
cosine ignored, find the same 23.** The vector is carrying nothing, and every
must-not-collapse pair is refused by a guard — identical numbers, lexical
overlap, agreement about needing today's facts — rather than by the threshold
sitting just above it. That is what a lexical embedding is worth here, and it
is the number that will change on the day a real sentence model is installed
in `~/.fam/embed`.

### Answered without a change

* **"Will Morning, At the gym and Wind down remain once the dummy data is
  scrubbed?"** Yes. They are `mixes.STARTER_MIXES` — three suggestions in
  code, offered on an empty DailyFAM page, whose members are real bank topic
  ids (verified: all nine resolve). They are not seeded rows and scrubbing
  demo data does not touch them.
* **"Is Go Deeper on myFAM wired on the backend?"** Yes, end to end and at no
  extra cost: the model writes a trailing `<<NEXT:>>` line that is stripped
  before synthesis, `extract_thread` reads it, the pipeline stores it beside
  the script, `/api/next` returns it for free, the `complete` event carries
  it, and `EventStore.open_threads` reads it back — dropping any thread the
  listener has since asked about, which is why it queries the log rather than
  keeping a list. It fills as episodes are finished, and the bank fills the
  grid before any have been.
* **Renaming the Render URL.** Not a code change; `DEPLOY.md` now carries the
  procedure and the one thing that actually bites — the old URL stops
  resolving the moment the rename takes effect, and every share link already
  posted anywhere is built from it.

### The part nobody has verified

The same caveat this log carries every time, and it applies to most of the
above. There is **no API key and no GPU in this container**, so nothing here
has been heard. The live caption track, the web-search provenance and the
near-match cache are covered by tests and by the browser smoke checks against
fixtures; what none of that proves is that an episode on a real machine now
shows its sources and builds its transcript while it plays. That is one
session with a key, and it is the first thing to do with one.

### Postscript: the §106 CI failure, diagnosed

Preparing this branch to merge meant reading the gate, and the gate has been
red on `Main` for six consecutive merges with one check failing:

    FAIL  Go Deeper titles are not cut off: ['What happens to the grid
          operators when the subsidy expires next year']
    worst: {'scrollHeight': 53, 'clientHeight': 39}
    rendered with: Fraunces, serif at 10.5px/13.125px (webfonts: loaded)

§106 could not reproduce it and left it deliberately, with the reading that
*"the runner falls back to its own serif, which is wider than the one this
container falls back to"*. That guess was right, and the font now has a name.
Measured in the local browser by forcing each candidate onto the element:

    font stack                                 scrollH  clientH  fits?
    'Fraunces', serif                               39       39  yes
    serif                                           39       39  yes
    'Liberation Serif', serif                       39       39  yes
    'DejaVu Serif', serif                           53       39  NO
    'FreeSerif', serif                              39       39  yes
    'Fraunces', 'Liberation Serif', …, serif        39       39  yes

**DejaVu Serif produces exactly 53** - the number the runner reports, to the
pixel. So the runner's generic `serif` binds to DejaVu Serif and this
container's binds to Liberation Serif, and that one difference is the whole
failure. Fraunces itself loads on both (`document.fonts.check('12px Fraunces')`
is true here), so nothing is wrong with the webfont; it is purely what happens
when Fraunces is *absent*.

Which makes §106's more interesting reading concrete rather than speculative:
**a listener whose browser has no Fraunces and a DejaVu-metric default serif
sees these titles shortened.** Not cut mid-word - `-webkit-line-clamp:3`
ellipsises cleanly, which is the designed overflow - but shortened, and the
guard is right to call it.

The fix is one declaration and measured at 39px:

    .gd-card-title{ font-family:'Fraunces', 'Liberation Serif',
                    'Times New Roman', Times, serif; … }

It changes nothing where Fraunces loads, touches neither the card height nor
the clamp, and does **not** loosen the tolerance - which §106 rules out, and
which remains ruled out.

**Not applied here, and the reason generalises.** Fraunces is named in about
ten places in `static/index.html`. Fixing the one the guard happens to measure
leaves every other display element still falling through to the OS generic, so
it would turn a known failure into a hidden one - the guard would go green
while the product fact it is reporting stayed true everywhere else on the page.
The honest version is a single statement about what the brand face falls back
to, applied everywhere it is named and looked at through `tools/shots.py`, and
that is a typography decision rather than a CI repair. §106 said it belongs to
whoever owns that guard; what is added here is the cause, so that whoever picks
it up is choosing rather than guessing.

---

## 108. The first ten seconds were written by the half that knew least

**Reported from listening, and the report contains the diagnosis.** *"The
opening first couple of sentences doesn't make much sense and is a confusing
response to the prompt that is typed in. After the start, the rest of the
episode sounds almost perfect."*

That shape - wrong at the start, right everywhere else - is not a writing
problem, and no amount of prompt work would have fixed it. It is the signature
of two different pieces of text produced under two different conditions, and
FAM was producing exactly that on purpose.

### What was actually happening

`ANSWER_FIRST` started **two model calls at once** on every researched episode:

* the **cover** - `search=False`, `role="opening"`, EI deliberately skipped,
  no evidence by construction - which began speaking immediately;
* the **researched half**, which was still reading, and took over mid-flow.

So the first ten seconds of every researched episode were written by a call
that had been given no brief, no evidence and no idea what the episode was
going to be about. Then the good half arrived. The listener heard the seam
exactly where they said they heard it.

The setting's own default had already concluded this: unset, it followed the
research backend, and Exa - half a second to retrieve - implied
`answer_first=False`. **But `Dockerfile.gpu` pinned `ANSWER_FIRST=1`**, because
the configuration that had been listened to on a card ran with the cover on.
The knob that had been left behind was turned back on by a line in a
Dockerfile, which is the second time this project has lost a session to
exactly that (the cold open, §55, was turned back on by an example file).

There was a second path with the same defect. When no evidence packet came back
- `RESEARCH_BACKEND=claude`, or Exa finding nothing - the `web_search` tool was
attached to the **writing** call, so the model searched while it wrote. §77
added a prompt paragraph asking it to search before writing; §94 added
`OpeningGuard` to hold back the disclaimers it wrote anyway. Both are
mitigations of an ordering, and the ordering was the bug.

### The fix, which is an ordering rather than a wording

**Nothing is written until the writer holds the whole picture.**

* The cover is **deleted** - `_answer_first`, `ANSWER_FIRST`,
  `ANSWER_FIRST_SHARE`, `ANSWER_FIRST_MAX_SHARE`, `role`, `ROLE_BRIEFS`, the
  handover marks and stats, the Dockerfile pin. Deleted rather than defaulted
  off, on the Piper and cold-open reasoning, and this time with the receipt:
  the default *was* off, and a deployment turned it back on.
* **Both research backends retrieve before the writing call.** `claude` now
  means a call of its own whose only job is to come back with evidence
  (`research.retrieve_with_claude`), shaped into the same packet as Exa's. The
  call that speaks is given **no tools at all**, and the `research_now`
  paragraph is gone with them.
* **Episode intelligence runs for every episode**, including unresearched
  ones. The skip was worth a second of latency and cost the brief - the
  intent, the resolved subject, the story shape, what the writer must not
  assume - which is the material an opening is made of.
* **`EFFORT` is `high`, was `low`.** The reason it was low was written down:
  "effort directly costs time-to-first-audio". Deciding what the whole episode
  is happens before the first token, and that is the only budget there is for
  it. A model that starts talking before it has decided writes a confusing
  opening and a good episode.
* The prompt now says so in as many words: decide the whole piece before the
  first word, and read the first two sentences back against what the listener
  typed - if they would also open an episode about something else, it has not
  started yet.

### What this costs, stated rather than discovered

Seconds in front of the first word, deliberately, and this is the second
amendment to the one-sentence spec after §82's. On Exa the retrieval is about
half a second and the new cost is mostly the effort budget; on the `claude`
backend it is the 10-25 seconds the model's own search has always cost, moved
from underneath the episode to in front of it. The interface already shows an
honest wait that names what it is waiting for (§55), which is what makes that
payable.

Two rules were bent and one of them deliberately:

* **A backend that cannot serve now falls back to the other one.** research.py
  refused to, and was right to while an exception meant "the cover keeps
  playing". With one stream it means "no episode". So the other retriever gets
  one go and the packet records `fell_back_from`, which is visible in
  `notes.research` and on the episode. The rule was never "do not fall back",
  it was **never fall back silently** - and the behaviour being replaced was a
  silent fallback: a tool quietly attached to the writing call.
* **`OpeningGuard` stays.** It is not a speed guardrail. A thin packet is still
  possible and a model handed one can still reach for a disclaimer. What
  changes is what a drop now means: the retrieval came back thin, not that the
  writer was guessing.

### Unverified here, and this is the first thing to check

There is no API key in the build container, so nobody has heard an episode
written this way. `python write.py "<query>" --minutes 3` prints the brief and
the script without audio and is the loop for judging it. The specific thing to
listen for is the one that was reported: whether the first two sentences are
recognisably about the question that was typed.

---

## 109. What happens when the search comes back empty

§108 made every episode wait for its evidence before a word is written. The
question it left unanswered was asked immediately, and it is the right one:
*how often does the search come back empty, and what happens then?*

The answer to the second half was "FAM writes the episode from model memory
and says nothing about it", which is §89's rule reached by a different road -
**never infer a current-world fact from the absence of current-world
evidence** - and it was worse after §108 than before, because there is no
longer a from-knowledge half whose job that was.

### Three defects found while checking, in order of severity

**1. A retriever that raised took the episode with it.** `research.retrieve`
raises `ResearchUnavailable` for a missing key and raises *whatever the vendor
raised* for everything else - a 502, a timeout, a rate limit, a malformed
reply. `ScriptGenerator.research` caught only the first. While the cover
existed this was survivable: the cover was already speaking and the researched
half simply never arrived. With one stream it is the whole episode, so a
five-second blip at a search vendor was a listener getting no audio at all.

**2. An empty packet did not buy the retry that exists for it.** The second
look was gated on `not covered`, and `packet_covers("", [])` is `True` - so a
brief that named nothing specific to establish turned a search that found
*absolutely nothing* into a satisfied one. The commonest way in is the recency
window: EI says the answer must be from the last day, nothing was published in
that window, Exa returns zero results, and the one mechanism designed for this
- search again with the window dropped - never ran.

**3. And when it did run, its results were thrown away.** The merge keeps the
second packet only when it misses fewer of `must_establish` than the first.
With nothing to establish, both miss zero, so the tie rule kept the first -
which is the empty one. A retry that finds sources and then discards them for
an empty packet is worse than no retry: it pays for the search and reports the
episode as thin.

All three are the same shape: code that reasons about a packet being *thin*
and does not consider it being *empty*.

### When the search can actually come back empty

With those fixed, and in rough order of how often they happen:

* **A recency window nothing falls inside.** The brief asks for the last
  1-3 days on something niche. Now retried without the window.
* **A resolved subject that the index does not know by that name.** EI names
  the subject, the query is that subject, and Exa has nothing under it.
* **A vendor failure** - 5xx, timeout, rate limit, a rotated key. Was fatal,
  now a rung.
* **No retrieval credential at all.** `EXA_API_KEY` unset, which is what
  `render.yaml` ships as. Was fatal for every researched episode.
* **A question about something that genuinely has not been reported yet** -
  the seam `live_facts.py` exists for. No amount of retrying fixes this one;
  it is the case the refusal is for.

### The fix: a ladder, then a refusal that is narrow on purpose

The ladder, in **cost order**, stopping at the first rung with evidence:

1. the configured backend (`exa` by default);
2. the same backend again with the recency window dropped (inside `retrieve`);
3. **GDELT** - promoted from additive cross-check to a retriever of its own,
   because it is keyless and one HTTP call, so a deployment with no credential
   at all still researches;
4. the model's own `web_search`, last because it is 10-25 seconds and a model
   call.

No rung can raise. Every fallback is recorded on the packet (`fell_back_from`),
which reaches `notes.research` and the episode's own record - the rule was
never "do not fall back", it was **never fall back silently**.

At the bottom, `research.NoEvidence` - the request fails with a sentence the
listener can act on - **but only when the question turns on something
current.** The precedence is the one `cache.ttl_for` already uses, for the same
reason: a live state from a provider is evidence and ends the question; then
`Brief.outcome_dependent`; then any recency window at all; then, for a degraded
brief, the keyword heuristic as the floor. An evergreen question is never
refused, because there the model's own knowledge is accurate and a refusal is a
worse answer than the episode.

Two placement details are load-bearing. The check is in **`prepare`, not
`research`**, because `prepare` is the first point where the live lookup and
the retrieval have both answered - a game whose score came back from a scores
provider is answered whether or not anybody has indexed an article about it.
And a refused episode is **refunded whatever was billed**, which is the one
exception to `_refund_if_unspent`'s "was money spent" rule: the spend was a
decision FAM made and then declined to deliver on, and charging for it would
let one bad afternoon at a search vendor eat a free tier's whole day.

### What this costs

A rare question now fails instead of producing a confident episode about
nothing. That is the trade, taken deliberately at the owner's direction, and it
is bounded by the ladder above it: reaching the refusal takes four retrievals
finding nothing, on a question that needs today's facts.

### Nine more, found by reviewing the above rather than by running it

The ladder and the refusal were written, tested green, and then read back
against the rest of the app. Everything below was uncovered by tests, and
three of them were introduced by §108 and §109 themselves - which is the
argument for the review being part of the change rather than a later pass.

1. **`/api/script` reserved an episode and had no failure path.** Its own
   comment said so, and that stopped being true the moment `NoEvidence`
   existed: a refusal there was a bare 500 with the unit still spent. It
   refunds and answers 503 now, like `/api/audio`.
2. **An attachment was not counted as evidence.** With `SEARCH_MODE=always`
   an attached document is researched too, so an empty retrieval could refuse
   an episode whose evidence the listener had supplied themselves - the one
   kind of episode with *more* to go on than a researched one.
3. **The ladder promised a rung that cannot serve.** `GDELT=0` is the shipped
   default and `gdelt.retrieve` returns `[]` when it is off, so
   `/api/health` and the startup warning were describing a wish.
   `research.ladder()` now lists only rungs that can actually fetch, the
   runtime walks that same function rather than a second hard-coded list, and
   `report()["unavailable"]` asks about the configured backend rather than
   only about Exa - `RESEARCH_BACKEND=gdelt` with `GDELT=0` used to report
   healthy and retrieve nothing, for ever.
4. **A discarded rung was a free rung.** Only the winning packet was metered,
   and the `claude` rung is a real model call with a web search in it that is
   *most* likely to be discarded on the episodes that then get refused and
   refunded. Every rung that ran is metered now; the rungs keep their own
   numbers rather than rolling them into the winner, so nothing is counted
   twice.
5. **GDELT skipped the sufficiency check.** `thin_on` was therefore always
   empty on that backend, and GDELT is the rung most likely to produce a
   packet of headlines with no passages under them - the §88 shape `thin_on`
   exists to prevent. One `_note_gaps` helper runs on every rung now.
6. **A report of having found nothing was taken for evidence.** Asked to
   search and report, a model that finds nothing sometimes writes a sentence
   saying so; a sentence is non-empty, so it satisfied `Packet.__bool__`,
   stopped the ladder, suppressed the refusal and landed inside the
   `<evidence>` block. The test is now whether it reported a URL it read.
7. **Everything was counted as an Exa search.** `Usage.exa_searches` was
   fed by every backend, making the one retrieval number `usage_report.py`
   prints a mixture of three things.
8. **A client per retrieval.** `research_client()` built a fresh
   `AsyncAnthropic`, each with its own httpx pool, and never closed it.
   Cached on the credential, so a rotated key still reaches a later call.
9. **`write.py` exited 0 on a refusal.** A sweep over twenty prompts reads
   exit codes; a refused episode and a written one must not look the same.

And the same review found the one piece of copy the change had made false:
the startup warning still said researched episodes would **FAIL** rather than
search another way. They fall down the ladder now. A warning describing a
failure mode the code no longer has sends the next person looking for the
wrong thing, so it names the rungs that will actually serve - read from
`research.ladder()`, not written out a second time.

## 110. What EI can do about an empty search, for no extra latency

§109 built the ladder that runs *after* a search comes back empty. This is
what happens before one: the layer that decides what to search for is also
the cheapest place to stop the search failing, because it is a model call
that is already being made.

**Two fields and a floor**, none of which costs a round trip:

* **`search_fallback`** - the same search, broader, written in the same call
  as the precise one. An empty search is most often a query too specific for
  the index rather than a subject nothing was published about, and the
  cheapest rephrasing is the one that costs no second call. `Brief.broader`
  picks it, then the resolved subject, then the raw query, and never returns
  the string that just failed. This is what §82's "one more search, never a
  model call to rephrase" always wanted and could not have.
* **The ladder uses it.** The precise query already found nothing, so asking
  a second index the same string is a second search that can only fail the
  same way. Every rung after the first asks the broader question.
* **A recency floor.** The window is a *filter* - nothing outside it is
  considered - so a one-day window on a subject nothing was published about
  yesterday returns zero results. Widening costs almost nothing, because
  `rank_results` sorts newest-first inside the window anyway and the packet
  carries every date, so a two-day-old source is still spoken of as two days
  old. `RECENCY_FLOOR_DAYS = 2`, applied in `gate` where every other field of
  the brief is normalised, and the widening is recorded in `Brief.notes` like
  everything else the gate changes. Zero is untouched: evergreen means no
  window at all, not a short one.

The prompt says why, not just what - "a search that comes back empty is the
worst outcome here, worse than one that comes back broad" - because a rule
with no reason attached is the first thing a later rewrite drops.

What this does not do is make the refusal unreachable. It makes it rarer, for
a handful of output tokens on a call already being made, which is the only
kind of latency this layer is allowed to spend.

## 111. A mix could not be given a topic anybody typed, and nothing said so

**Reported:** create a DailyFAM album, type a subject into the picker, and the
add button does nothing. "AI updates" appears as a row with a `+` on it, the
tap lands, and the topic is not added - no toast, no error, no console
message, on the one screen whose entire job is collecting topics.

**The cause is one word, declared twice.** `static/index.html` had two
top-level `function addTypedTopic()` declarations four thousand lines apart:
the DailyFAM picker's, which reads `#pickerSearch` and pushes onto
`pickerSelection`, and the interests catalogue's (§100, "the catalogue's
search can add whatever was typed"), which reads `#catalogSearch` and pushes
onto `chosenTopics`. Function declarations hoist and the later one wins, so
the picker's version was dead code from the moment the catalogue's was
written. Every tap on a typed row in the picker ran the catalogue's function,
which looked for a search box that does not exist on that screen, found
nothing, and returned on its own `if(!raw) return`.

So the failure was *silent by construction*. Nothing threw - the catalogue's
function is a perfectly good function doing exactly what it says, on the
wrong screen. And it is legal JavaScript, so `node --check` passed, the tests
passed, and the browser smoke test passed too.

**The smoke test is the more interesting half.** There was a check for this
screen, and it read:

```python
page.fill("#pickerSearch", "a topic nobody has in the bank")
assert page.query_selector(".typed-offer"), "typing offers no way to add it"
```

It asserted the control was *on screen* and stopped there. The control was
always on screen; it had just stopped doing anything. That is the shape to
watch for in every check in `tools/smoke_preview.py`: **asserting a control
exists is not asserting it works**, and the gap between the two is exactly
where a silently-inert button lives. The check now types, clicks the offer,
and asserts the selection grew, that what was added is what was typed, that
it is drawn under its own heading, that the box was cleared, and that a bank
row still toggles beside it.

**The fix is names that say which screen they serve** - `addTypedMixTopic`
and `addTypedInterest` - rather than one of them keeping the generic name and
waiting for the next collision.

**And the guard is derived** (§107's rule, applied to a different subject).
`tools/check_js.py` already parses the interface on every `./dev.sh check`;
it now also reads every `function NAME(` declaration out of the sources and
fails on any name declared twice at the shallowest indent - the top level,
where the survivor is a global and every inline `onclick` in the markup
follows it. Indentation standing in for scope is a proxy rather than a parse,
and it is deliberately narrowed to the top level: a nested function shadowing
a name is ordinary, two globals sharing one is this bug. It was run against
the pre-fix file first, and it finds `addTypedTopic`.

This matters past one button because of how this interface is wired. Every
control in `static/index.html` is an inline `onclick` naming a global. There
is no import graph, no bundler and no linter that can see a name being
quietly replaced, so a duplicated top-level name is not a style problem - it
is a control that silently calls somebody else's function. The check that
catches it costs a regex.

## 112. One failure on RunPod became a whole repair process

The reported symptom was small and specific: **FAM writes the episode, Render
reaches RunPod, Chatterbox is loaded on the card - and `POST /synth` answers
404.** No audio, so the episode fails. The immediate fix is a corrected worker
image on the pod, which is half an hour.

The reported *problem* was the other thing: that fixing it takes a day, and
takes a day again the next time anything on RunPod changes.

    RunPod changes -> the address or the worker changes -> Render loses
    compatibility -> a code fix -> a Docker rebuild -> a RunPod redeploy ->
    a Render redeploy -> test

Every arrow is a human keeping two systems in agreement by hand. That is the
thing this section is about; the 404 is one symptom of it.

### Why it kept happening

The address of the voice was a **fact about the world, written down in a second
place**. `REMOTE_VOICE_URL` (or the endpoint id) was correct until RunPod moved
the pod, and then it was wrong in the one way that cannot be seen from the
app's side: the address still resolves, something still answers, and what comes
back is a 404 that reads exactly like a missing route - which is §78's finding,
arrived at again from the other end.

Three consequences, all of which cost time rather than audio:

* **Nothing knew until a listener did.** The first thing that discovered the
  mismatch was an episode failing, so the diagnosis started in the middle of a
  stream rather than at the moment the pod changed.
* **The diagnosis was spread over four consoles.** Render holds an address,
  RunPod holds a pod, the pod holds a mode and a port, and the image on it
  holds a version of this repo. No single screen answered "which of these
  moved", so the answer was found by bisecting them.
* **Every fix ended in a redeploy**, because the correction was an environment
  variable, and an environment variable is part of the deployment.

### What was built

**`voice_control.py`: the address is discovered, verified and held.**
`remote_voice.py` kept the conversation with a worker - sentences in, PCM out -
and gave up the question of *which* worker. That question is now a ladder, and
`ladder()` is its one definition, read by the runtime, `/api/health`, the
startup log and the doctor, so the documented order cannot drift from the real
one (`research.ladder()`'s rule, in a second place):

    1. pinned       REMOTE_VOICE_URL, when somebody set one
    2. registered   a worker that told us where it is, inside the TTL
    3. runpod-pod   pods on the account, matched by NAME, resolved through
                    RunPod's own API
    4. serverless   RUNPOD_ENDPOINT_ID

A rung is used because a **real call** to it came back correct, and that answer
is held for `VOICE_VERIFY_TTL` - so the synth path pays nothing on a healthy
deployment, which was the constraint that decided the shape. The check is
deliberately the cheap real call (`/health`, or RunPod's endpoint health) and
never a synthesis: waking a serverless worker to keep a health page green is a
bill for a colour.

**`voice_worker/register.py`: the pod says where it is.** Only it can. RunPod
puts the pod id in the container's environment and fronts each port at
`https://<pod id>-<port>.proxy.runpod.net`, so the address is derivable there
and guessable nowhere else. On boot and every minute after, the worker posts
its address, mode, port, contract version, sample rate, image and commit to
`POST /api/voice/register`. **A replaced pod is back in service within one
heartbeat and nobody edits anything** - which is the arrow this whole section
exists to delete.

**And the one-word mistake behind the reported 404 is gone.** The image had
`VOICE_WORKER_MODE=serverless` as its default, so a pod started without
`VOICE_WORKER_MODE=http` ran the handler and opened *no port at all* - 404 on
every path, indistinguishable from a missing route, which is §78 exactly.
`voice_worker/start.py` derives it instead: `RUNPOD_ENDPOINT_ID` means a
Serverless worker (only one has an endpoint to belong to), `RUNPOD_POD_ID`
alone means a pod and therefore a port, anything else means a port because it
is the only way in, and `VOICE_WORKER_MODE` still overrides all three. The
first line of the pod's log now says which half is running and why, because
"the container is running the wrong half" has to be visible in the pod's own
log rather than inferred from an app's 404 an hour later.

**A supervisor, so the app finds out before a listener does.** One cheap probe
every `VOICE_SUPERVISE_SECONDS`. It is the browse-surface argument applied to
infrastructure: the resolution that would otherwise happen in front of the
first listener after a pod moved has already happened by the time they arrive.

**`tools/voice_doctor.py`: the whole chain on one screen.** What this app is
configured to do, how it will look for a worker, what that search finds,
whether each candidate is really there, which build is answering, and - with
`--speak` - whether it can actually make audio. Every failure prints its fix
beside it, because the answer to "the pod is in serverless mode" is one
environment variable and nobody should have to go and find out which. It reads
`voice_control.ladder()` rather than being a checklist in a document, so it
cannot describe an order the app does not use.

**`.github/workflows/voice-worker.yml`: the image builds itself.** The contract
job runs on every push that touches the worker - seconds, no GPU - because the
two halves are deployed separately and can be different versions of this repo,
which is the failure neither side can see alone. The image build is opt-in, a
CUDA image being ~10 GB. It deliberately does not deploy: a workflow that can
replace the running voice on a push is a workflow that can take the voice down
on a typo.

### Three rules this had to obey, and one it had to not break

**Failing over is not falling back.** CLAUDE.md is categorical that a hosted
voice which fails must fail rather than become a different one, and that is
untouched. Every rung is the same `Dockerfile.voice` image, the same weights
and the same `reference_3.wav`, and a candidate whose `/health` reports a
sample rate the stream header has not already claimed is **refused** rather
than used - a wrong rate is a failure you hear. What moves is the address; what
comes out of it does not. When no rung can speak, the episode fails with the
reason attached, exactly as before.

**Never fall back silently** (§109, in a second place). Every switch is
recorded with what it moved from, why, and when, and it is on `/api/health` and
in the log at WARNING.

**A layer that adds quality must not subtract availability** (§82's rule). The
worker now reports a contract version, and a mismatch is *reported and never
refused*: an older worker that still serves `/synth` is a working voice, and
taking it away over a version number would be this layer causing the outage it
exists to prevent.

**Registration is authenticated or it does not exist.** `VOICE_REGISTRY_TOKEN`
unset means the endpoint answers 404 - not an open endpoint with a warning. An
endpoint that accepts "the voice is at this URL" from anybody redirects every
script FAM writes to a machine of their choosing, and it would look exactly
like the feature working. And a registration is a *claim*, never a promotion:
it makes a candidate, and the candidate is verified with a real call before a
listener is sent to it.

### What it does not do, deliberately

**Nothing here starts, stops, resizes or pays for a pod.** The address is
automatic; the machine is not. `.github/workflows/runpod-schedule.yml` is the
one thing that starts and stops one, on a clock somebody set - and that
schedule is why `_pods_from` records a pod it found and did not use: this
deployment stops its pod at 23:00, so **"the voice cannot be found" and "the
voice is asleep until 08:00" are different problems**, and an empty rung that
said nothing made them look the same.

*(§117 deleted that schedule. The recording stayed, and reads better for it:
with nothing stopping the pod on purpose, `EXITED` is always a fault.)*

### What is still unverified

Nothing here has made a real request to RunPod from this container, which has
no credentials and no GPU. The parsing of RunPod's answer is defensive for
that reason - REST first, because it is RunPod's current API and the one this
project's key is used against, GraphQL second, three response shapes accepted, and any
failure costs a rung and a log line rather than the voice. `python
tools/voice_doctor.py` against the real deployment is what turns that from
careful into known.

## 113. The wake was suppressed for the first minute of every machine's life

The Voice worker workflow's `contract` job failed on two tests that pass on
any developer machine:

    test_wake_does_not_synthesise_and_cannot_raise  IndexError: list index out of range
    test_wake_does_not_stampede                     expected 1 POST but got 0

Both say the same thing: `RemoteChatterboxEngine.wake()` sent nothing at all.

**The cause is a sentinel that the clock can produce.** `_woken_at` was
`0.0`, meaning "never woken", and the guard is

    if now - cls._woken_at < settings.remote_voice_wake_interval:
        return  # already asked recently

`time.monotonic()` is `CLOCK_MONOTONIC`, which counts **from boot**. So on a
machine whose uptime is under `REMOTE_VOICE_WAKE_INTERVAL` - 60 seconds by
default - `now - 0.0` is smaller than the interval, and a worker that had
never been woken read as one woken moments ago. The sentinel and a real
reading were the same number.

That is why it could only be seen on the gate. This build container has been
up for hours, so `time.monotonic()` is large and the tests pass; a
GitHub-hosted runner is a VM that boots immediately before it runs them. §106
said a green `./dev.sh check` is not the same claim as a green CI and blamed
the Python version; this is a second way that gap opens, and it has nothing to
do with the interpreter. **The build container's uptime is part of the test
environment, and nothing declares it.**

**It was a production bug, not a test bug.** Every app container spent its
first sixty seconds declining to wake the worker - which is exactly the window
the wake exists to cover, since a cold start is most likely on a process that
has only just started. And it failed in the one way that leaves no trace:
`wake()` never raises, never blocks and logs only at debug, because a failed
wake must not be able to cost an episode. So the feature was off at the moment
it was worth the most, on every deployment, and the only symptom was a cold
start somebody would have blamed on RunPod.

The fix is `float("-inf")`, which is not a reading that clock can return, in
the class and in the test helper that resets it. The generalisation is worth
more than the fix: **a sentinel must come from outside the range of the thing
it stands in for.** `0.0` is a valid monotonic reading, `None` and `-inf` are
not - and the same trap is waiting for any "never happened yet" written as a
zero timestamp.

`test_wake_fires_on_a_machine_that_has_only_just_booted` pins it by faking a
twelve-second-old clock, because the condition cannot otherwise be reached on
a machine that has been up long enough to run the suite.

## 114. A share link that was correct and useless, and three other things nobody could see from inside

Six items in one packet, and four of them share a shape worth naming before
the individual fixes: **the app was behaving correctly against a question
nobody was asking.** A relative URL is a correct URL. A profile page drawn
for a guest is full of true statements. A rail filled from what was left over
is filled in the order it was told to fill. Seeded demo data is data. Each of
these was working exactly as written and answering the wrong question, and
none of them can be seen from inside the app - which is why they all arrived
as a listener's report rather than as a failure.

### The link did not work, and had never worked anywhere

`POST /api/share` returned `/s/abc123` on every deployment, because
`_share_url` read `PUBLIC_BASE_URL` and **nothing anywhere prompts for it**:
not `render.yaml`, not the Dockerfile, not the first run, not the health page
beyond a boolean. So the setting was unset, and unset was handled - honestly,
even, with `public: false` and a sentence in the share sheet saying the link
only works on this network. The feature was complete apart from the one part
that leaves the machine.

The fix is the rule `/api/health` already lives by: **measure the thing
rather than read a setting that describes it.** A request arrived, so this
server has an address at least one client outside it could reach, and that
address is in the request. `app._public_base` reads `X-Forwarded-Proto` and
`X-Forwarded-Host` (first value of each, because a forwarded header
accumulates one entry per hop) and falls back to `Host`. Behind Render's
router the connection itself is plain HTTP, so a link built from
`request.url` alone would be `http://` on an HTTPS site.

Two things it is careful about. `PUBLIC_BASE_URL` still wins when set,
because it is the only way to name a host this server is *not* reached at -
a custom domain in front of a Render URL. And a loopback or wildcard host is
refused outright and still reports `public: false`: a link to `localhost`
is worse than a relative one, because it looks like a URL, so it gets posted,
and it resolves on the recipient's own machine to whatever they are running.

`/api/health` now reports `link_host` as `env`, `request` or `none`. Three
states that were indistinguishable from outside, and only one of them used to
exist.

### Instagram and Snapchat, in two lines and three failures

    window.open(shareTargets.card, "_blank");
    toast("Card ready - add it to your story");

`window.open` on a mobile browser is a blocked popup, and a blocked popup is
silent - the toast said the card was ready and nothing appeared. When it did
open, what opened was an **SVG document in a browser tab**: neither platform
accepts an SVG, and there is no "add to story" anywhere on a tab, so the
listener's only move is a screenshot - which is the exact thing the card
exists to stop them doing. And the wording, which is the whole point of a
share, was left behind in the tab they came from.

The card is now fetched, rasterised to PNG in the page, and handed to
`navigator.share` as a **file**, which is what the platforms' own apps
accept, with the text alongside. Rasterising in the browser rather than on
the server keeps `sharing.story_card`'s reason for being SVG intact - it
needs no image library - and a browser already has one. Through a `data:` URL
rather than a blob URL, because Safari treats an SVG from a blob URL as
cross-origin and taints the canvas, so `toBlob` throws on exactly the browser
this feature is mostly used from. Where there is no file sharing it
downloads: a file on disk is a card somebody can post, an unexplained tab is
not.

### Trending showed one tile because it was filled last

Reported as "there are times when there is only one trending episode being
displayed... at all times I want there to be at least four". The cause is in
`FILL_ORDER` rather than in any source. Trending was filled **after** the four
personal rails, from what they had not claimed, and Made for you draws on the
same live pool - so on a day the pool held five stories and a listener's taste
matched four of them, the world row got one.

`WORLD_FLOOR` tiles are now set aside before anything else chooses, and the
trade is stated rather than buried: on a thin pool Made for you loses its best
live tile. That is the right way round *only* because Made for you draws on
both inventories and can never be empty - the bank is twenty-eight topics -
while Trending draws on the live pool alone and has nowhere else to go.

One refinement that matters: the reservation happens **only when it buys the
floor**. A pool of one cannot fill this row however it is shared out, so
holding that story back would take it off the personal rail and still leave
Trending short - a cost with nothing bought.

### "A random small school college football matchup I've never indicated interest in"

Two causes, and the interesting one is that the vocabulary could not express
the problem.

The first is that `_affinity` was **blind to subtags**. §80 added twenty-nine
of them precisely so a listener who plays chip episodes could be told apart
from one who plays tech generally - and the scorer summed every tag with the
same weight, so `sports` and `sports-drama` counted identically. The
resolution added to the vocabulary was being thrown away by the ranking.
`SUBTAG_WEIGHT` is the other half of that change.

The second cannot be fixed that way at all, and saying so is the useful part:
**there is no tag for the NFL and none for college football.** Both are
`sports`. No weighting distinguishes them, and nothing should pretend it
does. What is knowable without inventing a vocabulary is whether this listener
has ever *said* any of the words on the tile - `topics.familiar_words` reads
their own searches and plays out of the event log, which is a fact about them
rather than a guess about the subject. A live story whose only claim is a
whole facet and whose words they have never used is cut by
`BROAD_MATCH_PENALTY`.

It damps and never excludes, and it is applied to live stories only. A
listener one episode into the app has almost no familiar words, and a rule
would empty their rail in the name of relevance; the evergreen bank is
twenty-eight subjects chosen to be broad, so penalising breadth there would
penalise the whole inventory.

`RELEVANCE_FLOOR` is the third and bluntest of the three. `rank_from_history`
kept anything scoring `> 0`, which every tile sharing one barely-touched facet
clears - so the rail was very nearly the bank, sorted, under a heading
claiming it had been chosen for this listener.

### "What you missed last week" widened, and the fill order had to move with it

At the owner's direction the rail now draws on three things, any of which
qualifies: offered to them (the impression log, as before), played by other
listeners this week, or in the live story pool. CLAUDE.md said plainly that
there is "no top-up from the bank and a short rail is short, because the
heading is a claim about what this app did" - and the direction is that the
heading should be a claim about *the week*, which is a bigger and still true
thing. The standing bank is deliberately still not a source: an evergreen
explainer nobody was offered and nobody played did not happen last week.

Two consequences that were not obvious.

**The fill order could not simply stay.** `missed` fills first because it was
the narrowest inventory on the page. Widening it to the live pool made it
claim the brand-new story that Made for you exists to offer, and a tile that
is on the page is not one anybody missed. So trending is excluded from the
rail's first pass and it is topped up from the leftovers afterwards - trending
is the *last* source for this row rather than the first.

**A relevance floor over an empty profile rejects everything.** Impressions
deliberately never reach `taste`, so a listener who has chosen nothing and
played nothing scores 0.0 against every tile, and the rail most use to exactly
that listener would have been empty. With nothing to rank on it falls back to
what it always was: what was put in front of them, newest first. The claim
shrinks to the one the evidence supports, which is the same move §88 makes
about thin evidence.

### The profile listed everything and updated never

The pill row was chosen facets, then chosen subjects, then whatever the log
had inferred, twelve of them, in that fixed order. Two faults in one line:
six words picked in thirty seconds on the first run outranked a month of
listening for good, and twelve is not a row, it is an inventory.

`topics.ranked_interests` ranks by the same `taste` profile every myFAM rail
is built from, so it moves as they listen, and declared interests are not
lost by that - `taste` folds them in at `INTEREST_WEIGHT` before it
normalises, which is exactly a starting position that behaviour outvotes.
`topics.profile_interests` cuts it to four, pinned or top, and returns which
- because those look identical on screen and the line of copy under them is
only true of one.

The editor moved with it. "Shared on your profile" was inside Edit profile,
three taps from the row it changed, and phrased as hiding rather than as
choosing. It is now on the profile, next to the pills, and it is a choice
about **showing** and never about liking: nothing there writes `interests`
and nothing there touches the ranker.

The boundary that needed thinking about: **their own page may be ranked off
their listening and `/api/person` may not.** §104's rule is that what
somebody has listened to is theirs, and an inferred pill row on a stranger's
view of them would publish exactly that, in a form that reads as a statement
they made. A pin is a statement. A declared interest is a statement. So the
public view is the pinned set, or declared-minus-hidden, capped at the same
four - and pinning is how somebody's own page becomes their public one.

### The profile page, for somebody who has not signed up

It drew the whole thing - name, counts, shelves, vibes - with a note at the
bottom offering an account. Everything on it was true, which is why it
survived this long, and it is still the wrong screen: a profile is the one
page that is *about* having an account, so drawing a full one for a guest
invites them to furnish a room the app is about to say is not theirs. It is
now a door, and it makes no request for a profile it is not going to draw.

Under it, a second door that should never have existed. Every gate in the app
- DailyFAM, messages, Settings, the profile's own note - opened
`createAccount()` and `signIn()`, two chained modals asking for an address
and then a password. That form **cannot offer a phone number, Google or
Apple**, all of which the real screen has. So a listener who reached an
account from DailyFAM and one who reached it from the front door were being
shown two different products, and only one of them was the product.

`gateActions()` is the one pair of buttons now, and the makeshift pair is
**deleted rather than left unused** - the Piper reasoning, for the third
time: a second sign-up form left standing is one somebody wires a new gate to
by accident. `openAuth` already knew how to come back to the screen it was
opened from, which is why this is a smaller change than it looks.

And "Skip for now" on the welcome screen became **"Continue as guest"**. The
two setup steps behind it still say "Skip for now" and that is right there -
a step you skip comes back. This one does not: it is a way of using the app.

### The seed could not be taken back out

`tools/seed_demo.py` writes three invented listeners and their plays so the
browse surfaces have something to show on a fresh install. That is right while
*showing* the product and wrong while **measuring** it: every seeded play is a
vote in `topics.taste`, so "is myFAM recommending the right things" had an
unknown share of its answer coming from people who do not exist. There was no
way to undo it, and the place it is actually needed - a container host - is
the one place there is no shell.

`tools/wipe_demo_data.py` is the inverse and lives beside it.
`POST /api/admin/wipe` is the same function behind `FAM_ADMIN_TOKEN`, 404
without it. Both default to a dry run, in both directions: a request body that
forgot a field must not be the one that empties the event log.

The seed scope removes exactly what the seed wrote, identified by
`scripts.author` - which the cache already keeps so Explore can leave
somebody's own episodes off their own feed. A script with **no** author is
left alone: it was not provably seed data, and guessing is how a real
listener's episode gets deleted.

### Why a redeploy erases the listeners

The answer is yes, the databases live inside the deployment, and the code was
already correct: every store is pinned to `/data` in the Dockerfile,
`tests/test_data_paths.py` derives that list from the modules rather than
from a second hand-written copy, and `render.yaml` declares a 1 GB disk at
that mount path. What is missing is the disk itself - a service created from
the dashboard rather than from the blueprint has none, and a disk added later
needs a redeploy to take effect.

§107 built the measurement for this: `/api/health` reports `persistence` per
database from `st_dev`, so a store pointed at `/data` on a host with no disk
attached reports `image`. **It was answering to an empty room.** Nobody reads
a health page on the way past, and from outside a wiped database and a fresh
install are identical - the app comes up, the schema is created, the page is
green.

So it is said now. `_announce_storage` logs it at boot, once, and the
condition is deliberately narrow: a store is ephemeral **and** its environment
variable is set. That pair is the whole diagnosis - it means this deployment
asked for a disk and did not get one. A laptop trips neither half, which is
the point: a warning every developer sees on every run is a warning nobody
reads, which is how this one got missed.

`python tools/storage_doctor.py`, locally or `--url` against the deployment,
asks the same question on demand and prints the Render steps beside the
answer.

## 115. The board had been red for nine merges, and the cause was a font

Found while getting §114 ready to merge rather than by looking for it, which
is the whole point of the entry: **CI had failed on every push to `Main` for
at least nine merges**, always on the same assertion, and nobody had looked.
§106 wrote that exact sentence down about an earlier stretch of red - "a red
board stops being read" - and it happened again, which says the note was not
enough on its own.

The assertion was `Go Deeper titles are not cut off`, and it was **right**.
The longest thing a `<<NEXT:>>` follow-up can be - the prompt asks for six to
twelve words - wraps to four lines in Fraunces at 10.5px in the 109px column
a Go Deeper card gives it. The card was 68px with `-webkit-line-clamp: 3`, so
the fourth line was cut mid-word, on the one card type whose text the topic
bank does not control.

### Why it survived, which is the part worth keeping

**The check renders in whatever font the machine has.** This build container
has no route to Google Fonts, so Chromium falls back to a narrower generic
serif and the title fits three lines; the CI runner loads the real face and it
does not. Same markup, same viewport, same browser, two answers - and the half
that was wrong was the half a developer looks at:

    local:  scrollHeight 39, clientHeight 39   ok
    CI:     scrollHeight 53, clientHeight 39   FAIL

So `./dev.sh check` was green on a genuinely clipped headline. That is §106's
finding arriving through a second door: the first time the gap was the Python
version, and this time it has nothing to do with the interpreter at all. **The
build container's installed fonts are part of the test environment, and
nothing declares them** - exactly as §113 found about its uptime.

The check itself was already built for this and is why the cause took minutes
rather than a session: it prints what it *measured* - the font family, the
size, the line height, the two heights, and whether webfonts loaded - rather
than only which titles lost. A check that had asserted and said nothing would
have read as a flake on a machine where it passes.

### The fix, and why the number is written down

80px and a four-line clamp. The arithmetic is in the CSS beside it: four lines
at 13.125px is 52.5, plus the title's 2px margin, plus the meta line at
~9.4px, plus 15px of card padding - 79.4, rounded up. Verified by forcing a
wider face into the same card and confirming the meta line still lands inside
it, because the failure mode of getting this wrong is not a clipped title, it
is a clipped *timestamp* under an uncut one.

Written down rather than eyeballed because the next person to change the font
size, the line height or the clamp has to redo it, and because the check that
would catch them getting it wrong only fires on a machine with the font.

**What is still open**: nothing declares the fonts, so the next
font-dependent assertion can diverge the same way. The cheap answer is for
the check to fail loudly when `document.fonts.check` says the real face is
absent - measuring layout in a substituted font is not a weaker version of
the test, it is a different test - but that is a change to the harness rather
than to the app, and it is worth doing when somebody is next in that file.

## 116. The browse card answered the wrong question, and a new listener's page was four explanations

Two requests, one from each end of the app, and they turned out to be the same
mistake made twice: **a surface that was saying something true about itself
instead of something useful about the episode.**

### The card

A tile on myFAM carried a title and one line under it, and that line said why
the *rail* had chosen it — "Because of what you have played", or for the
generic rows "Playing across FAM now". Both accurate. Neither any help: a
listener scrolling a rail is deciding whether an episode is worth three
minutes, and how the ranker arrived at it does not bear on that at all.

The bank had a good one-line description for every tile all along — `subtitle`,
hand-written, twenty-eight of them — and it was drawn on the "View more"
screen and nowhere a listener actually chooses from. So the card had the
information and showed the ranking instead.

The rail's reason was not a bad idea; it was the answer to "why did we show
this?", and that question has a place. It just took the one line the card has,
on the one screen where the other question is the whole point.

**Fixed by giving the line to the episode.** `seedWhy` is `seedHook`, and the
order is `angle` (a live story's claim about today), then `subtitle`, then the
rail's reason — kept only for a tile carrying neither, which no inventory
produces today. And the twenty-eight bank subtitles are rewritten from
descriptions into hooks: "NIL money, facilities, and the new power brokers"
became "Somebody is paying. It isn't who you think."

Two details that are not obvious:

* **The line is clamped to two lines and the strings are budgeted in Python.**
  A clamp is the one failure that hides itself — an over-long hook is cut
  mid-word and nothing says so. The budget is asserted on the strings
  (`startup.MAX_HOOK`) rather than measured in a browser, because §115 is
  exactly what happens when a layout assertion depends on which fonts the
  machine running it has.
* **The rail and the "View more" screen now draw one line through one
  function.** They were two: the screen read `subtitle`, the rail read the
  rail's reason, so one tile had two different second lines depending on which
  surface was drawing it.

### The page

The second half was reported as "the app should be populated for someone who
hasn't made an account or entered their interests yet", and measuring it was
the quickest part. `build_feed` for a listener with nothing:

    from_history   0   Your first episode starts this one off.
    world_trending 0   FAM isn't connected to a live news source yet.
    missed         0   Nothing went past you this week.
    most_played    6
    followers      0   Follow some people and this fills up with what they play.

One row of content and four explanations, on the only impression of FAM that
listener will ever form for free.

**The cause is not a bug, which is why it had lasted.** Every rail is a query
over an event log; `taste` returns `{}` for an empty log; `_affinity` is
therefore zero against every tile; `RELEVANCE_FLOOR` — added in §114 for good
reasons — drops all of them. Each step is right. The rail whose heading claims
relevance *should* be short rather than padded with the least bad thing in the
bank.

So the answer is not to weaken the ranker. It is that **there was nothing to
rank.**

#### What the startup topics are

`startup.py`. Eight questions, one per facet, and each is *time-anchored*:
"the most consequential world news story of the past week, explained from the
beginning and why it matters now", not "what habit research actually shows".

That distinction is the whole feature, and the mechanism behind it is one this
project already pays for: `SEARCH_MODE=always` means **every episode is
researched on the tap** (§76). So a startup tile is a title, a hook and a
question — exactly like a story-pool tile — and its freshness is bought at the
tap rather than at page load. Three consequences, each of which ruled out an
alternative:

* **It costs nothing on the browse path.** `build_feed` may never cause a
  model call, and eight hand-written strings cannot.
* **It needs no live provider.** The story pool is the other way to be about
  today and it is off until somebody sets `GDELT=1`, which no deployment has.
  A first impression that depends on optional configuration is one most
  deployments would not have.
* **It degrades honestly.** These questions turn on something current by
  construction, so with no evidence at all `research.NoEvidence` refuses the
  episode rather than writing a stale one from memory.

**One per facet, because that is the only principled size.** `TAG_LABELS` is
the whole pickable vocabulary, and a cold start knows nothing about which of
the eight this listener wants — so complete coverage with no guess about which
is the answer. Fewer silently gives some listeners a worse first page; more is
a guess dressed as an editorial decision. Which of the eight *leads* is a
separate question and is not answered in the file: `startup_profile` orders
them by what FAM's listeners actually play, falling back to
`PICKER_DEFAULT_ORDER`, and says which — the same function and the same
reasoning §98 used for the first-run picker.

**One tag each, and it is a facet.** It is true — "the world this week" is the
whole facet, not a corner of it — and it makes the set rank evenly, because
the prior is over facets and a subtag would contribute nothing to `_affinity`
while still counting in its `sqrt(len(tags))` denominator.

#### Four things that took deciding

**A prior is not a taste, so the heading had to change.** The tiles are worth
offering and the ordering is real, but it is what *other people* play. "Made
for you" over that is the kind of claim this project keeps paying for, so the
rail stays exactly where it is and the heading becomes **Start here**.
`taste_source` carries it, and `build_section` carries it too — a startup rail
whose "View more" ran the ordinary ranker would open on the empty list the
rail exists to avoid.

**Only that rail gets the prior.** "What your friends are listening to" and
"What you missed last week" are statements about a graph and an impression log,
and a prior cannot supply either. They stay empty and keep their sentences. A
test asserts they stay empty, so the set cannot quietly spread.

**`cold` is derived, never stored.** It is `not profile`. A "they skipped the
intro" flag would have been the obvious implementation and is strictly worse:
a flag cannot say whether behaviour has since arrived. As it is, one play, one
search or one chosen interest retires the whole thing for that listener, which
is what makes it the algorithm *before* the algorithm rather than a second
algorithm beside it.

**Plays of the startup set must never feed `popular_facets`.** This one was
found while mirroring the change into the preview and is the sharpest thing
here. The prior decides what every cold-start listener is offered, and a
cold-start listener's first play is *by definition* a play of what it offered
them — so counting those plays would close the loop and the prior would spend
the rest of the deployment confirming its own opening guess. It is the same
failure the impression rule already names ("an impression must never become
taste — that is a feedback loop where the feed teaches itself its own
preferences") arriving through a different door. `popular_facets` counts the
bank, which the prior does not choose, and a test pins it.

The play is not wasted by that: it enters *that listener's* own taste in full,
which is exactly what retires the prior for them. It is only barred from
voting on what the next stranger sees.

### Two things that fell out

**`tags_for_id` was being bypassed on the play path.** `/api/audio` read
`BANK_BY_ID` directly and fell through to `tags_for_text(query)`, which
silently skipped the catalogue and the story pool as well. The startup set is
what made it matter: those queries are written for a research backend, and
"the most consequential world news story of the past week" contains not one of
the keywords `world` is matched on — so the *first* play of every new
listener, the one event that decides whether the ranker ever learns anything,
would have been logged with no tags at all.

**The feed prefetch source now warms the startup set, and the test that said
it should not is reversed.** `FeedSource` warmed nothing for a listener with
no history, on the reasoning that guessing for them is paying for a random
tile. Correct while their rail was ranked off an empty taste. The startup set
is the *most* shareable inventory in the app — eight tiles, identical for every
cold-start listener in the deployment, so one warmed brief serves all of them —
and it is the highest-value brief there is, because a cold start's first rail
is the first thing anybody ever taps. Nothing special was added for it:
`FeedSource` reads `build_feed`, which is what makes a warmed tile and a shown
tile agree.

### What is not fixed

**Trending, "missed" and the friends rail are still empty for a new listener**,
and deliberately: filling them would mean inventing a live source, a week of
impressions or a follow graph. So a cold start is now two full rails and three
honest empties rather than one and four. Fixing the remaining three is
`GDELT=1` and a second visit, not a change here.

**Nobody has heard one of these episodes.** There is no API key in this
container, so whether "the most consequential world news story of the past
week" actually produces a good three minutes is unverified — and the packet is
explicit that quality matters most here, because it is the first impression.
`python write.py "<the query>" --minutes 3` against a key is the check, and the
briefs are the first thing to read.

### Previewing it

The live preview seeds the viewing listener one completed episode, so it could
not show this state at all. The inspector has a second button — **"As a new
listener"** — which wipes and reloads with `seedMine` suppressed: the crowd's
history still seeds Explore and the most-played row, because other people's
listening is not this listener's taste, and the page opens on "Start here".

## 117. The voice was healthy, the address was right, and the edge said no

The report was as clean as this project has had. Chatterbox up on the pod;
`GET http://127.0.0.1:8002/health` inside the container returning
`ready:true, engine:"chatterbox"`; the same URL through RunPod's proxy
returning the *same healthy body* in a browser; and, from the Render
production shell, `403 Forbidden` on that exact URL. Production threw
`RemoteVoiceError: no voice worker can speak`.

Every instinct built up over §78 and §112 says that is an address problem. It
is not. **It is a path problem**, and the two look identical from the app.

### Why a browser and a server get different answers

RunPod fronts a pod's HTTP port with Cloudflare: the request goes user →
Cloudflare → RunPod's load balancer → the pod. Cloudflare's protections are
tuned for the thing that address is *for*, which is a person opening a
notebook or a web UI in a tab. A request arriving from a datacentre range
with no browser about it is exactly what those protections exist to refuse,
and they refuse it with a 403 before it is ever a request to the worker.

So the proxy URL is a **browser** address. It has been used as an **API**
address for as long as this app has had a pod, and it worked until it did
not - which is the worst way for a dependency like this to behave, because
nothing in the app changed on the day it stopped.

That is the regression, and it is a configuration one rather than a code one:
`render.yaml` still documents `REMOTE_VOICE_TRANSPORT=runpod`, the serverless
endpoint at `api.runpod.ai/v2`, which is a first-class server-to-server API
with an API key and no bot filtering in front of it. The pod migration moved
production onto `http` + a proxy URL and inherited a browser path for the
whole audio product without anybody choosing it.

### What it looked like from inside, which is the part that cost the time

    /health returned HTTP 403: <!DOCTYPE html><html class="no-js" lang="en-US"...

Two hundred characters of Cloudflare markup under a sentence saying the
*worker* answered 403, in front of a worker that was answering perfectly. The
generic `status_code >= 400` branch was doing exactly what it was written to
do and was reporting the wrong subject.

A 401 and a 403 at a worker's address are not the same problem and had never
read differently. A **401** is the worker: `REMOTE_VOICE_TOKEN` disagrees. A
**403 on a proxied address** is almost never the worker at all. `_refused()`
now says which, and on a proxied host it says what the fix is, because the
one thing an operator cannot get from a 403 is the knowledge that the machine
they are worried about is fine.

### The rule: the address that works from a server is the one to find

§112's argument was that the address of a rented GPU is not a constant, so it
must be discovered rather than typed. The correction here is that
**discovering the wrong *kind* of address is still a fact about somebody
else's infrastructure written into this app** - the ladder was finding, and
registering, and verifying, an address that a server cannot use.

A pod has a second address: RunPod maps an exposed TCP port to a public IP
and a port it chooses, and publishes both to the container as
`RUNPOD_PUBLIC_IP` and `RUNPOD_TCP_PORT_<port>`. Nothing sits in front of it.
It is derivable exactly as the proxy URL is and it changes for exactly the
same reasons, so it belongs on the ladder on the same terms: found, verified
with a real call, and never written down twice.

It is plain HTTP, and that is a genuine cost rather than a detail. The bearer
token and every sentence of the script ride on that connection in clear, and
`voice_registry.clean_url` has refused non-loopback HTTP since it was
written, for that reason. Loosening it quietly would have been the wrong
shape of fix, so **`VOICE_ALLOW_PLAIN_HTTP` is a decision somebody makes**:
off by default, named in the 403's own error message, reported on
`/api/health` as `plain_http`, and asked once - `voice_control.allow_plain_http()`
is what both the registry and the ladder read, because an address one accepts
and the other refuses is a worker that registers successfully and is never
used.

The proxy stays on the ladder below it. Failing over is not falling back:
both are the same worker image, and a pod whose TCP port is firewalled must
still be reachable.

### Two bugs found on the way that would have made the automation fail anyway

**The worker announced `$PORT`, not the port it was listening on.** This
project's pod runs the app on 8001 and the voice beside it on 8002, so
`register.public_url()` would have announced `-8001` - and the app would then
have health-checked a *web service* looking for a voice. §78 was "the port in
the proxy URL must be the port the worker listens on"; this is the same
sentence one layer up, and it means that even with `FAM_APP_URL` and
`VOICE_REGISTRY_TOKEN` set, self-registration would have pointed the ladder
at the wrong half of the container. `register.port()` now reads
`VOICE_WORKER_PORT`, then the `--port` the process was actually started with,
then `PORT` - what it was told, in the order that can be trusted.

**The nightly schedule was managing a pod that no longer exists.**
`.github/workflows/runpod-schedule.yml` carried the pod **id** as a literal,
and an id does not survive a pod being replaced. It was already wrong: the
comment above it explains the *first* time this happened and the literal
underneath it was stale again, so the 08:00 start and the 23:00 stop were
acting on a retired machine while the live pod ran unmanaged. The workflow
now resolves the pod by **name** through the same REST API `voice_control`
asks - a name survives a recreation - and **fails the run** when it cannot,
printing every pod on the account. A schedule that does nothing looks exactly
like a schedule that worked, which is the whole of the bug.

*(Superseded within the hour by §118, which deleted the schedule outright at
the owner's direction. The finding stands and is worth keeping: a literal id
in a workflow went stale twice. The workflow it was fixed in no longer
exists.)*

### And the two paths called themselves different things

`remote_voice` sent `User-Agent: FAM/remote-voice`; `voice_control` sent
whatever httpx defaults to. That is one constant now (`voice_control.USER_AGENT`,
used by both), because a difference there is a worker that passes a health
check and refuses an episode, or the reverse - a health page that lies, which
is the failure this project has lost the most time to.

### What is unverified

**All of it, against RunPod.** There is no route from this container to
`proxy.runpod.net` - the egress policy denies `CONNECT` with a 403 of its
own, which is a different 403 and worth saying so nobody bisects it later.
The Cloudflare reading is inference from the shape of the report (browser
200, server 403, worker healthy on its own port) plus RunPod's own
documentation that the proxy is Cloudflare-fronted, not something measured
here. `python tools/voice_doctor.py --url <the app>` against the running
deployment is what turns it from reasoned into known, and the direct address
is what it should find.

## 118. The schedule is deleted; the voice is up at all times

At the owner's direction, immediately after §117. The nightly workflow that
started this project's pod at 08:00 and stopped it at 23:00 is gone, and
nothing in this repository now starts, stops or resizes a pod. The voice is
expected to be running continuously.

**Deleted rather than disabled**, which is the only part of this that is a
judgement rather than an instruction. Commenting out a `cron:` leaves a
workflow that still has the account's API key, still knows how to stop the
production pod, and needs one uncommented line to do it - and this project
has now paid three times for a knob left behind (Piper's engine fell through
to itself, the cold open was turned back on by an example file and then by
`Dockerfile.gpu`, the makeshift sign-up form was wired to new gates by
accident). A voice that disappears at 23:00 for reasons nobody remembers is
exactly that failure with an audience.

### What it costs, stated rather than buried

A GPU billed by the hour is now billed for the hours nobody is listening.
That is the whole of the trade and it was made deliberately: the schedule's
saving was real and its cost was that the product was *unavailable for nine
hours a day*, which for something being taken towards an iOS app and a
listening test is the wrong side of the trade. `METERING.md` records what
Claude and Exa cost per episode; the GPU is the **fixed floor** that section
describes, and this change makes the floor a 24-hour one.

### What it buys back, beyond the hours

**`EXITED` now means something.** §112 made `_pods_from` record a pod it
found and did not use, specifically because the schedule made a stopped pod
ambiguous - a clock or a fault, and an empty rung made them look the same.
With no schedule, a pod that is found and not running is **always** a fault:
RunPod evicted it, the account ran out of credit, or somebody stopped it by
hand. The note that names it is a diagnosis now rather than a
disambiguation, and the comments that explained it say so.

### What is left behind on GitHub, and is not this repository's to remove

Two settings are now unused by any workflow, and both are harmless:

* the `RUNPOD_API_KEY` **secret**, which no remaining workflow reads -
  `voice-worker.yml` builds an image and touches RunPod not at all;
* the `RUNPOD_POD` / `RUNPOD_POD_ID` repository **variables**, added one
  commit earlier for the by-name lookup that no longer runs here.

`RUNPOD_POD` is still live and still load-bearing **on Render**, where it is
the `runpod-pod` rung of the ladder. The GitHub copy is the one that is now
dead, which is the pleasant half of this change: §117's finding was an id
kept in two places, and there is only one place left.

## 119. The ladder was built, and the engine that walks it was never built

Reported from a Render deployment carrying every commit up to §118, as two
lines that had been in the log since the pod migration:

    remote is unavailable (REMOTE_VOICE_URL is not set); serving a
    placeholder tone, not a voice
    voice supervisor: no voice worker could be found. Configured rungs:
    registered.

Read together they are contradictory, and the contradiction is the bug. The
second line says the app knows about a rung and is walking it. The first says
the app has already decided there is no voice, on the strength of a variable
§112 exists to stop anybody setting.

### The question that was asked instead of the one that mattered

`RemoteChatterboxEngine.available()` was `not cls.config().problem()`, and
`RemoteConfig.problem()` asks exactly one thing: **is an address written down
in this environment** - `REMOTE_VOICE_URL` on the `http` transport,
`RUNPOD_ENDPOINT_ID` on `runpod`. That was the whole of the question until
§112, when finding a worker stopped being the same thing as being told where
one is.

The ladder was then wired into the synth path - `_endpoint()` resolves
`voice_control.current()` when the configured address is incomplete - and into
`/api/health`, the startup log and `tools/voice_doctor.py`. It was not wired
into the question that decides whether the engine exists at all. So:

    build_engine()
      -> production_engines()            # (RemoteChatterboxEngine,) - correct
      -> RemoteChatterboxEngine.available()
           -> config.problem()           # "REMOTE_VOICE_URL is not set"
      -> None
      -> PLACEHOLDER_ENGINE()            # a tone

`_endpoint()` is downstream of every one of those arrows. **The code that
walks the ladder is inside the engine that was just ruled out**, so on a
deployment configured the way `REMOTE_VOICE.md` documents - one
`VOICE_REGISTRY_TOKEN` on the app, `FAM_APP_URL` and the same token on the
pod, and deliberately no pinned URL - the registered rung could never serve
anybody. Not because anything on it was broken: a worker could register, the
supervisor could verify it, `/api/health` could report the ladder green, and
every listener would still get a 220 Hz sine.

That is the shape §117 had one layer down, and it is worth naming as a class:
**a mechanism is only built where every caller reads it.** The ladder has one
definition and four documented readers. The fifth reader - the one that
decides whether there is a voice - was still reading the variable the ladder
replaced, and nothing failed, because falling back to a tone is a supported
state.

### The fix, and why being generous here costs no honesty

`diagnose()` asks the ladder. If the configured address is complete it is used
as before with no probe; otherwise, if any rung is configured, the engine is
available and says which rung will be asked (`24000 Hz, address discovered:
registered`); if nothing is configured and nothing is discoverable, it is the
tone, as it should be.

The worry this had to clear is CLAUDE.md's rule that there are exactly two
honest states, Chatterbox or a tone that says so. It clears it because
`available()` has always meant *configured well enough to try* and never
*reachable*: §52's two questions stay two, and the second is still answered by
a real call and reported separately in `remote_voice.report()` and
`voice_control.report()`. What changed is only which configurations count as
worth trying. When nothing on the ladder can speak, the episode now fails with
the reason attached - which is what `REMOTE_VOICE.md` already said happens,
and is more honest than a tone, because a tone is indistinguishable from a
worker that is speaking badly.

`RemoteConfig.fatal()` is the half discovery cannot answer, and it is split
out rather than folded in: a transport that is not a transport and a sample
rate that cannot be a rate are wrong wherever the worker turns out to be. The
rate especially - it goes into the stream header before any audio exists, so
claiming a voice on the strength of a ladder would only move the failure
later.

A second deployment shape was fixed by the same change, unasked: the transport
defaults to `runpod`, so setting `REMOTE_VOICE_URL` for an always-on pod and
nothing else used to report `RUNPOD_ENDPOINT_ID is not set` and serve a tone.
The pinned rung can serve that, and now does.

### And a refusal was only ever told to the pod

Found while reading the other half of the report - why nothing had registered.
Everything `POST /api/voice/register` rejects is raised as a 401 or a 422, and
the detail travels in the response body to a GPU on somebody else's network.
Nothing was logged on this side. So a pod heartbeating every sixty seconds and
being turned away every sixty seconds is, from the app's logs, identical to no
pod at all - and the only symptom is the supervisor's "no voice worker could
be found", which names the rung and not the reason.

That is §51's rule (failures must be visible) with the twist that the failure
was visible, to the one party that could do nothing about it. Both refusals
are now logged at WARNING: the address and the reason for a 422 - which since
§117 is routinely `VOICE_ALLOW_PLAIN_HTTP=0` refusing the direct TCP address a
pod correctly announced - and, for a 401, the name of the variable whose two
copies disagree and neither of the two strings.

### The storage half of the same report, which is not a code problem

The second thing in that log is `_announce_storage` doing its job:

    STORAGE: 12 database(s) are inside the container image and a redeploy
    will erase them

§114 built that line for exactly this deployment and it is accurate. It is a
Render service created outside the blueprint, so `render.yaml`'s disk was
never attached - the `/data` paths come from the `Dockerfile`'s `ENV`, which
is in the image, and the disk that should be under them is not. The fix is in
the dashboard (add a disk, Mount Path `/data`, redeploy), and the app already
says so in the line after it.

Worth noting where the two halves of this report meet: `VOICE_REGISTRY_DB` is
on `/data` like everything else, so on that deployment **every redeploy also
empties the registry of workers**. A live pod re-registers on its next
heartbeat, so it is self-healing within a minute - but it does mean the
supervisor's first pass after a deploy reliably finds nothing, which is what
the reported log line is timestamped one second after boot.

### What is unverified

Nobody has heard this. The fix makes the engine exist on a deployment that
discovers its worker; whether that worker then speaks is the listening test
open problem #1 has been waiting on since Piper was removed. What can be said
from here is that the tone is no longer guaranteed by construction.

## 120. FAM discovered its own front door and health-checked it

`RUNPOD_API_KEY` reached Render, the `runpod-pod` rung woke up, RunPod's API
answered `200 OK` - and the voice still failed, now on a different line:

    GET https://<pod>-8001.proxy.runpod.net/health -> 404 Not Found

The pod runs two things. FAM on **8001**, exposed over HTTP; Chatterbox on
**8002**, exposed over TCP. So the address the ladder discovered, verified and
reported as a dead worker was **this application's own front door**, reached
the long way round through Cloudflare. The 404 is the app being honest: it
serves `/api/health`, not `/health`.

### Why the port was wrong, twice over

`_http_port` ended in `return http_ports[0] if http_ports else 0`. Two
separate failures met in that line.

With `VOICE_WORKER_PORT` unset it defaults to **8001**, so `preferred in
http_ports` matched - correctly, by the rules, and on the wrong service. The
default is right for a pod running only the voice, which is what
`Dockerfile.voice` serves on `${PORT:-8001}`, and it is exactly wrong on a pod
that also runs the app.

And setting it would not have helped. With `VOICE_WORKER_PORT=8002` the
configured port is not among the pod's http ports, so the fallback returned
`http_ports[0]` - **8001 again**. An operator who diagnosed this correctly and
fixed their configuration would have got the identical 404 and concluded the
port was not the problem.

That fallback is the bug proper, and it is §78 one layer up. §78 was the
*worker* announcing `$PORT` instead of the port it was listening on. This is
the *app* picking a port from a list, when the only thing that makes a port
the worker's is that the worker is listening on it. **A port chosen by
something that cannot know is a guess, and this one arrived dressed as a
discovery** - with a URL, a rung name and a reason in words.

So it no longer substitutes. A configured port that the pod does not expose
over HTTP returns no candidate and a sentence naming both numbers. An
unconfigured port still takes the single exposed one, because "nothing was
said about the worker's port" is a different state from "a port was named and
the pod does not have it", and the candidate is verified before anyone is sent
to it.

### And the address that would have worked was passed over in silence

`_direct_endpoint` looks for `VOICE_WORKER_PORT` among the pod's TCP mappings.
Looking for 8001 on a pod that maps 22 and 8002, it found nothing and returned
`None` - saying nothing at all. So the one address on that pod with no edge in
front of it, the rung §117 was written to add, was skipped without a word, and
the log showed only a proxy URL being probed.

It says so now, and the note carries both halves of the diagnosis: which ports
the pod publishes, and which one this app was told to look for. Three states,
each naming the next thing to set:

    VOICE_WORKER_PORT=8001 (the default)
      -> pod fam-voice publishes TCP port 22, 8002; VOICE_WORKER_PORT is 8001,
         so none of them is the worker's. Set it to the port the worker is
         listening on
    VOICE_WORKER_PORT=8002
      -> pod fam-voice publishes 203.0.113.7:41234, which needs no proxy, but
         VOICE_ALLOW_PLAIN_HTTP is not set so it is not offered
      -> pod fam-voice exposes http port 8001 and not 8002 ...
    VOICE_WORKER_PORT=8002, VOICE_ALLOW_PLAIN_HTTP=1
      -> http://203.0.113.7:41234

The 404 verdict names the cause it now knows about: on a pod that runs
anything besides the voice, the likeliest reading of a 404 is not a broken
worker but **a different service answering**.

### The general form, which is this seam's third instance

§112 said the address of the voice must be discovered, not written down twice.
§117 said the discovered address has to be one a *server* can use. This one
says: **a discovered address is a host and a port, and the port is as much a
fact about somebody else's machine as the host is.** Discovery got the host
right and kept guessing the port.

The honest fix for the port is the same as for the host, and it already
exists: **a worker that registers itself announces the port it is actually
listening on** (§117 fixed `register.port()` to read that rather than `$PORT`).
That is the only place the fact is known instead of declared. `RUNPOD_POD`
plus `VOICE_WORKER_PORT` is the fallback for a pod that cannot reach this
service, and it is now declared in `render.yaml` with the reasoning attached -
because nothing anywhere prompted for it, which is §114's finding about
`PUBLIC_BASE_URL` in a second place.

### What this does not fix

The deployment that reported it is running `Main`, which does not carry §119 -
so even once the ladder finds the worker, `build_engine()` still returns the
placeholder tone, because `available()` is still asking whether an address is
configured. The two are independent and both are needed: §119 makes the engine
exist, §120 makes the ladder find the right port. Neither has spoken on a real
machine from this container.
## 121. Three signals collected and discarded, a question never asked, and a vocabulary with a ceiling

Four changes, asked for in this order and done in it. They are one entry
because they are one argument: **the recommender's inputs were poorer than
anybody thought**, and three of the four turned out to be about data the app
was already collecting and not reading.

### 1. `share` was recorded and dropped, and two more were never recorded

`app.py` has written a `share` event every time somebody sent an episode to a
person since the messages feature shipped:

```python
EVENTS.record(topics_mod.Event(
    user, "share", "", req.query, topics_mod.tags_for_text(req.query)))
```

`EVENT_KINDS` never contained `"share"`. `EventStore.record` checks the kind,
logs `ignoring unknown event kind 'share'`, and returns. **Every one of those
rows has been discarded**, for as long as the feature has existed.

Nothing failed. Nothing was slower. The log line went to a logger nobody
tails, and from outside, an app that records a signal and an app that drops it
are the same app.

Looking for the others found two more, and they were worse in a quieter way:
**a vibe and a save were never recorded at all.** `/api/echo` writes a row in
`social.db` and `/api/saved` writes one in `saved.db`, and neither told
`topics.py` anything. So all three of the things somebody can do about an
episode *after hearing it* - send it to a person, post it to their followers,
keep it for later - were invisible to the taste model, while a *search* (a
passing thought, typed before hearing anything) was worth 1.0.

All three are now in `EVENT_WEIGHT`, between a play and a completion.
`ENDORSEMENTS` names the set, so a fourth has one obvious place to be added.
`share` and `vibe` weigh the same on purpose: one goes to a person and the
other to followers, and a rule making either worth more would need a number
nobody has any way to tune - the same call `social.circle_of` already makes
about a mutual follow.

**The general shape, which is worth more than the fix:** a write that can be
silently refused by a validator is a feature that can be absent and look
present. The kinds are now a `frozenset` derived from `EVENT_WEIGHT` plus
`IMPRESSION`, so a weight and a kind cannot disagree.

### 2. The click-through data existed and nothing had ever read it

The feed has always answered one question - *will this listener like this
tile* - with `_affinity` over their taste profile. It has never answered the
other one: *will they actually tap it*. The only thing in the system that
read an impression was `fatigue`, and fatigue can only say **no**.

The notable part is that **the data needed no collection**. Every impression
row has carried `(user_id, topic_id, section, algo, at)` since impressions
shipped, and every play carries `(user_id, topic_id, at)`. Joining them is a
click-through rate, thirty days deep, per tile and per rail and per version of
the ranking. Nothing in `tools/` had ever queried it.

`tools/ctr_report.py` does now, and three things it got wrong on the way are
the interesting part:

* **It counted the wrong unit.** One row per (listener, tile) over the whole
  window scored a tile offered on nine separate days and taken on the tenth
  as a tile that converts. The unit is the **occasion** - one
  `FATIGUE_BUCKET` hour - which is the unit `fatigue` already counts, so the
  positive and negative signals cannot disagree about what "shown" means.
* **`--by topic` read a key that does not exist.** The rows carry `topic_id`
  and the dimension was spelled `topic`, so `row.get(dimension)` returned
  `None` for every row, grouped the whole feed into one nameless bucket, and
  printed the global rate as though it were a per-tile finding. It did not
  raise. A `FIELD` map replaces the `.get`, because the failure mode of a
  wrong key here is a plausible wrong answer rather than an error.
* **It printed `0%` where it meant "no data".** `prefetch.py` learned this
  already: zero out of zero reads as failure and is actually silence.

`ENGAGEMENT_WEIGHT` is the ranking half. It is **global per tile, never per
listener**, and that is a decision rather than a shortcut: per (listener,
tile) a listener sees a given tile a handful of times ever, and per (listener,
facet) would be a second, noisier copy of `taste`, which already reads their
plays. What is left is a property of the *tile* - does this title get tapped -
which is dense, and one computation for the whole deployment.

The danger, written into the constant so it stays a decision: **an engagement
term optimised alone converges on whatever is most clickable for everybody**,
which is a row myFAM already has and deliberately keeps separate. Four things
hold that line - it is bounded to [0.6, 1.5]; a tile with no data scores
exactly 1.0 (shrinkage toward the feed's own average, so a new tile is not
buried for being new); it reaches two rails only; and `ALGO_VERSION` moved, so
`--by algo` can say afterwards whether it helped.

The two rails it does **not** reach are worth naming. `rank_most_played` is
what everybody plays, and adding what everybody taps would make it that twice.
`rank_missed` would be counting one event from two angles: every tile on that
rail is one this listener was already shown and did not take.

And it nearly took the browse page down. `engagement_for` read `store.path`
before anything else, and `test_a_broken_event_store_never_breaks_the_feed`
constructs a store with no `__init__` - so a ranking nicety became an
`AttributeError` on `/api/myfam`. The test was already there and caught it
first time, which is the whole argument for it existing.

### 3. Location: stored, typed, and allowed to matter in exactly two places

FAM had no notion of where anybody is. Not a column, not a header read,
nothing.

**Typed rather than sensed**, which is the decision worth recording. All three
routes work - IP lookup on the server, the browser geolocation API,
CoreLocation on the phone - and the typed one is primary because it is the
only one that is *stable* and *correctable*: an address derived from an IP
changes on every train journey and a listener's interests do not, a permission
prompt in front of a product somebody has not heard yet costs more than it
buys, and a location nobody can see is a guess about where somebody is. IP is
worth adding later as a **suggested default for that field**, shown and
editable, never a silent value.

Three free-text columns on `preferences`, validated against nothing. A country
list is a closed vocabulary and would be defensible on its own, but "United
States" and "USA" and "U.S." are the same answer, and a partial validation
that accepts one spelling and refuses the next fails in front of the listener
while looking like a rule.

It reaches the ranking through **two separate mechanisms**, not one weighted
score - the same shape as "recency filters; credibility sorts":

* The **free half**: a listener's city and region join `familiar_words`, so a
  story about their own town stops being damped by `BROAD_MATCH_PENALTY` for
  being a subject they have never typed into a search box. Living somewhere is
  a complete answer to the question that penalty is asking.
* The **paid half**: `LOCAL_BOOST` on a live story that names their place.

**The country is stored and deliberately never ranks.** Boosting every story
about the United States for every listener in the United States is not
personalisation, it is a different global sort order, and the page already has
two rails for what everybody is playing.

The best use of it turned out to be the **cold start**, which is also the
place the analysis predicted: `startup.LOCAL_TOPIC` is a ninth question -
"What Changed in {place}" - offered to somebody we otherwise know nothing
about. It goes *through* the startup sort rather than jumping it, with
`LOCAL_BOOST` applied, so fatigue still reaches it: a tile that leads
unconditionally leads forever, and somebody shown local news six times without
tapping it has told us something.

Two things found while wiring it:

* **`su-local` must be resolvable and must not be offerable.** It was put in
  `STARTUP_BY_ID` so `tags_for_id` could answer for it months later - a play
  on a startup tile is the first real thing the ranker learns, and an id it
  cannot resolve falls through to a keyword sweep of a question naming a city,
  which matches nothing. But `STARTUP_BY_ID` and `known_topics` are also read
  to *draw* a tile, and the template's title still says `What Changed in
  {place}`. `test_view_more_opens_on_the_same_ranking` caught it immediately.
  They are two different questions and the template answers only the first.
* **`build_section` had already diverged from the rail it opens.** It never
  passed `familiar`, so a live story damped by `BROAD_MATCH_PENALTY` on the
  rail was undamped on the "View more" screen behind it - a pre-existing bug,
  invisible because both orderings look plausible, in a function whose one
  documented rule is that the two must not disagree.

### 4. The vocabulary had a ceiling, and it was the binding constraint

`topics.py` has always ranked in a hand-written vocabulary: eight facets,
twenty-nine subtags, thirty-seven keyword lists somebody typed. Two levels,
and every word of it in a Python file.

The evidence that this was the real limit is already in that module. **Three
separate mechanisms exist purely to work around things the vocabulary cannot
say**: `SUBTAG_WEIGHT`, because matching `sports` and `sports-drama` counted
the same; `BROAD_MATCH_PENALTY`, because there is no `nfl` tag and no
`college-football` tag and no weighting can distinguish them; and
`familiar_words`, which **gives up on the vocabulary entirely** and reads the
listener's raw searches. When the fix for "recommend me better" is to stop
using the vocabulary, the vocabulary is what needs fixing.

`categories.py` is a tree with **no depth limit**, minted from three things
the app already collects: what listeners searched for (`events.text`), the
live pool's own story subjects, and what people typed into the interests
catalogue. `sports -> american football -> nfl -> cincinnati bengals` is four
levels, and `topics.tag_weight` scores the leaf 5.4x the root because it is
that much more specific a claim about an episode. `SUBTAG_WEIGHT` turns out to
be exactly this at one level, and is now defined as it.

The hierarchy comes from two places and the keyless one always runs:

* **Keyless**: a phrase's parent is the facet its own sightings were tagged
  with, deepened by containment against nodes already in the tree.
* **With a model**: one call per sweep for the whole deployment, batching
  every new subject, returning a *path* rather than a parent - which is the
  only way the levels **nobody typed** exist. No amount of reading what
  listeners wrote invents "American Football". No key means a real, shallower
  tree and every node records which half placed it, which is
  `episode_intelligence`'s rule applied to a vocabulary.

### The failure that made the first tree useless, and would not have been guessed

**N-gram explosion.** Every sub-span of a phrase is seen by exactly the people
who saw the phrase, so a `MIN_LISTENERS` threshold clears ten times over for
one four-word run and mints ten nodes, nine of them fragments. Eight seeded
queries produced **thirty-nine nodes**: `reserve interest rate`, `league
title`, `rate decision`, `interest rate decision`, and so on down.

A threshold on *listeners* cannot fix this, because the fragments have exactly
the listeners the real subject has. Two filters did, and took the same eight
queries to **four**:

* **`MIN_TEXTS`**: a phrase must appear in more than one *wording*. People ask
  about a subject several ways - "what the fed said about inflation", "the
  federal reserve rate decision" - and about a fragment only one, because a
  fragment is a piece of a sentence rather than a thing anybody is interested
  in.
* **Subsumption**: a phrase whose listener set *and* wording set are identical
  to a longer phrase containing it is the same subject with a word missing.
  Keeping it would put a node in the tree that can never match anything its
  parent does not already match.

And `chips` had to be refused: it is a *subtag slug* as well as a phrase six
listeners typed, and minting it would put two entries under one key in the
profile `taste` builds - the subtag's and the category's - so an episode about
chips would count as two different things being relevant to somebody.
`_reserved_slugs` normalises the whole hand-written vocabulary, because a
subtag slug is hyphenated and a phrase never is.

### The rule about history, which was the last thing to get right

**A node never rewrites history, and history is re-read anyway.** Events keep
the tags they were written with - the log stays append-only and an old feed
stays reproducible - but `taste` now re-matches each event's own *text*
against the current tree. Without that, a node minted today would be worth
nothing to anybody already here until they went and searched again, and a
vocabulary that takes a month to pay off is one nobody keeps.

### What is not known

**None of this has been run against a real event log.** The trees above were
grown from synthetic seeds, the placer has never made a real call from this
container, and the click-through report has never seen a real impression.
`python tools/categories_report.py --tree --dry-run` spends nothing and says
what a deployment's vocabulary would become; `python tools/ctr_report.py`
says what its rails actually convert at. Those two commands against the
running deployment are what turn this entry from reasoned into known.

## 122. Checking the work found four things the tests could not

§121 shipped green: 2,289 tests, `./dev.sh check` twice, CI green on the
branch. Then the instruction was to actually *run* the things §119 said to run
and to double-check the work, and that turned up four defects - three of them
introduced by §121 itself and none of them visible to any test in the suite.

They have one shape in common, which is the reason this entry exists: **every
one of them was invisible at the scale the tests run at.** A unit test builds
a tree of four nodes and a corpus of eight queries. All four failures need a
realistic size to appear at all.

### 1. The one tool you were told to run would not have told you

`tools/storage_doctor.py` is what answers "does a redeploy erase this
deployment's listeners". Run for the first time after §121, it listed **twelve**
stores. There are fourteen. `categories.db` - a brand new store, holding the
ranking vocabulary the whole change is about - was not in it.

The guard was there and passed. `tests/test_data_paths.py` asserted:

```python
reported = {entry["name"] for entry in body["databases"]}
assert reported == {"scripts", "events", "social", ...}   # twelve, typed out
```

That is **this file's own §107 finding, made inside the test written to
enforce it**: "two lists somebody types agreeing with each other is the same
mistake made twice and then compared to itself." The health report's list is
hand-written in `app.py`; the test's list is hand-written in the test; they
agreed, and both were wrong.

It is derived now - against `DECLARED`, the `data_path(...)` calls read out of
the modules - with `LAZY_STORES` naming the one store legitimately absent and
why. Verified by deleting the entry and watching it fail.

The general form, which is worth more than the fix: **a guard that enumerates
its subject by hand is decorative, and writing a test does not make it
otherwise.** The only guards that hold are the ones whose subject is derived
from the thing they are guarding.

### 2. Ninety-one seconds of blocked event loop, once an hour

`categories._drop_subsumed` compares candidate phrases pairwise to drop the
ones that are a longer phrase with a word missing. On the corpora the tests
use - a few hundred phrases - it is instantaneous.

`subject_texts` caps a sweep at 20,000 texts. Measured at that size: **52,930
phrases, 2.8 billion comparisons, 91.6 seconds.**

That is not a slow sweep. `categories.sweep` is started by
`asyncio.create_task` from `/api/myfam`, so it is ninety-one seconds of
**blocked event loop**: every episode, every browse page, every request on
that worker, stopped dead, once an hour, with nothing anywhere saying why.

The rule makes the fix free. Subsumption requires *identical* support, so two
phrases with different support can never be paired and never need comparing.
Bucketing by support first: **346 ms**, a 265x change, and the same answer -
checked against the pairwise version on forty random corpora rather than
assumed.

**And the structural fix matters more than the algorithmic one**, because the
next rule added to `promote` will not necessarily be cheap either:
`asyncio.to_thread` now keeps that pass off the loop. A background sweep that
shares a thread with the product is not a background sweep.

### 3. A property that looked free, called a million times

`taste()` is called on every browse page. With §121 it re-reads each event's
text against the category tree, and against a 1,500-node tree that measured
**134 ms**.

Profiling found one line: `Node.words` is a `@property` computing
`frozenset(self.id.split())`, and `match` called it once per candidate node -
1,052,646 times per `taste()`. `ancestors()` was the same mistake smaller,
re-walking a node's parents on every hit.

Both are precomputed at `reload` now. That took it to 53 ms; the rest was the
index, which stored each node under **every** one of its words, so a six-word
question's candidate set was very nearly the whole tree. Each node is now
indexed under its **smallest** word, which is complete rather than a
heuristic - a node matches only when every one of its words is in the text, so
a matching node's smallest word is necessarily in the text. **27 ms.**

The completeness argument is one line and easy to break (indexing on "the
first word" or "the longest word" passes every other test in the file), so it
is checked against a brute-force scan over sixty random texts.

### 4. A race introduced by the fix for #2

Moving `promote` into a worker thread made an existing latent bug reachable.
`reload()` swapped four structures in two statements:

```python
self._nodes, self._index, self._words = nodes, index, words
self._ancestry = {...}                       # <- a second statement
```

`match` indexes `_words` and then `_ancestry`. A request thread landing
between those two statements sees a node in the first and not in the second
and raises `KeyError` **on a browse page**.

Three changes, and the third is the one worth keeping: all four structures are
assigned in **one** tuple assignment, which cannot be observed half done;
ancestry is computed from the local `nodes` rather than through
`self.ancestors`, which would read `self._nodes` and make the result depend on
how far through the swap it is; and `match` reads the structures into locals
once, because the assignment being atomic does not make *two reads of it* one
read.

### And two smaller ones

`import re` inside a property on the ranking path, hoisted. And the category
tree was not in `conftest.py`'s store isolation - so tests wrote a
`categories.db` into the project root and, because the tree is reached through
a **process-global** cache, a tree minted by one test was still ranking the
next one's feed. A developer who had run `tools/categories_report.py` locally
would have got different results from CI, which is exactly the class of thing
that file exists to prevent, arriving through a module-level cache instead of
through the environment.

### What this says about the tests in §121

They were not bad tests. They pinned every rule the feature has, and every one
of them still passes. What they could not do is notice that the feature is
**fast enough to run where it runs**, because nothing about correctness says
how big the input gets.

So three of the tests added here assert a *bound* rather than a result - a
full 20,000-text window finishes, `taste` against a 1,200-node tree finishes,
the sweep is off the loop. They are deliberately loose: they catch a change of
complexity, not a millisecond.

**Nothing here was found by reading the diff.** All four came from running the
thing at a realistic size and measuring it, which is the same lesson §52
records in a different register: verify, do not inspect - and a test that
never runs at production scale is inspecting.

## 123. Three controls that were each right about something nobody asked

Three reports off a phone, and the interesting thing is that none of them is a
broken control. Each is a control telling the truth about a question next to
the one in front of it.

### The (+) after a search in a DailyFAM picker did nothing — again

Reported as "still not working", which is the right word: §111 fixed a
different cause of the same symptom. There, two top-level functions shared the
name `addTypedTopic`, the catalogue's copy won, and the row rendered and did
nothing. That is fixed and stayed fixed — logged in, the whole flow works, and
a browser driving the real server creates the album with a typed topic and a
bank topic in it.

Logged **out**, there is nothing to tap at all. `loadMixes` asks two endpoints
at once:

```js
Promise.all([ fetch("/api/mixes"), ... fetch("/api/topics?ranked=1") ])
  .then(function(res){
    if(res[0].locked){ renderMixesLocked(); return; }   // <-- returns here
    ...
    topicBank = res[1].topics;
```

`/api/mixes` answers 401 to a listener without an account, by design: a mix is
one of the things an account is *for*. `/api/topics` is not gated and had
answered perfectly — and the locked branch returned before taking it. So
`topicBank` stayed `[]`, and `renderMixPicker` opens with:

```js
if(!topicBank || !topicBank.length){ ... "Could not load the topic list" ... return; }
```

Which means the typed offer — the row whose whole job is "anything you type is
a valid topic" — is never rendered. Search it and nothing offers itself. The
(+) after a search had nothing to add because there was nothing on screen to
add.

**The bug is the sentence, not the gate.** A fact about the listener's account
was reported as a fact about the server, in a message that sends somebody to
look at their connection. It is §89's rule on the browse surfaces — an empty
row is a fact about this deployment and never a claim about the world — one
screen over, and §119's shape as well: a mechanism ruled out by a question
asked one layer up, so the code that would have worked never runs.

Two lines move, and the bank is taken before the branch. A test reads
`loadMixes`'s own source and fails if they swap back, because the failure is
silent: the picker renders a plausible sentence and nothing throws.

### And the (+) that opened it should not have been there

Separately reported, and the same subject from the other end: the "new mix"
button in the DailyFAM header was in the markup unconditionally. Tapping it
opened a naming modal, then a whole topic picker, and refused only at the
save — two screens to say "you need an account for this", when the screen
behind it already says exactly that with a Sign up button on it.

A control with nothing behind it is worse than no control, which this project
has now applied to a demo-only search bar, a toast-only transcript toggle,
three invented contacts and a folder chip nobody had filed anything into. It
is hidden until `/api/mixes` answers, and shown by `renderMixList`, which runs
only when there is an account to keep a mix in. **Hidden to start rather than
shown and taken away**: a control that appears and then vanishes reads as a
fault.

One fact decides the whole screen. The "+" and the body were about to be two
reads of one answer — `renderMixesLocked` for the body, `AUTH.authenticated`
for the button — and that is §104's finding (two things deciding one state
is one bug wearing several symptoms) waiting to happen.

### Search opened on three minutes and generated two

`selectedLengthMinutes` is 2, with a comment saying why. The markup printed
`3 min` in five separate places: the search chip, both modal rows, and both
playback pills. So a listener landed on search reading "3 min", opened that
very control, and found **2 min** ticked as their current choice — the
interface disagreeing with itself about one setting, in two elements a tap
apart.

Nothing was broken. The number was settled in one place and copied into five,
which is `.env.example` against `config.py` (§54) in a different file: a value
is settled only where it is copied. The literals are painted over at boot by
`paintLengthControls`, the length menu delegates to it rather than keeping its
own list of where the number is printed, and a test pins each literal to the
variable so they cannot drift apart again.

The speed placeholders had the same crack, smaller: the markup said `1×` and
`pillText` writes `1x`, so the pill changed character the first time anything
repainted it.

**The playback pills are deliberately not pinned to the default.** They name
the length of the episode that is *playing*, which is a different question and
may honestly differ — the first draft of the smoke check asserted otherwise and
failed against an episode legitimately running at seven minutes.

### One thing found while writing the checks

`page.evaluate("window.fetch = window.__realFetch;")` hands Playwright the
function as the expression's value to serialise, and the failure it raises is a
`TypeError` naming `fetch` with a fetch stack — which reads exactly like the
thing the check is testing. An arrow function with no return value is the fix.
Worth writing down because the misleading part is not the mistake, it is that
the error impersonates the subject.

## 124. A blank slate that was not blank: the vocabulary outlived its own source

A deployment carrying episodes from many iteration cycles had to be taken
back to what the interface looks like before any of it existed.
`tools/wipe_demo_data.py --all` is that tool and has been since it was
written - the whole script cache, the whole event log, the seeded listeners
and the live story pool.

It left one thing standing, and it was a **ranking input**.

### What was wrong

`categories.py` (§121) is a vocabulary the app grows for itself, minted from
what listeners search for. It lives in its own store, so nothing in the wipe
touched it. But it is not *stored data* in the sense the wipe was written
around - it is **derived from the event log the wipe empties**, and `taste`
re-reads each event's own text against the current tree.

So after a full wipe the deployment held a vocabulary of subjects minted from
episodes nobody can play any more, ranking a feed built from an empty log.
Nothing on the outside said so: the wipe reported success, the counts it
printed were all correct, and the tree is not a row anybody counts.

Two smaller versions of the same thing came with it. `topics.category_tree`
caches the store **per process**, so clearing the table without dropping that
handle is a wipe that reports success and changes nothing until the next
restart - the destructive operation silently half-applied, which is the exact
failure `demo_data.py` is written against. And the engagement table (§121's
click-through rate) is held in process for `ENGAGEMENT_TTL`, computed from
the impressions and plays being deleted one line above it.

### The fix, and the rule under it

`CategoryStore.clear()` beside `prune()` - a different operation, named
differently: `prune` drops what has gone quiet and keeps the tree standing,
`clear` is for a deployment going back to before anything was listened to.
`_forget_what_the_log_taught()` calls it, drops the module-level handle and
resets the engagement cache, and never raises: a wipe that emptied the log
and then threw would be the worst outcome available.

The generalisation, which is why this is written down rather than just
fixed: **a wipe has to enumerate what is derived from the thing it empties,
not only what is stored beside it.** Stores are easy - `storage_doctor`
lists them and §107's test derives that list rather than typing it twice. The
things that are neither a store nor a row are the ones that survive: a
vocabulary, a warm handle, a cached table. Each of those went on affecting
what a listener is shown, after an operation whose entire purpose was that
nothing should.

The dry run counts the vocabulary now, because it is the one item on the list
nobody expects to be on it.

### What the wipe deliberately still does not remove

None of it is an episode. A mix holds topic ids, a saved item and a vibe hold
a question - all three are pointers, so they survive and play again from a
freshly written script, which is the design. Accounts, credentials and the
metering ledger are untouched in both scopes: somebody who signed up stays
signed up, and what the app spent stays reconcilable against an invoice.

### What a wiped deployment actually shows

Measured on an emptied database rather than reasoned about, which is the
whole point of §52:

* **Signed out, nothing in the log** - `taste_source` is `startup`,
  `personalised` is false, and the first rail is §116's time-anchored
  starter set under the heading **Start here**. Trending, "What you missed
  last week" and the friends rail are honestly empty with their own
  sentences; Explore says "Nothing here yet".
* **Signed in, after listening** - `taste_source` becomes `taste`,
  `personalised` becomes true, and the rail is "Made for you", ranked.

Note the switch is on **having a profile**, not on being signed in, and that
is §116's decision rather than an oversight: a brand-new account has nothing
to personalise on, so it gets the prior too, and one play retires it. `cold`
is derived (`not profile`) and never stored, so behaviour arriving later
always wins.

**One row over-claims on a blank slate, and it is a pre-existing decision
rather than something this change introduced.** `rank_most_played` fills from
the bank when nothing has been played - "a stable slice beats an empty
section, and beats a random one" - so "What FAM can't stop listening to"
shows six tiles on a deployment where nobody has played anything. The content
is fine; the heading is a claim about FAM's listeners that a fresh
deployment cannot back, which is the rule §89 and §90 both state. Left alone
deliberately: it is one line in `rank_most_played`, and turning a documented
decision over belongs in a change that is about that decision.

## 125. The generic episodes, and who they are for

Reported from the phone, against the DailyFAM mix picker: *these are the
pre-populated episodes that have been in the app every time we open it for
the first time, and I want them out so it can be a fresh start.*

The first thing worth writing down is what they were, because it decided the
whole shape of the change. They are not seeded rows and `wipe_demo_data.py`
could never have touched them: they are `topics.TOPIC_BANK`, twenty-eight
hand-written evergreen topics compiled into `topics.py`. That is why they
came back on every fresh open of every deployment, and it is why "clear the
demo data" was never going to be the fix.

Asked which way to take it, the direction came back in three parts, and each
one reverses something this file had previously recorded as deliberate:

1. The bank stays - *"I like the idea of having guardrails in there"*.
2. **"What FAM can't stop listening to" must only be populated by what people
   are actually listening to in the app.**
3. **The bank is meant for people who have downloaded the app but have not
   made an account yet** - and it *"should be optimized... make sure it has
   up to date information and is not directly using episodes from a while
   ago"*.

### The row that over-claimed

§124 ends by naming this exact line and declining to touch it, on the ground
that turning a documented decision over belongs in a change about that
decision. This is that change.

`rank_most_played` topped itself up from the bank whenever the play counts
ran out, on the argument that "a stable slice beats an empty section, and
beats a random one". That argument is about the **content** and it is true.
It is also beside the point, because the heading is a **claim**: "What FAM
can't stop listening to" over twenty-eight tiles nobody has ever played says
something about this deployment's listeners that is not so. §89's rule is
that an empty row may report a fact about this deployment and may never
invent one about the world, and what FAM's own listeners have played is the
most checkable fact on the whole page.

So the filler is gone, the row is empty until somebody plays something, and
its sentence says which: *"Nothing has been played here yet. This fills up as
people listen."* Not "nothing is popular", which would be a claim about
listeners this deployment has not got - and not "yet today" either, which the
old copy said about a three-day window.

The cost, stated rather than discovered later: a fresh deployment shows that
row empty, and `tools/seed_demo.py` is what fills it for a demo. That is the
same bargain Explore already makes, for the same reason - it replays what
listeners did, so where nobody has listened there is honestly nothing to
replay.

### Who the bank is for

The second half is a boundary rather than a deletion. `browse_inventory` is
the one definition of what a rail may **offer**:

    no account   live story pool + the evergreen bank
    account      live story pool + the startup set

**It is a swap and not a subtraction, and that is the load-bearing part.**
Removing the bank on its own would have left an account holder on a
deployment with no live provider - which is every deployment today, since the
story pool is off until `GDELT=1` - looking at a page with nothing on it. It
would also have broken the premise `WORLD_FLOOR` reserves its four Trending
tiles on, which is written down in §114 as "Made for you draws on both
inventories and can never be empty". So the floor is replaced rather than
removed, and it is replaced with the fresher of the two.

That replacement is also the answer to the third instruction, and it is worth
saying why it is the *right* answer rather than a convenient one. "Make the
bank up to date" reads as an instruction to rewrite twenty-eight topics, and
a bank rewritten to be about today stops being evergreen - which is the one
property that lets twenty-eight tiles serve every listener from one shared
script. The mechanism for "a generic tile that is about now" already exists
and `startup.py` already argues for it: eight time-anchored questions, one
per facet, researched on the tap by `SEARCH_MODE=always`, refusing rather
than answering from memory when there is no evidence. An account holder's
generic tile is now one of those. Nothing was rewritten, nothing costs
anything at page load, and the tile is current because it is *researched*
rather than because somebody edited a string.

**It does not make the startup set warm inventory for a guest.** `startup.py`
is explicit that the set is for a listener who has said and done nothing, and
`rank_startup` still leads with it on exactly that listener and nobody else.
A guest who has played something keeps the bank, which is the behaviour that
shipped. Confining the change to "what replaces the bank once there is an
account" is what keeps *"the set is for somebody who said nothing, and only
them"* true where it was written - and the test that says so
(`test_choosing_interests_is_not_a_cold_start`) is what caught the first
version of this, which had stacked the two inventories instead of swapping
them.

### Every surface reads it, including the one that starts by itself

§119 is the reason this is one function with four call sites rather than four
`live + list(TOPIC_BANK)` expressions. That section is about a ladder with
one definition and a fifth caller still reading the variable it replaced, and
the failure was invisible because falling back is a supported state. The same
trap is set here in three places and all three are wired:

* **"View more"** - `build_section` takes the flag, or the screen behind a
  rail would hand back the twenty-eight tiles the rail had just decided this
  listener is not shown. Two surfaces, one ranking.
* **The post-episode popup** - `rank_next_up` takes it, on both passes. Its
  last-resort pass drops the *already-played* rule to guarantee four tiles,
  and it now drops that rule over the same inventory rather than reaching for
  the bank. A grid of three is an honest shortfall; a fourth tile from an
  inventory this listener has stopped being shown is not, and this is the one
  surface that starts playing its first tile without being asked.
* **The request boundary** - `app._has_account` reads
  `request.state.listener.is_authenticated`, beside `_place_for`, which reads
  the same field. Never a parameter: a query string that could ask for
  somebody else's inventory is the rule `?user=` lost.

The default is `False`, which is the **generous** answer, and that is
deliberate. A caller that has not been taught about accounts - a test,
`write.py`, the fixture preview - does not know, and the honest reading of
"we do not know" here is the cold-start one. Defaulting the other way would
have silently taken the bank off every surface whose caller was never
updated, and an emptier page is exactly the kind of failure that looks like a
design decision rather than a bug.

### Two exemptions, written down so they are decisions

Neither is an oversight and both turn on the same distinction: **the rule is
about what FAM offers somebody unprompted, not about what a listener can go
and find.**

* **The DailyFAM mix picker** (`rank_bank`) keeps the whole bank. It is a
  menu somebody opened in order to choose subjects, a mix holds topic ids
  rather than audio - so a bank member in a mix is a fresh episode every
  morning and never a standing one replayed - and a saved mix needs an
  account, so applying the rule here would have emptied the picker for
  exactly the listeners who can use it. That is §123's failure one screen
  over: a fact about the account gate reported as a broken topic list. It is
  also the screen the original report was made against, which is worth
  noting - the complaint was that these episodes are *everywhere*, and a
  picker is the one place a list of subjects is the point.
* **Explore New** (`rank_might_like`) keeps it too. It is off the page
  entirely (`UNSHELVED`) and reached only by a listener who went looking to
  widen their taste; widening it over eight questions whose facets that
  ranker mutes would leave almost nothing to widen into.

### What the tests could not see, and now can

Ten existing tests failed, and every one of them was asserting the old
behaviour rather than finding a bug - which is the useful signal here: four
separate tests about the *shape* of the crowd row (view-more shows more, a
written tile leads, a broken store does not empty the page, the personal
rails are not starved) were all passing against an inventory **nobody had
touched**. A row that can always fill itself from a fixed list is a row whose
tests never have to produce the data it is supposed to be made of.
`crowd_plays` in `tests/test_myfam.py` supplies it now.

`tests/test_generic_inventory.py` pins the three rules through
`build_feed`, `build_section`, `rank_next_up` and HTTP rather than through
the helper, because the helper being right has never been the failure mode
here.

### Two things the double-check found, both about what a test was really asserting

**A rail nobody is looking at was starving the crowd row.** `might_like` is
`UNSHELVED` - ranked, reachable at `/api/explorenew`, and not drawn on this
page - and it fills before `most_played` in `FILL_ORDER` so the *drawn* rails
do not show what Explore New would show. That is a sensible rule for a rail
that chooses between things to offer. This row does not choose; it reports
what listeners actually played. So a tile held back by a ranking nobody is
looking at was a genuinely most-played episode missing from the one row whose
job is to say what was played.

The coupling is older than §125 and was invisible until it: the row used to
top itself up from the bank, so being starved here never showed. Removing the
filler is what made it visible - and it was **data-dependent**, which is
worse than always-wrong: whether `might_like` wanted that particular tile
decided whether the crowd row was right. `most_played` now ignores the
unshelved rail's reservation and still avoids `mine` and every drawn rail, so
nothing appears on two rails.

**And the gate is on what FAM offers, never on what it reports.** The test
asserting "an account holder is never offered the bank" swept every rail on
the page, and it passed - because the only play in its fixture was the
member's own, so every rail was empty of the bank whichever way the code
went. Run at a realistic size, an account holder legitimately sees a bank
tile in "What FAM can't stop listening to" when other listeners really played
it, and should: `rank_most_played` and `rank_friends` are measurements over
the play log, and hiding the most-played episode in the app because of who is
looking would be this section's own over-claim in reverse. The rails the rule
is about are the ones that *choose for you* - Made for you, What you missed,
and the popup's own passes. The test names those two sets now and asserts
both halves.

Both findings have the same shape and it is §122's: a test that never runs at
production scale is inspecting rather than verifying. Neither was visible to
2,361 passing tests, and both turned up in one scripted boot of the real
thing.

### What is still open

**Nobody has heard one of these episodes.** There is no API key in the build
container, so the claim that an account holder's generic tile is better
because it is researched fresh is a claim about the mechanism and not about
the writing - the same gap §116 left on the startup set and for the same
reason. It is the first thing to listen for on a machine with a key.

## 126. A vocabulary that started from nothing, and a ranker that never read it

Asked for, in the owner's words: keep the topics and subtopics growing from
what people search for, and *"have an initial topic tree already inside the
database"* serving two purposes - more variety on myFAM before there is heavy
traffic, and something for the algorithm to build on later.

### What was actually wrong, which was two things

The first is the one that was asked about. §121 built a vocabulary that grows
itself out of real searches, and everything about it is right except its first
day. `MIN_LISTENERS` is three and `MIN_TEXTS` is two, deliberately - that pair
is the whole spam control, and §121 found what happens without it (thirty-nine
nodes from eight queries, mostly fragments). But it means a deployment with no
traffic has **no grown vocabulary at all**, and one with a little has whatever
shape the first few arrivals gave it. Until then the ranker is back on the
eight facets and twenty-nine subtags that `categories.py` exists because of.

The second was found while checking whether a seed would actually do anything,
and it is the larger of the two:

    >>> tree.match(BANK_BY_ID["nil-arms-race"].query)
    ('college football', 'sports')
    >>> BANK_BY_ID["nil-arms-race"].tags
    ('sports', 'money', 'sports-business')

The tree could see that the tile is about college football. `_affinity` reads
`topic.tags`. So against a listener whose entire history was college football:

    nil-arms-race   0.173
    golf-evolution  0.212

The bank's college-football tile scored **below** its golf tile, for the
listener it was most obviously right for. That is the exact complaint CLAUDE.md
records as the reason `BROAD_MATCH_PENALTY` and `familiar_words` exist - "there
is no tag for the NFL and none for college football, both are `sports`" - and
§121 built the vocabulary that can say it. Nothing joined the two up. A seed
tree shipped on its own would have been a table nothing read.

### The seed

`category_seed.py`: 180 nodes, two levels under each of the eight facets,
which are roots and not rows. Applied by `categories.apply_seed` at boot -
awaited rather than scheduled, unlike the story and growth sweeps beside it,
because it is a pass over a dict into SQLite with no network in it, so
scheduling would buy nothing and would leave a window where the first browse
page ranked without it.

Three rules on what went in, because the obvious way to write that file is the
wrong one. **Real subjects, not the bank's table of contents** - it would be
easy to write twenty-eight nodes that each match one evergreen topic and get a
vocabulary that is worthless the moment somebody searches for something else,
so the test that counts bank coverage asserts a floor and never a total.
**Broad at the top, specific at the bottom**, because the middle of a branch is
what containment has nothing to deepen against and what no amount of reading
what people typed can invent. And **nothing the hand-written vocabulary already
owns** - `mint` silently returns None on a collision, so a bad entry is not an
error, it is a node that never exists and a tree quietly smaller than the file
claims. A test asserts every entry mints.

It is a **floor and never a ceiling**, which took four separate refusals:

* `apply_seed` never reparents. `mint` leaves an existing node's parent alone,
  and that is what this relies on: once a placer has moved something, this
  file is a record of where the tree started.
* It never refreshes what it did not add. Bumping `last_seen` on every boot
  would make the whole vocabulary immortal, because a process restart would
  look exactly like somebody being interested in something.
* It claims zero listeners and zero uses. `MIN_LISTENERS` is the spam control
  and a seed that inflated it would be lying about the one number deciding
  what gets in.
* `prune` exempts it, and the reason is worth separating from the one already
  there. `NODE_TTL` asks "has this subject stopped being talked about", which
  is a question about an *observation*; a seed node was never an observation,
  and on the deployment it exists for - no traffic - every leaf of it looks
  stale by construction. Pruning it would also be a loop rather than an
  eviction, since the next boot mints it straight back.

**A wipe puts it back.** §124's rule is that a wipe takes what the *log*
taught, and a seed node was never taught by anything - a wiped deployment is
precisely the deployment a seed is for. Read the other way round rather than
an exception to it.

### The join

`topics.topic_tags(topic)` is the tile's declared tags plus whatever the tree
recognises in its **query** - the query rather than the title, because a title
is a label and `<<TITLE:>>` means it may not even be the one the episode ends
up with.

The scoring change is deliberately *not* a new mechanism. A tile is scored as
though somebody had hand-written those tags onto it: numerator and denominator
both, exactly as `sports-business` already is. A tile the tree says nothing
about comes back as the **identical tuple** rather than a re-sorted copy of the
same set, so the guarantee is the strong one - a deployment with an empty tree
ranks precisely as it did before any of this existed, which is the rule this
whole layer lives by.

Memoised on the tree's generation, because §122. Measured: 5,600 lookups in
1.4ms warm, 28 cold against a 180-node tree in 0.21ms. The memo is dropped by
`reset_category_tree` rather than only by noticing the generation changed -
noticing means *two floats from the clock differ*, and a sentinel the clock
could reproduce is what §113 was.

### The half of the join that was nearly missed

`_affinity` was not the only thing reading a tile's declared tuple, and the
other one is the function this whole problem is named after. `BROAD_MATCH_
PENALTY` cuts a live story to 0.3 when "nothing specific about this tile
matches this listener", and `_is_broad_match` decided *specific* by looking
for a subtag in `topic.tags`. Its own constant is documented as answering
"the case the vocabulary **cannot express** - there is no tag for the NFL and
none for college football, both are `sports`".

So with the tree seeded and `_affinity` fixed, a live story about college
football, whose only hand-written tag is `sports`, was still being damped to
0.3 for a listener whose profile contains `college football` - by the
mechanism that exists because the vocabulary could not say it, at the moment
it finally could. Measured on a pool-shaped tile:

    empty tree   broad match: True    affinity 0.300
    seeded       broad match: False   affinity 1.681

The one thing that had been rescuing such a tile was `_subject_is_familiar`,
which reads raw search words - so the workaround was carrying a case the
vocabulary now covers properly.

One more thing the memo had to be right about, found in the same pass: it
caches the **tree's half only**, keyed on the question, because
`tree.match(query)` is a pure function of the query and the tree while the
combined answer also depends on the tile's own declared tuple. Caching the
combined answer under the query alone would hand two tiles that happen to
share a question each other's declared tags. Nothing in the bank shares a
query - a test says so - but a live story and a bank topic are minted by
different code and nothing makes that true *across* inventories, which is
the shape of failure that survives a long time because it is silent and
rare.

`_is_specific(tag)` is split out and read by both `tag_weight` and
`_is_broad_match`, because they are the two places that ask how specific a
tag is, for two different purposes, and the first already counted a category
while the second did not. A test asserts they agree.

**`diversify` deliberately keeps the declared tags**, and that is the mirror
of the same question. It caps how many tiles of one *heading* a rail shows,
and `facet_of` returns an unknown tag unchanged - so a grown category would
arrive as a facet of its own, every tile would be alone in its bucket, and
the variety cap would silently stop binding. The join belongs where a tile is
scored against a listener; a rule about the shape of a row wants the eight
headings.

### What it is worth, measured

On the same listener as above, after seeding:

    nil-arms-race   1.373   (was 0.173)
    golf-evolution  0.173   (was 0.212)

and for a golf listener the two swap, which they could not do before. The bank
goes from 27 distinct tag signatures to 28 of 28 - every tile now
distinguishable from every other - and the tree recognises a subject in 19 of
the 28 bank queries.

The nine it does not are the honest result rather than a gap to close: the
bank is broad on purpose, and "why worrying feels useful when it is not" names
no subject a vocabulary should have. The same goes for six of the eight
startup tiles, which are *written* to name no particular subject ("the biggest
storylines in professional sport right now") - the tree correctly says nothing
about them, and forcing it to would be inventing a claim about a tile that has
not been researched yet.

### What this does not do, said plainly because it was half the ask

**A category is never a tile.** The request was for a seed that would "populate
the myFAM page with more variety", and a vocabulary cannot populate anything -
it is the words the ranker reasons in. A deployment with a rich tree and an
empty bank still has an empty bank. What it changes is how sharply the tiles
that *are* there can be told apart, which on a page of five rails drawn from
one shared inventory is most of what "variety" means in practice: before this,
a listener with one corner of `sports` in their history got several
identically-scored candidates and a grid ordered by `topic.id` (§80's finding,
one vocabulary later).

If what is wanted is more *tiles*, that is a third inventory or a live
provider, not a vocabulary - `GDELT=1` and `MYFAM.md`.

### Still open

**Nothing here has been run against a real event log.** §121 ended with that
sentence and it is still true: the seed is hand-written and checked against the
bank, and what the tree looks like after real searches have grown on top of a
seeded base is the first thing to look at. `python tools/categories_report.py
--tree` now says how much of a tree was declared and how much was learned,
which is the number that answers it - a deployment still showing 180 declared
and 0 learned is one where either nobody is searching or the sweep has stopped,
and a node count cannot tell those apart.

## 127. Eleven changes from one packet, and the three decisions they reverse

"9.21.26 II Implementations" asked for eleven things. Eight are additions;
three reverse something this log had recorded as deliberate, at the owner's
direction, and those are written down first so nobody re-litigates them.

### What is reversed

**The event log is behind the account gate now.** `ACCOUNT_REQUIRED` used to
say the interaction log was deliberately *outside* it, because gating it would
mean an anonymous feed could never be ranked. The owner's answer is that it
should not be: everything the algorithm learns belongs to an account, and a
guest session is a device rather than a person. `app._remembers` is the one
predicate every write site reads - `/api/event`, the play recorded from
`/api/audio`, and the impressions on `/api/myfam`, `/api/myfam/section`,
`/api/explorenew` and `/api/nextup`. A guest's event is answered
`{"ok": true, "remembered": false}` rather than refused, because a client
firing events on a timer must not read a guest as a broken server.

On the client, a guest's chosen interests, subjects and language are now held
in the page's memory (`sessionChoices`) and gone when it closes; the rest of
`fam.prefs` - speed, browse length, whether the first run is done - is about
the device and stays on it. The copy that promised "signing up keeps the
listening you have already done" is gone from four places, because nothing a
guest does is kept.

**The concrete symptom was resume positions.** "Pick up where you left off"
read `localStorage`, so it outlived a log-out and the next account on that
phone was offered the previous one's half-heard episodes. Positions are now a
`progress` table in `saved.db` (the shelf's store: same per-listener
pointer-not-audio shape, same `(query, minutes)` identity), written through
`POST /api/progress` every fifteen seconds of playback and at once on a pause
or an end. `forget` erases it with the account.

**Every drawn rail but the friends one has a floor** (`topics.RAIL_MINIMUM`):
six for Made for you and Trending, four for "What you missed last week" and
"What FAM can't stop listening to". This reverses §125's crowd row that
"holds plays and nothing else", the missed rail's relevance floor as a
*length*, and Trending drawing on the live pool alone. It is done by topping
up **after** every rail has chosen, never by weakening a ranking - a rail's
own picks keep their order and only the gap under the floor is filled, from
`_rail_fallback`: Trending from the live pool, the held pool, then the
startup set (one time-anchored question per facet, researched on the tap -
the nearest thing to "what is happening" with no live provider configured,
which is every deployment today), then the bank; the others from their own
inventory in affinity order, then everything else. The friends row stays
empty until somebody follows somebody, because a friends row filled with
strangers is §102's bug. "View more" gets the same floor, so it never shows
fewer than the rail that opened it.

### What is added

**Pick up where you left off** is empty until there is something to pick up
- it used to top itself up to four with bank tiles, so a first run was told it
had left something off. Now it is part-heard episodes, the `<<NEXT:>>`
follow-ups of finished ones, and (only to fill) episodes like the last one
heard, off `rank_next_up` so it cannot disagree with the post-episode popup.
Each card carries a one-sentence summary, written by the model on a new
`<<SUMMARY:>>` line beside `<<TITLE:>>` - free, never spoken, cached in a
`summary` column - so the section reads like the rest of myFAM.

**Titles arrive before the first word.** `<<TITLE:>>` is the better title, but
it lands with the last token, and until then the player showed the question
with capital letters on. The brief is a model call already made in front of
the first word, so it now writes a `title` too (a handful of output tokens, no
second call), refused by `ei.clean_title` when it is only the question
re-cased. It is published to the live track (`live_captions.publish_title`),
`/api/next` returns it with `title_final: false`, and the interface asks every
two seconds during the wait and keeps asking until the writer's own replaces
it. A provisional title can never overwrite a final one.

**myFAM's header does not scroll** - the wordmark, messages and the episode
length control sit outside the scroller. The length control moved there from
the "Pick up where you left off" heading, which is no longer drawn at all for
a listener with nothing to pick up.

**The loading screen has an X.** It aborts the request (which is also the
only signal the server has that nobody is listening), takes the screen down,
records no skip - nothing was heard - and goes back to where the tap came
from.

**Messages draw faces.** The inbox, the conversation header, the new-chat
picker and the share sheet drew initials for everybody; `personAvHTML` is now
the one place a person's circle is drawn, a picture where they set one.
`/api/messages` and `/api/messages/thread` carry `avatar`.

**Typing dots.** `typing_indicator.py` is a dictionary with a clock - a note
lasts five seconds, it is directed (A's typing is only ever told to B), it is
cleared on send, and it is never written to disk. The thread poll carries
`typing`, so the dots cost no request of their own; the sender posts at most
one note every 2.5 seconds while typing. On more than one worker the note and
the poll can land on different processes and the dots simply do not show,
which is the right way for it to fail.

**The drop-down banner** stays three seconds rather than six, swipes up to
dismiss, and is announced within about three seconds rather than ten: the
poll is 3s, skipped while the page is hidden, and fired at once when it comes
back. It was already drawn above every screen; tapping it opens the
conversation.

**Captions follow the voice, one sentence at a time.** The lag was the
estimate: it divided the playback position by the *planned* length, and an
episode usually runs under its ceiling, so the highlight ran about a sentence
behind. The speaking path now publishes where each sentence starts in the
audio (`_sentence_starts`: the chunk's start and length are measured; only the
split *inside* one synthesised chunk is by characters), `/api/transcript`
returns `starts`, and the panel shows only the sentence being spoken, the old
one fading out as the new one fades in. **It adds no latency**: the timings are
arithmetic on audio already produced, and they ride on the caption poll that
already existed. A replay publishes a fresh live track as it is spoken, so it
is measured too; a script read only from the cache falls back to the estimate.

**Audio plays with the ringer switch off.** iOS treats Web Audio as ambient
sound, which obeys the silent switch, so a listener with the ringer off heard
nothing. `FamAudio` now sets `navigator.audioSession.type = "playback"` where
Safari has it (16.4+), and for older iOS plays a looping silent `<audio>`
element alongside - while a media element plays, iOS moves the whole page onto
the media channel. It is paused whenever the episode is, so the lock screen
never claims something is playing when it is not.

### What reviewing it found

Two independent reads of the diff, one per half, found ten things - none
visible to the suite, which passed throughout. All fixed, each with a test or
a smoke check where one could see it:

- **A guest's VIBE! still reached the log.** `/api/vibe` records a taste
  event and was the one write site `_remembers` did not cover.
- **The typing dots could stay on screen** after the state said they had
  gone: a poll returning only already-drawn messages cleared the flag without
  a redraw, and your own message coming back cleared *their* typing.
- **A finished episode could be saved as part-heard**: the end handler paused
  - which sends the position - before clearing it, as two unordered POSTs.
- **The cancel X said "Cancelled" on Play All and started the episode anyway**
  from a timer. Those timers now go through `afterLoading`, which a cancel
  invalidates.
- **A resume card started from 0:00**, and its first tick erased the position
  it had promised to return to. It now jumps there once that audio exists and
  writes nothing back until it has.
- **The silent `<audio>` element ran even where `navigator.audioSession`
  exists**, which would put an empty Now Playing entry on the lock screen.
- **Logging out left the last account's resume cards on screen.**
- **A near-match cache hit published its title under the neighbour's key**,
  and `publish_title` could create a live track nothing would ever close -
  which would hide the cached transcript behind an empty live one. Titles
  now go under the listener's own key and never create a track.
- **A resumed follow-up lost its context**, which is part of the cache key, so
  its card found no title and the resume played a different episode. The
  `progress` table keeps it now.
- **`/api/next` computed the cache key three times** per poll - three model
  calls each with `CACHE_SEMANTIC_KEY` on. `episode_meta` computes it once.

**And CI found one the container could not.** The gate runs Node 20, which
has no global `navigator`; this container runs Node 22, which does. The
silent-switch guard read `navigator.audioSession` outside its `try`, so
`tools/check_stretch.js` - which runs `fam-audio.js` under Node - passed here
and threw a `ReferenceError` there. Reproduced here by deleting
`globalThis.navigator` before the check, and guarded with `typeof`. §106's
"a green `./dev.sh check` is not a green CI", with the Node version as the
difference this time.

### Still open

**None of the interface half has been on a real iPhone.** The ringer fix in
particular is a platform behaviour and can only be verified on a device with
the switch flipped. The summary and the brief's title are prompt changes made
without a key, so nobody has read one yet.

## 128. Some episodes take 45 seconds, and nothing could say which step

**The report:** search episodes taking up to 45 seconds to start, with the
instruction that any fix must cost no quality at all.

**The first finding was that the question could not be answered from a log.**
`claude_start` is marked before the brief, so `claude_ttft` - the number the
episode log and `pod_episode.py` both print as "Claude first token" - was
brief + live lookup + retrieval + the writer's own thinking, as one span. On a
researched episode those are four different costs with four different fixes,
and a 45-second one could have been any of them.

**Instrumentation only; nothing a listener hears changed.** `ScriptNotes.marks`
carries the episode's clock into the generator, which marks `brief_start` /
`brief_ready`, `evidence_start` / `first_rung_ready` / `retrieval_ready` /
`live_ready` / `evidence_ready`, and `writer_request`. `EpisodeMarks.stages()`
is the critical path to the first audio - setup, brief, evidence, writer
thinking, first sentence, voice queue, first synthesis - consecutive, so the
parts add up to `first_pcm` and anything left is printed as `unaccounted`
rather than absorbed. Three places read it: a `stages q=...` log line written
**at the first audio** rather than at the end of the episode, an
`X-Stage-Seconds` header that `tools/pod_episode.py` prints, and
`tools/latency_probe.py`, which runs the production pipeline in-process with
the cache off and prints the table and its median across questions.

**Nothing here has measured a real episode.** There is no key in the build
container and the proxy refuses `fam.onrender.com`, so the numbers that matter
come from the next slow episode on Render (read its `stages` line) or from
`python tools/latency_probe.py` where the server's credentials are.
`tests/test_stage_marks.py` proves the plumbing: stubbed delays come back out
of the right labels and the parts sum to the first audio.

### What reading the path found without a key

Each of these costs no quality, because none of them changes a prompt, a model,
an effort level or what is retrieved:

- **EI builds a new client on every call**, so every brief pays a fresh TCP
  and TLS handshake to the API; `research_client()` already caches its one.
- **The writer's ~2,500-token system prompt is never cached.** It is byte-for-
  byte stable (checked), above Sonnet 5's 1,024-token minimum, and re-prefilled
  on every episode.
- **Retrieval is serial where it need not be**: the one retry searches only
  after the first search comes back thin, and the GDELT cross-check (off by
  default) runs after Exa rather than beside it.
- **Retrieval waits for the whole brief** although it reads only the fields EI
  writes first (`search_query` .. `recency_days`); the rest (`structure`,
  `cautions`, `live_domain`, `outcome_dependent`, `title`) could be generated
  while Exa searches.
- **`render.yaml` declares no `EXA_API_KEY`.** If the dashboard has none either,
  every episode falls down the ladder to the model's own search - 10-25 seconds
  by this file's own measurement - which alone could explain the report.
  `/api/health`'s `research` block says which.

### Still open

All of the above is a proposal until a real `stages` line says which step the
45 seconds is in.
