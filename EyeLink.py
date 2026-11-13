"""SR Research EyeLink layer. Everything that talks to an EyeLink
tracker (developed for the EyeLink 1000 Plus) lives in this file, built
on top of the generic engine in chamois.py. It is the EyeLink
counterpart of TPx.py and offers the same interface (the EyeLink class
at the end of this file: settings as keyword arguments, ReadingTrial,
ExperimentApp, setup, shutdown), so the experiment script only has to
pick one of the two. On a
machine without a reachable EyeLink the module drops into dummy mode:
no calibration, no gaze, eye-tracking CSVs with only a header row, and
reading trials end with the space bar.

An EyeLink is a two-computer system. The Host PC runs SR Research's
tracker software, records the session's EDF file and shows the camera
image and the live gaze to the experimenter; this script runs on the
Display PC and talks to the Host over Ethernet through pylink. That is
why this layer is much smaller than TPx.py: nothing about the display
hardware needs handling, and the Host draws its own gaze cursor.

What this layer adds:

  - Calibration, validation and drift checks inside the experiment,
    with the Chamois fixation cross as the target, drawn by
    PygletCalibration. The experimenter runs them from the Host PC
    (Enter = camera image, c = calibrate, v = validate, Esc = back to
    the experiment); the camera image is only shown on the Host.
  - EyeLinkRecorder: per-trial CSV of the samples streamed over the
    link, written live. The EDF on the Host stays the complete record
    and is copied into data/eyetracking/ at the end of the session.
  - EyeLinkReadingTrial: recording, trial messages for the EDF (TRIALID,
    word onset, word AOIs, screenshot) so Data Viewer shows each trial
    with its AOIs, the words on the Host screen, and the
    gaze trigger that ends a trial once the participant looks at the
    lower right corner.
  - EyeLinkExperimentApp: gaze polling, mid-session recalibration
    (hidden 'r' key on instruction and break screens), every other page
    on the Host screen (without gaze: the tracker records only during
    reading trials, so the EDF holds nothing else), and the Host's
    abort menu during reading trials: Ctrl+Alt+A on the Host PC, then
    Skip (or Repeat) ends the trial, Terminate ends the experiment.
    Ctrl+Alt+Q on the Host quits SR Research's Host software itself;
    the experiment then stops too, but the EDF stays on the Host.

Notes, verified against pylink 2.2.596:
  - Gaze arrives in the pixel coordinates set with screen_pixel_coords:
    origin top left, y pointing down. That is already the coordinate
    system of the logged word AOIs and the screenshots, so nothing is
    converted. Missing data (blinks, track loss) is -32768.
  - pylink's own setup and drift-check loops block until they finish
    and call back into PygletCalibration for every screen update and
    every key. They are therefore never started from inside a key
    handler, only from the app's tick().
  - The Host screen shows each page as a screenshot (HOST_DISPLAY =
    'image': a PNG file copied to the Host and shown with its
    draw_image command, which works while recording), or as text the
    Host draws in its own font ('text'). The console prints how long
    each page took.
"""

import csv
import math
import os
import statistics
import struct
import sys
import tempfile
import textwrap
import time
import unicodedata
import zlib

import numpy
import pyglet

try:
    import pylink
except ImportError:
    sys.exit(
        "pylink could not be loaded. Start the experiment with "
        "`uv run demo_experiment_eyelink.py`, which installs it (package "
        "sr-research-pylink)."
    )

import chamois
from chamois import pkey

# --------------------------------------------------------------------
# Tracker settings: the defaults. An experiment script changes them
# through the EyeLink class at the end of this file, with the same
# names in lower case, e.g. EyeLink(host_ip="102.1.1.1",
# drift_check=True). Sizes are in height units (1.0 = screen height),
# like in chamois.py.
# --------------------------------------------------------------------
HOST_IP = "100.1.1.1"      # the Host PC; the Display PC needs 100.1.1.2
# The sample rate, the eye(s) to track, the pupil measure (area or
# diameter), the calibration grid (HV3, HV5, HV9, HV13) and the
# calibration area are left to the Host PC (Camera Setup and Set Options
# screens), also between calibrations; the script never changes them.
# It prints all but the area at the start. The EDF records which grid
# each calibration used.
DRIFT_CHECK = False        # True: a drift check at the line-start cross
                           # before every reading trial; False: only
                           # the blinking cross, as with the TRACKPixx

# Where the live gaze is shown (only during reading trials, the only
# time the tracker records):
#   'host'   on the Host PC only, as its gaze cursor; the participant
#            sees nothing. The default.
#   'both'   additionally as markers on the participant's screen, drawn
#            on top of the page (never into the screenshots or the Host
#            images), e.g. for demonstrations or piloting.
GAZE_DISPLAY = 'host'
# What the participant-screen marker shows:
#   'eyes'     one marker per eye: cyan = left, magenta = right
#   'average'  a single green marker at the average of the two eyes
GAZE_MARKER = 'eyes'
MARKER_RADIUS = 0.01       # radius of the markers, in height units

