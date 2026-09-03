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

---

## 2026-08-18 — Lock UVC autofocus/auto-exposure after a warmup window

**Decided:** `UvcCamera._open()` now calls
`_lock_autofocus_and_exposure()`: read a bounded number of frames (10, or
2.0s, whichever comes first) with autofocus/auto-exposure left at
whatever `cv2.VideoCapture`'s driver defaults are, then
`cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)` and
`cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)`. Motivation: nothing
previously stopped the third-person webcam from continuously
autofocusing/auto-exposing through an entire recording, which can
visibly hunt (refocus, re-expose) mid-session — exactly the distracting-
artifact problem `ids_camera.py`'s `ExposureAuto`/`GainAuto` = `Once` (not
`Continuous`) convergence already solves for the two instrument cameras
(see the 2026-08-12 hardware smoke test entries above). Never fails
camera open: `cap.set()` returning `False` just means this device doesn't
support that control, the same tolerance `ids_camera.py`'s
`IsAvailable()` guard gives an axis the hardware doesn't expose.

**Not the same shape as `ids_camera.py`'s convergence, and that's a real
platform limitation, not a stylistic choice.** GenICam's
`ExposureAuto`/`GainAuto` nodes report back `"Off"` once `Once` mode
actually finishes, so `_converge_auto_exposure()` polls for that signal
with a timeout that only fires if convergence is genuinely stuck (see
`IdsCameraAutoExposureTimeoutError`). UVC/DirectShow via
`cv2.VideoCapture` exposes no equivalent "has this converged yet" flag —
there is nothing to poll. So this is a fixed warmup window followed by an
unconditional lock attempt, not poll-until-done, and there's no
equivalent timeout-error path: a fixed window can't "get stuck," it just
elapses.

**Explicitly unverified against real hardware as of this entry.** The
`0.25` value for `CAP_PROP_AUTO_EXPOSURE` is a commonly cited convention
for "manual mode" across various OpenCV+webcam combinations, not a
documented standard the way GenICam's node model is — `cv2.VideoCapture`
property behavior via DirectShow is well known to vary per device/driver,
the same category of uncertainty this project already treats
`vendor/ids_peak_api.txt` as the antidote for on the IDS side, except
there's no equivalent authoritative reference for arbitrary UVC webcams
to check against. The real ELP camera happened to be unplugged when this
was implemented (confirmed via `Get-PnpDevice` — no camera-class device
present), so this shipped as best-effort per explicit instruction rather
than blocking on hardware access. **Still needs confirming against the
real camera:** whether `0.25` actually engages manual exposure on this
device (vs. e.g. `0`/`1`/`3` conventions some drivers use instead) and
whether `CAP_PROP_AUTOFOCUS` does anything on a device that may be
fixed-focus to begin with (in which case `cap.set()` returning `False`
here would be the correct, expected outcome, not a bug). Update this
entry once verified.

---

## 2026-08-19 — `setup.ps1`: script the environment bootstrap, not role assignment

**Decided:** Add `setup.ps1` at the repo root, a PowerShell script covering
SETUP.md Section 1 (venv + `requirements.txt`) and optionally Section 2's
`requirements-ids.txt` install, ending with a check for whether the IDS
peak SDK runtime is actually importable and a pointer to `settings.py` as
the next step. It does not touch `config.json` or camera role assignment.

**Why:** Deploying to a new PC (see CLAUDE.md's setup story generally)
turned out to have two different kinds of friction bundled together: (1)
Python/venv/pip — mechanical, identical every time, safe to script — and
(2) the IDS peak SDK — a real third-party MSI/EXE with kernel drivers that
no script of ours can install or wrap, and where the interesting choices
(extended vs. standard setup, whether this machine drives the slit lamp)
are exactly the human judgment calls SETUP.md Sections 2-3 already walk
through. Scripting (1) removes real, repetitive friction; scripting (2)
isn't possible and wasn't attempted — `setup.ps1` just detects the outcome
(bindings installed, runtime importable or not) and tells the operator
which SETUP.md section to go read.

This is a distinct thing from ROADMAP's "Device compatibility & camera
setup system" entry's rule that role assignment is "explicitly *not* a
one-shot install script" — that rule is about `settings.py` staying a
persistent, re-run-any-time tool for *which physical device fills which
role*, not a general objection to automating setup steps. `setup.ps1`
doesn't do role assignment at all; it prepares the environment and then
hands off to `settings.py` unchanged. Re-running `setup.ps1` itself is
also safe (reuses an existing `.venv`, `pip install` is idempotent) — it
follows the same "repeatable tool, not a one-shot installer" spirit for
the step it *does* own, it just owns a different step. Amended a
clarifying parenthetical into ROADMAP's existing line rather than leaving
it to read as a broader rule than it was.

**Rejected (at first, then partially revisited — see below):** a full GUI
install wizard covering the entire process end-to-end, including the IDS
SDK. Still rejected in that full form: it would not actually remove the
SDK's driver-install friction (still a third-party installer requiring
the same human choices), and this project's actual remaining audience for
"set this up on a new machine" is a technician following SETUP.md, not a
student — the kiosk-facing simplicity CLAUDE.md's "Who uses it" demands
is about `app.py`, not the setup path.

---

## 2026-08-19 — `setup_wizard.py`: a GUI front end over setup.ps1, not a rewrite of it

**Decided:** Add `setup_wizard.py`, a paged (Welcome → IDS question → run →
finish) tkinter GUI that shells out to the *same* `setup.ps1`, passing the
IDS-cameras answer as `-DriveIds Yes|No` instead of `setup.ps1`'s own
interactive `Read-Host` prompt, and streams its stdout live into a log box.
`setup.ps1` gained a `-DriveIds` parameter for this; called with no
argument (a plain `.\setup.ps1` from a terminal) it still prompts
interactively, unchanged.

**Why a wizard now:** direct follow-up request after the `setup.ps1`
decision above, once "make it easier" specifically meant "GUI, not a
terminal prompt" rather than "eliminate the SDK-install step" (that step
still can't be automated — see above). Shelling out to `setup.ps1` rather
than reimplementing venv/pip logic in Python keeps exactly one copy of
that logic, consistent with this codebase's existing aversion to
duplicated logic (`qt_image.py`'s `bgr_to_pixmap` extraction is the
precedent).

**Why tkinter, not PySide6:** every other GUI tool here (`app.py`,
`preview.py`, `settings.py`) uses PySide6, but PySide6 is one of the
packages `setup.ps1` installs — a PySide6-based wizard couldn't run on the
truly fresh machine it's meant for, before `requirements.txt` has been
installed. tkinter ships with the standard python.org Windows installer,
so it works at that earlier point. This is a deliberate one-off exception
to "use PySide6 for GUIs" — the ordering constraint here doesn't apply to
`settings.py`/`preview.py`, which only ever run inside an already-set-up
venv.

**Why the subprocess call happens in a background thread, polled via a
queue:** `subprocess.Popen(...).stdout` iteration blocks, and Tkinter
widgets aren't thread-safe to update directly from a worker thread — the
standard pattern is a worker thread pushing lines onto a `queue.Queue`
and the Tk main loop draining it via `after()`, which is what `RunPage`
does. Confirmed working via a headless smoke test (constructed the
wizard, drove it through every page without `mainloop()`, and separately
verified a real `setup.ps1` invocation it kicked off completed and exited
cleanly with no leftover process or repo changes).

**Rejected:** reimplementing the venv/pip/IDS-bindings logic directly in
Python inside the wizard instead of shelling out to `setup.ps1`. Would
avoid a subprocess boundary, but at the cost of two independent
implementations of the same install steps needing to be kept in sync —
worse than the subprocess/streaming-output complexity it would save,
especially since `setup.ps1` is also meant to keep working standalone
for anyone in a terminal.

---

## 2026-08-20 — Removed the "will this machine drive real IDS cameras?" question

**Decided:** `setup.ps1` no longer asks (interactively or via the
`-DriveIds` parameter above); it unconditionally installs
`requirements-ids.txt` and runs the runtime check every time. `setup_wizard.py`
loses `OptionsPage`/`drive_ids` entirely — Welcome goes straight to Run.
**This supersedes the `-DriveIds Yes|No` mechanism described in the
2026-08-19 `setup_wizard.py` entry above**, which is left as-written for
the historical record rather than edited.

