# Decisions

Append-only. Newest at the bottom. One entry per decision that has a reason
someone might otherwise undo by accident.

Format: what was decided, why, and what was rejected.

---

## 2026-08-11 — Purpose-built application rather than OBS

**Decided:** Build a dedicated app instead of configuring OBS Studio as a
hidden recording engine.

**Why:** OBS is designed around having an operator. It works as a kiosk
engine, but its failure modes all require one — a replugged camera shows
black and needs re-selecting, a full disk stops recording silently, a
Windows update can reorder device enumeration and swap the panes. None of
these produce a useful error message; they produce "the video was black,"
discovered later. With unsupervised students the requirement isn't just that
a novice can use it, but that a novice can rely on it. A purpose-built app
can check both cameras before enabling the button, verify disk space, and
fail loudly mid-session.

**Rejected:** OBS + Source Record plugin + foot pedal + Lua post-stop
script. Faster to build, and still a reasonable prototype, but not
deployable unsupervised.

---

## 2026-08-11 — Composite live, not in post-production

**Decided:** Stack the two frames in memory during capture and encode one
output stream, rather than recording separately and combining afterward.

**Why:** The composite is the deliverable — students want to see hand
movement against the optical view, and they want it immediately while the
muscle memory is fresh. Live compositing makes the two streams synchronised
by construction: no offset calculation, no drift, no render wait, no post
step that can fail. A two-minute render is two minutes of a student losing
interest.

**Consequence:** The individual raw files drop from "deliverable" to
"debugging artifact." Keep writing them if disk allows, but nothing depends
on them.

**Rejected:** Separate capture with timestamp sidecars and an FFmpeg
`hstack` pass afterward. Necessary only if live compositing can't keep up,
which it should at 30fps.

---

## 2026-08-11 — Wide canvas, not a 1080p split

**Decided:** Default output 2560x1080 for side-by-side, giving each pane a
full 4:3 frame. Picture-in-picture (1920x1080, optical view large, room view
inset) offered as an alternative.

**Why:** Splitting a 1080p canvas evenly gives each pane 960px of width. The
slit lamp camera's 1600x1200 or 2056x1542 gets crushed, and the fine optical
detail is exactly what makes the recording worth watching. The room view is
about gross hand and body position and survives being small; the optical
view does not.

**Open:** Which layout students actually prefer. Worth asking two of them
before the UI is finalised — cheap to answer now, expensive later.

---

## 2026-08-11 — Camera abstraction with a synthetic implementation

**Decided:** `BaseCamera` defines the interface; `SyntheticCamera`
implements it with generated frames (burned-in counter, timestamp, sweeping
bar, optional artificial latency and drop rate).

**Why:** Development happens on machines without the cameras attached. More
importantly, a synthetic source with a visible frame counter makes dropped
frames and sync errors *observable* in a way real cameras never do, and
lets failure paths (camera disappears, queue backs up, frames stop) be
triggered on demand rather than by unplugging things.

**Consequence:** The IDS-specific code stays isolated in one module instead
of spreading through the app, which is what will make adding a third
instrument cheap.

---

## 2026-08-11 — Python 3.13, not 3.11

**Decided:** Python 3.13 (3.13.15).

**Why:** 3.11 was the initial choice on the theory that it maximised
compatibility with older IDS wheels. That was wrong. 3.11 is in
security-fixes-only mode with no binary installers since 3.11.9 (April
2024), so installing it today means a Python missing two years of patches —
which a campus IT security scan will flag. The IDS wheels are tagged
`cp311-abi3`, meaning forward-compatible with any Python 3.11+, so 3.13
works fine. 3.13 has binary installers and regular bugfix releases.

**Rejected:** 3.14 — current and fine, but 3.13 has had longer for the
ecosystem to settle, and IDS builds against 3.11+.

---

## 2026-08-11 — Correct for inter-camera latency, don't ignore it

**Decided:** Measure the fixed pipeline latency difference between the two
sources once, then delay the faster stream through a small ring buffer
before compositing. Store the offset in config, re-verify after any camera
or driver change.

**Why:** Different capture paths have different buffering. If the newest
frame from each is naively stacked, one view lags the other by several
frames — and the relationship between the two views is precisely what the
student is trying to study. An uncorrected offset doesn't look like a bug,
it looks like bad technique.