TRIGGER_RADIUS = 0.19      # size of the trial-ending corner region
TRIGGER_DWELL = 0.08       # seconds the gaze must stay in that region
                           # before the trial ends; a saccade that just
                           # passes through does not count

# What the Host screen shows behind the gaze cursor:
#   'image'  a screenshot of each page, so it looks like the
#            participant's screen; it is copied to the Host as a small
#            PNG file and shown with the Host command draw_image, which
#            works while recording (the console prints how long it took)
#   'text'   the Host draws the text itself, in its own font: instant,
#            but it looks different from the participant's screen
HOST_DISPLAY = 'image'

# Colors on the Host screen (EyeLink palette 0-15).
HOST_TEXT_COLOR = 15       # white: the words and the text of other pages,
                           # in 'text' mode
HOST_CROSS_COLOR = 4       # red: the line-start cross, in 'text' mode
HOST_TRIGGER_COLOR = 2     # green: the corner trigger region, in 'text'
                           # mode ('image' mode draws both into the image)

# The open link to the tracker, and True once setup_hardware() has
# reached a Host PC; stays None/False in dummy mode.
tracker = None
hardware_present = False
edf_name = None            # the EDF's name on the Host (8 characters)


def setup_hardware():
    global tracker, hardware_present, edf_name
    try:
        tracker = pylink.EyeLink(HOST_IP)
    except RuntimeError as e:
        print(f"NOTE: No EyeLink reachable at {HOST_IP} ({e}) — running in "
              "DUMMY mode (no calibration, no gaze, no eye-movement "
              "recording; end reading trials with space).")
        return
    hardware_present = True
    print("Connected to " + tracker.getTrackerVersionString())

    # The EDF is opened right away so that calibrations and validations
    # are recorded in it too. Host filenames are limited to 8
    # characters; the copy in data/eyetracking/ gets the full session
    # name at the end. Month, day, hour and minute of the session:
    edf_name = chamois.session_id[5:16].replace('-', '').replace('_', '')
    tracker.openDataFile(edf_name + ".EDF")
    tracker.sendCommand("add_file_preamble_text 'Chamois reading experiment'")

    # Changing these settings requires offline (idle) mode:
    tracker.setOfflineMode()
    rate = host_setting("sample_rate")
    print(f"Sample rate: {rate or 'unknown'} Hz (set on the Host PC).")
    grid = host_setting("calibration_type")
    print(f"Calibration type: {grid or 'unknown'} (set on the Host PC).")
    both = host_setting("binocular_enabled")
    eye = host_setting("active_eye")
    if both and both.upper()[:1] in ("Y", "1", "T"):
        eyes = "both"
    else:
        eyes = (eye or "unknown").lower()
    print(f"Eye(s) tracked: {eyes} (set on the Host PC).")
    diameter = host_setting("pupil_size_diameter")
    if diameter is None:
        pupil = "unknown"
    else:
        pupil = "diameter" if diameter.upper()[:1] in ("Y", "1", "T") else "area"
    print(f"Pupil size: {pupil} (set on the Host PC).")
    # What goes into the EDF and what is streamed to this computer. The
    # link carries gaze, pupil and resolution, enough for the live CSV
    # and the corner trigger; the EDF additionally keeps head-referenced
    # and raw pupil positions, and the parsed fixations, saccades and
    # blinks.
    tracker.sendCommand("file_sample_data = LEFT,RIGHT,GAZE,HREF,RAW,AREA,"
                        "GAZERES,BUTTON,STATUS,INPUT")
    tracker.sendCommand("file_event_filter = LEFT,RIGHT,FIXATION,SACCADE,"
                        "BLINK,MESSAGE,BUTTON,INPUT")
    tracker.sendCommand("link_sample_data = LEFT,RIGHT,GAZE,GAZERES,AREA,"
                        "STATUS,INPUT")
    tracker.sendCommand("link_event_filter = LEFT,RIGHT,FIXATION,SACCADE,"
                        "BLINK,BUTTON,FIXUPDATE,INPUT")


def host_setting(name):
    """The current value of one of the Host's settings, as text, or
    None if the Host does not answer within half a second."""
    tracker.readRequest(name)
    deadline = time.time() + 0.5
    while time.time() < deadline:
        reply = tracker.readReply()
        if reply:
            return reply.strip()
        time.sleep(0.005)
    return None


def shutdown_hardware():
    """Stop recording, fetch the EDF from the Host and close the link.
    Runs even after an error or Ctrl+Q, so the session's EDF always
    ends up in data/eyetracking/."""
    if not hardware_present:
        return
    try:
        if not tracker.isConnected():
            # The Host software was quit (Ctrl+Alt+Q) or the cable pulled:
            print(f"WARNING: the EyeLink Host PC is no longer connected. "
                  f"The EDF is still on the Host PC as {edf_name}.EDF.")
            return
        if tracker.isRecording() == 0:   # 0 means "recording" here
            tracker.stopRecording()
        tracker.setOfflineMode()
        # The Host would otherwise keep showing the last page:
        tracker.sendCommand("clear_screen 0")
        tracker.sendCommand("record_status_message 'Session ended'")
        tracker.closeDataFile()
        local = chamois.data_path(
            "eyetracking", chamois.session_prefix() + ".edf")
        try:
            tracker.receiveDataFile(edf_name + ".EDF", local)
            print("EDF stored in: " + local)
        except Exception as e:
            print(f"WARNING: could not copy the EDF ({e}). It is still on "
                  f"the Host PC as {edf_name}.EDF.")
    finally:
        tracker.close()
        try:
            pylink.closeGraphics()
        except Exception:
            pass   # the app never got as far as opening it


