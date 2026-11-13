"""VPixx hardware layer. Everything that talks to the TRACKPixx3 /
DATAPixx3 lives in this file, built on top of the generic engine in
chamois.py. On a machine without a DATAPixx3 the module drops into
dry-run mode: normal display, no gaze, eye-tracking CSVs with only a
header row, and reading trials end with the space bar. That way the
whole experiment can be tested anywhere.

What this layer adds:

  - M16 video mode with the channel-packing shader. Pyglet renders one
    frame with the stimulus luminance in the red channel and overlay
    indices (gaze markers, trial tag) in the blue channel; the
    DATAPixx3 turns that into a clean grayscale stimulus display for
    the participant and a console display with colored gaze markers
    for the experimenter (cyan = left eye, magenta = right eye). The
    blinking fixation cross runs in true-color C24 mode instead, so
    its red dot is really red; the trial switches to M16 just before
    the words appear.
  - TPxRecorder: per-trial eye-movement recording to CSV.
  - TPxReadingTrial: recording, the pixel-mode condition tag, and the
    gaze trigger that ends a trial once the participant looks at the
    lower right corner.
  - TPxExperimentApp: gaze polling, marker drawing, the packing-shader
    present(), and mid-session recalibration (hidden 'r' key on
    instruction and break screens).

Hardware notes, verified against pypixxlib 1.12.1:
  - TPxGetEyePosition() returns [xL, yL, xR, yR, ...], left eye first,
    in cartesian pixels with the origin at the screen center.
  - DPxSetVidCluts() takes three channel lists [[R]*512, [G]*512,
    [B]*512]. Entries 0-255 are the stimulus-display CLUT, 256-511 the
    console CLUT, values are uint16.
  - The DATAPixx3 composites overlay colors onto the console output
    only, never onto the M16 stimulus display. Anything the
    participant is supposed to see in color has to be shown in C24
    mode; that is what the fixation phase is for.
  - Register writes only reach the box after DPxWriteRegCache(), and
    reads are stale without a DPxUpdateRegCache() first. Forgetting
    either fails silently.
  - The fragment shader is a port of VPixx's own M16O_mode_Shader.
    Third-party monitors show only the high byte of the luminance, but
    the encoding is the same, so the code works with VPixx and
    ordinary stimulus displays alike.
  - C24 video mode is restored on exit, also after errors, so the box
    is never left stuck in M16.
"""

import csv
import math
import subprocess
import sys

import numpy as np
import pyglet
from pyglet.gl import (
    GL_BLEND,
    GL_FALSE,
    GL_TRIANGLE_STRIP,
    GL_TRUE,
    glBindTexture,
    glColorMask,
    glDisable,
    glEnable,
)
from pyglet.graphics.shader import Shader, ShaderProgram

try:
    from pypixxlib import _libdpx as dp
except ImportError:
    sys.exit(
        "pypixxlib is not installed. Run setup_once.py once (python3 on "
        "Linux/macOS, python on Windows), then start the experiment with "
        "`uv run demo_experiment_tpx.py`."
    )

import chamois
from chamois import pkey

# --------------------------------------------------------------------
# Hardware settings: the defaults. An experiment script changes them
# through the TPx class at the end of this file, with the same names
# in lower case, e.g. TPx(gaze_display='both', trigger_dwell=0.1).
# Sizes are in height units (1.0 = screen height), like in chamois.py.
# --------------------------------------------------------------------
TRIGGER_RADIUS = 0.19      # size of the trial-ending corner region
TRIGGER_DWELL = 0.08       # seconds the gaze must stay in that region
                           # before the trial ends; a saccade that just
                           # passes through does not count
MARKER_RADIUS = 0.01       # radius of the gaze markers

# Where the live gaze markers appear:
#   'console'   colored markers (cyan = left eye, magenta = right) on
#               the experimenter's console only; the participant sees
#               nothing. The default.
#   'stimulus'  gray markers drawn as ordinary stimulus content, so the
#               participant sees their own gaze. The console mirrors
#               the stimulus image, so the gray markers appear there
#               too — M16 has no way to hide content from the console.
#   'both'      the gray participant-visible markers plus the colored
#               console markers drawn on top of them.
# Participant-visible markers can only be grayscale: the DATAPixx3
# never composites overlay colors onto the M16 stimulus display.
GAZE_DISPLAY = 'console'
STIMULUS_MARKER_GRAY = 180   # brightness of the participant-visible markers

# What the marker shows:
#   'eyes'     one marker per eye: cyan = left, magenta = right
#   'average'  a single marker at the average position of the two
#              eyes; green on the console
GAZE_MARKER = 'eyes'

# The DATAPixx3 can composite its own live eye image (the camera view
# LabMaestro shows, with the built-in gaze cursor) onto the console
# output. The experiment normally switches that off, so only its own
# markers appear on the console; set True to keep the live eye video
# on throughout the experiment instead. Either way it is switched on
# during a mid-session recalibration, where the operator needs it, and
# it never appears on the participant's display.
SHOW_EYE_IMAGE = False

