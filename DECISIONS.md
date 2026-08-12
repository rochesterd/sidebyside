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

---

## 2026-08-12 — IDS Python bindings install from PyPI, pinned — supersedes the local-wheel instruction

**Decided:** Install `ids_peak` and `ids_peak_ipl` from PyPI, pinned to
exact versions in `requirements-ids.txt`, instead of installing wheels out
of the local IDS peak installation directory. This supersedes the
local-wheel instructions that previously lived in `SETUP.md`'s IDS peak
section — a future reader should not reinstate them.

**Why:** As of IDS peak 2.10, IDS stopped bundling the Python wheels in
the Windows setup and publishes them on PyPI instead. Confirmed on this
machine running IDS peak 26.06.1-943: `ids_peak\generic_sdk\api` contains
only `cmake_finder`, `doc`, `include`, `lib` — no `binding` folder — and a
recursive search of the whole install tree for `*.whl`, `*.pyd`, and
`ids_peak*.py` returns nothing. The Custom installer's component tree
offers no Python binding option either. The original "install from the
local install, not PyPI" reasoning was sound when written against IDS
peak 2.x, where the wheels genuinely lived there and PyPI had nothing
IDS-published to install against. It describes a layout that no longer
exists in 26.06, not a wrong idea at the time.

**What still holds:** The underlying concern — binding version must match
the installed SDK runtime — is real and unchanged. It's now enforced by
pinning `ids_peak`/`ids_peak_ipl` versions in `requirements-ids.txt`
rather than by installing from a runtime-local path. Drivers and
transport layers (`ids_ueyegentl` for the UI-3250, `ids_u3vgentl` for the
U3-327x) still come from the IDS peak Windows installer, per machine, same
as before — only the Python bindings moved. Bindings and runtime must be
upgraded together: a peak update on a lab machine without a matching
bindings pin fails at device enumeration, not at import time.

**Rejected:** Leaving the local-wheel instructions in place and noting
PyPI as an alternative. IDS peak 26.06 doesn't ship a local wheel option
at all, so "install from local, or PyPI" isn't actually a choice — the
local path is gone, and presenting it as one path among two would send
the next reader on the same dead-end search this entry documents.

---

## 2026-08-12 — One IdsCamera class for both real cameras, not one per model

**Decided:** `ids_camera.py` has a single `IdsCamera(BaseCamera)`,
parameterized by serial number, used for both the Haag-Streit slit lamp
(UI-3250CP-C-HQ) and the Keeler (U3-327xCP-C).

**Why:** `vendor/ids_peak_api.txt` shows no camera-family-specific
classes in the bindings — `Device`, `DataStream`, `NodeMap`, `Buffer` are
all generic GenICam/GenTL types. Once the uEye Transport Layer is
installed (SETUP.md Section 3), both cameras enumerate through the same
`DeviceManager` and the same buffer/acquisition lifecycle applies to
both. The only real difference between them (resolution, Bayer pattern)
is read from each device's own node map and each captured buffer at
runtime, not hard-coded per model — so a second class would duplicate the
entire acquisition path for no behavioral difference.

**Consequence:** Frames are converted from whatever Bayer pattern the
buffer reports to BGR8 per-frame via `ids_peak_ipl.Image.ConvertTo`,
rather than assuming a fixed pattern for either camera.

**Rejected:** `ImageConverter` with `PreAllocateConversion` for the BGR8
conversion, which would avoid a per-frame allocation. Its exact argument
signature isn't recoverable from `vendor/ids_peak_api.txt` (SWIG strips
argument info from `inspect`), and this path can't be exercised without
hardware attached — a wrong guess there fails silently or crashes deep in
a background thread instead of at an obvious call site. `Image.ConvertTo`
takes one argument and is unambiguous. Revisit once real hardware is
available to measure whether the per-frame allocation actually matters at
30fps, per this project's existing "measured throughput over datasheet
numbers" stance.

---

## 2026-08-12 — Hardware smoke test found two real IdsCamera bugs; both fixed

**Decided:** Ran `tools/smoke_test_camera.py` against a real Keeler
(U3-327xCP-C, serial 4110050487) for the first time. Two bugs surfaced
and were fixed in `ids_camera.py`, both now confirmed working end to end
(final capture: full 0-255 dynamic range, recognizable image).

**Bug 1 — child GenTL handles invalidated by Python GC.** `_open()` held
`device` as a local variable; `self._node_map` and the `DataStream` are
derived from it. The moment `_open()` returned, `device` had no
remaining Python reference and was garbage collected, which invalidated
the handles derived from it — the very next `WaitForFinishedBuffer()`
call from the capture thread raised
`InvalidInstanceException: dataStreamHandle is invalid!`. Fixed by
storing `self._device` and `self._remote_device` as instance attributes
for the camera's whole open lifetime, not just locals in `_open()`.