# --------------------------------------------------------------------
# The Host screen. What the experimenter sees behind the gaze cursor is
# drawn with Host commands, in the same pixel coordinates as the gaze.
# Drawing needs offline mode, so it happens before recording starts.
# --------------------------------------------------------------------
# The Host font is plain ASCII, so the Host gets a transliteration.
# Double quotes would end the command's quoted text and become single
# quotes.
HOST_ASCII = str.maketrans({
    'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'Ä': 'Ae', 'Ö': 'Oe', 'Ü': 'Ue',
    'ß': 'ss', '"': "'", '“': "'", '”': "'", '„': "'", '‘': "'", '’': "'",
    '‚': "'", '—': '-', '–': '-', '…': '...'})


def host_text(x, y, text, color=None):
    """One line of text on the Host screen, centered on (x, y) (pixels,
    origin top left), in HOST_TEXT_COLOR unless a color is given.
    Characters without an ASCII form (e.g. Arabic) are left out."""
    if color is None:
        color = HOST_TEXT_COLOR
    text = unicodedata.normalize('NFKD', text.translate(HOST_ASCII))
    text = text.encode('ascii', 'ignore').decode().strip()
    if text:
        tracker.sendCommand(
            f'draw_text {round(x)} {round(y)} {color} "{text}"')


def png_bytes(rgb):
    """An RGB image (numpy array, rows from the top) as the contents of
    a PNG file; standard library only, about 20 ms for a full screen."""
    h, w, _ = rgb.shape
    raw = numpy.zeros((h, w * 3 + 1), numpy.uint8)   # filter byte 0 per row
    raw[:, 1:] = rgb.reshape(h, w * 3)

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw.tobytes(), 6))
            + chunk(b"IEND", b""))


def host_label(label, screen_height):
    """A page's pyglet label on the Host screen. A wrapping label is
    re-wrapped by character count: the Host font differs from the
    participant's anyway, so the lines only approximate theirs."""
    lines = []
    for paragraph in label.text.split('\n'):
        if label.multiline and label.width and paragraph:
            # Characters per line, from the label's own average
            # character width:
            single = chamois.make_label(paragraph, 0, 0).content_width
            per_line = max(1, int(label.width * len(paragraph) / single))
            lines += textwrap.wrap(paragraph, per_line) or ['']
        else:
            lines.append(paragraph)
    if not any(line.strip() for line in lines):
        return
    width, height = label.content_width, label.content_height
    # The label's center in pyglet coordinates (y up), from its anchor:
    cx = label.x + {'left': width / 2, 'right': -width / 2}.get(
        label.anchor_x, 0)
    cy = label.y + {'bottom': height / 2, 'baseline': height / 2,
                    'top': -height / 2}.get(label.anchor_y, 0)
    pitch = height / len(lines)
    top = screen_height - cy - height / 2      # y down from here on
    for n, line in enumerate(lines):
        if line.strip():
            host_text(cx, top + (n + 0.5) * pitch, line)


# --------------------------------------------------------------------
# Calibration display. pylink runs calibration, validation and drift
# checks in its own loop and calls these methods to draw the targets
# and to fetch keys. The camera image is shown on the Host PC only, so
# the image methods do nothing.
# --------------------------------------------------------------------
# Pyglet key -> pylink key code, for the keys that are not plain
# characters. Letters, digits and space pass through as their ASCII
# code, which is what pyglet's key symbols already are.
PYLINK_KEYS = {
    pkey.RETURN: pylink.ENTER_KEY, pkey.ENTER: pylink.ENTER_KEY,
    pkey.ESCAPE: pylink.ESC_KEY,
    pkey.UP: pylink.CURS_UP, pkey.DOWN: pylink.CURS_DOWN,
    pkey.LEFT: pylink.CURS_LEFT, pkey.RIGHT: pylink.CURS_RIGHT,
    pkey.PAGEUP: pylink.PAGE_UP, pkey.PAGEDOWN: pylink.PAGE_DOWN,
}