**How to measure:** Wave a hand across both fields of view and count the
frame offset between the two streams.

---

## 2026-08-11 — Licensing is a non-issue at NECO, but stays worth watching

**Decided:** Use x264 via PyAV, and PySide6 or PyQt6 as convenient.

**Why:** GPL obligations trigger on distribution, not use. This runs on
college machines and doesn't leave the institution. Even if it were later
shared with another school, compliance means publishing source, which a
non-profit educational tool has no reason to object to.

**Watch for:** If this ever becomes something sold or distributed
commercially, the encoder choice (hardware NVENC/QuickSync instead of x264)
and the Qt binding both need revisiting. Cheaper to change before building
around them.

---

## 2026-08-11 — recorder.py ships without latency correction

**Decided:** `recorder.py` composites whatever each camera's queue has most
recently delivered, with no ring buffer or measured offset applied.

**Why:** The "Correct for inter-camera latency" decision above is still
open — it needs a measurement step and a config slot that don't exist yet.
Building it speculatively risked guessing the wrong shape. Recorder does
track per-camera dropped-frame counts (gaps in each `Frame.index` sequence)
so a real skew will at least be visible in `session.json`, not silent.

**Consequence:** Until the offset correction lands, recordings may show a
few-frame lag between panes. Do not let students start relying on frame-tight
sync before this is closed out.

---

## 2026-08-11 — PyAV remux filters on empty packets, not missing dts

**Decided:** When remuxing MKV to MP4, drop only packets where
`packet.size == 0`, not packets where `packet.dts is None`.

**Why:** The MKV demux stream's leading keyframe sometimes reports
`dts is None` even though it holds valid frame data. Filtering on that
condition silently drops the keyframe, and the resulting MP4 has valid
headers but decodes zero frames — no error, just an empty-looking file.
Found by decoding the remuxed output and getting 0 frames back instead of
the expected count.

**Rejected:** Filtering on `packet.dts is None` — looked like the obvious
"skip flush packets" check and is the kind of thing a future cleanup could
reintroduce.

---

## 2026-08-11 — Disk-space preflight: 2x a 10-minute estimate, not a flat number

**Decided:** `app.py`/`kiosk.py` block Start until free space under
`sessions/` is at least 2x the estimated size of a 10-minute recording at
Recorder's default canvas (2560x1080, 30fps), using ~0.08 bits/pixel/frame
as the libx264 crf=23 quality estimate (~498 MB for 10 minutes at those
settings, so ~1 GB required).

**Why:** A flat "500 MB free" style check doesn't scale if the canvas
resolution changes later, and undercounts by half in practice: `Recorder`
keeps both `composite.mkv` and `composite.mp4` after `stop()` (see the
MKV/MP4 decision above), so a completed session occupies roughly two
copies of the single-file estimate, not one. The 2x isn't a safety margin
bolted on top — it's what one real session needs on disk. 10 minutes was
picked as "a full CSE practice attempt," not a hard limit; recordings can
run longer or shorter, this is only a preflight tripwire.

**Rejected:** Checking free space only once at launch. Disk fills up over a
day of back-to-back student sessions; the check re-runs on a timer (same
poll as the camera-liveness check) so Start disables live if space runs
low mid-day, not just at boot.

---

## 2026-08-11 — 2-second stall timeout, and ERROR is a transient state

**Decided:** During recording, if a camera's `Frame.index` hasn't advanced
for 2 seconds, `kiosk.KioskController` stops the recording and enters
`State.ERROR` immediately. The next `poll_preflight()` call (same timer,
next tick) re-evaluates conditions and moves `state` on to READY/IDLE —
`State.ERROR` isn't a stuck state requiring a dismiss action. What
persists until the next session starts is `error_message` and
`last_session_info`, which the UI displays in a dedicated banner/summary
label independent of `state`.

**Why:** 2 seconds is short enough that "loud and early" actually means
early — at 30fps that's ~60 missed frames, well past normal jitter but
fast enough that a student doesn't practice for two more minutes against a
frozen pane before finding out. The transient-ERROR design exists because
CLAUDE.md's kiosk constraint is strictly two buttons, Start and Stop —
adding a third "acknowledge error" button to clear a stuck ERROR state
would violate that. Auto-resolving `state` while keeping the banner text
sticky gets both: the state machine never wedges, and the failure stays
visible until a student (or staff) starts a new session.