**Bug 2 — sensor power-on defaults are unusable.** With the SDK/buffer
path working, captured frames were still near-black (raw Bayer max
value 3-4 out of 255) even pointed directly at a lamp — confirmed via
IDS peak Cockpit that this wasn't a physical/mounting issue, and via a
raw-buffer-before-conversion capture that it wasn't our BGR8 conversion
either. Root cause: the device's power-on defaults were `ExposureTime
~15ms, Gain 1.0` — nowhere near enough for the room. Cockpit's live view
had converged to `ExposureTime ~47.5ms, Gain ~25.4` for the same scene.
Fixed by adding `_converge_auto_exposure()`: sets `ExposureAuto`/
`GainAuto` to `Once` right after `StartAcquisition()`, drains buffers
until both read back `Off` (converged), then leaves them locked for the
rest of the session. Measured convergence time on this hardware: ~10
frames, ~0.5s.

**Why `Once` and not `Continuous`, and why converge at all instead of a
fixed value:** A hardcoded exposure/gain number would be exactly the
kind of measurement-dependent magic constant CLAUDE.md's Conventions
section warns against — this session's numbers are already known wrong
for at least one room. `Continuous` was rejected because it would keep
adjusting during a recording, and visible exposure "pumping" mid-session
is a distracting artifact in a video students review for technique.
Converge once at open, lock for the session.

**Consequence:** `_converge_auto_exposure()` skips whichever axis
(`ExposureAuto`/`GainAuto`) isn't available rather than failing camera
open over it — SETUP.md Section 3 already documents that the slit lamp
camera's uEye Transport Layer only exposes a basic feature set and may
lack these nodes, untested since only the Keeler was available for this
smoke test.

**Open:** Only the Keeler has been hardware-tested. The slit lamp camera
(UI-3250CP-C-HQ via uEye Transport Layer) still needs its own smoke
test before anyone trusts `IdsCamera` against it — the "one class for
both" decision above is sound on API-surface grounds, but is now
verified for one of the two cameras, not both.

---

## 2026-08-12 — Slit lamp camera smoke test: one more bug fixed, one real hardware limitation confirmed and accepted

**Decided:** Ran `tools/smoke_test_camera.py` against the real slit lamp
camera (UI325xCP-C, uEye Transport Layer, serial 4103484089) for the
first time, closing the "Open" item above. One more bug found and fixed;
one limitation SETUP.md Section 3 already warned about turned out to be
real, and is accepted rather than worked around.

**Bug — `DataStream.PayloadSize()` isn't implemented on the uEye
Transport Layer.** Raises `InternalErrorException` wrapping GenTL error
`GC_ERR_NOT_IMPLEMENTED` (`STREAM_INFO_PAYLOAD_SIZE is not
implemented!`) — note the exception class, not `NotImplementedException`
despite the underlying GenTL code, and this may differ by transport
layer. Fixed with `_payload_size()`: try `DataStream.PayloadSize()`
first (still the primary path, unchanged and still verified on the
Keeler), catch both `NotImplementedException` and
`InternalErrorException`, and fall back to reading the standard
`PayloadSize` GenICam node from the remote device's node map instead —
confirmed on this camera to report the same value
(1600×1200×1 byte/px = 1920000 for `BayerRG8`) that the DataStream
method would have.

**Limitation — no `ExposureAuto`/`GainAuto` on this camera.** Confirmed
via `IsAvailable()`: both `False`, before and after
`StartAcquisition()`. `_converge_auto_exposure()`'s existing
`IsAvailable()`/`IsWriteable()` guard (written before any hardware could
verify it — see the "One IdsCamera class" entry above) skips both
correctly, so this did not require a code change, only confirms the
defensive path was right. Captured frames without hand-tuned
exposure/gain were near-black (raw max ~20/255) — manually maxing
`ExposureTime` (87.2ms, its ceiling) and `Gain` (4.0, its ceiling — note
this camera's gain ceiling is far below the Keeler's ~25x) brought raw
mean from ~10 to ~76/255, confirming the capture/conversion pipeline
itself is correct for this camera too; the near-black default is purely
an exposure problem with no software fix available.

**Why accepted rather than worked around:** This camera's real operating
context is imaging through the slit lamp's own illumination path, not
ambient room light (same reasoning as the Keeler needing to be worn on
the BIO headset, see the exposure-convergence entry above) — a bench
test with no instrument light on isn't representative, and there's no
GenICam-level auto-exposure to fall back on. A hardcoded exposure/gain
value would be exactly the kind of measurement-dependent magic constant
CLAUDE.md's Conventions section warns against, and unlike the Keeler
there's no way to measure it here without the physical instrument.

**Open:** This camera needs a one-time manual exposure/gain calibration
via IDS peak Cockpit once mounted on the actual slit lamp with its
illuminator on, analogous to focusing an optical instrument — not
something `IdsCamera` can determine on its own. Until that happens,
whatever `ExposureTime`/`Gain` the device last had persists across
sessions (GenICam devices retain these across power cycles unless
explicitly changed) — fine once correctly tuned once, but means a stale
or accidentally-changed value would silently carry into the next kiosk
session with no code-level check catching it.