class PygletCalibration(pylink.EyeLinkCustomDisplay):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.keys = []          # filled by EyeLinkExperimentApp.on_key_press
        self.abort = False      # Ctrl+Q was pressed during setup

    def _show(self, target=None):
        """Draw the empty screen, or the target at (x, y) in tracker
        coordinates (y pointing down), straight into the window."""
        window = self.app.window
        window.switch_to()
        window.clear()
        if target is not None:
            x, y = target
            chamois.FixationCross(
                x, window.height - y, dot_color=chamois.LINE_DOT_COLOR).draw()
        window.flip()

    def setup_cal_display(self):
        self._show()

    def exit_cal_display(self):
        self._show()

    def clear_cal_display(self):
        self._show()

    def erase_cal_target(self):
        self._show()

    def draw_cal_target(self, x, y):
        self._show((x, y))

    def get_input_key(self):
        if not self.app.in_setup:
            # pylink also asks for keys during the EDF transfer at the
            # end, when the window may already be closed (and keys must
            # not reach a page then either): no keys outside setup,
            # drift checks and the Host's abort menu.
            return []
        self.app.window.dispatch_events()   # calls on_key_press below
        keys = []
        for symbol, modifiers in self.keys:
            if symbol == pkey.Q and modifiers & pkey.MOD_CTRL:
                # Leave pylink's loop first; the abort is raised once
                # it has returned (see EyeLinkExperimentApp.tick).
                self.abort = True
                keys.append(pylink.KeyInput(pylink.ESC_KEY, 0))
            elif symbol in PYLINK_KEYS:
                keys.append(pylink.KeyInput(PYLINK_KEYS[symbol], 0))
            elif 32 <= symbol < 127:
                keys.append(pylink.KeyInput(symbol, 0))
        self.keys.clear()
        return keys

    def alert_printf(self, message):
        print("EyeLink: " + message)

    # Sounds and the camera image are not used on the Display PC:
    def play_beep(self, beepid): pass
    def record_abort_hide(self): pass
    def setup_image_display(self, width, height): return 1
    def image_title(self, title): pass
    def draw_image_line(self, width, line, totlines, buff): pass
    def set_image_palette(self, red, green, blue): pass
    def exit_image_display(self): pass
    def draw_line(self, x1, y1, x2, y2, colorindex): pass
    def draw_lozenge(self, x, y, width, height, colorindex): pass
    def get_mouse_state(self): return ((0, 0), 0)


# --------------------------------------------------------------------
# Eye-movement recording: the samples streamed over the link, written
# to one CSV per trial. Gaze is in screen pixels with the origin at the
# top left (the AOIs' system), pupil size in arbitrary units,
# resolution in pixels per degree of visual angle. -32768 means the eye
# was lost (blink, track loss) and is written unchanged; the columns of
# an eye that is not being recorded stay empty.
# --------------------------------------------------------------------
EYE_DATA_COLUMNS = [
    'TimeTag', 'LeftEyeX', 'LeftEyeY', 'LeftPupil',
    'RightEyeX', 'RightEyeY', 'RightPupil', 'ResolutionX', 'ResolutionY']


def eye_values(eye):
    if eye is None:
        return ['', '', '']
    x, y = eye.getGaze()
    if x <= pylink.MISSING_DATA or y <= pylink.MISSING_DATA:
        x = y = pylink.MISSING_DATA
    else:
        x, y = round(x, 2), round(y, 2)
    return [x, y, round(eye.getPupilSize(), 1)]


class EyeLinkRecorder:
    """Collects one trial's link samples. The app drains the link every
    tick; only samples from word onset on are kept, like the TRACKPixx
    CSVs. Without a tracker (dummy mode) the CSV is still written, but
    contains only the header row."""

    def __init__(self):
        self.onset = None       # tracker time of word onset, in ms
        self.rows = []

    def start(self):
        if hardware_present:
            self.onset = tracker.trackerTime()

    def drain(self):
        while True:
            item = tracker.getNextData()
            if not item:
                break
            if item != pylink.SAMPLE_TYPE or self.onset is None:
                continue
            sample = tracker.getFloatData()
            if sample.getTime() < self.onset:
                continue
            ppd_x, ppd_y = sample.getPPD()
            self.rows.append(
                [sample.getTime()]
                + eye_values(sample.getLeftEye())
                + eye_values(sample.getRightEye())
                + [round(ppd_x, 2), round(ppd_y, 2)])

    def save_csv(self, filename):
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(EYE_DATA_COLUMNS)
            writer.writerows(self.rows)