**Rejected:** A dismiss/acknowledge button. Simpler state machine, but
breaks the one-Start-one-Stop kiosk rule for a corner case that doesn't
need a third control.

---

## 2026-08-11 — `_drain_remaining` is one bounded pass, not a loop to empty

**Decided:** `Recorder._drain_remaining()` pulls whatever's currently
queued from each camera exactly once and encodes a final pair if both are
present, instead of looping until both queues are simultaneously empty.
`Recorder.stop()` also now checks `thread.is_alive()` after `join(timeout=
10.0)` and raises rather than proceeding if the capture thread is still
running.

**Why:** Found via a real crash: `app.py`'s cameras keep running (for the
live preview) well past a recording session ending, continuously refilling
their queues. The old `_drain_remaining` looped until a poll found both
queues empty at once - a condition that's only reliably reached if encoding
keeps pace with the camera's frame rate. When it doesn't (observed on a dev
machine at the full 2560x1080 canvas), the drain loop chases fresh frames
indefinitely and never returns. `stop()`'s `thread.join(timeout=10.0)`
would then time out, and - since it never checked whether the join actually
succeeded - proceed to call `stream.encode(None)` to flush from the calling
thread while the capture thread was still mid-`encode()` on the same
stream. Two threads touching one PyAV encoder produced
`av.error.EOFError: avcodec_send_frame()`.

**Consequence:** A single bounded pass can't silently loop forever, so the
join can no longer race the flush this way. If the capture thread somehow
still doesn't finish in 10s, `stop()` now raises instead of quietly
producing a corrupt or truncated `composite.mkv`.

**Rejected:** Raising the join timeout, or stopping the cameras before
calling `recorder.stop()`. Both mask the symptom without fixing the actual
mismatch between "drain until empty" and "cameras never stop."

---

## 2026-08-11 — `compositor.py` canvas fills were the frame-drop bottleneck, not the encoder

**Decided:** `_fit_into_pane` now writes resize output directly into a
`dst` view of the caller's canvas (via `cv2.resize(..., dst=...)`) instead
of building a separate array and copying it in, and only calls a new
`_fast_fill` helper when there's actual letterbox padding to cover.
`side_by_side`/`picture_in_picture` allocate their canvas with `np.empty`
instead of `np.full`, since between them the `_fit_into_pane` calls always
write every pixel.

**Why:** Recordings at the real target resolutions (1600x1200 + 2056x1542
composited into 2560x1080 @ 30fps) were dropping frames at a rate that
scaled linearly with duration - a sustained per-tick deficit, not a
one-time cost. Instrumenting every pipeline stage (`side_by_side` →
`draw_timer` → `VideoFrame.from_ndarray` → `.reformat(yuv420p)` →
`stream.encode()` → `container.mux()`) found the cause: `np.full(shape,
(0,0,0))` - a *tuple* fill value - forces numpy into a slow element-wise
broadcast loop instead of a memset. Three of these ran per frame (the
outer canvas plus one per pane), costing ~23ms of a ~36-39ms
`side_by_side` call, out of a ~50-53ms total tick against a 33.3ms budget
at 30fps. The encoder itself was cheap (~3ms even at "ultrafast") and was
never the bottleneck - which is why the earlier `veryfast` → `ultrafast`
preset swap "reduced but didn't eliminate" drops: it optimized a small
piece of the problem. The outer canvas fill in `side_by_side` was also
pure waste on top of being slow: it's unconditionally fully overwritten by
the two pane writes that follow it, so nothing ever read the color it
just spent 11.5ms painting.

**Measured effect** (30s recording, real `Recorder`, full production
settings): dropped frames per camera went from a sustained ~37% of frames
(336/899 in a matched benchmark run) to **2 out of 901** - not a tuning
change, a bug fix. `side_by_side` cost dropped from ~36ms to ~13ms mean.
Pixel output is unchanged (verified byte-identical against the prior
implementation across several letterbox/background-color cases before
replacing it).

**Rejected:** Increasing `BaseCamera`'s queue size to buffer more frames.
That only delays when drops start - it doesn't fix the underlying
per-tick deficit, so drops would still accumulate linearly, just from a
later starting point. Shrinking the recording canvas was also rejected -
it would help the same way (less work per frame) but directly undoes the
"wide canvas, not a 1080p split" decision above, and wasn't needed once
the actual bug was fixed.