**Why:** every machine this installer actually targets is a real
deployment running the prescribed slit lamp/BIO cameras (CLAUDE.md's
Hardware table), not a bare dev checkout — a dev machine like the one
this project is built on doesn't run `setup.ps1` at all, it already has
its environment set up ad hoc. The question was answering a case that
doesn't occur in practice, at the cost of one more decision a technician
has to get right (and one more way to end up in the "answered No by
mistake, `settings.py` shows empty IDS candidate rows" state documented
in the ROADMAP's IDS-installer entry). Removing it doesn't change
behavior for the actual audience, only removes a place they could
misclick.

**Not revisited:** whether a genuinely `SyntheticCamera`-only development
machine should be able to skip `requirements-ids.txt`. It still can —
just by not running `setup.ps1` at all and installing
`requirements.txt` directly, which is exactly what this project's own
dev machine has always done.

---

## 2026-08-20 — Frozen-exe installer built: PyInstaller + Inno Setup, config paths, technician-configurable sessions folder

**Decided:** Executed ROADMAP.md's "Distribute a frozen-exe installer,
not a Python source bootstrap" plan. `app.py`/`settings.py` freeze into
`app.exe`/`settings.exe` via `packaging/app.spec`/`packaging/
settings.spec`; `packaging/sidebyside.iss` (Inno Setup) packages those
plus a bundled, interactively-launched copy of the IDS peak *extended*
installer into one real installer. `config.py` gained
`resolve_default_config_path()`/`resolve_default_sessions_dir()`,
splitting on `is_frozen()` (PyInstaller's `sys.frozen`): relative to CWD
in dev/test (unchanged), under `%ProgramData%`/`%PUBLIC%\Documents`
under a frozen install. `AppConfig` gained an optional `sessions_dir`
field, technician-set via a new Browse-button field in `settings.py`
(not a per-role `DeviceRow` — recordings aren't a camera). New
`PACKAGING.md` documents the developer-only build procedure;
`SETUP.md`/`CLAUDE.md`'s module table updated to mark `setup.ps1`/
`setup_wizard.py` as developer-only tooling, no longer part of any path
a clinic machine goes through.

**Why sessions_dir needed to become config-driven, not just relocated:**
originally planned as a fixed `%PUBLIC%\Documents\sidebyside\sessions`
default (mirroring `config.json`'s `%ProgramData%` placement) — revised
during implementation because recordings are literally "the actual
deliverable handed to a student" (CLAUDE.md), and a technician needs to
be able to point that somewhere else (a network share, an external
drive) without editing `config.json` by hand. `resolve_default_sessions_dir()`
is still what pre-fills the picker and what `app.py` falls back to if
`sessions_dir` is absent — existing/dev-mode `config.json` files and
every existing test fixture needed no changes, confirmed via Explore
before writing any code (`test_config.py`/`test_settings.py` never
depend on `DEFAULT_CONFIG_PATH`'s actual value; the only bare
`load_config()` call anywhere is `app.py:439`).

**The PyInstaller freeze needed no hidden-imports or `--collect-all`
overrides at all** — confirmed empirically, not assumed: `pyinstaller-
hooks-contrib` (installed alongside PyInstaller 6.22.2) already covers
PySide6, `cv2`, `av`, and `comtypes.client` (the `pygrabber`/
`uvc_enumeration.py` dependency), and PyInstaller's own binary-dependency
scan picked up `ids_peak`/`ids_peak_ipl`'s native DLLs without any
vendor-specific hook. Verified by actually running the frozen `app.exe`
three ways, not just checking the build exited 0: `--synthetic` (all-
synthetic path), `--third-person-synthetic` (forces the real `from
ids_camera import IdsCamera` import and `ids_peak`/`ids_peak_ipl` module
load at startup, using this dev machine's real slit-lamp/BIO serials from
`config.example.json`), and confirming both `config.json` and `logs/`
correctly resolved to `%ProgramData%\sidebyside\...` once frozen. All
three ran cleanly with no import or DLL-load errors. Real-camera-attached
verification of `IdsCamera.start()` itself is still a follow-up for
whoever has hardware attached, same caveat this project always carries
about dev-machine hardware access.

**`upx=False` in both specs, deliberately:** UPX-compressed executables
are a known source of false-positive antivirus flags, a real risk on a
locked-down clinic machine and not worth the smaller file size.

**Inno Setup script specifics:**
- Bundled installer file expected at a fixed, version-agnostic path,
  `vendor/ids-peak-win-extended-setup-64.exe` (gitignored, same
  convention as `vendor/ids_peak_api.txt`) — deliberately not named with
  today's version number, so the `.iss` script never needs editing just
  because IDS ships a new release; whoever builds the installer
  re-downloads and renames.
- `[Run]`'s `Filename` has no silent switches — `waituntilterminated`
  (Inno's default for a non-`postinstall` entry, made explicit) pauses
  this installer's own wizard while the technician clicks through IDS's
  real one, same as if they'd double-clicked it directly. This is
  bundling for convenience, not a reopening of the "Can setup.ps1 drive
  the IDS peak SDK installer?" entry's silent-vs-interactive decision.
- Added a `Check:` guard (`IdsPeakAlreadyInstalled`, checking
  `DirExists('{pf}\IDS\ids_peak')`) so re-running the installer later
  (e.g. to update the app itself) doesn't force a technician back through
  IDS's wizard every time it's already installed. That install path was
  confirmed directly on this machine during the earlier EULA/licensing
  investigation (see ROADMAP.md's IDS-installer entry) — more version-
  independent than checking a specific product GUID in the Uninstall
  registry key, which changes across IDS peak releases.
- Compiled successfully end to end (`packaging/installer_output/
  sidebyside-setup.exe`, ~513MB — expected, given it embeds the 356MB
  IDS installer). **Not run** on this machine as part of this work — that
  writes to Program Files/Start Menu and chain-launches a real
  third-party installer, confirmed as a deliberate stop point before
  doing so on a real or disposable machine, per `PACKAGING.md`.

**Rejected:** hand-writing the `.spec`/`.iss` files from a blank template
based on assumed dependency needs. Built empirically instead — a first
bare `pyinstaller app.py`/`pyinstaller settings.py` CLI run, actually
launching the result, then encoding whatever that run actually needed
(nothing extra, as it turned out) into the checked-in spec files. Matches
this project's existing preference for integration-style verification
over assumed-correct configuration.

## 2026-08-25 — Missing-config startup failure was silent in the frozen exe

Found by actually running `sidebyside-setup.exe` on a machine with no
`config.json` yet (a fresh Windows Sandbox session, standing in for a
never-before-configured clinic machine) — exactly the "not verified" gap
the 2026-08-20 entry above flagged. The installer itself worked correctly
(IDS peak's wizard launched and completed, both shortcuts appeared), but
clicking the `app.exe` Desktop shortcut produced nothing visible at all.

**Root cause:** `app.py`'s `main()` handled a missing/malformed
`config.json` by logging the error and returning 1, on the reasoning
(recorded in the removed comment) that this is "a technician setup
error... it never needs Qt at all." That was true when the only way to
run this was `python app.py` from a terminal, where `logger`'s
`StreamHandler` reaching `stderr` was actually visible. `packaging/
app.spec` builds `app.exe` with `console=False` (deliberately, for a
windowed kiosk app), and Explorer launches a Desktop shortcut with no
console attached at all — so that `stderr` write reaches nobody. The
`RotatingFileHandler` still captured the error durably in `LOG_FILE`
(`%ProgramData%\sidebyside\logs\app.log`), so the information wasn't
*lost*, but nothing pointed a technician at it. This directly contradicts
CLAUDE.md's "failures must be loud and early" — a double-click producing
literally nothing is quieter than the "black pane" that principle already
calls out as the worst case.

**Fix:** `QApplication.instance() or QApplication(sys.argv)` moved before
the `load_config()` call (previously constructed only after it succeeded,
further down `main()`), so a `ConfigError` can show `QMessageBox.critical`
before returning 1 — matched by a regression test
(`test_app.TestMissingConfigStartup`) mocking `load_config` to raise and
asserting the dialog fires. `QApplication.instance() or ...` rather than
an unconditional constructor call: makes `main()` safe to call from a
test process that already created one at module import time (this test
file's existing `_qt_app = QApplication.instance() or QApplication([])`
pattern), with no behavior change in real usage where no instance exists
yet when `main()` runs.

Verified by rerunning the frozen `app.exe` directly with no
`%ProgramData%\sidebyside\config.json` present: the process now stays
alive holding a blocking dialog open, instead of exiting within
milliseconds as it did before the fix. Installer recompiled with the
fixed `app.exe`; re-verification on a clean machine is the immediate
follow-up, same as the 2026-08-20 entry's original "not yet verified"
item this replaces.

## 2026-08-25 — `IdsPeakAlreadyInstalled()` checks a version, not just a folder

Follow-up investigation into the restart-prompt friction from the
Sandbox re-test above led to comparing how other real optometry/
ophthalmology device vendors handle IDS peak deployment — specifically
Haag-Streit's EyeSuite and Keeler's own Kinexis (Vantage Plus Digital
BIO) installers, both supplied by the user for analysis. Binary/archive
inspection (embedded-string scanning for the InstallShield-vs-Advanced-
Installer-vs-Inno-Setup engine question, then `innounp` — the standard
Inno Setup archive unpacker — to list Kinexis's actual embedded file
table) found:

- **EyeSuite** (Advanced Installer, MSI-backed) bundles and installs a
  full IDS peak SDK + both transport layers (`ids_ueyegentl`,
  `ids_u3vgentl`) via a real named-property silent install (`/qn`,
  `APPDIR=`) — confirming Advanced Installer's silent path is genuinely
  robust, unlike IDS's own InstallScript-based installer.
- **Kinexis** (Inno Setup) bundles `ids_peak_2.9.0.0.exe` plus a real,
  working InstallShield response file and drives it exactly the way this
  project's earlier IDS-installer entry described hypothetically:
  `ids_peak_2.9.0.0.exe /s /f1".\Install_Setup.iss"`, no exit-code check
  afterward beyond a flat `timeout /t 4`. The response file's own
  `[...SetupType2-0] Result=303` / `Result=304` entries are exactly the
  positional, non-semantic dialog-index recording described in that
  entry — real confirmation, not a hypothetical. It does *not* enable the
  uEye Transport Layer, consistent with the Vantage Plus Digital's camera
  being native USB3 Vision (no Transport Layer needed) per CLAUDE.md's
  hardware table.

**The concrete, actionable finding:** Kinexis silently installs IDS peak
**2.9.0.0** — a version far older than `26.06.1`, what
`vendor/ids-peak-win-extended-setup-64.exe` currently bundles — into the
same `{pf}\IDS\ids_peak` location `IdsPeakAlreadyInstalled()` in
`packaging/sidebyside.iss` was checking with a bare `DirExists()`. A real
clinic machine that's only ever run Kinexis (plausible, since it's
Keeler's own software for the same BIO) would have that check wrongly
report "already installed" and skip the bundled installer entirely,
silently leaving a years-out-of-date SDK in place instead of the current
one `requirements-ids.txt`'s pinned Python bindings actually expect —
this was a real gap, not a hypothetical one, found by actually inspecting
a real vendor installer rather than assumed.

**Fix:** `IdsPeakAlreadyInstalled()` now checks
`{pf}\IDS\ids_peak\program\ids_peak.dll`'s own `FileVersion` via Inno's
`GetVersionNumbers`/`ComparePackedVersion`, against a new
`IdsPeakMinDllVersion` preprocessor constant (`"1.16.0.0"`) — confirmed
empirically to be what the currently-bundled `26.06.1` extended setup
actually installs, via `(Get-Item '...\ids_peak\program\ids_peak.dll').
VersionInfo.FileVersion` on this dev machine (which has run that exact
installer). IDS's installer-package version string (e.g. `"26.06.1"`) and
this DLL's own `FileVersion` (`"1.16.0.0"`) are unrelated numbering
schemes — confirmed by checking both directly, not assumed to match —
so the DLL's own version field is what has to be compared, and
`IdsPeakMinDllVersion` needs updating (the same empirical way) whenever
`vendor/ids-peak-win-extended-setup-64.exe` is bumped to a new IDS
release.

**A syntax pitfall hit while writing this fix, worth remembering:** Inno
Setup's Pascal `[Code]` section comments (`{ ... }`) don't nest, and don't
treat their own contents as inert — a literal `{pf}` constant reference
*inside* a `{ }`-delimited comment closes the comment early at that `}`,
silently turning the rest of the intended comment into parsed code
(`Error on line 96: Column 52: Syntax error.` was the actual symptom).
Fixed by switching that comment block to `(* ... *)` delimiters, which
don't conflict with literal braces, and by avoiding constant-reference
syntax inside comments in general going forward.

**Decision on the restart/silent-install question this whole thread
started from:** unchanged — still keep IDS's own bundled installer
interactive, with "choose Restart Later if prompted" as the
documentation fix (sidebyside's own files/shortcuts are already
installed by the time `[Run]` fires `ids-peak-win-extended-setup-64.exe`,
so nothing functionally depends on that restart happening immediately).
Kinexis proves silent driving is *possible* — a real vendor ships it —
but its own implementation (no error-checking beyond a flat sleep, a
now-confirmed-real version-drift case) is a live example of the exact
fragility risk the earlier entry was concerned about, not a
counterargument to it.

## 2026-08-25 — Silent IDS peak install, with verification Kinexis doesn't have

### Reversal

The entry directly above concluded "stays manual," and the 2026-08-19
entry before that reached the same conclusion for `setup.ps1`. Both are
now superseded: `packaging/sidebyside.iss` drives IDS peak's installer
**silently**. What actually changed the calculus, across this
conversation's back-and-forth:

- **The "student finds a dead camera weeks later" framing was wrong.** A
  technician runs `settings.py` and uses its Preview button right after
  installing, before ever handing the machine to a student -- a bad
  silent install surfaces there, close in time to the actual cause, not
  weeks later disconnected from it.
- **Version-drift risk assumes a careless process; this project doesn't
  have one.** One developer (the user) controls every
  `vendor\ids-peak-win-extended-setup-64.exe` bump and can re-record
  deliberately each time, rather than a team where "someone forgot" is
  realistic.
- **The installer's own success signal was never the only available
  one.** `IdsPeakAlreadyInstalled()`'s real `ids_peak.dll` version check
  (previous entry) already exists and can be run *after* a silent
  install, not just before one -- closing the actual gap in Kinexis's
  own approach (it trusts a silent replay with zero verification) without
  needing a human to watch the wizard to get that verification.

### What was built

`InstallIdsPeakSilently()` in `packaging/sidebyside.iss`'s `[Code]`
section, called from `CurStepChanged(ssPostInstall)` (replacing the old
declarative interactive Run entry entirely):

1. Skips entirely if `IdsPeakAlreadyInstalled()` already passes (unchanged
   behavior from the previous entry).
2. Otherwise runs `ids-peak-win-extended-setup-64.exe /s /f1"<response
   file>" /f2"<log file>"` via `Exec()`, capturing the real exit code --
   this is the same `/s /f1` mechanism Kinexis uses in production (see
   previous entry), just with the verification it lacks layered on:
   - `Exec()` returning False, or a nonzero `ResultCode`, is a **hard
     failure** (Windows blocked it, the installer crashed) -- shown as an
     explicit error dialog pointing at `SETUP.md`'s manual steps, not
     swallowed.
   - Even `ResultCode = 0` isn't trusted as the whole story: a stale
     response file can replay cleanly against a *different* dialog layout
     in a newer IDS peak version and report success while silently
     picking the wrong components -- exactly the failure mode discussed
     in the previous entry. `IdsPeakAlreadyInstalled()` runs *again*
     afterward as the real verification; if it still fails, that's shown
     as its own explicit error rather than assumed fine because the exit
     code was 0.
3. `NeedRestart(): Boolean` -- Inno Setup's own built-in hook, returning
   True only when this run actually installed and verified IDS peak.
   Needed because the response file's recorded answer to IDS's own
   restart prompt is deliberately "restart later" (below) -- with nobody
   watching a silent install, that answer just fires unprompted if
   recorded as "restart now," so it has to defer, which means *something*
   still needs to surface "you should restart" to the technician. Inno's
   native Finished-page restart prompt is that surface, rather than a
   custom dialog.
4. The IDS installer's own `/f2` log now writes to `{app}\
   ids-peak-install.log` (not `{tmp}`, which Inno wipes once setup
   finishes -- a log path a technician can't actually open after the fact
   isn't useful in an error message).

### Recording the response file

`vendor\ids-peak-response.iss`, recorded via `<installer>.exe /r /f1"..."`
on a **fresh Windows Sandbox session** (not this dev machine, which
already has IDS peak -- recording against an already-installed machine
gets InstallShield's Modify/Repair/Remove flow instead of a fresh-install
one, recording the wrong thing). Full steps in `PACKAGING.md`. Two things
learned doing this for real, not assumed:

- **This IDS peak version (26.6.1) is better-behaved than Kinexis's much
  older bundled 2.9.0.0.** Its `SdComponentTree` dialog records **named**
  component keys (`UEyeSupport\UEyeDrivers`, `USB3VisionProducer`, ...),
  not pure numeric positions the way the older `SetupType2` dialog does
  for the top-level install type. Doesn't remove the positional-replay
  risk in general (the top-level dialogs are still numeric `Result=`
  codes), but it's a real, observed data point that "positional" isn't
  uniformly true across every dialog in every IDS peak version.
- **The recorded restart answer matters more here than in the abandoned
  interactive design.** Told the user explicitly, before recording: since
  the replay is silent, whatever gets recorded at the final restart
  prompt fires automatically on every future clinic machine with nobody
  present to answer it -- recording "restart now" would mean an
  unannounced reboot on a real clinic machine every time this response
  file gets replayed. Recorded "No, I will restart later"
  (`SdFinishReboot-0`: `Result=1, BootOption=0`), consistent with
  `NeedRestart()` above picking up the slack instead.

### A recurring Pascal Script syntax pitfall (three more hits, same bug)

The 2026-08-25 "checks a version, not just a folder" entry above already
noted that a literal `{pf}` inside a `{ }`-delimited Pascal comment closes
the comment early, since Inno's Pascal comments don't nest. Building this
feature hit the *same* bug twice more in one sitting -- `{app}`/`{tmp}`
inside a `{ }` comment, and separately a literal `[Run]` inside a `{ }`
(now `(* *)`) comment, which isn't a Pascal nesting issue at all but a
different mechanism: Inno's own section-tag scanner runs a line-based
pass looking for `[SectionName]`-shaped lines *before* Pascal comment
parsing happens at all, so a bracketed reference to another section's
name inside any comment can break section detection regardless of
comment style. Take away for next time: avoid literal `{constant}` and
`[SectionName]` text inside any `[Code]` comment, in any delimiter style
-- reword around it instead of assuming the comment is inert.

### Status

Built and compiles cleanly (`packaging\installer_output\sidebyside-setup.exe`).
**Not yet verified end-to-end** on a real clean machine -- the sandbox
session used to record the response file now has IDS peak installed from
that recording, so it can't also be the clean-machine test for the silent
replay; that needs a *separate* fresh sandbox session. Also not yet
verified: the deliberate-failure path (temporarily renaming/breaking
`vendor\ids-peak-response.iss` and confirming the error dialogs actually
fire instead of silently continuing).

## 2026-08-25 — Silent IDS peak install: the restart prompt never showed

First real Sandbox test of the silent-install build (previous entry)
surfaced a bug: after a genuinely fresh, verified-successful silent IDS
peak install, Setup went straight to the plain "Completing the sidebyside
Setup Wizard" Finished page -- no restart-choice radio buttons, even
though `NeedRestart()` returned `IdsPeakInstalledThisRun`, which
`InstallIdsPeakSilently()` had just set `True`. No error dialog either,
and `ids_peak.dll` was confirmed present on disk -- so the install itself
genuinely worked; only the restart notification was silently missing.

### Root cause, confirmed empirically, not guessed

Three throwaway `.iss` test scripts (compiled and run with `/VERYSILENT
/LOG=`, entirely outside the sandbox loop, to avoid another 15-minute
round trip per iteration) isolated this precisely:

1. A script returning a **hardcoded** `NeedRestart(): Boolean := True`
   worked correctly (`Need to restart Windows? Yes` in Setup's own log).
2. A script matching the *real* structure -- a global flag set to `True`
   inside `CurStepChanged(ssPostInstall)`, read back by `NeedRestart()`
   -- did **not** work (`Need to restart Windows? No`), reproducing the
   bug exactly.
3. Adding `Log()` calls to every step nailed the exact ordering:
   ```
   CurStepChanged called with CurStep=1     (ssInstall)
   Creating directory: ...                   (Files/Icons written here)
   NeedRestart() called, MyFlag=False        <- queried HERE
   CurStepChanged called with CurStep=2     (ssPostInstall)
   Setting MyFlag to True                    <- too late, already asked
   Need to restart Windows? No
   ```
   Inno queries `NeedRestart()` exactly once, right after Files/Icons
   finish writing but **before** `CurStepChanged` is ever called with
   `ssPostInstall`. Nothing re-queries it afterward. A flag set from
   inside `ssPostInstall` is therefore structurally always one step too
   late, regardless of what the flag's value is -- this wasn't a
   Pascal bug, it was a wrong assumption about Inno's own call order
   (confirmed against Inno's actual source on GitHub,
   `jrsoftware/issrc`, which shows the internal `NeedsRestart` decision
   and its "Need to restart Windows?" log line both happening inside the
   same `Install` procedure that later calls `CurStepChanged(ssPostInstall)`
   -- not after it).

### Why not just move the IDS install earlier (to `ssInstall`)

That would fix the ordering, but reopens the exact risk `ssPostInstall`
was chosen to avoid: `ssInstall` fires *before* `[Files]`/`[Icons]` are
written, so if the silent `Exec()` call ever hung indefinitely (not just
returned a nonzero code, which is already handled either way) rather
than erroring, sidebyside's own `app.exe`/`settings.exe`/shortcuts might
never get installed at all. Keeping the IDS install in `ssPostInstall`
preserves "sidebyside itself is safe regardless of what happens to the
bundled IDS installer" from the original 2026-08-20 frozen-exe entry.

### Fix

Dropped `NeedRestart()` and the `IdsPeakInstalledThisRun` global
entirely. `InstallIdsPeakSilently()` now shows an explicit
`MsgBox(mbInformation)` directly -- "A restart is recommended..." --
right after a verified-successful install, instead of depending on
Inno's internal call order at all. Simpler code, and a message that's
guaranteed to actually appear rather than a native mechanism with a
timing gotcha this project only found by instrumenting it directly.
Matches CLAUDE.md's general "loud and early" bias better than the
native mechanism did anyway.

Verified against the real `sidebyside.iss` compiling cleanly; **not yet
re-verified in an actual fresh Sandbox** that the MsgBox appears at the
right moment -- that's the next real test, along with the
still-outstanding deliberate-failure-path check from the previous entry.

## 2026-08-25 — Silent IDS peak install: native restart page

Follow-up to the entry directly above. The MsgBox fix worked, but wasn't
what was actually wanted -- a plain "OK"-only popup, not a real Restart
now/Restart later choice like IDS's own installer. First attempt at that
(`TaskDialogMsgBox` with custom button labels, triggering `shutdown.exe
/r` directly on "Restart now") worked but was still a separate popup, not
integrated into Setup's own Finished page. Getting the *native* Finished-
page radio-button choice back on the table required working out, and
then accepting, what it actually costs -- summarized here since the
reasoning spans several back-and-forths, not one decision.

**Why `ssPostInstall` can't produce the native choice, confirmed, not
assumed:** `NextButtonClick` doesn't fire for the Finished page's Finish
button (confirmed against Inno's own documentation) -- so even manually
toggling `WizardForm.YesRadio`/`NoRadio` visibility from `ssPostInstall`
would show a choice with no supported way to read back which one got
picked when Finish is clicked.

**Why moving the whole install to `ssInstall` isn't just risky, it's
broken outright, as literally described:** a purpose-built empirical test
(a `[Files]` entry logged for both `{tmp}`-file existence and `{app}`-dir
existence at each `CurStepChanged` step) showed both are `0` at
`ssInstall` and `1` by `ssPostInstall` -- Setup's normal file-copy phase,
`{tmp}` extraction included, hasn't run yet when `ssInstall` fires. Naively
moving `Exec()` there would point at a 356MB file that doesn't exist.

**What actually made it work:** `ExtractTemporaryFile()` (confirmed via
Inno's own docs: pulls a specific `[Files]` entry, flagged `dontcopy`, out
of the archive on demand, ahead of Setup's normal bulk-copy timing) plus
`ForceDirectories(ExpandConstant('{app}'))` to manually create the install
directory early for the log path. A combined throwaway test (dontcopy +
ExtractTemporaryFile + ForceDirectories + setting the restart flag, all
from `ssInstall`) confirmed the full chain works: `NeedRestart()` sees the
flag as `True`, and Setup's own log shows `Need to restart Windows? Yes`.

**The real tradeoff, worked through with direct pushback, not glossed
over:** moving the silent IDS install to `ssInstall` means sidebyside's
own files/shortcuts are no longer guaranteed installed before it runs --
the entire reason `ssPostInstall` was chosen in the first place. Several
rounds of direct questioning actually about what this protects against in
practice:
- A genuine indefinite hang (not a clean error -- those are already
  caught by the exit-code/log checks either way) is the only scenario
  where ordering matters at all.
- Inno has no timeout/cancel on `Exec(..., ewWaitUntilTerminated, ...)`,
  so a real hang can only ever be resolved by a technician force-killing
  the process by hand -- there is no automatic rollback in either
  ordering once that happens; whatever was already written to disk stays
  written.
- Without a working system-wide IDS peak install, neither `app.exe` (no
  instrument cameras) nor `settings.exe` (no IDS devices to detect) can
  do their real job anyway -- "sidebyside is installed" was overstating
  the benefit as "sidebyside works," which it doesn't, without IDS peak.
- The odds of an actual indefinite hang (as opposed to a clean, already-
  handled error) are low, and `WizardForm.StatusLabel.Caption` already
  identifies which step is running if a technician comes back to a stuck
  installer either way.

Net conclusion: the `ssPostInstall` ordering's real remaining benefit was
narrower than originally presented (diagnostic clarity + not re-doing the
fast file-copy step on retry, not "a working app"), the failure mode it
protects against doesn't resolve cleanly under either ordering regardless,
and the native restart-page UX was worth that narrower tradeoff. Decision:
switched to `ssInstall`.

**What changed in `packaging/sidebyside.iss`:**
- `vendor\ids-peak-win-extended-setup-64.exe` and
  `vendor\{#IdsPeakResponseFile}`'s `[Files]` entries: `deleteafterinstall`
  → `dontcopy`, moved to the top of `[Files]` (solid-compression
  decompression cost grows with position in the archive).
- `InstallIdsPeakSilently()`: calls `ExtractTemporaryFile()` for both,
  then `ForceDirectories({app})`, before doing anything else. Ends by
  setting `IdsPeakInstalledThisRun := True` on verified success (the hard-
  failure and verification-failure `MsgBox`es are unchanged from the
  previous entry -- those still apply regardless of timing).
- `NeedRestart()` and the `IdsPeakInstalledThisRun` global are back, this
  time actually working -- the `TaskDialogMsgBox`/`shutdown.exe` block
  from the previous entry is removed entirely.
- `CurStepChanged`: `ssPostInstall` → `ssInstall`.

Verified against the real script compiling cleanly, including the
`vendor\` files still landing in the compressed archive despite the
`dontcopy` flag change (confirmed via the compile log's `Compressing:`
lines). **Not yet re-verified end-to-end in a fresh Sandbox** -- same
outstanding real test as the previous two entries, now including
confirming the native Finished-page choice actually appears and that
choosing "Yes" there genuinely triggers a restart.

---

## 2026-08-25 — Built the exposure/gain calibration feature (not yet hardware-verified)

**Decided:** Implemented ROADMAP.md's "In-app exposure/gain calibration"
design in full: `IdsCamera` gains `needs_manual_calibration()`,
`get_/set_exposure_time_us()`, `exposure_time_range_us()`,
`get_/set_gain()`, `gain_range()`, and `auto_calibrate()`; `InstrumentConfig`
gains optional `exposure_time_us`/`gain` fields that `_open()` applies
per-axis every session; `settings.py`'s `PreviewDialog` shows exposure/gain
sliders plus an Auto-Calibrate button whenever the previewed camera reports
`needs_manual_calibration()`, and `DeviceRow`/`SettingsWindow` round-trip
the resulting values into `config.json`.

**Why split `exposure_calibration.py` out of `ids_camera.py`:** the actual
median-brightness-and-correction-step math (`median_brightness()`,
`is_converged()`, `next_exposure_gain()`) has zero IDS SDK dependency, so
pulling it into its own module makes it unit-testable on this dev machine
(no `ids_peak` installed -- see CLAUDE.md's Environment section) instead of
being untestable dead weight inside a module this dev machine can't even
import. `test_exposure_calibration.py` covers it directly;
`ids_camera.py`'s `auto_calibrate()` is a thin loop calling into it plus
the real node reads/writes, same division of labor `compositor.py`/
`test_compositor.py` already model elsewhere in this codebase.

**Why `_open()` applies exposure_time_us/gain per-axis, not all-or-nothing:**
`InstrumentConfig`'s two fields are independently optional in the schema,
so a camera with e.g. working `ExposureAuto` but a calibrated manual `gain`
(unlikely today, not ruled out) gets exactly what config says for the axis
it specifies and today's auto-converge for the one it doesn't --
`_converge_auto_exposure()` grew `skip_exposure`/`skip_gain` params for
this rather than becoming two copies of near-identical code.

**What's NOT verified, and why this entry says so instead of claiming
otherwise:** this dev machine has no `ids_peak` installed and
`vendor/ids_peak_api.txt` doesn't exist here (it's gitignored, generated on
a machine with the SDK) -- CLAUDE.md is explicit that IDS method names must
come from that dump or hardware, not be invented. Every node-map call this
feature adds beyond what `_converge_auto_exposure()` already used
(`TryFindNode`/`IsAvailable`/`IsWriteable`/`SetCurrentEntry`, all
pre-existing and hardware-confirmed) is the `ExposureTime`/`Gain` float
node's `Value()`/`SetValue()`/`Minimum()`/`Maximum()` -- standard GenApi
`IFloat` accessor names, and the same `Value()`/`SetValue()` pattern this
file already uses for `Width`/`Height` (though those are integer nodes),
but not independently confirmed against real hardware or the dump. Flagged
in `ids_camera.py`'s module docstring and inline above those methods, and
in ROADMAP.md's status line for this entry, rather than presented as
verified.

**Rejected: a runtime brightness preflight check.** Already rejected in the
ROADMAP entry this implements, for the same reason (real false-positive
risk against a legitimately dim scene) -- not revisited.

**Also not done:** re-recording a hardware smoke test
(`tools/smoke_test_camera.py`) against the real slit lamp to confirm the
node accessor names above, and closing the loop on whether writing
ExposureTime/Gain from the GUI thread while the capture thread concurrently
calls `WaitForFinishedBuffer()`/`QueueBuffer()` on the same device is
actually safe -- assumed reasonable (this is how live exposure adjustment
during streaming works in GenICam generally, and how IDS peak Cockpit
itself already does it), not confirmed against this specific SDK's
concurrency guarantees.

---

## 2026-08-26 — Built the vignette/white-balance/fps-cap/backlight-compensation follow-ups

**Decided:** Implemented ROADMAP.md's 2026-08-26 entry in full: a centered
crop before brightness/color measurement, white balance (automatic or
manual-software depending on hardware), an acquisition frame-rate cap on
both cameras, and UVC backlight compensation.

**`exposure_calibration.py`:** added `center_crop()` (used by both
`auto_calibrate()` and the new `auto_white_balance()` -- one crop, reused
for both), `channel_medians()`, `is_white_balanced()`,
`next_balance_ratios()`. All pure and SDK-free, directly unit-tested in
`test_exposure_calibration.py` -- the only part of this work fully
verifiable on this dev machine (no `ids_peak` installed).

**`ids_camera.py`'s `_converge_auto_exposure()` was replaced, not wrapped,
by `_converge_auto_nodes(node_names)`:** exposure, gain, and (now)
white-balance convergence all follow the identical
Once-then-poll-until-Off-then-lock shape, so `_open()` now builds one list
of whichever `*Auto` nodes lack a manually-calibrated config value and
converges all of them in a single pass, rather than a second,
separately-polled loop bolted on for white balance. Rejected keeping the
old method as a thin wrapper: it had no external callers (no
`test_ids_camera.py` exists -- `ids_peak` isn't importable here), so there
was no compatibility cost to removing it, and keeping it alongside a
second loop would have been strictly worse than one combined pass. Renamed
`_AUTO_EXPOSURE_TIMEOUT_S`/`_AUTO_EXPOSURE_MAX_FRAMES` to
`_AUTO_CONVERGE_TIMEOUT_S`/`_AUTO_CONVERGE_MAX_FRAMES` and
`IdsCameraAutoExposureTimeoutError` to `IdsCameraConvergenceTimeoutError`
to match (private/internal-only, no external references, so both renames
are low-risk).

**White balance, two cases, both handled because it's unknown which
applies to either camera:** a camera with `BalanceWhiteAuto` gets it
automatically, folded into the `_converge_auto_nodes()` pass, no config, no
UI. A camera without it (the likely case for the slit lamp, which already
lacks `ExposureAuto`/`GainAuto`) gets `needs_manual_white_balance()`,
`get_/set_red_balance_ratio()`, `get_/set_blue_balance_ratio()`,
`red_/blue_balance_ratio_range()`, and `auto_white_balance()` -- mirroring
the exposure/gain accessor shape exactly, backed by GenICam's standard
`BalanceRatioSelector`+`BalanceRatio` selector-then-value pattern (six
public methods, one private `_select_balance_ratio()` helper, so the
selector-set line isn't duplicated six times).

**Config validation is asymmetric with exposure/gain, deliberately:**
`InstrumentConfig` gained `red_balance_ratio`/`blue_balance_ratio`, but
unlike exposure/gain (independent hardware axes, validated independently),
these two are two facets of one concept -- `BalanceWhiteAuto=Once`
converges both together, there's no "auto blue, manual red" -- so
`config.py` rejects exactly one of the pair being present as a
`ConfigError` rather than silently accepting a half-specified state.

**`settings.py`'s white-balance controls are a separate block from
exposure/gain's, not merged into one abstraction:** a camera could
plausibly lack `ExposureAuto`/`GainAuto` *and* `BalanceWhiteAuto`
simultaneously, so both blocks can be visible on the same dialog at once,
and a single shared status label would have one calibration's result
message clobber the other's -- confirmed as an actual correctness
requirement, not just a style preference, by
`test_exposure_gain_and_white_balance_status_labels_stay_independent` in
`test_settings.py`. The only extraction was the repetitive slider-row
construction (`_add_slider_row()`), used by both blocks.

**Acquisition frame-rate cap:** both `IdsCamera` and `UvcCamera` gained an
independently-optional `target_fps` constructor param (`None` = untouched
free-run -- preserves every existing call site, including
`tools/smoke_test_camera.py`/`tools/dual_camera_smoke_test.py`, which never
pass it). `app.py` threads `cfg.recording.fps` into both instrument and
third-person camera construction. Motivation, confirmed by reading
`recorder.py`'s `_tick()`/`_drain_latest()` rather than assumed: the
recorder already discards any frame a camera captures faster than
`recording.fps` before encoding, so letting cameras free-run at native
~58-60fps produces zero benefit and burns exactly the USB bandwidth margin
CLAUDE.md's Hardware section flags as tight. `settings.py`'s Preview
cameras deliberately never receive `target_fps` -- Preview is single-camera
and technician-supervised, so the bandwidth concern this cap exists for
(two simultaneous instrument streams) never applies there.

**Rejected: capping frame rate by reading it back from the device instead
of config.** Not actually considered as a real alternative -- `target_fps`
is sourced from `recording.fps`, which DECISIONS.md's "config-driven
recording fps" entry already established as config-driven for its own
reasons (encoder pacing, not a camera capability). This entry only adds
that the *camera* should also respect that same number, not that the
number's source should change.

**Backlight compensation (UVC):** `uvc_camera.py`'s
`_lock_autofocus_and_exposure()` renamed to `_configure_capture()` (it now
does more than autofocus/exposure) and gained `cv2.CAP_PROP_BACKLIGHT`,
set *before* the existing warmup loop so the auto-exposure convergence that
warmup lets run happens with backlight compensation already active, not
after.

**Black balance was scoped out entirely, not just deprioritized** -- it
turned out to be the user conflating it with backlight compensation above,
not a separate thing actually wanted, so nothing was built for it. No
`get_/set_black_level()`, no config, no UI.

**What's NOT verified, stated plainly:** every new call into
`ids_camera.py`'s GenICam node map (`BalanceWhiteAuto`,
`BalanceRatioSelector`/`BalanceRatio`, `AcquisitionFrameRateEnable`/
`AcquisitionFrameRate`) is unconfirmed against real hardware or
`vendor/ids_peak_api.txt` -- this dev machine has neither. Also unverified:
whether `AcquisitionFrameRate` can be set *after* `AcquisitionStart` the
way `ExposureAuto`/`GainAuto` already are (confirmed working post-start),
since it more directly reconfigures stream timing than a pure value node;
if hardware testing shows otherwise, the call needs to move earlier in
`_open()`, before `data_stream.StartAcquisition()`. The `0.5` center-crop
fraction is a starting guess, not a measurement against real slit-lamp/BIO
footage. `_converge_auto_nodes()` itself is a refactor of
previously-hardware-verified control flow (exposure/gain), not purely
additive, so re-running `tools/smoke_test_camera.py` against both real
cameras is warranted even though per-axis semantics didn't change.
`cv2.CAP_PROP_BACKLIGHT`/`CAP_PROP_FPS`/`CAP_PROP_AUTO_WB` are, by
contrast, confirmed to exist on this dev machine (cv2 is installed here) --
lower risk, though their real-device *effect* still isn't verified. Full
test suite (134 tests) passes; nothing IDS-node-specific has
`test_ids_camera.py` coverage since `ids_peak` isn't importable here, same
precedent the exposure/gain feature was already shipped under.

---

## 2026-08-26 — `Net2860Camera`: 32-bit helper process for the older Vantage Plus BIO

**Decided:** A second, older-model Keeler Vantage Plus Digital BIO was
connected for testing. Built and hardware-verified `Net2860Camera`, a new
`BaseCamera` implementation for it, plus a 32-bit helper subprocess
(`net2860_helper.py`) and a small framed stdout protocol
(`net2860_protocol.py`) between the two. Not yet wired into
`config.py`/`app.py`/`kiosk.py`/`settings.py` -- this entry covers only the
camera module itself; making it selectable end-to-end in the running kiosk
app is separate follow-up work (`kiosk.py` needs zero changes for that
when it happens -- confirmed it's already generic over `BaseCamera` and
never branches on `InstrumentConfig.kind`).

**Hardware identity:** Not IDS Imaging hardware (VID `0x1409`) at all --
raw USB descriptor shows VID `0x20F1` (NET GmbH), PID `0x0004`,
`bInterfaceClass = 0xFF` (Vendor Specific, i.e. not UVC). Opening the unit
confirmed the actual capture silicon is an eMPIA EM2860 USB video bridge
chip (marking on the board: "eMPIA EM2860 PGNA7-014 1510-01AG"), OEM'd by
NET GmbH under their own VID/PID with their own driver rather than eMPIA's
reference identity (`0xEB1A`). The sensor line is a 1/2" CCD (NET GmbH
"KS722OUP" board), interlaced PAL, negotiating `720x576` in practice
(device-derived at runtime, not hardcoded -- see below).

**Why neither `ids_camera.py` nor `uvc_camera.py` can see it:** No
GenICam/USB3 Vision presence (not IDS hardware). No UVC/DirectShow-
video-capture-source-category presence either -- confirmed via
`uvc_enumeration.list_uvc_devices()`, which only saw the laptop's built-in
webcam and a phone virtual camera. The vendor driver (`net2860_usbx64.sys`
+ `netvecam4.ax`) registers a DirectShow filter as an ordinary COM class
(`{6B83EF35-8FB5-45CB-BFF4-0876FF6F31D5}`, registered name `"KS722OUP"`)
but NOT under `CLSID_VideoInputDeviceCategory`, so nothing that discovers
capture devices by enumeration (OpenCV, `pygrabber`, this project's own
`uvc_enumeration.py`) can find it. It has to be instantiated directly by
CLSID -- confirmed this is exactly the mechanism Keeler's own bundled app
("Kapture") uses internally, by reading `Kapture`'s `Logs\Capture.log`
(`AddFilterbyCLSID Name,pF = USB source,...`) from a prior Kapture install
already present on the dev machine. Kapture itself was not used or needed
for any part of this -- its license only gates its own app UI, not the
driver/filter, which is an ordinary Windows COM component independent of
it.

**Why a 32-bit helper subprocess, not in-process:** Every user-mode piece
of this vendor filter (`netvecam4.ax`, and the `Sample Grabber`/`qedit.dll`
registration used alongside it) is registered only in the WOW6432Node
(32-bit) COM view -- confirmed by reading PE headers directly:
`net2860_usbx64.sys` (kernel driver) is native x64, but `netvecam4.ax`,
`NET_USBIO_EMP1.dll`, and Kapture's own `Kapture.exe`/`VC2860.exe` are all
x86. File dates suggest this is simply a 2011 shipping decision NET GmbH
never revisited (the kernel driver is dated 2009, already 64-bit; the
32-bit-only user-mode filter is dated two years later) rather than a
technical requirement -- the product line appears dead since (NET GmbH's
current site has moved on to unrelated GenICam/USB3/GigE Vision products).
The project's venv Python is 64-bit, so this camera's capture code cannot
run in the same process as the rest of `sidebyside`; `net2860_camera.py`
launches a 32-bit Python subprocess (`net2860_helper.py`, under a separate
`.venv32/` -- see `setup_net2860_helper.ps1`) that does the actual
DirectShow work and streams frames back over a pipe. This mirrors
CLAUDE.md's "nothing outside a camera module may reference the vendor SDK"
rule, with the process boundary standing in for the module boundary
`ids_camera.py`/`uvc_camera.py` normally provide on their own --
`net2860_camera.py` itself never imports `comtypes`/`pygrabber`.

**Generic eMPIA driver considered and rejected:** Microsoft's own Update
Catalog (WHQL-signed, downloaded directly from
`catalog.s.download.windowsupdate.com`) has a genuine 64-bit eMPIA EM28xx
driver package (`emPRP64.ax`, `emBDA64.sys`, `emOEM64.sys`, dated 2015),
which would have eliminated the 32-bit constraint entirely. Rejected after
inspecting its `.inf`: all 54 hardware IDs it declares use eMPIA's own
reference VID (`0xEB1A`) only -- none match NET GmbH's OEM VID
(`0x20F1&PID_0004`). Using it would require hand-editing the INF to add
our hardware ID and enabling machine-wide test-signing mode (both kernel
drivers in the package are unsigned once the INF is modified) -- a
security-posture change affecting the whole machine, for an unverified
payoff (no guarantee this differently-designed board even initializes
correctly under a generic reference driver). The proven CLSID/32-bit-helper
path was already working by the time this was evaluated; not worth the
risk for an uncertain improvement.

**Wire protocol (`net2860_protocol.py`), why not named pipe/socket/shared
memory:** A subprocess stdout pipe with a small framed protocol (`RDY1`
handshake carrying device-derived resolution, `FRM1` per frame, `ERR1` on
failure) is the simplest correct option for what is always a 1:1
parent/child relationship -- process lifetime and pipe lifetime are
naturally tied together (no separate handle/port/segment to leak or clean
up), and it needed no new project dependency. Frame timestamps are
`time.monotonic()` taken in the helper at the moment its
`ISampleGrabberCB.BufferCB` callback fires, which is valid to compare
against the main process's own `time.monotonic()` since Windows'
monotonic clock (`QueryPerformanceCounter`) is one system-wide clock
domain, not per-process.

**`Frame.index` deviation:** Assigned by `net2860_helper.py`, incrementing
once per `BufferCB` callback -- not a vendor-reported sequence number,
since this filter's frame-numbering (if it has one at all) isn't
documented or observed anywhere, and inventing a read of one would be
guessing at an undocumented API surface the same way CLAUDE.md already
warns against for the IDS SDK. Same accepted deviation `uvc_camera.py`'s
`UvcCamera` already makes from `ids_camera.py`'s contract (a
locally-assigned counter standing in for a real FrameID), but assigned by
the helper rather than by `Net2860Camera` itself, since the helper is the
component actually sitting at the capture callback -- `net2860_camera.py`
is one pipe-read removed from that boundary, and re-counting there would
only count "frames this process happened to read," not "frames the source
produced."

**Verified against real hardware:** `tools/smoke_test_net2860_camera.py`
against the real camera through the actual `Net2860Camera`/`BaseCamera`
capture thread (not a standalone script): resolution negotiated as
`720x576`; 30 frames captured via `camera.read()` at roughly 14fps;
`Frame.index` increments contiguously 0-29 with no gaps. Captured image
content is real sensor noise (confirmed non-corrupted, non-garbage pixel
data) rather than a real picture, because the BIO's own illumination
wasn't on and it wasn't pointed at anything during this test -- a physical
setup issue, not a software one; see the mechanism verification earlier in
this investigation for the equivalent finding via a scratch proof-of-concept
before this module existed. Full test suite (158 tests) passes, including
new `test_net2860_protocol.py` (pure wire-format round-trip/error-case
tests, no mocking needed) and `test_net2860_camera.py` (mocks
`subprocess.Popen`, same boundary-mocking pattern `test_uvc_camera.py`
uses for `cv2.VideoCapture`) -- no `test_ids_camera.py`-style gap here,
since both are fully offline/hardware-independent.

**What's NOT done, stated plainly:** Not wired into
`config.py`/`app.py`/`kiosk.py`/`settings.py` -- this camera is not yet
selectable in the running app. Not packaged -- `net2860_helper.py` is not
frozen into a standalone exe, so a clinic install would currently need a
32-bit Python present, which `PACKAGING.md`'s frozen-exe distribution
model doesn't provide; freezing it is real, separate future work. No
`IAMStreamConfig`-based frame-rate control (mirroring `UvcCamera.
_apply_frame_rate_cap`) -- this vendor filter's stream-config support
hasn't been observed or tested, so it isn't claimed. `SUPPORTED_HARDWARE.md`
lists this under a new "Prototyped, not yet integrated" section rather
than "Confirmed tested" -- it doesn't fit that table's implicit "usable in
the app today" meaning (it isn't wired in or packaged), but calling it
merely "untested" alongside things nobody has ever tried would undersell
the real hardware verification above.

---

## 2026-08-26 — `Net2860Camera` wired into `config.py`/`app.py`/`settings.py`, real recording verified

**Decided:** Wired the previous entry's `Net2860Camera` into the running app
as `kind: "net2860"`, an alternative to `kind: "ids"` for the existing `bio`
instrument role -- not a new role. `kiosk.py` needed zero changes (confirmed:
already fully generic over `BaseCamera`, never branches on `kind`); the
picker still shows exactly two buttons.

**`config.py`:** `InstrumentConfig.serial` becomes `str | None`.
`_parse_instrument()` accepts `kind: "net2860"`, requiring only `label` and
loudly rejecting `serial`/`exposure_time_us`/`gain`/`red_balance_ratio`/
`blue_balance_ratio` if present (catches copy-pasting an `"ids"` entry and
only changing `kind`, rather than silently ignoring the leftover fields).

**`app.py`:** `_make_camera()` had no real `kind` dispatch before this --
it inferred real-vs-synthetic from `serial is None` and always built an
`IdsCamera`. Now takes the whole `InstrumentConfig` plus an explicit
`synthetic: bool`, branching on `inst.kind` (`net2860` -> lazily-imported
`Net2860Camera(label=name)`, same lazy-import-avoids-a-hard-dependency
reasoning as the existing `ids_camera` import).

**`settings.py`:** the BIO row's dropdown needed a net2860 candidate
alongside real IDS devices, but this camera has no device-manager-visible
category to enumerate (see the previous entry) -- so it's a **static**
candidate (`_net2860_candidates()`), always offered, BIO row only, not
device-scanned. `RowCandidate` gained a `kind` field so a row can mix
candidate kinds; the preview factory (`ids_preview_camera_factory` ->
`instrument_preview_camera_factory`) now receives the whole candidate and
branches on `.kind` instead of always building an `IdsCamera`. Save
(`_instrument_data()`) branches the same way, writing `{"kind": "net2860",
"label": ...}` with no `serial`/calibration keys. Load
(`_load_existing_config()`) preselects via `inst.kind` rather than
`inst.serial` when the kind isn't `"ids"` -- the static candidate's
`key="net2860"` doubles as its own sentinel, since there's exactly one of
this camera to select.

**Verified end-to-end with real hardware, headlessly** (no GUI-automation
tool available, so driven through the same production code the GUI calls --
`kiosk.py` is documented as "unit-testable headlessly" for exactly this
reason):
1. `settings.py` with real enumeration/factories: `rescan()` lists the
   net2860 candidate on the BIO row only; selecting it and calling the real
   `_instrument_data()` produces `{"kind": "net2860", "label": "BIO
   (legacy)"}`; the real default preview factory builds a working
   `Net2860Camera` that starts and returns a real frame
   (`shape=(576, 720, 3)`).
2. `app.py`'s real `_make_camera()` with a real `InstrumentConfig(kind=
   "net2860", ...)` and `synthetic=False` returns a working `Net2860Camera`
   producing real frames -- confirms the dispatch itself, not just that the
   class works standalone.
3. A full real recording via a real `KioskController` (real `Net2860Camera`
   for `bio`, the real UVC "Integrated Webcam" for third-person, no slit
   lamp -- none is attached to this dev machine right now, and
   `KioskController` doesn't require exactly two instruments): reached
   `READY`, recorded 3 real seconds, produced a real `composite.mp4`
   (2,739,629 bytes) and `session.json` with sane numbers -- composite
   `1360x576` (720+640 wide, `max(576, 480)` tall, matching
   `side_by_side`'s aspect-preserving letterbox), 31 composite frames at
   the 10fps target over ~3.1s. `bio`'s `dropped_frames: 46` against 31
   delivered is expected, not a bug -- the net2860 camera free-runs faster
   than the 10fps recording target used for this test, and
   `recorder.py`'s `_drain_latest()` deliberately discards the excess
   (CLAUDE.md: "prefer dropping frames over blocking a capture thread"),
   showing up correctly in the drop count exactly the way real hardware
   drops do.

**Tests:** `test_config.py` gained 4 cases (valid net2860 parse; rejects
`serial`; rejects each calibration field; still requires `label`).
`test_settings.py`'s `_make_window()` fixture updated (factory now receives
a candidate, not a bare target) plus 5 new cases (BIO-only candidate
presence, save shape, load preselection, preview routing). Full suite: 166
tests, all passing.

**What's still NOT done:** Packaging -- `net2860_helper.py` isn't frozen
into a standalone exe, so a clinic install built via `PACKAGING.md`'s
current process still needs a 32-bit Python present, which it doesn't
provide. Explicitly the next, separate step (per the user: wire in and
confirm it works first, then decide on packaging).

---

## 2026-09-01 — settings.py Preview leaked the IDS device when closed via Esc

**Decided:** `PreviewDialog` stops its camera from a slot connected to the
`finished` signal (plus an explicit teardown on the `__init__` control-build
failure path), not only from `closeEvent`.

**Why:** `PreviewDialog` runs modally via `.exec()`. `QDialog.reject()` --
which the Esc key triggers directly -- calls `done()`/`hide()` without ever
delivering a `QCloseEvent`, so a `closeEvent`-only teardown never ran
`camera.stop()`. Because `_on_preview_clicked` parents the dialog, the
leaked `IdsCamera` then stayed alive holding the IDS device open for the
rest of the settings session, and every subsequent Preview on that row
failed with `GC_ERR_RESOURCE_IN_USE` ("Module IDS/... is open already").
Only a full restart of `settings.py` recovered it. A second variant of the
same leak: `start()` succeeds, then one of the calibration-control builders
(which touch IDS nodes this codebase flags as unverified) raises inside
`__init__`, propagating out before the dialog is ever shown or closed.

`finished` fires for `accept()`, `reject()`, Esc and the window X alike, so
it covers every way `.exec()` can return. `_shutdown()` is idempotent
(reached from both `finished` and `closeEvent`) and tolerates being called
before `self.timer` exists.

**Rejected:** `WA_DeleteOnClose` -- `_on_preview_clicked` reads
`dialog.final_*` after `.exec()` returns, which would be use-after-free.
Overriding `reject()`/`done()` -- narrower than connecting `finished`, and
easy to miss a path.

**Tests:** `test_settings.py` gained `test_reject_stops_the_camera` and
`test_camera_stops_when_building_calibration_controls_raises`.

---

## 2026-09-01 — slit lamp has no white-balance nodes at all; `needs_manual_white_balance()` must check

**Decided:** `IdsCamera.needs_manual_white_balance()` returns True only when
the camera has no `BalanceWhiteAuto` **and** does expose the manual
`BalanceRatioSelector`/`BalanceRatio` nodes.

**Why:** Hardware-surfaced once the Preview device-leak fix (above) let
execution reach this path on the real slit lamp. The slit lamp via the uEye
Transport Layer's basic feature set exposes *neither* `BalanceWhiteAuto`
*nor* `BalanceRatioSelector` -- it has no white balance to control at all,
the same practical situation as the Keeler (which has working auto). The
old check keyed purely on `BalanceWhiteAuto` being absent, so it returned
True and `settings.py`'s `_build_white_balance_controls()` then crashed
with `NotFoundException` on `FindNode("BalanceRatioSelector")`, taking down
the whole Preview dialog.

This mirrors what `needs_manual_calibration()` already gets right for
exposure/gain by luck -- the slit lamp *does* expose `ExposureTime`/`Gain`,
so that path was fine; white balance was the one axis where the manual
fallback nodes are also missing.

**Still not hardware-verified:** the manual `BalanceRatio` read/write path
itself (`get_/set_red_balance_ratio()` etc.) -- no attached camera exercises
it, since the only one that lacks `BalanceWhiteAuto` also lacks
`BalanceRatio`. If a future camera has that combination, re-check against
`tools/smoke_test_camera.py`.

---

## 2026-09-01 — Device-model rotation presets

**Decided:** The Keeler Vantage Plus Digital BIO's camera mounts inverted,
so frames need a 180° rotation. That rotation is keyed on the IDS **model
name** in `device_presets.py` (`rotation_for_model()`), not set per-install
in `config.json`. `IdsCamera._open()` resolves it once the model is known
(`descriptor.ModelName()`); `BaseCamera._run()` applies it to every frame
before queueing, so recorder, preview and kiosk all agree. A `config.json`
`instruments.<role>.rotation` (0 or 180) overrides the preset as an escape
hatch, but there is **no `settings.py` UI for it** yet.

**Why keyed on model, not config:** every unit of that Keeler product ships
the camera in the same orientation -- it's a property of the hardware
model, not something that varies between clinics or that a technician
should have to discover and type. Confirmed model strings on real hardware:
BIO camera reports `U3-327xCP-C`, slit lamp reports `UI325xCP-C`, so the
`"U3-327"` substring token hits the former only. Verified end to end: the
BIO camera resolves `rotation=180` and delivers contiguous 2048x1536
frames; the slit lamp resolves `0`.

**Why 0/180 only (no 90/270):** a 90/270 rotation makes frames no longer
match the camera's reported `.resolution`, which `recorder.py`/`kiosk.py`
use to size the recording canvas -- supporting it would mean making
`BaseCamera.resolution` rotation-aware across all four subclasses. No
instrument needs it. `camera.ALLOWED_ROTATIONS` and
`config._parse_optional_rotation()` both enforce this.

**Why a config override at all:** cheap (a dataclass field + one parser),
and it's the escape hatch if a preset is ever wrong or a clinic has a
non-standard mounting, without a code change. `rotation: 0` explicitly
survives as 0 (not None) precisely so it can cancel a preset.

**Rejected:** device-side `ReverseX`/`ReverseY` GenICam nodes -- 180° needs
both, which shifts the Bayer phase, and this dev machine has no
`vendor/ids_peak_api.txt` to confirm the sensor compensates. Software
rotation is one contiguous-array copy per frame (~9MB on the BIO camera,
well under a frame interval) and provably correct.

**Not done:** `settings.py` still has the technician pick a camera from a
free dropdown and type a label. The better shape -- a category dropdown of
known-compatible devices, each carrying its own presets -- is a real
redesign, written up in ROADMAP.md.

**Tests:** new `test_device_presets.py` (lookup logic) and `test_camera.py`
(the `BaseCamera` rotation mechanic, via a fixed-frame fake and a real
`SyntheticCamera`); `test_config.py` gained 5 rotation cases.

---

## 2026-09-01 — Device-model presets: generalized "rotation" to "orientation" (BIO image is mirrored, not rotated)

**Supersedes the mechanic in the "Device-model rotation presets" entry
above** (the model-keyed-preset rationale and the 0/90/270 exclusion still
stand). On real hardware the Keeler BIO image, after the 180° rotation that
entry applied, was still wrong: correct top-to-bottom but mirrored
left-to-right. The BIO's instrument optics mirror the image; the camera
isn't physically rotated. Composing the applied 180° with the needed
horizontal mirror, the actual correct fix is a **pure vertical flip**
(`image[::-1, :]`) -- which is exactly what `net2860_helper.py` already
does (`np.flip(axis=0)`) for the older BIO camera. Same instrument, same
fix.

**Decided:** `camera.py` now carries an `orientation` (one of
`VALID_ORIENTATIONS`: `"none"` / `"rotate_180"` / `"flip_horizontal"` /
`"flip_vertical"`) instead of a `rotation` int -- the full Klein-four group
of dimension-preserving symmetries, since a mounting/optics quirk can be
any of them, not just 180°. `apply_orientation()` is the one place the
transform is defined. `device_presets.orientation_for_model()` maps
`U3-327x` → `"flip_vertical"`. `config.json`'s override field is renamed
`rotation` → `orientation` and takes the same four strings.

**Why a full rename, not `rotation` + a new `flip` field:** two fields
would be redundant (`rotate_180` == both flips) and so ambiguous when they
disagree. One enum over the closed group is unambiguous and still just a
string in config. Nothing was deployed on the old `rotation` field (no
clinic config, not in `config.example.json`), so a clean rename costs
nothing.

**Not yet hardware-verified:** the IDS cameras dropped off USB enumeration
during packaging (a force-killed frozen `app.exe` left the transport layer
wedged -- needs a replug) before the `flip_vertical` result could be seen
through the BIO. The transform math is unit-tested
(`test_camera.test_apply_orientation_composition_matches_the_group`) and
the model→orientation mapping is confirmed against the real model string;
the actual through-the-instrument image still needs a look.

**Tests:** `test_camera.py` / `test_device_presets.py` reworked for the
four orientations; `test_config.py`'s rotation cases became orientation
cases.

---

## 2026-09-01 — UVC camera reconnects itself when it drops mid-stream

**Decided:** `UvcCamera._grab()` counts consecutive failed `cv2.VideoCapture.read()`
calls; after `_RECONNECT_AFTER_FAILURES` (~0.5s at 30fps) it calls
`_try_reconnect()`, which releases the capture and reopens the device
(re-resolving the `vid_pid` index if that's the identification mode),
rate-limited to one attempt per `_RECONNECT_COOLDOWN_S`. The reopen uses
`_configure_capture(warmup=False)` -- no autofocus/exposure re-warmup,
since the scene is unchanged and the capture thread can't stall 2s there.
A failed read below the threshold, and a cooldown wait, each `time.sleep`
briefly so the capture thread doesn't spin a core while the device is down.

**Why:** reported on real hardware -- an integrated webcam used as the
third-person camera "randomly cut out" and stayed dead. `cv2.VideoCapture`
does not recover on its own from a USB power-management suspend, another
process grabbing the camera, or a brief unplug: `read()` just returns
`(False, None)` forever. Before this, that left a permanently black
third-person pane with nothing trying to fix it -- exactly the
"a replugged camera shows black and needs re-selecting" failure the
2026-08-11 "purpose-built app rather than OBS" entry says this app exists
to beat. (The 2026-08-17 "Third-person UVC camera" entry claimed a
disconnect was "caught by `_grab()` raising" -- it never was; `read()`
returns a falsy tuple, it doesn't raise. This is that gap actually
closed.)

**Interaction with `kiosk.py`'s stall detection (unchanged):** a drop that
self-heals within `DEFAULT_STALL_TIMEOUT_S` (2.0s) during a recording
leaves `poll_recording()` untouched -- `get_latest().index` resumes
advancing, the recording keeps going with a short gap in the third-person
view. A drop that outlasts the reconnect still trips the stall timeout and
fails the session loudly, which is the right call for a camera that's
genuinely gone. `Frame.index` (self-counted) is deliberately *not* reset on
reconnect, so it stays monotonic and `recorder.py` doesn't miscount the
pause as a burst of drops.

**Scope:** `UvcCamera` only. `IdsCamera` has its own acquisition model
(`WaitForFinishedBuffer` timeouts, the `ids_peak.Library` lifecycle) and no
reported reconnect problem; not touched.

**Tests:** `test_uvc_camera.py` gained `UvcCameraReconnectTest` (5 cases:
sub-threshold failures don't reopen, sustained failure reopens exactly once
within the cooldown, a successful reopen restores frames and resets the
counter, a failed reopen is swallowed and retried, reconnect bails when
stopping) plus a `warmup=False` case.

---

## 2026-09-01 — Delete composite.mkv after the MP4 is verified (not "keep both")

**Supersedes** the "keeps both `composite.mkv` and `composite.mp4` after
`stop()`" behavior noted in the 2026-08-11 disk-preflight entry.

**Decided:** `Recorder.stop()` now deletes `composite.mkv` once it has
remuxed `composite.mp4` **and** verified that MP4 decodes. Verification
(`_mp4_verifies()`): decode the first ~15 frames (must yield >0 -- catches
the dropped-leading-keyframe failure from the "PyAV remux filters on empty
packets" entry: valid headers, zero decodable frames) and demux-count the
video packets (must be within 2 of what was encoded -- catches gross
truncation). Both checks are cheap; no full decode pass a waiting student
would feel. If verification fails, **both files are kept** and
`session.json` records `mp4_verified: false` plus `composite.mkv` in
`output_files`.

**Why:** the MKV exists only as the interruption-safe copy *during*
capture (an interrupted MKV is still playable, an interrupted MP4 is
lost). The remux is a stream copy, so after a clean stop the MP4 holds
byte-identical video and the MKV is pure redundancy -- and it's not small:
keeping it doubled every session's footprint on disk, on a kiosk that
records ~500 MB/session unattended all day. The 2026-08-11 "Composite
live" entry already classes the raw/interim files as "debugging artifact...
nothing depends on them," so this isn't reversing a principle, just
stopping paying 2x storage for a file whose job is done.

**Never deletes both:** the irreplaceable-data rule (CLAUDE.md) means a
failed verification must leave *something* playable. The MKV is the
fallback, and the failure is loud (logged ERROR, surfaced in
`session.json`).

**Disk preflight (`kiosk.py`) unchanged at 2x:** during finalization both
files still briefly coexist at full size (remux writes the MP4 before the
MKV is deleted), so the transient *peak* one session needs is still ~2x
the estimate, even though a completed session now settles to 1x. Comment
updated; `REQUIRED_SPACE_MULTIPLIER` not touched.

**Tests:** `test_recorder.py`'s happy-path test now asserts the MKV is
gone, `mp4_verified` is true, and `output_files` has no `mkv`; a new test
forces `_mp4_verifies()` false and asserts both files survive and
`session.json` says so.

---

## 2026-09-01 — Automatic cleanup of old recordings (opt-in, age sweep + low-disk pass)

**Decided:** New `retention.py`, run once by `app.py` at startup (never on
the recording timer, never against an in-progress session). Driven entirely
by an opt-in `retention` section in `config.json` -- absent means no
cleanup at all, which stays the default. Configured via `settings.py`'s
"Automatically delete old recordings" group (a checkable `QGroupBox`,
unchecked by default).

Two passes:
1. **Age sweep** (`max_age_days`, always runs): delete every *completed*
   session older than N days, regardless of free space.
2. **Capacity pass** (`min_free_gb` + `protect_days`, set together or not
   at all; only runs when free space is actually below `min_free_gb`):
   delete the oldest completed sessions -- oldest first, but never one
   younger than `protect_days` -- until free space recovers. If it can't
   recover without crossing `protect_days`, it stops and
   `RetentionResult.capacity_target_met` is False; `app.py` logs a warning
   and `kiosk.py`'s existing disk preflight takes over (Start disabled,
   loud status). Deleting this week's recordings to keep a kiosk running is
   a human's call, not this module's.

**Never deleted, either pass:** a folder that isn't a well-formed
`YYYY-MM-DD_HHMM[_N]` session dir (something a technician put there); a
session with no `session.json` (in progress, or a *failed* session a
technician should review -- deliberately not auto-cleaned even though
that's the junk you'd most want gone); the single newest completed
session, whatever its age.

**Why opt-in, not a conservative default-on:** recordings are the
irreplaceable deliverable (CLAUDE.md), the retrieval step is manual, and
there's no "retrieved" marker in a session folder. A full disk today is
already a *safe* failure (preflight disables Start, nothing corrupts), so
this trades an occasional "tech has to clear space" for a standing risk of
auto-deleting an un-retrieved recording. That trade is the institution's
to opt into with a policy, not something shipped hot.

**Why "both" passes rather than one:** age-only removes recordings even
when there's plenty of room; capacity-only lets a burst of long sessions
blow past a sensible keep-window before the disk-pressure trigger fires.
Together: a predictable steady-state window, with a pressure valve that can
reach past it when genuinely necessary, floored by `protect_days`.

**Config validation (`config.py`):** `max_age_days` required positive int;
`min_free_gb`/`protect_days` both-or-neither (like `red/blue_balance_ratio`);
`protect_days <= max_age_days`. `settings.py` always writes all three when
the group is checked (an age-only policy needs a hand-edit) -- keeps both
the UI and the validation simple.

**Failure isolation:** `app.py` wraps the whole pass in try/except and
logs -- a cleanup bug must never stop the kiosk starting. `retention.py`
itself swallows a per-session delete/size error and moves on.

**Tests:** new `test_retention.py` (10 cases: age sweep incl.
newest-session and incomplete-session protection, non-session folders
ignored, minute-collision suffixes recognised, capacity pass oldest-first /
protect-days floor / target-not-met / no-op when space is fine / skipped
when unconfigured, missing dir no-op). `test_config.py` +8, `test_settings.py`
+4. Full suite 218.

---

## 2026-09-02 - Recorder/Viewer split, phase 1: two VFR streams instead of a live composite

**Decided:** `recorder.py` no longer composites. A session writes
`instrument.mp4` + `third_person.mp4`, each at its camera's native
resolution and true variable frame rate, plus a `session.json` with
`format_version: 2`. `compositor.py` moves from capture time to watch
time (the Viewer, phase 2). Design and the other three phases are in
ROADMAP.md's "Recorder/Viewer split: design" entry; the four shaping
decisions (two raw streams only, Viewer both in-kiosk and standalone,
true per-camera timestamps, students/self-review) were made in
conversation on 2026-09-02.

**This supersedes the 2026-08-11 "Composite live, not in post-production"
entry.** That decision's guarantees are kept, not dropped:

- *Synchronized by construction* -> every frame's PTS is
  `Frame.timestamp - clock.origin_monotonic` on a 1/1000 time base, so
  equal PTS in two files means the same instant. Stronger than before: it
  survives a slow camera instead of duplicating its frames into a
  fixed-rate composite. Measured on mismatched synthetic cameras (11fps
  instrument, 30fps third-person, 5s): 56 vs 145 frames spanning the same
  0.02-5.03s, cross-stream lag at any point 10-49ms, bounded by the slow
  camera's own frame period. The old model would have stored 145
  composite frames, duplicating the instrument frame ~2.6x.
- *Student watches immediately, no render wait* -> the Viewer's Watch
  button opens the session as-is. Nothing is rendered unless the student
  asks for a single-file Export.
- *No post step that can fail* -> watching needs none; the only post step
  is the MKV->MP4 remux+verify that already existed, now run per stream.

**Why two files rather than keeping the composite too:** three encoders
would have cost noticeably more CPU and ~2x the disk for a file that the
Viewer can produce on demand, and the composite is lossy about what was
captured (it bakes in a layout and duplicates the slower camera).

**`_StreamWriter` per camera, each on its own thread:** a slow encode on
one stream can't starve the other camera's bounded queue. Each drains
with `read()` (not `get_latest()`) so gaps in `Frame.index` are still
real dropped-frame counts, per CLAUDE.md's Architecture section. Writers
drain their camera's queue at `start()` so a frame captured before the
session origin isn't clamped to pts 0.

**Recorder-side fps ceiling, counted separately:** a writer skips a frame
that arrives less than `0.9/fps` after the last one it encoded, counting
it in `rate_limited_frames` (distinct from `dropped_frames`). The
camera-side caps (`IdsCamera._apply_frame_rate_cap`, `UvcCamera`'s
`CAP_PROP_FPS`) are best-effort and a device may ignore them; this is the
guarantee. The 0.9 slack matters: a camera pacing itself at exactly the
recording rate jitters a millisecond either way, and a strict `1/fps`
threshold rejected a random ~half of its frames.

**GOP set to `recording.fps` frames:** libx264's default (~250) would
make a Viewer scrub decode up to ~8s forward from the previous keyframe.
At `fps` frames it's <=1s of media for a full-rate stream and always <=30
frames of decode-forward, for a few percent of file size.

**Both time bases must be pinned to 1/1000** -- `stream.time_base` *and*
`stream.codec_context.time_base`. Found in a feasibility run against the
pinned PyAV 18.0.0 before writing any of this: `add_stream(..., rate=fps)`
leaves the encoder at 1/fps, so ms PTS get rescaled into 1/fps ticks, two
jittered ~33ms-apart frames collapse into one tick, DTS goes
non-monotonic, and the MP4 muxer rejects the remux with EINVAL. The MKV
muxer tolerates it, so this surfaces only at the remux step, and only on
the faster stream -- exactly the kind of thing that would have looked
like a random late-stage bug. Also confirmed there: exact PTS round-trip
through MKV->MP4, and seek landing at or before the target within one GOP.

**No compatibility with the old `composite.mp4` layout.** Decided
2026-09-02: nothing in that format is in circulation, so there's no
migration path to maintain and the Viewer will read `format_version: 2`
only.

**Also changed:** `Recorder`'s `width`/`height` params are gone (no
canvas). `KioskController` gained `instrument_labels`/`third_person_label`
so labels land in the manifest, and its `width`/`height` now only feed the
disk-space estimate -- which is unchanged, since sum-of-widths x
max-height is still the right proxy for total pixels/sec whether the two
streams are stacked or separate. `app.py`'s post-session summary reports
per-stream counts and flags an unverified stream.

**Tests:** `test_recorder.py` rewritten around two decoded outputs (5
cases); `test_kiosk.py`'s two session assertions moved to the v2 shape.
Full suite 219.

---

## 2026-09-02 - Recorder/Viewer split, phase 2: playback engine and Viewer

**Decided:** `session_reader.py` (no Qt) reads a session back;
`viewer.py`'s `ViewerDialog` is a thin PySide6 shell over it, opened
modally from a new Watch button in the kiosk and runnable standalone.
Phases 1 and 2 land together, so the app is never in a state where a
session has no side-by-side view. Phases 3 (Export, Past recordings) and
4 (`viewer.exe` packaging) remain.

**Alignment is by timestamp, and only by timestamp.** Each stream keeps a
decode cursor; presenting media time `t` means "advance each cursor to the
last frame at or before `t`". Because the recorder wrote PTS as
`grab time - clock origin`, that yields frames genuinely captured at the
same instant, with no frame pairing, sidecar or offset search. Confirmed
visually on an 11fps-instrument/30fps-third-person recording: at t=2.0s
the panes show instrument frame 21 (t=1.910s) beside third-person frame 59
(t=1.967s) -- 57ms apart, bounded by the slow camera's own ~91ms frame
period, and the frame *numbers* are unrelated, as they should be.

**Decoding and presenting are separate.** Advancing past several frames
still decodes each one (H.264 P-frames leave no choice) but only the frame
actually shown pays for `to_ndarray()`. So a UI that falls behind skips
presentation, not decoding, and media time never drifts from wall-clock.
`ViewerDialog._tick()` computes `t` from wall-clock elapsed since Play
rather than accumulating per-tick increments, for the same reason.

**A stream shows its first frame for any `t` before it, rather than
black.** Found by a test failing at t=0: the recorder discards frames
captured before the session origin, so stream 0 is a few ms in, and the
two cameras don't deliver their first frame at the same instant. Black
would misrepresent a camera that was running the whole time.

**`any(cursor.advance_to(t) for ...)` was a real bug**, caught by the same
test run: `any()` short-circuits, so every stream after the first one that
moved never advanced -- one pane playing, the other frozen. Now a list
comprehension. Worth recording because the generator form reads as
obviously correct.

**`ViewerDialog` is a `QDialog`, not a `QMainWindow`**, so `app.py` can
`.exec()` it modally (which is what disables the kiosk's own controls
while it's up) and `main()` can still show it standalone. Teardown hangs
off the `finished` signal, not `closeEvent` -- Esc routes through
`QDialog.reject()`, which delivers no `QCloseEvent`, and this dialog holds
open PyAV decoders. Exactly the leak the "settings.py Preview leaked the
IDS device" entry above describes, applied preemptively this time, with a
test driving `reject()` specifically.

**Watch pauses the live preview but not the cameras.** Restarting an
instrument camera costs seconds and re-enters the device-busy class of
failures; there's no comparable cost to skipping preview compositing
behind a modal window. `app.py` restarts the preview timer in a `finally`,
so a viewer that raises can't leave the kiosk with a dead preview.

**Also added:** `compositor.fit_into_canvas()` -- the single-image
counterpart to `side_by_side`/`picture_in_picture`, so the viewer's
"instrument only" / "third-person only" modes get the same
aspect-preserving letterbox rather than letting Qt stretch a raw frame.

**Tests:** new `test_session_reader.py` (12) and `test_viewer.py` (12),
both against real recorded sessions rather than mocked PyAV -- what's
worth testing is whether alignment actually holds across two
independently-encoded files, which a mock can't tell us. `test_app.py`
gained 5 Watch-button cases. Full suite 248.

---

## 2026-09-02 - Recorder/Viewer split, phase 3: Export and the recordings picker

**Decided:** `session_export.py` renders a session's streams into one
constant-frame-rate MP4 in the chosen layout, on demand; `viewer.py` gains
an Export button (worker thread, cancellable progress dialog) and a
`SessionPickerDialog`; `app.py` gains a Past recordings button. This is
the last of the app-side work -- only packaging (phase 4) remains.

**Export is constant-frame-rate, unlike the streams it reads.** The
session files are VFR to preserve each camera's true cadence. An export is
one file for sharing or an LMS, where broad player compatibility matters
more, so it samples media time at a fixed `fps`. Both facts can be true at
once precisely because the session keeps the real timing and the export is
derived.

**`compose_layout()` lives in `compositor.py`, used by both the viewer and
the exporter.** Layout selection was about to exist twice -- once for the
screen, once for the file -- which is exactly how "the export doesn't
match what I was looking at" bugs happen. One function, two callers.

**Export writes `<name>.partial.mp4` and moves it into place.** A
cancelled or crashed export must not leave something that looks like a
finished file; the rename is the commit point. The partial keeps the real
suffix rather than being `<name>.mp4.partial` because PyAV infers the
container format from the extension -- found immediately, as 10 failing
tests.

**Export refuses to write over one of the session's own stream files**
(`ValueError`), since the save dialog defaults into the session folder and
`instrument.mp4` is one keystroke away from a plausible export name. The
recording is irreplaceable; the export is not.

**"No outcome" is reported as unfinished, not as success.** If the export
thread outlives the join timeout, the viewer says so and names the file to
check, rather than claiming either result. A student being told "saved"
when nothing was is worse than being told to look.

**`duration_s` added to the manifest** (per stream and overall). The
picker shows each recording's length, and doing that by opening two video
files per row would make a term's worth of sessions feel slow.
`SessionPlayer` still derives duration from the containers themselves,
which stays authoritative for playback.

**The picker has an explicit "Open a recording folder..." browse**, and
takes being pointed straight at a single session folder as a pick rather
than an empty list. This is a phase-4 prerequisite surfacing early: the
standalone viewer runs on machines with no `config.json`, where recordings
have been copied to a USB stick or Downloads (see ROADMAP.md's "Phase 4:
two installers").

**Also:** `session_format.py` now holds the three shared format constants,
so `session_reader.py`/`session_export.py` no longer import the *writer*
for its vocabulary -- which read oddly and dragged the encoder path into a
viewer-only build. `app.py`'s two viewer buttons share
`_with_preview_paused()`, which restarts the preview in a `finally`.

**Verified end to end** on two mismatched-rate recordings: the picker
lists both newest-first with labels and durations straight from the
manifests, and a side-by-side export of the 5s session produced a real
2240x1200, 151-frame MP4 with the session folder untouched.

**Tests:** new `test_session_export.py` (12); `test_viewer.py` +11
(export wiring, picker); `test_app.py` +2 (Past recordings). Full suite
273.

---

## 2026-09-02 - Recorder/Viewer split, phase 4: two installers

**Decided:** the build produces `sidebyside-setup.exe` (clinic: app +
settings + bundled IDS peak, admin, Program Files) and
`sidebyside-viewer-setup.exe` (review: `viewer.exe` only, no IDS peak,
per-user under `%LOCALAPPDATA%`, no admin). New `packaging/viewer.spec`
and `packaging/sidebyside-viewer.iss`; `PACKAGING.md` gains a "Two
installers" table and a step 6. Completes the Recorder/Viewer split.

**Why a second installer rather than "just run the full one":** the
machine that reviews a recording usually isn't the machine that made it.
A student reviewing at home has no cameras, often no admin rights, and no
reason to install 356MB of machine-vision SDK and kernel drivers.
Measured result: **92 MB vs 513 MB**. That difference is whether
reviewing happens at all.

**The clinic installer needed no change.** `app.py` imports `viewer.py`,
so `app.exe` already contains the viewer and Watch / Past recordings work
from inside the kiosk. Shipping a separate `viewer.exe` there would have
added a second full PySide6+OpenCV+PyAV tree -- hundreds of MB -- for a
capability that machine already has. Its `[Files]`/`[Icons]` are
untouched.

**`viewer.spec`'s `excludes` is an assertion, not a size optimisation.**
Naming `ids_peak`/`ids_peak_ipl`/`pygrabber`/`comtypes` means a future
edit that makes the viewer reach something camera-facing fails the build
loudly, rather than silently gaining a dependency the review machine
cannot satisfy. Verified on this build: the frozen `viewer.exe` contains
none of `recorder`, `camera`, `kiosk`, `ids_camera` or `uvc_camera` --
cleaner than planned, because phase 3's `session_format.py` refactor had
already removed the last reader-imports-writer edge.

**Per-user install, `PrivilegesRequired=lowest`:** nothing in the viewer
writes outside the user's profile and there are no drivers, so requiring
admin would be friction with no purpose -- and students frequently don't
have it on their own machines. It gets a Desktop shortcut, unlike
`settings.exe`: students *are* this program's audience.

**Distinct `AppId` on the viewer installer only.** `sidebyside.iss`
deliberately leaves `AppId` implicit (Inno derives it from `AppName`);
adding one now would stop existing clinic installs being recognised as
upgradable. The viewer installer sets `AppId=sidebyside-viewer`
explicitly so the separation is deliberate and documented rather than an
accident of differing `AppName`s.

**Verified on the build machine:** `viewer.exe <session folder>` plays
it; `viewer.exe` with no arguments logs "no usable config.json ... using
the default recordings folder" and shows the picker rather than erroring
-- the correct review-machine path, since there is legitimately no
config there. **Not yet verified:** installing
`sidebyside-viewer-setup.exe` on a machine that has never had sidebyside
on it (PACKAGING.md step 6's checklist).

---

## 2026-09-03 - Frozen-picture detection: a camera can deliver frames without seeing anything

**Decided:** `KioskController` compares consecutive frames' pixels per
camera (`_frame_signature()` + `_update_freshness()`). A camera whose
picture is byte-identical for `DEFAULT_FREEZE_TIMEOUT_S` (5s) is reported
in `PreflightStatus.frozen_cameras`, which blocks Start, and fails a
recording in progress with its own message.

**Why:** found while diagnosing a report that an integrated webcam
"disconnects after a second". Every existing check said the camera was
perfectly healthy -- reads succeeded, 30fps steady, zero reconnects,
standalone and inside a real `KioskWindow`. Comparing consecutive frames
showed 39 of 39 pairs pixel-identical: the camera was blocked at the
OS/driver level and the stream was re-delivering one still image.

Nothing in the app could see that. `cap.read()` returns True, and
`UvcCamera` self-counts `Frame.index` (UVC exposes no source counter --
see the "Third-person UVC camera" entry), so the counter climbs even
when the pixels are dead and `poll_recording()`'s stall check never
fires. A student could have recorded a full session of frozen video and
been told "0 dropped". That is exactly the outcome CLAUDE.md calls the
worst: a black pane discovered a week later.

**Exact equality, not a difference threshold.** A real sensor emits noise
on every frame, so two byte-identical frames mean the pixels are not
coming from a sensor at all. A threshold would have to guess how dark and
how static a legitimate scene may be -- and the scene this app points at
*is* dark and static (a slit lamp beam on a dark field). Equality needs no
such guess. `_frame_signature()` therefore uses strided indexing rather
than any resampling, which would smooth away the one-bit differences that
prove liveness.

**Only ever judges a frame it has not already judged.** The first
implementation compared whatever `get_latest()` returned each poll, and
the tests immediately caught it flagging a perfectly healthy camera: at a
4Hz poll against a slower camera you re-examine the *same* frame and
conclude the picture is frozen. Tracking the last `Frame.index` assessed
fixes it and gives the right decomposition -- index not advancing is the
stall check's business; index advancing while pixels do not is this one's.
That matters concretely: the slit lamp runs at ~11fps.

**Freeze timeout (5s) is deliberately longer than the stall timeout
(2s).** A stalled camera has stopped delivering and is unambiguous; this
check asks a subtler question, so it trades a little lateness for a much
smaller chance of accusing a live camera. ~20 consecutive byte-identical
samples from a real sensor is essentially impossible.

**`frozen_cameras` is separate from `cameras_ready`** so the UI can say
which problem it is. "Waiting for cameras" would send a student to check
a cable; the camera is running. `app.py` says the picture is frozen and
names what to check (covered, switched off, blocked).

**Also fixed here:** the stall and freeze messages now use the
technician-set labels ("BI900", "third-person camera") rather than the
internal role keys, which is what a student actually reads in the error
banner.

**Verified against real hardware, one direction.** The false-positive
direction -- the one that matters more, since wrongly refusing to record
is its own failure -- was confirmed on the real webcam: live at 30fps for
7s, never flagged. The machine's camera recovered before the true
positive could be reproduced on it, so that direction rests on the tests
(`_FreezablePictureCamera` advances `Frame.index` while returning the same
image, which is precisely the observed real behaviour).

**Tests:** `test_kiosk.py` +7 -- `_frame_signature` (equality, a changed
sampled pixel, that it copies rather than views), a live camera never
flagged, a frozen camera blocking Start while `cameras_ready` stays True,
a camera freezing mid-recording stopping it loudly with a real partial
session on disk, and deselecting an instrument dropping its history. Full
suite 280.

---

## 2026-09-03 - Camera configuration: which layer owns what

**Decided:** Recorded the ownership model for camera configuration as a
standing section in CLAUDE.md ("Camera configuration: who decides what"),
because a wrong assumption about it silently produced a bad calibration
that sat in `config.json` for weeks.

**The assumption worth killing:** that Keeler and Haag-Streit configured
these sensors better than we could, so the sensor layer is already
handled. Neither is true. Both instrument cameras are generic **IDS**
machine-vision cameras that the instrument makers merely mount, and
sidebyside talks to IDS peak directly rather than through Keeler's
Kinexis -- so there is no instrument-maker configuration to inherit. And
IDS's own defaults were measured unusable here: the 2026-08-12 hardware
smoke test found `ExposureTime ~15ms / Gain 1.0` gave a near-black frame
pointed straight at a lamp (raw Bayer max 3-4 of 255).

**The evidence that our calibration strategy, not the hardware, is the
outlier** -- three settings for comparable scenes:

| | exposure | gain |
|---|---|---|
| IDS factory default | 15 ms | 1.0 (near-black) |
| IDS peak Cockpit's own auto-exposure | 47.5 ms | 25.4 |
| `exposure_calibration.next_exposure_gain()` on the slit lamp | 87.2 ms | 1.0 |

The vendor's own implementation reaches for gain readily and keeps
exposure moderate. Ours is documented as doing the opposite ("only spills
the remainder onto Gain once ExposureTime is clamped"), which is correct
for stills and wrong for video: exposure time is a frame-rate budget.
87.2ms caps the slit lamp at ~11.5fps against a 30fps target and puts
87ms of motion blur on the view of a moving eye. That is where the "slit
lamp runs at ~11fps" figure in this project's notes actually comes from
-- it was read as a hardware property, and it is not.

**What the instrument makers do own: the optics.** Light path, image
orientation (the BIO's flip, handled in `device_presets.py`), and how
much light reaches the sensor at all. None of it is configurable; it is
compensated for, or improved at the instrument. This is why "turn up the
slit lamp's own illuminator" is the real fix rather than any software
setting -- more light buys short exposure *and* low gain together.

**Why `config.json` rather than the camera's own memory.** GenICam
devices persist `ExposureTime`/`Gain` across power cycles, so "set it
once in Cockpit" appears to work. It is invisible, unversioned state with
no provenance that walks off with a swapped or reset camera -- and the
2026-08-11 slit-lamp entry already flagged the hazard: a stale value
"would silently carry into the next kiosk session with no code-level
check catching it." Config values have provenance and are reapplied in
`_open()`. Same reasoning as serial-number identification over device
index.

**Why no "Advanced" tab in settings.py.** Tiering by user skill is the
wrong cut for a project with three humans separated by role rather than
ability. The cut that pays is *where the knowledge lives*, and the useful
move is pushing decisions up that table -- every value the app derives is
one nobody can set wrong. An advanced panel is usually a sign a preset
has not been decided yet, and it is the first place a confused technician
clicks. The 87ms case is the proof: it was not a missing setting (the
setting existed and was set), so no amount of extra configurability would
have prevented it. What was missing was any report of what the
calibration *cost*.

**Rejected:** deferring sensor configuration to IDS peak Cockpit
entirely, the common pattern for this class of app. The 2026-08-18
in-app-calibration entry already settled that for the routine case. This
entry only narrows it: Cockpit stays the right tool for genuine
sensor-level work (black level, pixel format), and once such a value is
known it belongs in `device_presets.py`, not in a dialog.

**Not yet acted on:** bounding exposure by `1/recording.fps` in
`next_exposure_gain()`, warning when a configured exposure cannot meet
the target frame rate, and reporting achieved fps/gain after a
calibration. See the CLAUDE.md section's rules.