# Overlay indices of the console colors. The values are arbitrary CLUT
# slots (1-255) with two constraints: they are parked high so the low
# numbers stay free for the pixel-mode condition codes, which can then
# run 1, 2, 3, 4, ... without gaps; and they must not equal any gray
# level that is actually drawn (the screen colors, which
# setup_hardware() checks, the progress bar's 128 and
# STIMULUS_MARKER_GRAY — the shader drops an overlay pixel where the
# stimulus gray equals the overlay index, see the module docstring).
LEFT_INDEX = 100        # left-eye marker
RIGHT_INDEX = 101       # right-eye marker
AVERAGE_INDEX = 102     # averaged-gaze marker
CORNER_DOT_INDEX = 103  # corner target's dot

# Hardware CLUT colors, 16 bit per channel:
TRANSPARENCY = [0, 65535, 0]      # the transparency key: pixels with
                                  # this CLUT color are not drawn at all
CYAN = [0, 65535, 65535]          # left-eye marker on the console
MAGENTA = [65535, 0, 65535]       # right-eye marker on the console
GREEN = [256, 65535, 256]         # averaged-gaze marker on the console;
                                  # a hair off pure green, because pure
                                  # green is the transparency key
YELLOW = [65535, 65535, 0]        # corner dot on the console. Not
                                  # green, for the same reason.


# Trial tagging through pixel mode: DPxEnableDoutPixelModeB() makes the
# digital output pins 16-23 broadcast the blue value of the top left
# screen pixel, and every eye-tracking sample records the pin state in
# its DigitalOut column. During a reading trial that pixel is painted
# (0, 0, code) with a per-condition code, so every sample carries the
# condition it was recorded under; decode with DigitalOut >> 16 & 0xFF,
# where 0 means no trial was running. The pixel maps to transparent
# CLUT entries and shows on neither display. Codes that collide with a
# visible console color (the markers, the corner dot) are skipped, or a
# tagged trial would paint a colored pixel into the console's corner.
RESERVED_PIXEL_CODES = {0, LEFT_INDEX, RIGHT_INDEX, AVERAGE_INDEX,
                        CORNER_DOT_INDEX}
condition_pixel_codes = {}

# The real tag is invisible, so dry-run mode can draw a visible
# stand-in (a blue swatch with the code next to it, top left) while
# testing the tagging logic without hardware. Off by default: without
# a tracker the experiment should look exactly like the real thing,
# e.g. when it is used as a plain reading experiment without
# eye-tracking. Never drawn on hardware either way.
SHOW_DRY_RUN_TAG = False


def assign_pixel_codes(conditions):
    """Map condition names to pixel codes: names sorted, reserved codes
    skipped, so the mapping is reproducible. The table file itself is
    written later by write_pixel_codes(), because its filename needs
    the participant ID, which is only entered on the first page."""
    available = [c for c in range(1, 256) if c not in RESERVED_PIXEL_CODES]
    distinct = sorted(set(conditions))
    assert len(distinct) <= len(available), "too many conditions to tag"
    condition_pixel_codes.clear()
    condition_pixel_codes.update(zip(distinct, available))
    # Belt and braces: if the skip logic above is ever edited badly, a
    # reserved code must never slip through to the screen.
    collisions = set(condition_pixel_codes.values()) & RESERVED_PIXEL_CODES
    assert not collisions, (
        f"reserved pixel codes assigned to conditions: {collisions}")
    print(f"Pixel-mode condition codes: {condition_pixel_codes}")


_pixel_codes_written = False


def write_pixel_codes():
    """Write the condition -> code table to data/logs/. Called at the
    first reading trial; does nothing after that."""
    global _pixel_codes_written
    if _pixel_codes_written or not condition_pixel_codes:
        return
    path = chamois.data_path(
        "logs", chamois.session_prefix() + "_pixel-codes.tsv")
    with open(path, 'w', encoding='utf-8') as f:
        f.write("condition\tpixel_code\n")
        for condition, code in condition_pixel_codes.items():
            f.write(f"{condition}\t{code}\n")
    _pixel_codes_written = True
    print("Pixel-code table stored in: " + path)


def build_cluts():
    """Build the two hardware CLUTs in the format DPxSetVidCluts wants.
    Every stimulus-display entry is transparent (the DATAPixx3 would
    not composite them anyway, see the module docstring). On the
    console everything is transparent too, except the two gaze markers
    and the corner dot, none of which the participant ever sees."""
    entries = np.tile(np.array(TRANSPARENCY, dtype=np.uint16), (512, 1))
    entries[256 + LEFT_INDEX] = CYAN
    entries[256 + RIGHT_INDEX] = MAGENTA
    entries[256 + AVERAGE_INDEX] = GREEN
    entries[256 + CORNER_DOT_INDEX] = YELLOW
    return entries.T.tolist()  # -> [[R x512], [G x512], [B x512]]


# True once setup_hardware() has found a DATAPixx3; stays False in
# dry-run mode.
hardware_present = False


