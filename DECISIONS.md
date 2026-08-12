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
