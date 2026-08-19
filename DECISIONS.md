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

---

## 2026-08-12 — Frame.index was never actually gap-detectable; fixed, and a dual-camera bandwidth test confirms no real drops

**Decided:** Ran `tools/dual_camera_smoke_test.py` (new) with both real
cameras plugged in and streaming simultaneously, to test CLAUDE.md's
single most emphasized hardware risk directly: "USB3 Vision degrades by
silently dropping frames rather than raising an error." That test
surfaced a real, previously-invisible bug in `camera.py` itself —
unrelated to IDS hardware specifically — which is now fixed.

**Bug — `BaseCamera._run()` assigned `Frame.index` as its own gapless
counter, incrementing only on delivered frames.** This meant
`Frame.index` could *never* show a gap from any frame a camera's
`_grab()` failed to deliver — not `SyntheticCamera`'s injected
`drop_rate`, not a real camera's `WaitForFinishedBuffer` timeout, not a
device silently skipping frames on the wire. `recorder.py`'s
`_CameraTrack.absorb()` already had correct gap-detection logic
(comparing consecutive `frame.index` values), so it looked like drop
tracking worked — it just never had a real gap to detect, other than
this class's own bounded queue evicting a frame a slow *consumer*
hadn't read yet (which does still create a gap, since eviction happens
after `Frame.index` is assigned). CLAUDE.md's own module table
description ("dropped frames ... computed from gaps in Frame.index, not
estimated") was accurate about the *mechanism* but the mechanism had a
hole: it only ever caught consumer-side backpressure, never source-side
loss — exactly the category CLAUDE.md's Hardware section warns about.

**Fix:** `BaseCamera._grab()`'s contract changed from returning
`(image, timestamp)` to `(image, timestamp, index)` — every camera
implementation now reports its *own* frame sequence number rather than
having `BaseCamera` renumber deliveries itself. `SyntheticCamera` uses
its existing internal counter (which already incremented even for
`drop_rate`-skipped frames, so this was almost free). `IdsCamera` uses
`Buffer.FrameID()`, confirmed via hardware smoke test to start at 0 and
increment per frame on both cameras. `recorder.py`'s
`_CameraTrack.frame_count` — previously derived as `last_index + 1`,
which only worked because indices used to be gapless and zero-based —
now tracks a separate `received` counter instead, since neither
assumption holds once gaps are real. Added a regression test
(`test_recorder.py`) proving `SyntheticCamera(drop_rate=0.3)` now
produces `dropped_frames > 0` in `session.json` — before this fix, that
exact assertion would have failed regardless of `drop_rate`.

**Bandwidth result, now trustworthy:** with the fix in place,
`tools/dual_camera_smoke_test.py` showed the Keeler losing ~5% of
frames (23/425) over a 20s simultaneous run while the slit lamp lost
none — but a follow-up raw-GenTL check (bypassing `BaseCamera`'s queue
entirely, reading `DataStream.NumUnderruns()` and `Buffer.FrameID()`
gaps directly) showed **0 underruns and 0 FrameID gaps on both cameras**
running simultaneously. So the 23 losses are this process's own
`BaseCamera` queue (`queue_size=2` default) being evicted by a consumer
that didn't drain fast enough — not USB bandwidth, not a device-level
drop. At current settings (Keeler ~20fps native, slit lamp ~11fps
native/exposure-limited, well under either camera's target), CLAUDE.md's
core bandwidth concern is not observed.

**Open:** This doesn't yet test the actual worst case CLAUDE.md
describes (both cameras at full resolution and 30fps) — the slit lamp
is currently capped well under 30fps by its uncalibrated ~87ms exposure
(see the entry above), so real target-rate bandwidth testing is blocked
on that calibration happening first. Separately: whether `queue_size=2`
needs to grow is still an open question — this test's simple
read-and-count consumer isn't `Recorder`, which does real per-frame
encoding work and could behave differently under load. Worth a real
`Recorder` run against both real cameras once the slit lamp's exposure
is calibrated, rather than tuning the queue size on guesswork now.

---

## 2026-08-12 — Fullscreen by default; closing the window during a
recording is refused, not confirmed or auto-stopped

**Decided:** `app.py` now launches with `showFullScreen()` (no title bar,
no border drag-to-resize, no minimize button) unless started with
`--windowed`, which keeps the old resizable-window behavior for
development. Separately, `KioskWindow.closeEvent()` now calls
`event.ignore()` and leaves the session running if `state == RECORDING`,
rather than either popping a confirm dialog or silently stopping the
recording and exiting (the previous behavior).

**Why:** CLAUDE.md's "Who uses it" is explicit that nothing besides Start
and Stop should be clickable during a session, for students with no
technical background and no instructor present. A plain resizable window
leaves several such surfaces live — the title bar's X button, Alt+F4,
drag-to-resize, minimize — none of which are "Stop" but all of which can
end or disrupt a session. A confirm dialog was considered and rejected:
it's itself a new clickable thing mid-session, and a student startled by
an accidental Alt+F4 is exactly who will reflexively click through a
dialog without reading it. Refusing outright means the only way to end a
session is the one button that says Stop.

**Consequence:** There is now no way to quit the app from the GUI while
RECORDING — not even for a developer or proctor — short of killing the
process. That's intentional for the student-facing build. A stalled
camera still exits RECORDING on its own via `poll_recording()`'s stall
detection, which calls `_fail()` (not `closeEvent`) and is unaffected by
this change.

**Not addressed:** this only closes off what Qt itself can intercept.
Alt+Tab, the Windows key, and the taskbar remain available to switch away
from the app — actually locking those down is an OS-level kiosk
configuration, out of scope for this codebase.

**Rejected:** a confirmation dialog on close ("Stop recording and exit?").
Rejected for the reason above — it reintroduces a clickable surface
during the one state where CLAUDE.md says there shouldn't be one.

---

## 2026-08-12 — Reverted fullscreen; close during recording confirms
instead of refusing outright

**Decided:** Walked back the entry immediately above, per direct
feedback. `app.py` goes back to launching as a normal resizable window
(no fullscreen, no `--windowed` flag — there's only one mode again).
`closeEvent()` no longer refuses to close during `RECORDING`; instead it
calls `_confirm_stop_and_exit()`, which shows a `QMessageBox` ("A
recording is in progress. Stop it and exit?", defaulted to **No**). Only
on explicit confirmation does it call `controller.stop_recording()` — a
clean stop that finalizes the MP4 remux and writes `session.json`, not an
abrupt kill — before proceeding to close.

**Why:** the fullscreen window was a worse experience than the plain
window it replaced, and refusing to close at all left no way to
force-quit even deliberately (e.g. to abandon a broken session, or for a
developer testing the app). A defaulted-to-No confirmation keeps a single
accidental click or Alt+F4 from silently ending a session — the failure
mode the previous entry was actually trying to prevent — while still
leaving a deliberate two-click exit available.

**Consequence:** `_confirm_stop_and_exit()` is a separate method
specifically so tests can monkeypatch it instead of driving a real modal
dialog headlessly (see `test_app.py`'s `TestCloseLockdown`).

**Rejected (again, different reason than last time):** refusing close
outright, i.e. keeping the previous entry's behavior. Still true that a
confirm dialog is a clickable surface CLAUDE.md's "nothing else
clickable" line argues against, but no escape hatch at all turned out to
be the worse tradeoff in practice.

---

## 2026-08-12 — Durable logging to `logs/app.log`, configured only in `app.py`

**Decided:** Every module that makes a decision or can fail
(`camera.py`, `recorder.py`, `kiosk.py`, `app.py`) now has a module-level
`logger = logging.getLogger(__name__)` and logs at the meaningful points
— capture thread start/stop, an exception inside `_grab()`, state
transitions in `KioskController`, recording start/stop with frame and
drop counts, `_fail()`'s error message, camera-start failure and
recovery, the force-close confirm decision. Handlers (a `RotatingFileHandler`
writing `logs/app.log`, capped at 4 × 2MB, plus a console handler) are
configured exactly once, in `app._configure_logging()`, called at the top
of `main()`. Every other module only calls `getLogger` and never adds a
handler itself — standard practice so importing e.g. `recorder.py` from a
test doesn't also wire up file logging as a side effect. `app.py` also
installs a `sys.excepthook` that logs uncaught exceptions before
delegating to the default hook.

**Why:** these are unsupervised, unattended machines — CLAUDE.md's "Who
uses it" already establishes that failures must be loud and early in the
UI, but the UI's error banner and `session.json` both disappear the
moment the window closes or a new session starts. When something breaks
on a specific student's machine and nobody was there to see the banner,
`logs/app.log` is the only thing left afterward. The `_grab()` try/except
in `camera.py` closes a real gap: previously an unhandled exception in
the capture thread died silently, and the *only* symptom downstream was
`kiosk.py`'s stall detector firing a couple seconds later with no
indication of the actual cause.

**Consequence:** state-transition logging in `poll_preflight()` only logs
on an actual `State` change, not every poll tick (it runs every 250ms via
`app.py`'s timer) — logging every tick would flood the file with
"IDLE -> IDLE" noise. Camera-start-failure logging in `app.py` similarly
logs the first failure and the eventual recovery, not every 2s retry.

**Rejected:** a per-session log file under each `sessions/<...>/`
directory, mirroring `session.json`. Rejected because the failures this
is most needed for — a camera never starting, a crash before any session
begins — happen *before* a session directory exists. A single rotating
app-level log covers those; `session.json` remains the authoritative
per-session record for anything that did complete.

---

## 2026-08-17 — Third-person UVC camera: index-identified, self-counted
`Frame.index`, and only one instrument camera runs at a time

**Decided:** Added an ELP-USB100W03M-L21 third-person camera via a new
`UvcCamera(BaseCamera)` backed by `cv2.VideoCapture`. It deviates from two
rules elsewhere in this codebase, both accepted deliberately, plus a
change to camera lifecycle: only one of the two instrument cameras (slit
lamp, BIO/Keeler) ever runs at a time, chosen by the student via
`KioskController.select_instrument()`, rather than both running for the
app's whole lifetime the way they used to.

**Why identified by device index/name, not serial:** CLAUDE.md's
Architecture section requires serial-number identification specifically
because index order changes across reboots and USB port changes — real
past failure mode for the two IDS cameras. OpenCV's DirectShow backend
(`cv2.VideoCapture`) has no equivalent stable identifier to key off of.
Accepted because there's exactly one UVC camera in this setup; revisit if
a second UVC camera is ever added, since two indistinguishable-by-name
UVC devices would reintroduce the exact enumeration-order risk the
serial-number rule exists to prevent.

**Why `Frame.index` is self-counted here, unlike `IdsCamera`:** The
"Frame.index was never actually gap-detectable" entry above fixed
`BaseCamera` subclasses to report the *source's own* sequence number
specifically so `recorder.py` could detect real source-side drops, not
just consumer-side queue evictions. `cv2.VideoCapture`/UVC exposes no such
counter, so `UvcCamera._grab()` assigns its own gapless counter —
reintroducing, for this one camera, the exact blind spot that entry
fixed. Accepted because UVC isn't subject to CLAUDE.md's core worry
(USB3 Vision silently dropping frames under bandwidth pressure); a
webcam-class device failing looks like a stall (caught by `kiosk.py`'s
stall timeout) or a disconnect (caught by `_grab()` raising), not a
silent partial drop. `dropped_frames` in `session.json` for this camera
will therefore only ever reflect consumer-side queue evictions, same
caveat as before the source-index fix for the other two cameras.

**Why only one instrument camera runs at a time:** Only one instrument is
ever physically in use in a real session — a student is at the slit lamp
or the BIO, never both. Running both continuously (as the two fixed
cameras used to) would mean the unused one sits open for no reason and
adds a picker-independent camera-liveness check with no session it's ever
part of. `select_instrument()` now owns starting the newly chosen
instrument camera and stopping whichever one was previously running; the
third-person camera keeps the old always-on lifecycle since it's used in
every session regardless of instrument.

**Consequence:** This is also why CLAUDE.md's "Who uses it" section no
longer says "one Start button, one Stop button, nothing else clickable" —
the app now needs an instrument picker. It's scoped as tightly as
possible to preserve the original intent: two large always-visible
choices (not a menu), enabled only outside `RECORDING`, so it can never
interrupt or be part of an active session.

**Rejected:** Keeping both instrument cameras running continuously and
just choosing which feed to composite. Rejected as pointless resource use
once "only one instrument in use" was confirmed — the camera that isn't
in the composite this session never needs to be open at all.

---

## 2026-08-17 — UvcCamera hardware-verified against the real ELP camera

**Decided:** Ran a standalone smoke test (`UvcCamera(0, ...).start()`,
polling `get_latest()`) against real hardware — a "HD USB Camera"
(VID_32E4&PID_9310), the only UVC camera-class device attached to this
machine and almost certainly the ELP-USB100W03M-L21. Confirmed working:
640x480 reported resolution (queried, not hardcoded — see the entry
above), real image content (mean pixel ~128, full 0-255 range, not a
black/broken frame), and `Frame.index` incrementing correctly over a 5s
run.

**Finding:** Time to first frame was variable across three separate runs
— roughly 0.9s to 1.6s from `start()` to the first non-`None`
`get_latest()` — noticeably slower and less consistent than the IDS
cameras' ~0.5s auto-exposure convergence (see the "Hardware smoke test"
entries above). No code change was needed: `KioskController._cameras_ready()`
already treats "not live yet" as not-ready regardless of camera type, so
Start simply stays disabled a little longer after picking this instrument,
the same mechanism that already absorbs the IDS cameras' convergence
delay. Worth knowing if the picker ever feels sluggish to respond in
practice — this is why, not a bug.

**Also confirmed:** `_open()` running in the calling thread (via
`BaseCamera.start()`) while `_grab()` reads from the spawned capture
thread — the pattern every `BaseCamera` subclass uses — does not corrupt
`cv2.VideoCapture`'s DirectShow backend across threads. Checked directly
because DirectShow's underlying COM apartment-threading rules can in
principle make this unsafe; a targeted cross-thread `read()` test showed
no failures under `CAP_DSHOW`, `CAP_MSMF`, or `CAP_ANY`. The original
"no frame after 1s" result while investigating this turned out to be the
warm-up latency above, not a threading bug.

---

## 2026-08-18 — config.json + loader (ROADMAP.md Phase 1)

**Decided:** Added `config.py`/`config.json` so which physical camera
fills each role (slit lamp, BIO, third-person) is no longer a source
constant in `app.py`. `config.json` is gitignored, like `sessions/` and
`logs/` — it's install-specific data, not source. `config.example.json`
is committed in its place, seeded with this machine's real (already
publicly documented in CLAUDE.md's Hardware table) serials, so a fresh
clone has something to copy rather than a silent fallback to someone
else's hardware. `kiosk.KioskController` needed no changes — it was
already generic over an `instruments: dict[str, BaseCamera]`; this phase
is purely about where `app.py` sources that dict's contents from.

**Why `third_person` still carries a `device` index, not `vid_pid`:**
ROADMAP.md's end-state schema sketch uses `vid_pid`/`friendly_name` for
the third-person role, since no reliable per-unit serial can be assumed
across arbitrary consumer webcams. That resolution logic (plus the
single-device-attached fallback) is explicitly scoped as Phase 3 —
"Runtime resolution hardening" — not this phase. Phase 1 keeps today's
identification method (a UVC device index) so this stays the mechanical,
low-risk move ROADMAP.md describes it as, rather than pulling forward
undecided work (the UVC-enumeration spike ROADMAP.md flags as needed
before `settings.py`).

**Why the config-load failure is checked before `QApplication` exists:**
It's a technician-facing setup error (wrong/missing `config.json` on this
specific machine), not the student-facing camera-liveness preflight
`kiosk.py` already owns — a student never reaches a state where
`config.json` could be the problem, since the picker only ever shows
instruments the config already defines. So it doesn't need Qt at all:
`logger.error(...)` (already reaching both `logs/app.log` and stderr via
the existing `StreamHandler`) plus a nonzero exit code is the whole
response, checked in `main()` right after `_configure_logging()` and
before `QApplication(sys.argv)` is constructed.

**Rejected:** A `--config` CLI flag for the file path. Nothing in
ROADMAP.md's Phase 1 description calls for one, `settings.py` (Phase 2)
will always write the canonical `config.json` path, and no test drives
`app.main()` directly, so there's no plumbing reason either — the
CWD-relative default matches existing conventions (`LOG_DIR`, `kiosk.py`'s
`output_root`).

**Also rejected:** Rejecting unknown/extra keys in `config.json` (strict
schema validation). Chose to validate only the presence and type of keys
Phase 1 actually reads, and ignore anything else — leaves room for a
stray hand-added field, or a transitional `vid_pid` key sitting next to
`device` once Phase 3 lands, without the loader needing to change in
lockstep.

---

## 2026-08-18 — UVC device enumeration: pygrabber's DirectShow internals,
not Get-PnpDevice/WMI

**Decided:** `uvc_enumeration.list_uvc_devices()` resolves the open
question ROADMAP.md's "Device compatibility & camera setup system" entry
left for the start of the `settings.py` work — how to get a UVC device's
friendly name and VID/PID *and* have it correlate reliably to the index
`cv2.VideoCapture(i, cv2.CAP_DSHOW)` opens by. It calls into `pygrabber`'s
DirectShow COM wrappers (`dshow_core.ICreateDevEnum`,
`dshow_ids.DeviceCategories`/`clsids`) directly — not `pygrabber`'s public
`FilterGraph.get_input_devices()`, which only exposes `FriendlyName` — and
additionally reads each moniker's `DevicePath` property bag entry, which
carries the device's VID/PID (confirmed against the real ELP camera
currently attached to this dev machine: `DevicePath` came back
`\\?\usb#vid_32e4&pid_9310&mi_00#...`, matching `Get-PnpDevice`'s
`InstanceId` for the same device exactly). Index, name, and VID/PID all
come from one walk of DirectShow's own device enumerator, so there's no
separate correlation step — verified end to end by opening
`cv2.VideoCapture(N, cv2.CAP_DSHOW)` for every index this function
reports and confirming each one actually opens (see
`test_uvc_enumeration.py`).

**Why not `Get-PnpDevice`/WMI (the other candidate ROADMAP.md named):**
It gives friendly name + VID/PID cleanly (confirmed working against the
same real hardware), but not a DirectShow enumeration index — matching
its result back to a `cv2.CAP_DSHOW` index would still need a second pass
(trial-opening indices in order and matching by friendly name), which is
both slower and, with two identically-named devices attached, genuinely
ambiguous. Shelling out to PowerShell from Python is also just more
moving parts than a direct COM call for a Windows-only project that
already accepts a `comtypes`-based dependency's weight (`pygrabber`
itself depends on it).

**New dependencies:** `pygrabber==0.2`, `comtypes==1.4.16` (pinned in
`requirements.txt`). Both Windows-only (`comtypes` wraps `ctypes.windll`),
consistent with this project already being Windows-only.

**Caveat, matching this project's existing `vendor/ids_peak_api.txt`
precedent for undocumented/compiled surfaces:** `DevicePath` and the
`ICreateDevEnum`/`dshow_ids` internals this relies on are not part of
`pygrabber`'s advertised public API (only `FilterGraph`'s methods are
documented) — verify against the installed `pygrabber` version if a
future upgrade ever makes `list_uvc_devices()` silently stop returning
`vid_pid`, rather than assuming today's behavior still holds.

---

## 2026-08-18 — settings.py + third-person identity moves to VID/PID now

**Decided:** Built `settings.py` (ROADMAP.md's Phase 2) and, alongside it,
moved the third-person role's identity in `config.json` from a plain UVC
device index to `vid_pid`/`friendly_name` — originally planned as Phase 3
("runtime resolution hardening"), pulled forward because `uvc_enumeration.py`
already provided everything the resolution logic needed and ROADMAP.md
already fully specced the algorithm; no design ambiguity remained to defer.
Confirmed with the user before implementing (an index is exactly the
fragile-across-reboots identity this whole effort exists to move away
from — shipping `settings.py` writing one would have meant an
almost-immediate follow-up rewrite). This **supersedes** the 2026-08-18
"config.json + loader" entry's "Why `third_person` still carries a
`device` index, not `vid_pid`" paragraph — that reasoning was correct for
Phase 1 in isolation, but the phase boundary moved once Phase 2 started.
IDS instrument roles are unaffected (already serial-based, already
correct).

**`config.json`'s `third_person` block:**
```json
"third_person": { "kind": "uvc", "vid_pid": "32E4:9310", "friendly_name": "HD USB Camera" }
```
`uvc_enumeration.py` gained `resolve_device(vid_pid, devices=None)`,
implementing exactly the strategy CLAUDE.md's Hardware table and
ROADMAP.md's "Third-person (UVC) role" section describe: exactly one UVC
device attached → use it regardless of configured `vid_pid` (zero
configuration friction for the common case, self-heals if that single
camera is swapped for a different model); more than one attached →
require a `vid_pid` match, refuse to guess otherwise. `devices` is
injectable so this is unit-tested directly with canned lists — no
hardware needed for the logic itself, though the underlying enumeration
was separately verified against the real ELP camera (see the prior
entry).

**Resolution happens inside `UvcCamera._open()`, not at construction
time — this is load-bearing, not a style choice.** Verified directly in
`camera.py`: `BaseCamera.start()` (lines 81-88) calls `self._open()`
*before* creating the capture thread, and only sets `self._thread` after
`_open()` succeeds. A raise inside `_open()` therefore leaves
`self._thread` as `None`, so the *next* `start()` call retries `_open()`
from scratch — this is exactly the mechanism `app.py`'s existing
`camera_retry_timer` already depends on for a busy/wrong-serial IDS
camera, and a third-person camera that isn't physically plugged in yet
when `app.py` launches needs to fall into that same loop rather than
raising once, synchronously, out of `main()` before any UI exists (which
would crash the whole process). `UvcCamera.__init__` therefore gained a
second identification mode alongside the existing `device` index —
`UvcCamera(device=..., ...)` XOR `UvcCamera(vid_pid=..., ...)`
(constructor raises `ValueError` if both or neither are given). Both
modes stay: `vid_pid` resolves dynamically at `start()` time (used by
`app.py`'s real runtime path); `device` opens a literal index with no
resolution (used by `settings.py`'s Preview, which wants to open exactly
the highlighted dropdown entry, not whatever the fallback logic would
pick). A resulting `UvcDeviceResolutionError` propagates out of `_open()`
unwrapped rather than being caught and re-raised as
`UvcCameraNotFoundError` — they're genuinely different failure classes
("couldn't decide which device to open" vs. "knew which device, `cv2`
couldn't open it"), and every caller already just stringifies whichever
exception it gets, so no unification was needed.

**`vid_pid` is normalized in `config.py`, not just validated.**
`uvc_enumeration._parse_vid_pid` always uppercases; `resolve_device`
matches by direct string equality. A hand-typed lowercase value in
`config.json` (e.g. `"32e4:9310"`) would otherwise pass shape validation
(`^[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}$`) but then silently and permanently fail
to match any attached device — a real, easy-to-hit bug, not a
hypothetical, so `config.py`'s `_parse_third_person` stores
`vid_pid.upper()`, not the raw string.

**A UVC device with no discoverable `DevicePath`/VID-PID** (a real,
documented case — see `uvc_enumeration.py`'s `UvcDeviceInfo.vid_pid: str
| None`) still appears in `settings.py`'s third-person dropdown rather
than being silently filtered out, but can't be saved: `DeviceRow`
represents it as a `RowCandidate` with `key=None`, which `is_valid()`
already treats the same as "nothing selected," plus a status-line note
when it's the highlighted selection ("This device has no discoverable
VID/PID and can't be saved") so the reason is visible, not just a
disabled Save button with no explanation.

**`ids_camera.py` gained `list_ids_devices()`**, a standalone addition
(not a refactor of the existing `_open_device()`, which needs the raw
descriptor objects to call `.OpenDevice()` on and wasn't worth risking).
Required by CLAUDE.md's architecture rule that nothing outside a camera
module may import the IDS SDK — `settings.py` cannot enumerate IDS
devices any other way. Brackets `ids_peak.Library.Initialize()`/`Close()`
itself, matching `SETUP.md`'s reference script, since — unlike
`_open_device()` — it isn't tied to a camera object's `_open()`/`_close()`
lifecycle. `settings.py` imports it lazily (inside a function body, not
at module level), mirroring `app.py`'s existing `_make_camera` pattern:
this dev machine has no `ids_peak` installed at all (`import ids_camera`
raises `ModuleNotFoundError` immediately, confirmed), so a top-level
import would break `python settings.py` here. `uvc_enumeration`/
`uvc_camera`, by contrast, are safe as normal top-level imports —
`pygrabber`/`comtypes` are unconditional `requirements.txt` entries, not
a separate per-machine install like `ids_peak`. When enumeration fails
this way, `settings.py` shows a distinct "Could not enumerate IDS
devices: ..." status on the instrument rows rather than the generic
"‹not connected›" a row shows with 0 *devices* — "SDK missing" and
"camera unplugged" are different problems with different fixes, worth
distinguishing even though both currently happen to gate Save the same way.

**`bgr_to_pixmap` extracted to `qt_image.py`.** It was duplicated
verbatim in `app.py` and `preview.py` already; `settings.py`'s
`PreviewDialog` needed it a third time, past the rule-of-three threshold
this codebase otherwise respects (see `compositor.py`/`uvc_enumeration.py`
as precedent for small, focused modules). `app.py` and `preview.py` now
both import it instead of keeping their own copies.

**`PreviewDialog` is opened modally (`.exec()`, not `.show()`).** Two
reasons: it sidesteps an open question this project has not answered —
whether `ids_peak.Library.Initialize()`/`Close()` is safely reentrant
across a Preview holding an `IdsCamera` open while a concurrent Rescan
tries to enumerate (not covered by `vendor/ids_peak_api.txt`'s scope, not
worth risking rather than just serializing the two) — and it prevents
Rescan from mutating a row's candidate list out from under a preview
that's currently showing "the highlighted entry." **This reentrancy
question is explicitly unresolved, not answered** — flagging it here
rather than asserting a behavior that hasn't actually been tested.

---

## 2026-08-18 — Config-driven recording fps, device-derived canvas size

**Decided:** `recorder.py`'s `width`/`height`/`fps` constructor defaults
(2560x1080x30, previously hardcoded and unreachable from `app.py`) split
into two different fixes, not one: `fps` became genuinely config-driven
(`recording.fps` in `config.json`, `config.py`'s new `RecordingConfig` /
`DEFAULT_RECORDING_FPS`); `width`/`height` did **not** — they became
device-derived instead. `Recorder.start()` now computes them from
`self._track_a.camera.resolution` / `self._track_b.camera.resolution`
whenever the constructor wasn't given explicit values (sum of widths, max
of heights — the same formula `compositor.side_by_side`'s own `out_size`
default already used, just previously unreached because `Recorder`
always passed an explicit `out_size`). `KioskController` mirrors the same
`None`-means-derive default and threads it through unchanged to
`Recorder`; its disk-space preflight estimate (`_estimated_canvas()`)
prefers the same real resolutions once both cameras are live, falling
back to the old 2560x1080 constant only before then — never a
correctness issue, since Start stays gated on `cameras_ready` regardless
of how accurate the estimate is at that point.

**Why not config for canvas size too:** it would work, but device-query
strictly dominates it for this value — a technician would otherwise have
to remeasure and re-edit `config.json` every time a camera is swapped for
a different-resolution model, exactly the kind of drift-prone manual step
config.json's `label`/`serial`/`vid_pid` fields already avoid for camera
*identity*. Resolution is a `BaseCamera.resolution` property specifically
*because* it's meant to be queried, not assumed (see `uvc_camera.py`'s
"resolution isn't known until it's opened" comment) — there is no
equivalent reason to keep it out of config the way there is for fps
(next paragraph), so there's no tradeoff being made here, just an unused
capability (`side_by_side(out_size=None)`) getting wired up.

**Why fps stays config, not device-queried:** the opposite call, on
purpose. A camera's advertised/nominal fps (GenICam's
`AcquisitionFrameRate` node, or UVC's `CAP_PROP_FPS`) is a datasheet
number, and CLAUDE.md's Hardware section already establishes that this
project treats measured throughput as authoritative over datasheet
numbers for exactly this hardware — the slit lamp is the live
counterexample: it advertises ~60fps but currently sustains far less
because of an uncalibrated long exposure time (see the "Frame.index was
never actually gap-detectable" entry above), a limit no device query
would reveal, since the sensor doesn't know its own effective throughput
under the current exposure settings. `fps` here also isn't really a
camera capability at all — it's `Recorder._run()`'s encode/composite
pacing rate, decoupled from actual per-camera capture rate by design
(gaps are caught via `Frame.index`, not by tying encoding to arrival).
Treating it as a measured, technician-set value (observe real sustained
throughput, set it once) fits `CLAUDE.md`'s existing "values that depend
on measurement belong in a config file" convention exactly, rather than
inventing an auto-throttling feature that isn't otherwise needed.

**Rejected:** auto-measuring real sustained fps at startup (a brief
calibration pass before each recording) instead of a static config value.
Would get closer to "true" throughput than either a datasheet number or a
stale manual setting, but it's a materially bigger feature — a recording
target that can silently change session-to-session isn't obviously
better than a stable value a technician sets once after watching real
performance, and no current need justifies the complexity.

---

## 2026-08-18 — settings.py blocks Save when two instrument roles pick the same camera

**Decided:** Nothing previously stopped a technician from selecting the
same physical IDS camera's serial for both `slit_lamp` and `bio` in
`settings.py` — each row's dropdown is independently populated from the
same enumerated device list, so picking the same entry twice was always
possible and `Save` would have written the duplicate serial into both
roles without complaint. `SettingsWindow._duplicate_serial_roles()` now
detects this (any serial selected by more than one instrument row) and
`_update_save_enabled()` disables `Save` with an explicit message
(`conflict_label`, styled like the existing malformed-config
`warning_label` but recomputed live on every row change, not just at
startup) rather than only catching it in a docstring or leaving it to
`app.py` to fail confusingly later — two roles both trying to open one
physical device isn't a state `app.py`'s per-role `IdsCamera` construction
has any way to reconcile. `_on_save_clicked()` also re-checks directly
(defense in depth against a future caller that bypasses the button's
`isEnabled()` gate, matching this file's existing pattern of checking
`is_valid()` in both places).

**Why block Save rather than just warn:** matches every other Save-gating
condition already in this file (empty label, "‹not connected›", no
VID/PID) — a real, catchable-at-config-time mistake gets caught here,
not deferred to a confusing runtime failure on the actual kiosk machine.