def apply_m16_mode():
    """Put the DATAPixx3 into the video state the experiment needs.
    Runs at startup and again after a mid-session recalibration."""
    # The box's built-in eye-image/cursor overlay: normally hidden so
    # only our own markers appear on the console, kept on if the
    # experimenter wants the live eye video (SHOW_EYE_IMAGE). Set
    # explicitly either way — LabMaestro tends to leave it switched
    # on, so the state it was left in cannot be relied upon.
    if SHOW_EYE_IMAGE:
        dp.TPxShowOverlay()
    else:
        dp.TPxHideOverlay()
    dp.DPxSetVidMode('M16')

    # Overlay pixels whose CLUT color equals the transparency key are
    # dropped by the hardware instead of being drawn:
    dp.DPxSetVidClutTransparencyColor(*TRANSPARENCY)
    dp.DPxEnableVidClutTransparencyColorMode()

    dp.DPxSetVidCluts(build_cluts())
    dp.DPxSetTPxAwake()

    # Trial tagging: pins 16-23 follow the top left pixel's blue value.
    dp.DPxEnableDoutPixelModeB()

    # None of the above takes effect until the register cache is
    # written:
    dp.DPxWriteRegCache()


def release_m16_mode():
    """Back to normal video, so LabMaestro can draw calibration targets
    on the stimulus display. The device stays open and the tracker
    stays awake."""
    dp.DPxDisableDoutPixelModeB()
    dp.DPxDisableVidClutTransparencyColorMode()
    dp.DPxSetVidMode('C24')
    dp.DPxWriteRegCache()