class EyeLinkReadingTrial(chamois.ReadingTrial):
    """chamois.ReadingTrial plus recording, the trial messages and the
    gaze trigger: the trial ends once the gaze has stayed on the lower
    right corner for a moment. Space and Escape from the base class
    still work as a fallback, and are the only way to end a trial in
    dummy mode."""

    corner_target = True    # show the look-target that ends the trial

    def __init__(self, item, condition, text, trigger_radius=None,
                 trigger_dwell=None, **layout):
        # `layout` is passed straight to chamois.ReadingTrial:
        # y_offset, align, cross_position, wrap.
        super().__init__(item, condition, text, **layout)
        # None = the tracker-wide setting, read now and not when this
        # file was loaded, so that EyeLink(trigger_radius=...) counts:
        self.trigger_radius = (TRIGGER_RADIUS if trigger_radius is None
                               else trigger_radius)
        self.trigger_dwell = (TRIGGER_DWELL if trigger_dwell is None
                              else trigger_dwell)

    def build(self, app):
        super().build(app)
        self.dwell = 0.0     # how long the gaze has been in the corner
        self.recorder = EyeLinkRecorder()
        if not hardware_present:
            return
        # The word boxes are needed now, for the Host screen, but the
        # base class only lays the words out when they appear. So the
        # layout is run once here and the trial reset to its blink
        # phase; this also runs the overflow checks before recording.
        super().show_words(app)
        self.host_aois = self.metadata1
        host_words = self.words
        self.words, self.metadata1 = [], None
        self.state, self.read_start = 'blink', None

        # The page number makes every TRIALID unique within the session,
        # even if an item/condition pair were shown twice; the same page
        # number is in the log row and in the eye CSV's name, so EDF
        # trial, CSV and log row always match one to one.
        tracker.sendMessage(
            f"TRIALID {self.pno} {self.item} {self.condition}")
        tracker.sendMessage(f"!V TRIAL_VAR page {self.pno}")
        tracker.sendMessage(f"!V TRIAL_VAR item {self.item}")
        tracker.sendMessage(f"!V TRIAL_VAR condition {self.condition}")
        tracker.sendMessage(f"!V TRIAL_VAR eye_csv {self.csv_name()}")
        tracker.sendCommand(f"record_status_message 'page {self.pno}, item "
                            f"{self.item}, condition {self.condition}'")
        # What the experimenter sees behind the gaze cursor: the words,
        # the line-start cross, and the corner trigger region (green
        # outline). It is sent right after the first frame of the trial
        # has been shown, so the participant already sees the fixation
        # cross. In 'image' mode cross and region are part of the image:
        # Host drawing commands sent after draw_image ended up hidden
        # under the image, which the Host draws a moment later.
        w, h = app.window.width, app.window.height
        r = int(chamois.px(self.trigger_radius))
        corner = pyglet.shapes.Box(w - r, 0, r, r, thickness=4,
                                   color=(0, 160, 0))

        def draw_words():
            for label in host_words:
                label.draw()
            self.cross.draw()
            corner.draw()

        def words_as_text():
            tracker.sendCommand("clear_screen 0")
            for box, word in zip(self.host_aois.split(";"),
                                 self.text.split()):
                left, top, right, bottom = (int(v) for v in box.split(","))
                host_text((left + right) / 2, (top + bottom) / 2, word)
            tracker.sendCommand(
                f"draw_cross {int(self.cross_x)} {int(h - self.cross_y)} "
                f"{int(HOST_CROSS_COLOR)}")
            tracker.sendCommand(f"draw_box {w - r} {h - r} {w - 1} "
                                f"{h - 1} {HOST_TRIGGER_COLOR}")

        def host_work():
            app.host_screen(draw_words, words_as_text, f"page{self.pno}")
            if DRIFT_CHECK:
                app.request_drift_check(self.cross_x, h - self.cross_y,
                                        then=self.start_recording)
            else:
                self.start_recording(app)
        app.after_next_flip(host_work)

    @property
    def cross_x(self):
        return self.cross.shapes[0].x     # center of the cross's disc

    @property
    def cross_y(self):
        return self.cross.shapes[0].y

    def start_recording(self, app):
        """Recording starts during the blink phase, as SR Research
        recommends (a little before the stimulus), and before the
        participant has to fixate; the CSV still begins at word onset."""
        if tracker.startRecording(1, 1, 1, 1) != 0:
            print("WARNING: the EyeLink did not start recording.")
        app.recorder = self.recorder
        app.skip_next_dt()   # startRecording blocks for a moment

    def csv_name(self):
        return (f"{chamois.session_prefix()}_page-{self.pno:03d}_{self.type}"
                f"_item-{self.item:03d}_cond-{self.condition}.csv")

    def show_words(self, app):
        super().show_words(app)
        if not hardware_present:
            return
        words = self.text.split()
        for n, box in enumerate(self.metadata1.split(";")):
            left, top, right, bottom = box.split(",")
            tracker.sendMessage(
                f"!V IAREA RECTANGLE {n + 1} {left} {top} {right} "
                f"{bottom} {words[n]}")
        # The words are laid out now but only appear with the next
        # flip; the onset is marked right after it.
        app.after_next_flip(self.word_onset)

    def word_onset(self):
        """Called right after the first frame with the words has been
        flipped: the word-onset message in the EDF, and the start of
        the CSV, at the same tracker time."""
        tracker.sendMessage("SYNCTIME")
        self.recorder.start()

    def update(self, app, dt):
        super().update(app, dt)  # blink rhythm, then word layout
        if self.state != 'reading' or app.gaze is None:
            return
        # Give the tracker a moment after word onset before sampling:
        if self.elapsed - self.read_start < 0.05:
            return
        x, y = app.gaze
        w, h = app.window.width, app.window.height
        # Gaze is already top-left based, so the lower right corner is
        # at (w, h):
        inside = (math.sqrt((x - w) ** 2 + (y - h) ** 2)
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

    def deactivate(self, app):
        reading = self.state == 'reading'
        end = None
        if hardware_present and app.recorder is self.recorder:
            self.recorder.drain()
            end = tracker.trackerTime()
            tracker.stopRecording()
        app.recorder = None
        app.after_next_flip(None)     # in case the words never got flipped
        super().deactivate(app)       # saves the screenshot
        if reading:
            path = chamois.data_path("eyetracking", self.csv_name())
            self.recorder.save_csv(path)
            self.metadata2 = path
        if not hardware_present:
            return
        if reading and end is not None and self.recorder.onset is not None:
            # A CSV that fell behind the link shows up here; the EDF has
            # every sample either way.
            # The sample interval comes from the trial's own
            # timestamps, so the check follows whatever rate the Host
            # is set to, even if it was changed during the session.
            times = [row[0] for row in self.recorder.rows]
            n = len(times)
            if n >= 2:
                interval = statistics.median(
                    b - a for a, b in zip(times, times[1:]))
                expected = int((end - self.recorder.onset) / interval)
                tracker.sendMessage(f"CSV_SAMPLES {n} EXPECTED {expected}")
                if n < 0.98 * expected:
                    print(f"WARNING: the CSV has {n} of about {expected} "
                          "samples; the EDF has all of them.")
            else:
                tracker.sendMessage(f"CSV_SAMPLES {n}")
                print(f"WARNING: the CSV has only {n} samples; the EDF has "
                      "all of them.")
            # The trial's screenshot as Data Viewer background, relative
            # to the EDF in data/eyetracking/:
            tracker.sendMessage(
                f"!V IMGLOAD FILL ../screenshots/{self.screenshot}")
        tracker.sendMessage("TRIAL_RESULT " + (self.response or "0"))


class EyeLinkExperimentApp(chamois.ExperimentApp):
    """chamois.ExperimentApp plus gaze polling, the calibration display,
    drift checks, mid-session recalibration, the Host screen for pages
    other than reading trials, and the Host's abort menu. Drawing and
    presenting are left to the base class: an EyeLink needs nothing
    special on the participant's screen."""

    def __init__(self, pages, fullscreen=True):
        super().__init__(pages, fullscreen)
        self.gaze = None          # (x, y) in pixels from the top left
        self.eyes = (None, None)  # the same per eye: (left, right)
        self.recorder = None      # the running trial's recorder
        assert GAZE_DISPLAY in ('host', 'both'), GAZE_DISPLAY
        assert GAZE_MARKER in ('eyes', 'average'), GAZE_MARKER
        radius = chamois.px(MARKER_RADIUS)
        self.left_marker = pyglet.shapes.Circle(0, 0, radius,
                                                color=(0, 255, 255))
        self.right_marker = pyglet.shapes.Circle(0, 0, radius,
                                                 color=(255, 0, 255))
        self.average_marker = pyglet.shapes.Circle(0, 0, radius,
                                                   color=(0, 200, 0))
        self.in_setup = False     # a blocking pylink loop is running
        self._setup_request = False
        self._drift_request = None
        self._skip_dt = False
        self._after_flip = None
        if not hardware_present:
            return
        self.calibration = PygletCalibration(self)
        pylink.openGraphicsEx(self.calibration)
        # Gaze and calibration targets in this window's pixels, origin
        # top left:
        w, h = self.window.width, self.window.height
        tracker.sendCommand(f"screen_pixel_coords = 0 0 {w - 1} {h - 1}")
        tracker.sendMessage(f"DISPLAY_COORDS 0 0 {w - 1} {h - 1}")
        print(f"EyeLink screen coordinates: {w} x {h} pixels.")
        # pyglet draws a frame with on_draw() and then flips it. A
        # message that marks what the participant sees must come after
        # the flip, so the window's flip() is wrapped:
        flip = self.window.flip

        def flip_then_mark():
            flip()
            # (Calibration flips the window too; waiting work is left
            # for the experiment's own next frame.)
            if self._after_flip is not None and not self.in_setup:
                callback, self._after_flip = self._after_flip, None
                callback()
        self.window.flip = flip_then_mark

    def after_next_flip(self, callback):
        """Run callback() right after the next frame is flipped (None
        cancels)."""
        self._after_flip = callback

    # ---- the page sequence ----
    def start(self):
        super().start()
        self._show_page_on_host()

    def advance(self):
        super().advance()
        if not self.finished:
            self._show_page_on_host()

    def _show_page_on_host(self):
        """Pages other than reading trials: their screen on the Host,
        without recording (the Host stays on its Offline screen and
        shows no gaze; user decision 2026-10-23, so the EDF holds only
        the reading trials). Reading trials draw and record themselves;
        the participant-ID and calibration pages are left out."""
        page = self.page
        if (not hardware_present or page is None
                or isinstance(page, (chamois.ReadingTrial,
                                     chamois.SubjectIDPage,
                                     CalibrationPage))):
            return
        # Right after the page has been shown, so the participant sees
        # it at once; a key pressed meanwhile is handled afterwards.
        self.after_next_flip(lambda: self._host_page(page))

    def _host_page(self, page):
        if self.page is not page:
            return
        tracker.sendCommand(
            f"record_status_message 'page {page.pno}, {page.type}'")

        def labels_as_text():
            tracker.sendCommand("clear_screen 0")
            for label in page.labels:
                host_label(label, self.window.height)
        self.host_screen(page.draw, labels_as_text, f"page{page.pno}")
        self.skip_next_dt()

    def host_screen(self, draw, as_text, name):
        """Put a page on the Host screen: in 'image' mode a screenshot of
        what draw() draws, saved on the Host as <name>.png; in 'text'
        mode (or once the image has failed) as_text() draws it with Host
        commands. Both work while recording. Prints how long it took."""
        global HOST_DISPLAY
        started = time.perf_counter()
        if HOST_DISPLAY == 'image':
            try:
                self._host_image(draw, name + ".png")
            except Exception as e:
                print(f"WARNING: the Host image failed ({e}); the Host "
                      "shows the text instead from now on.")
                HOST_DISPLAY = 'text'
        if HOST_DISPLAY == 'text':
            as_text()
        print(f"  Host screen ({HOST_DISPLAY}) drawn in "
              f"{1000 * (time.perf_counter() - started):.0f} ms.")

    def _host_image(self, draw, name):
        """Render draw() offscreen, copy it to the Host as a PNG file and
        show it there. The Host refuses a backdrop sent over the link
        (bitmapBackdrop) while recording and needs about 0.5 s for it;
        a file copied into its folder /elcl/data/recv/ and shown with
        its draw_image command (the route pylink's own library uses)
        works while recording and takes a few milliseconds (lab-tested
        2026-10-23). The Host keeps at most 100 such files per session
        and replaces the oldest, so each page gets its own name."""
        w, h = self.window.width, self.window.height
        self.fbo.bind()
        self.window.clear()
        draw()
        self.fbo.unbind()
        image = self.scene_tex.get_image_data()
        rgba = numpy.frombuffer(image.get_data('RGBA', w * 4), numpy.uint8)
        rgb = rgba.reshape(h, w, 4)[::-1, :, :3]     # rows top to bottom
        path = os.path.join(tempfile.gettempdir(), "chamois_" + name)
        with open(path, "wb") as f:
            f.write(png_bytes(rgb))
        tracker.sendDataFile(path, name)
        tracker.sendCommand(f"draw_image /elcl/data/recv/{name} 0 0 {w} {h}")

    # ---- the blocking pylink loops, run from tick() ----
    def request_setup(self):
        """Calibration/validation as soon as the next tick runs."""
        self._setup_request = True

    def request_drift_check(self, x, y, then):
        """A drift check at (x, y), in tracker coordinates, then call
        then(app)."""
        self._drift_request = (x, y, then)

    def skip_next_dt(self):
        """The next tick's dt includes the time spent blocked in pylink;
        dropping it keeps the blink phase of the trial intact."""
        self._skip_dt = True

    def _run_setup(self):
        self.in_setup = True
        try:
            tracker.setOfflineMode()
            tracker.doTrackerSetup()
        finally:
            self.in_setup = False
        message = tracker.getCalibrationMessage()
        if message:
            print("EyeLink: " + message.strip())

    def _run_drift_check(self, x, y):
        self.in_setup = True
        try:
            while True:
                # Escape during a drift check opens the setup screen
                # (e.g. to recalibrate); afterwards the check repeats.
                if tracker.doDriftCorrect(int(x), int(y), 1, 1) != pylink.ESC_KEY:
                    break
                if self.calibration.abort:
                    break
        finally:
            self.in_setup = False

    def _check_host(self):
        """The Host PC's side: a lost connection (Host software quit
        with Ctrl+Alt+Q, cable pulled) ends the experiment; the abort
        menu (Ctrl+Alt+A, during a reading trial's recording) ends the
        trial with Skip or Repeat (repeating is not supported, so it
        counts as Skip), and the experiment with Terminate."""
        if not tracker.isConnected():
            print("ERROR: the connection to the EyeLink Host PC was lost.")
            raise chamois.ExperimentAbortException()
        if self.recorder is None:
            return     # isRecording() is only meaningful while recording
        # While the Host shows its abort menu, isRecording() waits for
        # the experimenter's choice and reads keys through
        # PygletCalibration, so keys must not reach the page meanwhile:
        self.in_setup = True
        try:
            result = tracker.isRecording()
        finally:
            self.in_setup = False
        if result == pylink.TRIAL_OK:
            return
        self.skip_next_dt()
        if result == pylink.ABORT_EXPT:
            print("Experiment terminated from the Host PC.")
            raise chamois.ExperimentAbortException()
        # Skip or Repeat in the abort menu, or a recording that broke
        # off: the trial's data would be incomplete.
        print("  Page skipped from the Host PC.")
        self.page.response = "HOST_SKIP"
        self.advance()

    # ---- per-frame work ----
    def tick(self, dt):
        if self._skip_dt:
            dt = 0.0
            self._skip_dt = False
        if self._setup_request:
            self._setup_request = False
            if hardware_present:
                self._run_setup()
                self.skip_next_dt()
            self._check_abort()
            if isinstance(self.page, CalibrationPage):
                self.page.done(self)
            else:
                self._show_page_on_host()   # back on the same page
            return
        if self._drift_request is not None:
            x, y, then = self._drift_request
            self._drift_request = None
            self._run_drift_check(x, y)
            self._check_abort()
            then(self)
            return
        if hardware_present:
            self._check_host()
            if self.recorder is not None:
                self.recorder.drain()
            self.eyes = newest_eyes()
            points = [p for p in self.eyes if p is not None]
            self.gaze = (tuple(sum(c) / len(points) for c in zip(*points))
                         if points else None)
        super().tick(dt)

    def present(self):
        super().present()
        # The participant-screen gaze markers (GAZE_DISPLAY = 'both'),
        # drawn on the backbuffer after the page, so they are never in
        # a screenshot or a Host image; only while a trial records.
        if GAZE_DISPLAY != 'both' or self.recorder is None:
            return
        h = self.window.height
        if GAZE_MARKER == 'average':
            pairs = [(self.average_marker, self.gaze)]
        else:
            pairs = [(self.left_marker, self.eyes[0]),
                     (self.right_marker, self.eyes[1])]
        for marker, point in pairs:
            if point is not None:
                marker.x, marker.y = point[0], h - point[1]   # y up
                marker.draw()

    def _check_abort(self):
        if hardware_present and self.calibration.abort:
            raise chamois.ExperimentAbortException()

    # ---- input ----
    def on_key_press(self, symbol, modifiers):
        if self.in_setup:
            # pylink's loop owns the keyboard (see get_input_key):
            self.calibration.keys.append((symbol, modifiers))
            return pyglet.event.EVENT_HANDLED
        if (symbol == pkey.R and isinstance(self.page, chamois.Instructions)
                and hardware_present):
            # Hidden experimenter key: recalibrate mid-session. Only
            # accepted on instruction and break screens, where the
            # experiment is already standing still and nothing is being
            # recorded or answered.
            self.page.response = "RECALIBRATED"  # visible in the log
            self.request_setup()
            return pyglet.event.EVENT_HANDLED
        return super().on_key_press(symbol, modifiers)

    def on_text(self, text):
        if not self.in_setup:
            super().on_text(text)


def newest_eyes():
    """The latest gaze position of each eye, (left, right), in pixels
    from the top left; None for an eye that is not tracked or lost."""
    sample = tracker.getNewestSample()
    if sample is None:
        return (None, None)
    eyes = []
    for eye in (sample.getLeftEye(), sample.getRightEye()):
        point = None
        if eye is not None:
            x, y = eye.getGaze()
            if x > pylink.MISSING_DATA and y > pylink.MISSING_DATA:
                point = (x, y)
        eyes.append(point)
    return tuple(eyes)


class CalibrationPage(chamois.Page):
    """Calibration and validation at the start of the session. The
    screen stays empty while the experimenter calibrates from the Host
    PC; Escape there returns to the experiment, which then continues
    with the next page. The last calibration/validation result goes into
    the log's response column. In dummy mode the page is skipped."""

    def build(self, app):
        app.request_setup()

    def done(self, app):
        if hardware_present:
            message = tracker.getCalibrationMessage()
            self.response = (message or "").strip() or "CALIBRATED"
        else:
            self.response = "SKIPPED"
        app.advance()


# --------------------------------------------------------------------
# What the experiment script uses: one object for the whole tracker.
#
#     from EyeLink import EyeLink
#     tracker = EyeLink()                        # all defaults
#     tracker = EyeLink(host_ip="102.1.1.1",     # some non-defaults
#                       drift_check=True)
#
#     pages.append(tracker.CalibrationPage())
#     pages.append(tracker.ReadingTrial(item, condition, sentence))
#     tracker.setup()
#     try:
#         tracker.ExperimentApp(pages).run()
#     finally:
#         tracker.shutdown()
#
# TPx.py offers the same interface (without the calibration page).
# --------------------------------------------------------------------
class EyeLink:
    """The EyeLink. The keyword arguments are the tracker settings from
    the top of this file, in lower case; whatever is not given keeps
    its default. There is one tracker per experiment, so the settings
    are stored in this module, where every class in this file reads
    them."""

    SETTINGS = ('host_ip', 'drift_check', 'gaze_display', 'gaze_marker',
                'marker_radius', 'trigger_radius', 'trigger_dwell',
                'host_display', 'host_text_color', 'host_cross_color',
                'host_trigger_color')
    CHOICES = {'gaze_display': ('host', 'both'),
               'gaze_marker': ('eyes', 'average'),
               'host_display': ('image', 'text')}

    ReadingTrial = EyeLinkReadingTrial
    ExperimentApp = EyeLinkExperimentApp
    CalibrationPage = CalibrationPage

    def __init__(self, **settings):
        # Everything is checked first, and only then stored:
        for name, value in settings.items():
            if name not in self.SETTINGS:
                raise TypeError(
                    f"EyeLink() has no setting {name!r}. The settings are: "
                    f"{', '.join(self.SETTINGS)}.")
            allowed = self.CHOICES.get(name)
            if allowed and value not in allowed:
                raise ValueError(
                    f"{name} must be one of {allowed}, not {value!r}.")
        for name, value in settings.items():
            globals()[name.upper()] = value

    def setup(self):
        """Connect to the Host PC and open the session's EDF (dummy
        mode if no Host answers). Call after chamois.new_session()."""
        setup_hardware()

    def shutdown(self):
        """Stop recording, fetch the EDF and close the link. Call in a
        `finally`, so that it also runs after an error."""
        shutdown_hardware()