def ensure_full_rgb_range():
    """Force every connected X11 output into full RGB range.

    This is a Linux/X11 concern: with 'Broadcast RGB' on 'Automatic'
    the driver may quietly pick limited range (16-235), which shifts
    every pixel value at scan-out. The stimulus still looks close to
    normal, but the overlay indices no longer match the CLUT (so the
    gaze markers disappear) and the pixel-mode codes are corrupted.
    The shift happens after OpenGL, so it cannot be detected from
    inside the program; instead the output is simply set to 'Full' at
    every start, since the xrandr setting does not survive a reboot.
    On Windows, macOS, or Wayland there is no xrandr and this does
    nothing."""
    try:
        out = subprocess.run(['xrandr', '--prop'], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return  # no xrandr on this system
    output = None
    for line in out.splitlines():
        if ' disconnected' in line:
            output = None
        elif ' connected' in line:
            output = line.split()[0]
        elif 'Broadcast RGB:' in line and output is not None:
            value = line.split(':', 1)[1].strip()
            if value == 'Full':
                continue
            try:
                subprocess.run(['xrandr', '--output', output,
                                '--set', 'Broadcast RGB', 'Full'],
                               capture_output=True, text=True, timeout=5,
                               check=True)
                print(f"Video output {output}: Broadcast RGB was "
                      f"'{value}' — set to 'Full' (required for the "
                      "console gaze markers and Pixel-Mode codes).")
            except Exception:
                print(f"WARNING: could not set Broadcast RGB to 'Full' on "
                      f"{output} (currently '{value}'). Limited RGB range "
                      "breaks the console gaze markers and Pixel-Mode "
                      "codes. Fix manually:\n"
                      f'    xrandr --output {output} '
                      '--set "Broadcast RGB" "Full"')


def setup_hardware():
    global hardware_present
    dp.DPxOpen()
    if not dp.DPxIsReady():
        print("NOTE: No DATAPixx3 detected — running in DRY-RUN mode "
              "(no M16 encoding, no gaze, no eye-movement recording; "
              "end reading trials with space).")
        return
    hardware_present = True
    # M16 shows only grays, and the console-marker levels must not
    # appear in the stimulus (overlay pixels would drop out there):
    marker_levels = sorted(RESERVED_PIXEL_CODES - {0})
    for name in ('BACKGROUND_COLOR', 'TEXT_COLOR', 'HINT_COLOR'):
        r, g, b = getattr(chamois, name)
        if not r == g == b or r in marker_levels:
            sys.exit(f"chamois.{name} = {(r, g, b)} cannot be shown on the "
                     "VPixx display: use a gray (R = G = B) other than "
                     f"{marker_levels}.")
    # Recolor the corner target's dot: background luminance keeps it
    # invisible to the participant, who should not be lured to the
    # trigger corner early, while blue CORNER_DOT_INDEX makes it yellow
    # on the console so the experimenter can still see it. The
    # line-start cross keeps its true red dot, because its blink phase
    # runs in C24 mode where red is simply red.
    bg = chamois.BACKGROUND_COLOR[0]
    chamois.CORNER_DOT_COLOR = (bg, bg, CORNER_DOT_INDEX)
    ensure_full_rgb_range()
    apply_m16_mode()

    # Calibration is done beforehand in LabMaestro; this script only
    # checks that one exists.
    dp.DPxUpdateRegCache()
    if not dp.TPxIsDeviceCalibrated():
        print("WARNING: TRACKPixx is not calibrated — gaze markers and the "
              "corner trigger will not work. Calibrate in LabMaestro first.")


def shutdown_hardware():
    """Restore normal video mode. Must run even after an error, since a
    DATAPixx3 left in M16 mode breaks whatever uses it next."""
    if not hardware_present:
        return
    try:
        dp.DPxSetTPxSleep()
        # The sleeping camera sends no new frames, so a still-visible
        # eye-image overlay would hold the last frame frozen on the
        # console until the next session wakes the tracker. Blank it
        # instead (release_m16_mode's WriteRegCache commits this too):
        dp.TPxHideOverlay()
        release_m16_mode()
    finally:
        dp.DPxClose()


# --------------------------------------------------------------------
# Eye-movement recording: the 20 columns streamed by the TRACKPixx3
# schedule, written to one CSV per trial.
# --------------------------------------------------------------------
EYE_DATA_COLUMNS = [
    'TimeTag', 'LeftEyeX', 'LeftEyeY',
    'LeftPupilDiameter', 'RightEyeX', 'RightEyeY',
    'RightPupilDiameter', 'DigitalIn', 'LeftBlink', 'RightBlink',
    'DigitalOut', 'LeftEyeFixationFlag', 'RightEyeFixationFlag',
    'LeftEyeSaccadeFlag', 'RightEyeSaccadeFlag', 'MessageCode',
    'LeftEyeRawX', 'LeftEyeRawY', 'RightEyeRawX', 'RightEyeRawY']

# The four calibrated gaze columns arrive in pixels with the origin at
# the screen center and y pointing up. When the CSV is written they are
# moved to the top left origin with y pointing down, the coordinate
# system of the word AOIs in the log and of the screenshots. 32767
# means the eye was lost and is written unchanged; the raw gaze vectors
# are in radians and also left alone.
GAZE_X_COLUMNS = [1, 4]   # LeftEyeX, RightEyeX
GAZE_Y_COLUMNS = [2, 5]   # LeftEyeY, RightEyeY
EYE_LOST_SENTINEL = 32767


class TPxRecorder:
    """Records one trial's eye movements. Without hardware (dry-run)
    the CSV is still written, but contains only the header row."""

    schedule = None

    def start(self):
        if not hardware_present:
            return
        self.schedule = dp.TPxSetupSchedule()
        dp.TPxStartSchedule()
        dp.DPxUpdateRegCache()

    def stop(self):
        if self.schedule is None:
            return
        dp.TPxStopSchedule()
        dp.DPxUpdateRegCache()
        dp.TPxGetStatus(self.schedule)

    def save_csv(self, filename):
        if self.schedule is None:
            data = []
        else:
            data = dp.TPxReadData(self.schedule,
                                  self.schedule['newBufferFrames'])
        w, h = chamois.screen_width, chamois.screen_height
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(EYE_DATA_COLUMNS)
            for row in data:
                row = list(row)
                for i in GAZE_X_COLUMNS:
                    if abs(row[i]) < EYE_LOST_SENTINEL:
                        row[i] = round(row[i] + w / 2, 2)
                for i in GAZE_Y_COLUMNS:
                    if abs(row[i]) < EYE_LOST_SENTINEL:
                        row[i] = round(h / 2 - row[i], 2)
                writer.writerow(row)


# --------------------------------------------------------------------
# The M16 packing shader, a Pyglet/GLSL port of VPixx's own
# M16O_mode_Shader. The scene texture arrives with the stimulus
# luminance in red and overlay indices in blue; the shader outputs
#
#   red   = high byte of luminance * 65535
#   green = low byte
#   blue  = the overlay index, but only where blue != red
#
# The blue != red test is how overlays are told apart from content:
# grayscale content has blue == red everywhere and produces no overlay,
# while markers are drawn with the red channel masked off, so blue
# differs from red exactly where they were painted. Green is recomputed
# from red, which means a marker can never damage the stimulus
# luminance.
# --------------------------------------------------------------------
VERTEX_SRC = """#version 150 core
in vec2 position;    // fullscreen quad corners in clip space (-1..1)
in vec2 tex_coord;
out vec2 v_tex;

void main() {
    gl_Position = vec4(position, 0.0, 1.0);
    v_tex = tex_coord;
}
"""

FRAGMENT_SRC = """#version 150 core
uniform sampler2D scene;
in vec2 v_tex;
out vec4 frag_color;

void main() {
    vec4 c = texture(scene, v_tex);
    float index = c.r * 65535.0 + 0.01;
    float overlay = (c.b != c.r) ? c.b : 0.0;
    frag_color = vec4(floor(index / 256.0) / 255.0,  // luminance MSB
                      mod(index, 256.0) / 255.0,     // luminance LSB
                      overlay,                        // CLUT index
                      1.0);
}
"""


def make_packing_program():
    """Compile the packing shader; returns (program, fullscreen quad).
    The scene texture it samples belongs to chamois.ExperimentApp,
    which creates it with NEAREST filtering — interpolation would blend
    neighboring overlay indices into meaningless in-between values."""
    program = ShaderProgram(
        Shader(VERTEX_SRC, 'vertex'),
        Shader(FRAGMENT_SRC, 'fragment'),
    )
    quad = program.vertex_list(
        4, GL_TRIANGLE_STRIP,
        position=('f', (-1, -1,  1, -1, -1, 1,  1, 1)),
        tex_coord=('f', (0, 0,  1, 0,  0, 1,  1, 1)),
    )
    program['scene'] = 0  # sampler on texture unit 0
    return program, quad


# --------------------------------------------------------------------
# Pages and app on top of the chamois engine
# --------------------------------------------------------------------
class TPxReadingTrial(chamois.ReadingTrial):
    """chamois.ReadingTrial plus eye-movement recording, the pixel-mode
    condition tag, and the gaze trigger: the trial ends once the gaze
    has stayed on the lower right corner for a moment. Space and Escape
    from the base class still work as a fallback, and are the only way
    to end a trial in dry-run mode."""

    corner_target = True    # show the look-target that ends the trial

    def __init__(self, item, condition, text, trigger_radius=None,
                 trigger_dwell=None, **layout):
        # `layout` is passed straight to chamois.ReadingTrial:
        # y_offset, align, cross_position, wrap.
        super().__init__(item, condition, text, **layout)
        # None = the tracker-wide setting, read now and not when this
        # file was loaded, so that TPx(trigger_radius=...) counts:
        self.trigger_radius = (TRIGGER_RADIUS if trigger_radius is None
                               else trigger_radius)
        self.trigger_dwell = (TRIGGER_DWELL if trigger_dwell is None
                              else trigger_dwell)

    def validate(self, app):
        super().validate(app)
        # Pixel-tag sanity, checked before the participant is seated
        # (validate_pages runs at the start of run()): both mistakes
        # below would otherwise go unnoticed until data analysis. An
        # entirely empty table just means tagging is not used — fine.
        if not condition_pixel_codes:
            return
        code = condition_pixel_codes.get(self.condition)
        if code is None:
            raise RuntimeError(
                f"Condition {self.condition!r} (item {self.item}) has no "
                "pixel code: every eye sample of this trial would "
                "silently get DigitalOut = 0, as if no trial were "
                "running. Codes are assigned automatically from the "
                "reading trials when the app is created; if the script "
                "calls assign_pixel_codes() from TPx.py itself, FIX: include "
                "this condition in the list passed to it.")
        if code in RESERVED_PIXEL_CODES:
            raise RuntimeError(
                f"Condition {self.condition!r} is assigned pixel code "
                f"{code}, which is reserved (0 = no trial, {LEFT_INDEX}/"
                f"{RIGHT_INDEX}/{AVERAGE_INDEX} = gaze-marker colors, "
                f"{CORNER_DOT_INDEX} = corner dot). A trial tagged with "
                "it would paint a colored dot into the top-left corner "
                "of the console and be indistinguishable from a gaze "
                "marker in the DigitalOut data. FIX: let "
                "assign_pixel_codes() in TPx.py number the conditions "
                "automatically (it skips reserved codes), or hand-pick "
                "any code from 1-255 that is not in "
                "RESERVED_PIXEL_CODES (TPx.py).")

    def build(self, app):
        super().build(app)
        self.recorder = TPxRecorder()
        # The blinking cross is shown in true-color C24 mode so its red
        # dot is actually red (M16 cannot show color). The switch back
        # to M16 happens during the last off-phase of the blink, when
        # the screen is black anyway: black encodes the same in both
        # modes, so nothing visible changes.
        self.blink_total = sum(d for _, d in self.blink_phases)
        self.m16_restored = False
        self.dwell = 0.0     # how long the gaze has been in the corner
        app.begin_fixation_display()

    def update(self, app, dt):
        if (self.state == 'blink' and not self.m16_restored
                and self.elapsed >= self.blink_total - 0.03):
            app.end_fixation_display()   # during the final off phase
            self.m16_restored = True
        super().update(app, dt)  # blink rhythm, then word layout
        if self.state != 'reading':
            return
        # Give the tracker a moment after word onset before sampling:
        if self.elapsed - self.read_start < 0.05:
            return
        lx, ly, rx, ry = app.gaze
        w, h = app.window.width, app.window.height
        # The tracker reports gaze relative to the screen center:
        x = (lx + rx) / 2 + w / 2
        y = (ly + ry) / 2 + h / 2
        inside = (math.sqrt((x - w) ** 2 + y ** 2)
                  < chamois.px(self.trigger_radius))
        if inside:
            # The trial only ends after the gaze has stayed inside the
            # corner region for trigger_dwell seconds; leaving the
            # region resets the clock. A saccade that merely passes
            # through never ends the trial.
            self.dwell += dt
            if self.dwell >= self.trigger_dwell:
                app.advance()
        else:
            self.dwell = 0.0

    def show_words(self, app):
        if not self.m16_restored:    # fallback if a tick was skipped
            app.end_fixation_display()
            self.m16_restored = True
        super().show_words(app)
        write_pixel_codes()   # does nothing after the first trial
        self.recorder.start()
        app.set_pixel_code(condition_pixel_codes.get(self.condition, 0))

    def deactivate(self, app):
        app.end_fixation_display()   # never leave a trial in C24
        app.set_pixel_code(0)
        if self.state == 'reading':
            self.recorder.stop()
            path = chamois.data_path(
                "eyetracking",
                f"{chamois.session_prefix()}_page-{self.pno:03d}_{self.type}"
                f"_item-{self.item:03d}_cond-{self.condition}.csv")
            self.recorder.save_csv(path)
            self.metadata2 = path
        super().deactivate(app)


class TPxExperimentApp(chamois.ExperimentApp):
    """chamois.ExperimentApp plus gaze polling, the console-only
    markers, the packing-shader present(), the pixel-mode trial tag,
    and mid-session recalibration. The offscreen framebuffer itself
    lives in the base class."""

    def __init__(self, pages, fullscreen=True):
        super().__init__(pages, fullscreen)
        self.recalibrating = False
        self.c24_fixation = False
        # State of the video-mode switch, see _request_mode():
        self._pending_mode = None     # mode waiting for the register write
        self._await_mode = None       # mode written, waiting for readback
        self._await_ticks = 0         # cap on the readback wait
        self._black_frames = 0        # black frames flipped so far
        self._black_hold = 0          # final black frames after readback
        self.gaze = (0.0, 0.0, 0.0, 0.0)
        self.program, self.quad = make_packing_program()

        # The pixel-mode codes, one per condition of the reading
        # trials, unless the script has assigned its own already:
        if not condition_pixel_codes:
            assign_pixel_codes(page.condition for page in pages
                               if isinstance(page, TPxReadingTrial))

        assert GAZE_DISPLAY in ('console', 'stimulus', 'both'), GAZE_DISPLAY
        assert GAZE_MARKER in ('eyes', 'average'), GAZE_MARKER

        # The console gaze markers; color (0, 0, index) puts them on
        # the overlay channel, so they only appear on the console:
        self.markers = pyglet.graphics.Batch()
        marker_px = chamois.px(MARKER_RADIUS)
        self.left_marker = pyglet.shapes.Circle(
            0, 0, marker_px, color=(0, 0, LEFT_INDEX), batch=self.markers)
        self.right_marker = pyglet.shapes.Circle(
            0, 0, marker_px, color=(0, 0, RIGHT_INDEX), batch=self.markers)

        # The participant-visible gaze markers (modes 'stimulus' and
        # 'both'): plain grayscale circles, i.e. ordinary content that
        # shows on both displays.
        gray = (STIMULUS_MARKER_GRAY,) * 3
        self.lum_markers = pyglet.graphics.Batch()
        self.left_lum_marker = pyglet.shapes.Circle(
            0, 0, marker_px, color=gray, batch=self.lum_markers)
        self.right_lum_marker = pyglet.shapes.Circle(
            0, 0, marker_px, color=gray, batch=self.lum_markers)

        # The averaged-gaze marker (GAZE_MARKER = 'average'): a single
        # circle at the mean of the two eyes, again in a console
        # (overlay) and a participant (grayscale) version:
        self.avg_marker = pyglet.shapes.Circle(
            0, 0, marker_px, color=(0, 0, AVERAGE_INDEX))
        self.avg_lum_marker = pyglet.shapes.Circle(
            0, 0, marker_px, color=gray)

        # The trial tag: the top left pixel, blue = condition code,
        # 0 outside trials. Drawn over everything, without blending.
        self.pixel_code = 0
        h = self.window.height
        self.tag_pixel = pyglet.shapes.Rectangle(
            0, h - 1, 1, 1, color=(0, 0, 0))
        # The visible dry-run stand-in for the tag, only drawn when
        # SHOW_DRY_RUN_TAG is switched on (see the config up top).
        self.tag_debug = pyglet.shapes.Rectangle(
            0, h - 54, 40, 40, color=(0, 0, 0))
        self.tag_debug_label = pyglet.text.Label(
            "", font_name=chamois.FONT_NAME, font_size=12,
            x=48, y=h - 34, anchor_y='center',
            color=(*chamois.TEXT_COLOR, 255))

    def set_pixel_code(self, code):
        self.pixel_code = code
        self.tag_pixel.color = (0, 0, code)
        # The dry-run swatch exaggerates the blue so it is actually
        # visible (the real value would look black):
        self.tag_debug.color = (0, 0, min(255, 40 + code * 40))
        self.tag_debug_label.text = f"pixel tag {code}"

    # ---- the C24 fixation phase ----
    # The DATAPixx3 never composites overlay colors onto the M16
    # stimulus display, so the blinking cross is shown in true-color
    # C24 mode, where its red center dot is really red, and the trial
    # switches to M16 for the words. During the C24 phase present()
    # blits the frame unpacked and draw_overlay() skips the markers and
    # the tag: their channel coding would show up as colored artifacts
    # in C24, and nothing is being recorded then anyway.
    def begin_fixation_display(self):
        """Switch to true-color C24 for the blinking cross, under the
        black cover of _request_mode()."""
        self.c24_fixation = True
        self._request_mode('C24')

    def end_fixation_display(self):
        if self._pending_mode == 'C24':
            # C24 was requested but never actually written, so the box
            # is still in M16 and the request can simply be cancelled.
            self._pending_mode = None
            self._black_hold = 0
            self.c24_fixation = False
            return
        if hardware_present and self.c24_fixation:
            self._request_mode('M16')
        self.c24_fixation = False

    def _request_mode(self, mode):
        """Switch the video mode without visible artifacts.

        A frame that reaches the screen while the box is in the wrong
        mode flashes: packed M16 content shown in C24 looks like tinted
        text with the markers exposed. Just waiting a few frames before
        the switch is not reliable, because the driver queues flipped
        frames — what was drawn is not necessarily on screen yet. So
        the switch runs closed-loop, under black, black being the one
        thing that looks identical in both modes:

          1. hold the screen black for 5 flipped frames, long enough
             for any queued frames to drain,
          2. write the new mode register,
          3. stay black until the box itself reads the new mode back
             (capped at about half a second),
          4. two more black frames, then normal drawing resumes.

        Altogether roughly 130 ms of black per switch, which just reads
        as a short blank between trial phases."""
        if hardware_present:
            self._pending_mode = mode
            self._await_mode = None
            self._black_frames = 0
            self._black_hold = 0

    # ---- mid-session recalibration (hidden 'r' key) ----
    def _release_pointer_grab(self):
        """Give the mouse back to the rest of the desktop.

        Pyglet's X11 backend grabs and confines the pointer to any
        fullscreen window, even without exclusive-mouse mode, and never
        releases that grab on minimize or focus loss. While the grab is
        held, every click in the whole X session is delivered to this
        window: LabMaestro is visible but cannot be clicked. This uses
        pyglet internals, so it is best-effort — on other backends
        (Windows and macOS have no such grab) or a changed pyglet it
        silently does nothing."""
        try:
            from pyglet.libs.x11 import xlib
            xlib.XUngrabPointer(self.window._x_display, xlib.CurrentTime)
            xlib.XFlush(self.window._x_display)
        except Exception:
            pass

    def _restore_pointer_grab(self):
        """Re-apply pyglet's normal fullscreen pointer confinement
        (best-effort, see _release_pointer_grab)."""
        try:
            self.window._applied_mouse_exclusive = None
            self.window._update_exclusivity()
        except Exception:
            pass

    def suspend_for_recalibration(self):
        """Free the stimulus display so LabMaestro can calibrate."""
        print("Recalibration: window minimized, video mode restored. "
              "Calibrate in LabMaestro, then return to the experiment "
              "window and press space.")
        if hardware_present:
            release_m16_mode()
            # apply_m16_mode() hides the box's built-in eye-image
            # overlay, but that overlay is exactly where LabMaestro
            # draws its calibration eye image and gaze cursor, so it
            # has to come back on now — otherwise the operator has
            # nothing to calibrate against. Resuming re-applies M16
            # mode, which puts it back to whatever SHOW_EYE_IMAGE says.
            dp.TPxShowOverlay()
            dp.DPxWriteRegCache()
        # The engine hides the system mouse pointer at startup, but
        # LabMaestro has to be clicked to calibrate, so bring the
        # pointer back for the recalibration phase. Some window
        # managers keep the fullscreen window's cursor state active
        # even while it is minimized, so this cannot be left to the
        # minimize alone.
        self.window.set_mouse_visible(True)
        self._release_pointer_grab()
        self.window.minimize()

    def resume_after_recalibration(self):
        if hardware_present:
            apply_m16_mode()
            # Cover the C24->M16 transition with the closed-loop black
            # hold (see _request_mode): the register write above takes
            # a video frame to latch, and a packed frame shown while
            # the box is still in C24 would flash yellow.
            self._request_mode('M16')
            dp.DPxUpdateRegCache()
            if not dp.TPxIsDeviceCalibrated():
                print("WARNING: TRACKPixx still reports no calibration.")
        self.window.set_mouse_visible(False)   # hidden again for trials
        self.window.activate()
        self._restore_pointer_grab()
        print("Recalibration done — experiment resumed.")

    # ---- per-frame work ----
    def tick(self, dt):
        if self._pending_mode is not None and self._black_frames >= 5:
            # Enough black frames have been flipped that the screen is
            # certainly black; write the new mode and wait for the box
            # to confirm it.
            dp.DPxSetVidMode(self._pending_mode)
            dp.DPxWriteRegCache()
            self._await_mode = self._pending_mode
            self._pending_mode = None
            self._await_ticks = 0
        elif self._await_mode is not None:
            dp.DPxUpdateRegCache()
            self._await_ticks += 1
            if (dp.DPxGetVidMode() == self._await_mode
                    or self._await_ticks > 30):
                self._await_mode = None
                self._black_hold = 2
        if hardware_present:
            # One register-cache update per tick serves both the
            # markers and the reading trials' corner trigger:
            dp.DPxUpdateRegCache()
            eye = dp.TPxGetEyePosition()
            self.gaze = tuple(eye[0:4])  # [xL, yL, xR, yR], left first
            lx, ly, rx, ry = self.gaze
            w, h = self.window.width, self.window.height
            self.left_marker.x = self.left_lum_marker.x = lx + w / 2
            self.left_marker.y = self.left_lum_marker.y = ly + h / 2
            self.right_marker.x = self.right_lum_marker.x = rx + w / 2
            self.right_marker.y = self.right_lum_marker.y = ry + h / 2
            self.avg_marker.x = self.avg_lum_marker.x = (lx + rx) / 2 + w / 2
            self.avg_marker.y = self.avg_lum_marker.y = (ly + ry) / 2 + h / 2
        if not self.recalibrating:
            super().tick(dt)

    def draw_overlay(self):
        # No markers and no tag while the box is in C24 (fixation phase
        # or recalibration): their channel coding would show up as
        # colored artifacts on a true-color display.
        if (hardware_present and not self.c24_fixation
                and not self.recalibrating):
            glDisable(GL_BLEND)
            average = GAZE_MARKER == 'average'
            if GAZE_DISPLAY in ('stimulus', 'both'):
                # Participant-visible markers: grayscale content drawn
                # like any other stimulus, so both displays show them.
                if average:
                    self.avg_lum_marker.draw()
                else:
                    self.lum_markers.draw()
            if GAZE_DISPLAY in ('console', 'both'):
                # Console markers with the red channel masked off, so
                # they cannot touch the stimulus luminance, and without
                # blending, so the overlay indices keep exact values.
                glColorMask(GL_FALSE, GL_TRUE, GL_TRUE, GL_TRUE)
                if average:
                    self.avg_marker.draw()
                else:
                    self.markers.draw()
                glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE)
            # The tag pixel goes last so nothing can draw over it. All
            # channels enabled: red 0 guarantees blue != red in the
            # shader.
            self.tag_pixel.draw()
            glEnable(GL_BLEND)

    def present(self):
        # On hardware the frame goes out through the packing shader,
        # except during the C24 fixation phase, whose true-color frame
        # is blitted as it is. In dry-run everything is blitted plain;
        # a normal monitor would show the packed channels as a colored
        # tint.
        if hardware_present:
            if (self._pending_mode is not None
                    or self._await_mode is not None or self._black_hold):
                # A mode switch is in progress: leave the backbuffer
                # black, see _request_mode().
                self._black_frames += 1
                if self._pending_mode is None and self._await_mode is None:
                    self._black_hold -= 1
                return
            if self.c24_fixation or self.recalibrating:
                # The box is in C24, so the frame goes out unpacked.
                # (During recalibration the restored window would
                # otherwise show shader-packed content on a true-color
                # display: a yellow-tinted screen with the gaze markers
                # exposed, until space re-applies M16.)
                self.scene_tex.blit(0, 0)
            else:
                pyglet.gl.glActiveTexture(pyglet.gl.GL_TEXTURE0)
                glBindTexture(self.scene_tex.target, self.scene_tex.id)
                with self.program:
                    self.quad.draw(GL_TRIANGLE_STRIP)
        else:
            super().present()
            if SHOW_DRY_RUN_TAG and self.pixel_code:
                self.tag_debug.draw()
                self.tag_debug_label.draw()

    # ---- input ----
    def on_key_press(self, symbol, modifiers):
        if symbol == pkey.Q and (modifiers & pkey.MOD_CTRL):
            raise chamois.ExperimentAbortException()
        if self.recalibrating:
            if symbol == pkey.SPACE:
                self.recalibrating = False
                self.resume_after_recalibration()
            return pyglet.event.EVENT_HANDLED
        if symbol == pkey.R and isinstance(self.page, chamois.Instructions):
            # Hidden experimenter key: recalibrate mid-session. Only
            # accepted on instruction and break screens, where the
            # experiment is already standing still and nothing is being
            # recorded or answered.
            self.recalibrating = True
            self.page.response = "RECALIBRATED"  # visible in the log
            self.suspend_for_recalibration()
            return pyglet.event.EVENT_HANDLED
        return super().on_key_press(symbol, modifiers)

    def on_text(self, text):
        if not self.recalibrating:
            super().on_text(text)


# --------------------------------------------------------------------
# What the experiment script uses: one object for the whole tracker.
#
#     from TPx import TPx
#     tracker = TPx()                        # all defaults
#     tracker = TPx(gaze_display='both')     # some non-defaults
#
#     pages.append(tracker.ReadingTrial(item, condition, sentence))
#     tracker.setup()
#     try:
#         tracker.ExperimentApp(pages).run()
#     finally:
#         tracker.shutdown()
#
# EyeLink.py offers the same interface.
# --------------------------------------------------------------------
class TPx:
    """The TRACKPixx3/DATAPixx3. The keyword arguments are the hardware
    settings from the top of this file, in lower case; whatever is not
    given keeps its default. There is one tracker per experiment, so
    the settings are stored in this module, where every class in this
    file reads them."""

    SETTINGS = ('gaze_display', 'gaze_marker', 'marker_radius',
                'stimulus_marker_gray', 'trigger_radius', 'trigger_dwell',
                'show_eye_image', 'show_dry_run_tag')
    CHOICES = {'gaze_display': ('console', 'stimulus', 'both'),
               'gaze_marker': ('eyes', 'average')}

    ReadingTrial = TPxReadingTrial
    ExperimentApp = TPxExperimentApp

    def __init__(self, **settings):
        # Everything is checked first, and only then stored:
        for name, value in settings.items():
            if name not in self.SETTINGS:
                raise TypeError(
                    f"TPx() has no setting {name!r}. The settings are: "
                    f"{', '.join(self.SETTINGS)}.")
            allowed = self.CHOICES.get(name)
            if allowed and value not in allowed:
                raise ValueError(
                    f"{name} must be one of {allowed}, not {value!r}.")
        gray = settings.get('stimulus_marker_gray')
        if gray in RESERVED_PIXEL_CODES:
            raise ValueError(
                f"stimulus_marker_gray = {gray} is the index of a console "
                "color; use another gray level than "
                f"{sorted(RESERVED_PIXEL_CODES)}.")
        for name, value in settings.items():
            globals()[name.upper()] = value

    def setup(self):
        """Open the DATAPixx3 and switch to M16 mode (dry-run mode
        without a device). Call after chamois.new_session()."""
        setup_hardware()

    def shutdown(self):
        """Restore normal video mode. Call in a `finally`, so that it
        also runs after an error."""
        shutdown_hardware()
