"""Engine for psycholinguistic reading experiments, written in plain
Pyglet. Based on Chamois (https://github.com/tmalsburg/chamois) and
keeps its page model, log format, word-AOI conventions and Latin-square
counterbalancing, so analysis code written for Chamois logs keeps
working.

An experiment is a flat list of Page objects. Each page draws itself,
handles its own keys, and ends up as one row in the session log
(data/logs/). Reading trials additionally save a screenshot
(data/screenshots/) and an eye-tracking CSV (data/eyetracking/); all
files of a session share one filename stem, see session_prefix().

ExperimentApp owns the fullscreen window, a 60 Hz tick, the progress
bar and the page sequence. Every frame is first rendered into an
offscreen framebuffer (which is also where the screenshots are taken
from) and then put on screen by present(). Hardware layers subclass
the app and override draw_overlay(), present() and tick(); see TPx.py
and EyeLink.py. This file itself knows nothing about eye trackers.

The file you actually run is an experiment script: a page list and a
call of run_experiment(). See demo_experiment.py (no eye tracker),
demo_experiment_tpx.py and demo_experiment_eyelink.py.
"""

import csv
import os
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime

import pyglet
from pyglet.image import Texture
from pyglet.image.buffer import Framebuffer
from pyglet.window import key as pkey

# --------------------------------------------------------------------
# Settings: the defaults. An experiment script changes them with
# settings() (below, after the last of them), using the same names in
# lower case, e.g.
#
#     chamois.settings(fontsize=0.035,
#                      continue_text="Weiter mit der Leertaste.")
#
# so this file never has to be edited.
#
# Layout settings. All sizes and distances are fractions of the screen
# height (1.0 = full height), so the layout comes out the same on any
# resolution; px() and font_pt() convert to pixels and font points once
# the window exists. To get a feel for the numbers: on a 1080p screen,
# 0.02 is about 22 px and the default font size 0.03 about 24 pt.
# --------------------------------------------------------------------
FONT_NAME = ('Courier', 'Courier New', 'DejaVu Sans Mono', 'Liberation Mono')
FONTSIZE = 0.03         # glyph height
WORDSPACING = 0.02      # horizontal gap between words
LINESPACING = 0.04      # extra space above and below each line
LEFT_MARGIN = 0.08      # margin of left/right-aligned text; leaves room
                        # for a 'left' fixation cross in front of the text
CROSS_SIZE = 0.04       # footprint of the fixation cross
SCREEN_NUMBER = 0       # which monitor gets the fullscreen window

# Screen colors, as (R, G, B) with 0-255 per channel. Change them here,
# or from the experiment script (chamois.BACKGROUND_COLOR = ...) before
# the experiment starts. On the VPixx stimulus display (M16 mode) only
# grays exist, so with the tracker connected all three must be gray
# (R = G = B) and must avoid the console-marker levels 100-103;
# TPx.setup_hardware() checks this. Without the tracker any color works.
BACKGROUND_COLOR = (211, 211, 211)   # light gray screen (CSS/X11 "lightgray")
TEXT_COLOR = (0, 0, 0)               # black text
HINT_COLOR = (90, 90, 90)            # dimmer text for on-screen key hints

# Center dot colors of the two fixation targets. The line-start cross
# keeps a truly red dot: on VPixx hardware its blink phase runs in
# true-color C24 video mode (M16 itself is grayscale only), so the
# participant really sees red. The corner target has no visible dot at
# all, since a visible dot would draw the gaze to the trigger corner
# too early; TPx.setup_hardware() recolors it so that it stays
# invisible on the stimulus display but shows up yellow on the console
# monitor.
LINE_DOT_COLOR = (255, 0, 0)
CORNER_DOT_COLOR = None   # None = background color, i.e. no visible dot

# Screen size in pixels, filled in when the window is created.
screen_width = None
screen_height = None


def px(v):
    """Height units -> pixels."""
    return v * screen_height


def font_pt(size):
    """Height units -> font points. Pyglet assumes 96 dpi, so one
    point is 4/3 px."""
    return size * screen_height * 72.0 / 96.0


# Participant-facing strings; change these (with settings(), see
# below) to translate the experiment.
CONTINUE_TEXT = "Press the space bar to continue."
YESNO_HINT = "[f] key for “no” — [j] key for “yes”"
ANSWER_HINT = "Type your answer and press Enter to confirm."
SUBJECT_ID_PROMPT = "Please enter participant ID:"

# What settings() accepts, and what kind of value each one takes:
SETTINGS = {
    'font_name': 'font', 'fontsize': 'size', 'wordspacing': 'size',
    'linespacing': 'size', 'left_margin': 'size', 'cross_size': 'size',
    'screen_number': 'screen',
    'background_color': 'color', 'text_color': 'color',
    'hint_color': 'color', 'line_dot_color': 'color',
    'continue_text': 'text', 'yesno_hint': 'text', 'answer_hint': 'text',
    'subject_id_prompt': 'text',
}


def settings(**changes):
    """Change settings from the experiment script, before
    run_experiment(): chamois.settings(fontsize=0.035, text_color=(0,
    0, 0), continue_text="..."). The names are the ones of the settings
    above, in lower case; whatever is not named keeps its default. A
    misspelled name or an impossible value stops the script."""
    def is_number(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    checks = {
        'font': (lambda v: isinstance(v, str) or (
                     isinstance(v, (tuple, list)) and v
                     and all(isinstance(name, str) for name in v)),
                 "a font name, or several to try in turn, e.g. "
                 "('Courier', 'DejaVu Sans Mono')"),
        'size': (lambda v: is_number(v) and 0 < v < 1,
                 "a fraction of the screen height, e.g. 0.03"),
        'screen': (lambda v: isinstance(v, int) and not isinstance(v, bool)
                   and v >= 0,
                   "the number of a monitor: 0, 1, ..."),
        'color': (lambda v: isinstance(v, (tuple, list)) and len(v) == 3
                  and all(isinstance(c, int) and not isinstance(c, bool)
                          and 0 <= c <= 255 for c in v),
                  "three numbers from 0 to 255 (red, green, blue), e.g. "
                  "(211, 211, 211)"),
        'text': (lambda v: isinstance(v, str),
                 'a text in quotes, e.g. "Press the space bar."'),
    }
    # Everything is checked first, and only then stored:
    for name, value in changes.items():
        if name not in SETTINGS:
            raise TypeError(
                f"chamois.settings() has no setting {name!r}. The "
                f"settings are: {', '.join(SETTINGS)}.")
        is_valid, expected = checks[SETTINGS[name]]
        if not is_valid(value):
            raise ValueError(
                f"{name} must be {expected}, not {value!r}.")
    for name, value in changes.items():
        if SETTINGS[name] in ('color', 'font') and not isinstance(value, str):
            value = tuple(value)
        globals()[name.upper()] = value

# --------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------
session_id = None
subject_id = None              # entered on the SubjectIDPage
exp_starttime = None
latin_square_list_label = None


def new_session():
    """Set the session id (a timestamp) and create the data directory."""
    global session_id
    session_id = datetime.today().strftime('%Y-%m-%d_%H-%M-%S')
    if not os.path.exists("data"):
        os.makedirs("data")
    return session_id


def session_prefix():
    """Filename stem shared by all files of one session:
    <participant>_<date>_<time>_list-<label>. "anon" only appears if
    the session ends before the participant ID was entered."""
    return (f"{subject_id or 'anon'}_{session_id}"
            f"_list-{latin_square_list_label or 'NA'}")


def data_path(subdir, filename):
    """Return data/<subdir>/<filename>, creating the folder if it does
    not exist yet."""
    d = os.path.join("data", subdir)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, filename)


class ExperimentAbortException(Exception):
    pass


# --------------------------------------------------------------------
# Latin square counterbalancing and stimulus loading. Which lists have
# already been run is tracked in tested_latin_square_lists.txt. The
# four square functions are taken from the original Chamois (hence the
# two-space indents), so the counterbalancing behaves exactly like
# upstream. The checks are the original ones too; only their error
# messages were extended to name the items at fault. A design that
# fails a check stops the script right there, before any window opens.
# --------------------------------------------------------------------
def check_latin_square(target_sentences):
  if not target_sentences:
    raise RuntimeError(
      "Latin square: there are no target sentences (is the stimulus "
      "file empty?).")
  # Checks that all items have the same number of sentence:
  counts = Counter([x[0] for x in target_sentences])
  if len(set(counts.values())) > 1:
    usual = Counter(counts.values()).most_common(1)[0][0]
    odd = ", ".join(f"item {item} has {n}"
                    for item, n in counts.items() if n != usual)
    raise RuntimeError(
      "Latin square: all items need to have the same number of "
      f"sentences. Most items have {usual}, but {odd}.")
  # Checks that all items have the same conditions:
  d = dict()
  for k, v in [(x[0], x[1]) for x in target_sentences]:
    d.setdefault(k, []).append(v)
  items = list(d.keys())
  if len(set([tuple(l) for l in d.values()])) != 1:
    usual = Counter(tuple(l) for l in d.values()).most_common(1)[0][0]
    odd = "; ".join(f"item {item} has {', '.join(map(repr, l))}"
                    for item, l in d.items() if tuple(l) != usual)
    raise RuntimeError(
      "Latin square design looks unbalanced: every item needs the same "
      "conditions, in the same order. Most items have "
      f"{', '.join(map(repr, usual))}, but {odd}.")
  # Checks that each condition is present only once:
  item, conditions = d.popitem()
  if len(set(conditions)) < len(conditions):
    repeated = sorted(c for c, n in Counter(conditions).items() if n > 1)
    raise RuntimeError(
      "Latin square: a condition appears more than once per item: "
      f"{', '.join(map(repr, repeated))}.")
  return items, conditions


def latin_square_lists(target_sentences):
  items, conditions = check_latin_square(target_sentences)
  target_sentences.sort(key=lambda s: s[1])
  target_sentences.sort(key=lambda s: s[0])
  d = {}
  for s in target_sentences:
    d.setdefault(s[0], []).append(s)
  lists = [list() for _ in conditions]
  offset = 0
  for i in d.keys():
    for j, l in enumerate(lists):
      l.append(d[i][(j + offset) % len(conditions)])
    offset += 1
  return dict(zip(conditions, lists))


def next_latin_square_list_label(target_sentences):
  global latin_square_list_label
  filename = "tested_latin_square_lists.txt"
  items, conditions = check_latin_square(target_sentences)
  if not os.path.isfile(filename):
    latin_square_list_label = conditions[0]
  else:
    with open(filename, 'r', encoding='utf-8') as file:
      previous_lists = [line.strip() for line in file if line.strip()]
    c = Counter(previous_lists)
    x = [(cond, c[cond]) for cond in conditions]
    x.sort(key=lambda s: s[1])
    latin_square_list_label = x[0][0]
  return latin_square_list_label


def next_latin_square_list(target_sentences):
  next_list_label = next_latin_square_list_label(target_sentences)
  print(f'Next Latin square list: {next_list_label}')
  return latin_square_lists(target_sentences)[next_list_label]


def load_stimuli(filename):
  """Read a stimulus file with four columns and no header: item,
  condition, sentence, question. The file is either tab-separated
  (name ending in .tsv) or comma-separated (.csv; a sentence that
  contains a comma goes in double quotes, as spreadsheet programs
  write it). A literal \\n inside the sentence becomes a line break on
  screen. Empty rows are skipped. A malformed row raises a ValueError
  naming the file, the line and the problem — loading happens before
  the window opens, so a bad file stops the session before the
  participant is seated, never in the middle."""
  extension = os.path.splitext(filename)[1].lower()
  if extension not in ('.tsv', '.csv'):
    raise ValueError(
      f"{filename}: stimulus files must be tab-separated (.tsv) or "
      "comma-separated (.csv).")
  delimiter = ',' if extension == '.csv' else '\t'
  stimuli = []
  # utf-8-sig also accepts files saved with a byte-order mark (Excel,
  # Windows Notepad); without it, item 1 would read as '\ufeff1'.
  with open(filename, 'r', encoding='utf-8-sig', newline='') as file:
    if delimiter == '\t':
      reader = csv.reader(file, delimiter=delimiter, quoting=csv.QUOTE_NONE)
    else:
      reader = csv.reader(file, delimiter=delimiter)

    def bad(problem):
      raise ValueError(f"{filename}, line {reader.line_num}: {problem}")

    for row in reader:
      if not row or not row[0].strip():
        continue
      if len(row) != 4:
        kind = 'comma' if delimiter == ',' else 'tab'
        hint = ""
        if delimiter == '\t' and len(row) > 4:
          hint = " (a stray tab inside the sentence counts as a column break)"
        elif delimiter == ',' and len(row) > 4:
          hint = (" (a sentence or question that contains a comma has to "
                  "be in double quotes)")
        elif delimiter == ',' and ';' in row[0]:
          hint = (" (the file seems to be separated by semicolons; save "
                  "it with commas, or as a tab-separated .tsv)")
        bad(f"expected 4 {kind}-separated columns "
            f"(item, condition, sentence, question), found {len(row)}{hint}")
      try:
        item = int(row[0])
      except ValueError:
        bad(f"the item number {row[0]!r} is not a whole number")
      for name, value in (("condition", row[1]), ("sentence", row[2]),
                          ("question", row[3])):
        if not value.strip():
          bad(f"the {name} column is empty")
      stimuli.append((item, row[1], row[2].replace(r'\n', '\n'), row[3]))
  if not stimuli:
    raise ValueError(f"{filename}: the file contains no stimuli.")
  return stimuli


# --------------------------------------------------------------------
# Visual elements
# --------------------------------------------------------------------
class FixationCross:
    """A filled white disc with a cross cut out of it and a small
    colored dot in the center. Position in pixels, sizes scale with the
    screen. Disc and cross are grayscale, which makes them safe for M16
    mode; the dot color is up to the caller."""

    def __init__(self, x, y, dot_color=(120, 120, 120)):
        # Round everything to whole pixels. Sub-pixel positions get
        # smoothed by the renderer, and smoothing changes pixel values
        # that have to stay exact for the hardware CLUT lookup.
        ix = int(round(x))
        iy = int(round(y))
        r1 = int(round(px(0.0157)))            # outer disc
        r2 = int(round(px(0.0037)))            # center dot
        t = int(round(px(0.0065)))             # cross thickness
        bar = int(round(r1 * 1.1))

        self.shapes = [
            # Disc in the text color, the cross cut out of it in the
            # background color, so the target works on any background.
            pyglet.shapes.Circle(ix, iy, r1, color=TEXT_COLOR),
            pyglet.shapes.Rectangle(ix - bar, iy - t // 2, 2 * bar, t,
                                    color=BACKGROUND_COLOR),
            pyglet.shapes.Rectangle(ix - t // 2, iy - bar, t, 2 * bar,
                                    color=BACKGROUND_COLOR),
            # The dot is a little square rather than a circle: circle
            # edges are drawn smoothed, and the dot's pixels must keep
            # their exact color values.
            pyglet.shapes.Rectangle(ix - r2, iy - r2, 2 * r2, 2 * r2,
                                    color=tuple((dot_color or
                                                 BACKGROUND_COLOR)[:3])),
        ]

    def draw(self):
        for s in self.shapes:
            s.draw()


def make_label(text, x, y, size=None, color=None,
               anchor_x='center', anchor_y='center'):
    """x/y in pixels, size in height units (default FONTSIZE), color
    (R, G, B; default TEXT_COLOR)."""
    return pyglet.text.Label(
        text, font_name=FONT_NAME, font_size=font_pt(size or FONTSIZE),
        color=(*(color or TEXT_COLOR)[:3], 255),
        x=x, y=y, anchor_x=anchor_x, anchor_y=anchor_y)


def check_words_fit(text, max_width, where):
    """Raise if any single word in `text` is wider than max_width
    pixels. Pyglet would either split such a word in the middle (in
    wrapped labels) or the reading layout would clip it at the screen
    edge, and neither should ever reach a participant. So the
    experiment refuses to start and names the offending word instead;
    `where` says which page it came from."""
    for word in text.split():
        wpx = make_label(word, 0, 0).content_width
        if wpx > max_width:
            raise RuntimeError(
                "Word cannot be shown in full and would be broken/clipped "
                f"({where}): {word!r} is {int(wpx)} px wide but only "
                f"{int(max_width)} px are available. Fix "
                "the text (split the word or the line differently), reduce "
                "FONTSIZE, or lower the wrap width.")


def fitting_label(text, x, y, window_width):
    """Centered label that wraps into several lines instead of running
    off the screen edge — same margins as the other centered pages."""
    dy = px(0.04)
    label = make_label(text, x, y)
    if label.content_width > window_width - 2 * dy:
        label = pyglet.text.Label(
            text, font_name=FONT_NAME, font_size=font_pt(FONTSIZE),
            color=(*TEXT_COLOR, 255), x=x, y=y, anchor_x='center', anchor_y='center',
            width=int(window_width - 4 * dy), multiline=True, align='center')
    return label


# --------------------------------------------------------------------
# Pages. The experiment is a flat list of pages; each page becomes one
# row in the log.
# --------------------------------------------------------------------
class Page:
    def __init__(self):
        self.completed = False
        self.pno = None
        self.type = type(self).__name__
        self.starttime = None
        self.endtime = None
        self.item = None
        self.condition = None
        self.stimulus = None
        self.response = None
        self.screenshot = None
        self.metadata1 = None
        self.metadata2 = None
        self.labels = []  # the default draw() renders these

    def activate(self, app, pno):
        self.pno = pno
        self.starttime = round(time.time() - exp_starttime, 3)
        stim_abbrev = (self.stimulus or "").replace("\n", "")
        if len(stim_abbrev) > 50:
            stim_abbrev = stim_abbrev[:49] + "…"
        if self.item and self.condition:
            print(f"{self.pno}, {self.type} ({self.item}, "
                  f"{self.condition}): {stim_abbrev}")
        elif stim_abbrev:
            print(f"{self.pno}, {self.type}: {stim_abbrev}")
        else:
            print(f"{self.pno}, {self.type}")
        self.build(app)

    def deactivate(self, app):
        self.endtime = round(time.time() - exp_starttime, 3)
        self.completed = True

    # Subclasses override these:
    def build(self, app): pass
    def update(self, app, dt): pass
    def on_key(self, app, symbol, modifiers): pass
    def on_text(self, app, text): pass

    def validate(self, app):
        """Pre-flight check, run for every page before the experiment
        starts, so that text that cannot be displayed in full stops the
        run before a participant is seated rather than in the middle of
        a session. The default has nothing to check."""

    def draw(self):
        for l in self.labels:
            l.draw()

    def get_data(self):
        if not self.completed:
            raise RuntimeError(
                "Trying to retrieve results from a page that hasn't completed.")
        return (self.pno, self.type, f"{self.starttime:.3f}",
                f"{self.endtime:.3f}", self.item, self.condition,
                self.stimulus, self.response, self.screenshot,
                self.metadata1, self.metadata2)


class Instructions(Page):
    """A static page that advances on space. Also the class the hidden
    recalibration key looks for: TPx.py and EyeLink.py accept 'r' only
    while an Instructions page (or a subclass, like the break screens)
    is up."""

    def on_key(self, app, symbol, modifiers):
        if symbol == pkey.SPACE:
            app.advance()


class ExperimentalTrial(Page):
    """Base class for trial pages; saves a screenshot of the last frame
    when the page ends. The image is read from the offscreen
    framebuffer, i.e. before any packing shader has touched it, so the
    PNG stays human-readable."""

    def deactivate(self, app):
        self.screenshot = (
            f"{session_prefix()}_page-{self.pno:03d}_{self.type}"
            f"_item-{self.item:03d}_cond-{self.condition}.png")
        try:
            # The plain get_image_data().save() is the fast path; a
            # format conversion to RGB here costs about half a second
            # per trial. The PNG comes out with a solid background
            # because the framebuffer is cleared to an opaque color, see
            # ExperimentApp.__init__.
            app.scene_tex.get_image_data().save(
                data_path("screenshots", self.screenshot))
        except Exception:
            sys.stderr.write(f"Warning: Screenshot failed: {self.screenshot}\n")
        super().deactivate(app)


class CenteredInstructions(Instructions):
    def __init__(self, instructions):
        super().__init__()
        self.text = instructions
        instructions = re.sub('[\n\r\t ]+', ' ', instructions)
        self.stimulus = instructions
        if len(instructions) > 40:
            self.stimulus = instructions[:40] + ' …'

    def validate(self, app):
        # Only wrapping can break a word, so a one-line instruction
        # that fits is fine as it is. Make the same decision build()
        # will make, and if the text is going to wrap, check that every
        # word fits into the wrap width.
        dy = px(0.04)
        if make_label(self.text, 0, 0).content_width > app.window.width - 2 * dy:
            check_words_fit(self.text, app.window.width - 4 * dy,
                            f"instruction {self.stimulus!r}")

    def build(self, app):
        cx, cy = app.window.width // 2, app.window.height // 2
        dy = px(0.04)
        # Long instructions wrap instead of running off screen:
        text_label = make_label(self.text, cx, cy + dy)
        if text_label.content_width > app.window.width - 2 * dy:
            text_label = pyglet.text.Label(
                self.text, font_name=FONT_NAME, font_size=font_pt(FONTSIZE),
                color=(*TEXT_COLOR, 255), x=cx, y=cy + dy, anchor_x='center',
                anchor_y='center', width=int(app.window.width - 4 * dy),
                multiline=True, align='center')
        self.labels = [
            text_label,
            make_label(CONTINUE_TEXT, cx, cy - px(0.09)),
        ]


class Next(Instructions):
    """Break screen between trials."""

    def build(self, app):
        cx, cy = app.window.width // 2, app.window.height // 2
        self.labels = [make_label(CONTINUE_TEXT, cx, cy)]


class SubjectIDPage(Page):
    def build(self, app):
        cx, cy = app.window.width // 2, app.window.height // 2
        dy = px(0.04)
        self.entered = ""
        self.entry = make_label("_", cx, cy - dy)
        self.labels = [
            make_label(SUBJECT_ID_PROMPT, cx, cy + dy),
            self.entry,
        ]

    def on_text(self, app, text):
        if text.isprintable():
            self.entered += text
            self.entry.text = self.entered + "_"

    def on_key(self, app, symbol, modifiers):
        if symbol == pkey.BACKSPACE:
            self.entered = self.entered[:-1]
            self.entry.text = self.entered + "_"
        elif symbol in (pkey.RETURN, pkey.ENTER):
            global subject_id
            # The raw input goes into the log; the ID used in filenames
            # is reduced to filename-safe characters.
            subject_id = re.sub(r'[^A-Za-z0-9._-]+', '-',
                                self.entered).strip('-.') or "anon"
            self.response = self.entered
            app.advance()


def check_percent(percent):
    is_number = (isinstance(percent, (int, float))
                 and not isinstance(percent, bool))
    if not is_number or not 0 <= percent <= 100:
        raise ValueError(f"percent must be a number from 0 to 100, "
                         f"not {percent!r}.")
    return percent


def sample_questions(pages):
    """Thin out the question trials that were created with a `percent`
    below 100: of all question trials of the same type (yes/no or
    typed answer) sharing the same percent value, exactly that share
    is kept (rounded to a whole number of questions) and the rest are
    removed from the page list, so they are neither shown nor logged.
    Which ones are kept is drawn anew for every session. The count is
    exact: percent=30 on 40 questions gives every participant 12 of
    them."""
    groups = {}
    for index, page in enumerate(pages):
        percent = getattr(page, 'percent', 100)
        if percent < 100:
            groups.setdefault((type(page), percent), []).append(index)
    dropped = set()
    for (_, percent), indices in groups.items():
        n_asked = round(len(indices) * percent / 100)
        asked = set(random.sample(indices, n_asked))
        dropped.update(set(indices) - asked)
    return [page for index, page in enumerate(pages)
            if index not in dropped]


class YesNoQuestionTrial(ExperimentalTrial):
    """A yes/no comprehension question, answered with f (no) or j
    (yes). `percent` below 100 shows only that share of the questions
    created with the same value, see sample_questions()."""

    def __init__(self, item, condition, question, percent=100):
        super().__init__()
        self.item = item
        self.condition = condition
        self.stimulus = question
        self.percent = check_percent(percent)

    def validate(self, app):
        dy = px(0.04)
        if (make_label(self.stimulus, 0, 0).content_width
                > app.window.width - 2 * dy):
            check_words_fit(self.stimulus, app.window.width - 4 * dy,
                            f"question item {self.item}/{self.condition}")

    def build(self, app):
        cx, cy = app.window.width // 2, app.window.height // 2
        dy = px(0.04)
        question_label = make_label(self.stimulus, cx, cy)
        if question_label.content_width > app.window.width - 2 * dy:
            question_label = pyglet.text.Label(
                self.stimulus, font_name=FONT_NAME,
                font_size=font_pt(FONTSIZE), color=(*TEXT_COLOR, 255), x=cx, y=cy,
                anchor_x='center', anchor_y='center',
                width=int(app.window.width - 4 * dy),
                multiline=True, align='center')
        self.labels = [
            question_label,
            make_label(YESNO_HINT, cx, px(0.03),
                       size=FONTSIZE * 0.7, color=HINT_COLOR),
        ]

    def on_key(self, app, symbol, modifiers):
        if symbol == pkey.F:
            self.response = "no"
            app.advance()
        elif symbol == pkey.J:
            self.response = "yes"
            app.advance()


class ComprehensionTrial(ExperimentalTrial):
    """A comprehension question the participant answers in their own
    words: the question, a text field below it, Enter to confirm. What
    was typed goes into the log's response column. The page name is
    the one the original Chamois used, so old analysis code finds
    these rows. (For the yes/no version answered with f/j, use
    YesNoQuestionTrial — an experiment can mix both freely.)

    Typing is bounded by the screen: characters that would push the
    answer down into the hint line are ignored, so nothing a
    participant types can ever be hidden or run off the screen.

    `percent` below 100 shows only that share of the questions created
    with the same value, see sample_questions()."""

    def __init__(self, item, condition, question, percent=100):
        super().__init__()
        self.item = item
        self.condition = condition
        self.stimulus = question
        self.percent = check_percent(percent)
        self.entered = ""

    def validate(self, app):
        dy = px(0.04)
        if (make_label(self.stimulus, 0, 0).content_width
                > app.window.width - 2 * dy):
            check_words_fit(self.stimulus, app.window.width - 4 * dy,
                            f"question item {self.item}/{self.condition}")

    def build(self, app):
        cx, cy = app.window.width // 2, app.window.height // 2
        dy = px(0.04)
        self.entered = ""
        # The question grows upwards from just above the middle, the
        # answer downwards from just below it, so neither can run into
        # the other however long they get:
        question = fitting_label(self.stimulus, cx, cy + px(0.05),
                                 app.window.width)
        question.anchor_y = 'bottom'
        question.y = cy + px(0.05)
        self.entry = pyglet.text.Label(
            "_", font_name=FONT_NAME, font_size=font_pt(FONTSIZE),
            color=(*TEXT_COLOR, 255), x=cx, y=cy - px(0.05), anchor_x='center',
            anchor_y='top', width=int(app.window.width - 4 * dy),
            multiline=True, align='center')
        self.labels = [
            question,
            self.entry,
            make_label(ANSWER_HINT, cx, px(0.03),
                       size=FONTSIZE * 0.7, color=HINT_COLOR),
        ]

    def on_text(self, app, text):
        if not text.isprintable():
            return
        previous = self.entered
        self.entered += text
        self.entry.text = self.entered + "_"
        # Refuse the keystroke if the answer would reach the hint line,
        # or if it no longer fits the field's width — the latter happens
        # when a single word is too long to be wrapped anywhere and
        # would run off the screen sideways.
        if (self.entry.y - self.entry.content_height < px(0.09)
                or self.entry.content_width > self.entry.width):
            self.entered = previous
            self.entry.text = self.entered + "_"

    def on_key(self, app, symbol, modifiers):
        if symbol == pkey.BACKSPACE:
            self.entered = self.entered[:-1]
            self.entry.text = self.entered + "_"
        elif symbol in (pkey.RETURN, pkey.ENTER):
            # Collapse whitespace: the log is tab-separated, and an
            # empty answer is refused so a stray Enter cannot skip the
            # question.
            answer = ' '.join(self.entered.split())
            if answer:
                self.response = answer
                app.advance()


def wrap_text(text, width):
    """Greedy line wrap to at most `width` characters per line (the
    reading font is monospaced, so a character count maps directly to
    on-screen width). Each line takes as many words as fit; the word
    that would push it past `width` starts the next line, so a line may
    end a little short but never runs over. Words are never split: a
    single word longer than `width` gets a line of its own.

    Manual line breaks win: a text that already contains a '\\n' comes
    back unchanged, so hand-placed breaks are never disturbed. With
    `width` None the text also comes back unchanged."""
    if width is None:
        return text
    if isinstance(width, bool) or not isinstance(width, int) or width < 1:
        raise ValueError(
            "wrap must be None or a positive number of characters "
            f"per line, not {width!r}.")
    if '\n' in text:
        return text
    words = text.split()
    if len(words) < 2:
        return text
    lines = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current += ' ' + word
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return '\n'.join(lines)


class ReadingTrial(ExperimentalTrial):
    """A blinking fixation cross, then the text, laid out word by
    word. A '\\n' in the stimulus starts a new line; every line gets
    LINESPACING of padding above and below. The trial ends with space,
    or Escape to abort it. The eye tracker versions (TPxReadingTrial,
    EyeLinkReadingTrial) add eye-movement recording, a look-target in
    the lower right corner and the gaze trigger that ends the trial
    when the participant looks there.

    Where things appear is set per trial:

      y_offset        vertical position of the text block's center, in
                      height units with the screen center as origin and
                      y pointing up: 0.0 (default) centers the block on
                      the screen, 0.3 moves it up, -0.3 down.
      align           horizontal alignment of each line: 'left'
                      (default), 'center', or 'right'.
      cross_position  where the blinking fixation cross appears:
                      'center' (default) = the middle of the text
                      block, which with y_offset 0.0 is exactly the
                      screen center; 'left' = just before the first
                      word, so reading starts with a saccade into it;
                      'right' = just past the end of the first line.
      wrap            automatic line breaking: the maximum number of
                      characters per line, or None (default) for
                      none. A text that already contains a manual
                      '\\n' is shown exactly as written, whatever
                      `wrap` says (see wrap_text).
    """

    # The look-target in the lower right corner is only shown by the
    # eye tracker versions of this class, where looking at it ends the
    # trial. Without a tracker it would mean nothing.
    corner_target = False

    def __init__(self, item, condition, text, y_offset=0.0,
                 align='left', cross_position='center', wrap=None):
        super().__init__()
        self.item = item
        self.condition = condition
        self.text = wrap_text(text, wrap)             # with line breaks
        self.stimulus = self.text.replace('\n', ' ')  # one line, for the log
        self.y_offset = y_offset
        self.align = align
        self.cross_position = cross_position

    def validate(self, app):
        # A word wider than the widest possible line can never be
        # placed and would be clipped at the screen edge. Line, top,
        # and bottom overflow are checked again at layout time.
        avail = app.window.width - 2 * px(LEFT_MARGIN)
        check_words_fit(self.text, avail,
                        f"reading item {self.item}/{self.condition}")

    def build(self, app):
        w, h = app.window.width, app.window.height
        # Line metrics, needed before any word label exists:
        self.line_height = make_label("Ag", 0, 0).content_height
        self.line_pitch = self.line_height + 2 * px(LINESPACING)

        # Measure every line up front: the widths decide where each
        # line starts and where the cross goes, and the cross is shown
        # long before the words are.
        self.lines = [l for l in self.text.split('\n') if l.strip()]
        self.line_widths = []
        for line in self.lines:
            words = line.split()
            width = sum(make_label(word, 0, 0).content_width
                        for word in words)
            width += px(WORDSPACING) * (len(words) - 1)
            self.line_widths.append(width)

        # Vertical placement: the block is centered on y_offset.
        block_height = len(self.lines) * self.line_pitch
        block_center = h / 2 + px(self.y_offset)
        self.first_line_y = (block_center + block_height / 2
                             - self.line_pitch / 2)

        # Horizontal placement, one start position per line:
        self.line_starts = []
        for width in self.line_widths:
            if self.align == 'center':
                self.line_starts.append((w - width) / 2)
            elif self.align == 'right':
                self.line_starts.append(w - px(LEFT_MARGIN) - width)
            else:  # 'left'
                self.line_starts.append(px(LEFT_MARGIN))

        # The blinking cross. 'left' mirrors 'right': one word space
        # before the first word, never on top of it, so the eyes have to
        # make a saccade into the first word once the text appears.
        if self.cross_position == 'left':
            cross_x = (self.line_starts[0] - px(WORDSPACING)
                       - px(CROSS_SIZE) / 2)
            cross_y = self.first_line_y
        elif self.cross_position == 'right':
            cross_x = (self.line_starts[0] + self.line_widths[0]
                       + px(WORDSPACING) + px(CROSS_SIZE) / 2)
            cross_y = self.first_line_y
        else:  # 'center'
            cross_x = w / 2
            cross_y = block_center
        self.cross = FixationCross(cross_x, cross_y,
                                   dot_color=LINE_DOT_COLOR)
        self.corner_cross = None
        if self.corner_target:
            corner = px(0.04)
            self.corner_cross = FixationCross(w - corner, corner,
                                              dot_color=CORNER_DOT_COLOR)
        self.state = 'blink'
        self.elapsed = 0.0
        self.cross_visible = True
        # The cross blinks with on/off phases that get shorter and
        # shorter, ending on a brief off phase.
        self.blink_phases = []
        t = 0.2
        while t > 0.01:
            self.blink_phases.append((True, 0.03 + t))
            self.blink_phases.append((False, 0.03 + t / 2))
            t *= 0.55
        self.words = []
        self.read_start = None

    def show_words(self, app):
        """Lay the text out line by line and start the reading phase.
        Each word's bounding box goes into the log (metadata1) as
        left,top,right,bottom in screen pixels, origin at the top left
        corner, y pointing down (as in the original Chamois and in the
        screenshots). The eye-tracking data is saved in the same
        coordinate system."""
        w, h = app.window.width, app.window.height
        aois = []
        # A misconfigured y_offset must not push text off the screen:
        if self.first_line_y + self.line_height / 2 + px(LINESPACING) > h:
            raise RuntimeError(
                "Text extends beyond window top: " + self.stimulus)
        y = self.first_line_y
        for line, x in zip(self.lines, self.line_starts):
            for word in line.split():
                label = make_label(word, x, y, anchor_x='left')
                self.words.append(label)
                cw, ch = label.content_width, label.content_height
                if x + cw > w:
                    raise RuntimeError(
                        "Word cannot be shown in full and would be clipped: "
                        f"{word!r} in line {line!r} "
                        f"(item {self.item}/{self.condition}).")
                # Pyglet's y points up from the bottom; the log's y
                # points down from the top:
                aois.append(
                    f"{round(x)},{round(h - (y + ch / 2))},"
                    f"{round(x + cw)},{round(h - (y - ch / 2))}")
                x += cw + px(WORDSPACING)
            if x - px(WORDSPACING) > w:
                raise RuntimeError(
                    "Text extends beyond window boundaries: " + line)
            y -= self.line_pitch
        # The last line has to stay clear of the corner look-target
        # (the space is kept free without a tracker too, so a text that
        # fits in one case fits in the other):
        if (y + self.line_pitch - self.line_height // 2 - px(LINESPACING)
                < px(0.07)):
            raise RuntimeError(
                "Text extends beyond window bottom: " + self.stimulus)
        self.metadata1 = ";".join(aois)
        self.read_start = self.elapsed
        self.state = 'reading'

    def update(self, app, dt):
        self.elapsed += dt
        if self.state == 'blink':
            # Walk the blink schedule to find the current phase; when
            # the schedule has run out, put the words up.
            t = self.elapsed
            for visible, duration in self.blink_phases:
                if t < duration:
                    self.cross_visible = visible
                    return
                t -= duration
            self.show_words(app)

    def draw(self):
        if self.state == 'blink':
            if self.cross_visible:
                self.cross.draw()
        else:
            # The line-start cross disappears once the text is up; only
            # the corner look-target (eye tracker versions) stays
            # visible during reading.
            if self.corner_cross:
                self.corner_cross.draw()
            for word in self.words:
                word.draw()

    def on_key(self, app, symbol, modifiers):
        if self.state != 'reading':
            return
        if symbol == pkey.SPACE:
            app.advance()
        elif symbol == pkey.ESCAPE:
            self.response = "ABORTED"
            print("  Page aborted.")
            app.advance()


# --------------------------------------------------------------------
# Session log
# --------------------------------------------------------------------
def write_log(pages, partial=False):
    path = data_path("logs", session_prefix() + "_log.tsv")
    with open(path, "w", encoding="utf-8") as f:
        f.write('\t'.join(["pno", "type", "starttime", "endtime", "item",
                           "condition", "stimulus", "response", "screenshot",
                           "metadata1", "metadata2",
                           "screen_width", "screen_height"]))
        f.write('\n')
        # The resolution of this session's screen, read from the
        # window, on every row: the pixel coordinates in metadata1 and
        # in the eye-tracking files only mean something together with it.
        screen = tuple('' if v is None else str(v)
                       for v in (screen_width, screen_height))
        for p in pages:
            if not p.completed:
                if partial:
                    break
                continue
            t = tuple(str(v) if v is not None else '' for v in p.get_data())
            f.write("\t".join(t + screen))
            f.write("\n")
    print("Session log stored in: " + path)


# --------------------------------------------------------------------
# The experiment runner: window, offscreen framebuffer, progress bar,
# page sequence, 60 Hz tick. Hardware layers subclass this and override
# the hooks draw_overlay(), present() and tick().
# --------------------------------------------------------------------
class ExperimentApp:
    def __init__(self, pages, fullscreen=True):
        # Questions created with percent < 100 are thinned out here:
        self.pages = sample_questions(pages)
        self.index = -1
        self.page = None
        self.finished = False

        if fullscreen:
            screens = pyglet.display.get_display().get_screens()
            self.window = pyglet.window.Window(
                fullscreen=True,
                screen=screens[min(SCREEN_NUMBER, len(screens) - 1)],
            )
        else:  # small invisible window for offline testing
            self.window = pyglet.window.Window(1280, 720, visible=False)
        self.window.set_mouse_visible(False)

        # Clear to the opaque background color. The framebuffer doubles as the
        # screenshot source, and text is alpha-blended: with the default
        # transparent clear the saved PNGs had an alpha-0 background
        # that many image viewers render unreadably. The display path
        # ignores alpha, so this only affects the saved files.
        pyglet.gl.glClearColor(*(c / 255 for c in BACKGROUND_COLOR), 1.0)

        # The height-unit converters need the screen size:
        global screen_width, screen_height
        screen_width = self.window.width
        screen_height = self.window.height

        # All drawing goes into this offscreen texture first; present()
        # puts it on screen, and trial screenshots read it back. NEAREST
        # filtering is required: any interpolation would mix the exact
        # pixel values that the M16 overlay channel depends on.
        self.scene_tex = Texture.create(
            self.window.width, self.window.height,
            min_filter=pyglet.gl.GL_NEAREST,
            mag_filter=pyglet.gl.GL_NEAREST,
        )
        self.fbo = Framebuffer()
        self.fbo.attach_texture(self.scene_tex)

        # Progress bar along the top edge:
        w, h = self.window.width, self.window.height
        self.pbar_bg = pyglet.shapes.Rectangle(
            0, h - 14, w, 14, color=(128, 128, 128))
        self.pbar_fg = pyglet.shapes.Rectangle(
            0, h - 14, 1, 14, color=TEXT_COLOR)

        self.window.event(self.on_draw)
        self.window.event(self.on_key_press)
        self.window.event(self.on_text)

    # ---- experiment flow ----
    def validate_pages(self):
        """Run every page's validate() before anything is shown, so a
        word that cannot be displayed in full stops the experiment up
        front, with the offender named, and not mid-session."""
        for pno, page in enumerate(self.pages):
            page.pno = pno
            page.validate(self)

    def start(self):
        global exp_starttime
        exp_starttime = time.time()
        self.index = 0
        self.page = self.pages[0]
        self.page.activate(self, 0)

    def advance(self):
        self.page.deactivate(self)
        self.index += 1
        if self.index >= len(self.pages):
            self.finish()
        else:
            self.page = self.pages[self.index]
            self.page.activate(self, self.index)
            self.pbar_fg.width = max(
                1, int(self.window.width * self.index / len(self.pages)))

    def finish(self):
        write_log(self.pages)
        if latin_square_list_label:
            with open('tested_latin_square_lists.txt', 'a',
                      encoding='utf-8') as file:
                file.write(f'{latin_square_list_label}\n')
        print("Experiment finished.")
        self.finished = True
        self.page = None
        self.window.close()

    # ---- per-frame work ----
    def tick(self, dt):
        if self.page:
            self.page.update(self, dt)

    def on_draw(self):
        # First pass: render the scene into the framebuffer.
        self.fbo.bind()
        self.window.clear()
        if self.page:
            self.page.draw()
        self.pbar_bg.draw()
        self.pbar_fg.draw()
        self.draw_overlay()
        self.fbo.unbind()
        # Second pass: put the scene texture on the backbuffer.
        self.window.clear()
        self.present()

    # ---- hooks for hardware layers ----
    def draw_overlay(self):
        """Extra drawing into the scene framebuffer, e.g. gaze markers."""

    def present(self):
        """Put the scene texture on screen. Hardware layers override
        this to run it through a packing shader instead."""
        self.scene_tex.blit(0, 0)

    # ---- input ----
    def on_key_press(self, symbol, modifiers):
        if symbol == pkey.Q and (modifiers & pkey.MOD_CTRL):
            raise ExperimentAbortException()
        if self.page:
            self.page.on_key(self, symbol, modifiers)
        return pyglet.event.EVENT_HANDLED  # otherwise Escape closes the window

    def on_text(self, text):
        if self.page:
            self.page.on_text(self, text)

    # ---- main loop ----
    def run(self):
        self.validate_pages()
        self.start()
        pyglet.clock.schedule_interval(self.tick, 1 / 60)
        pyglet.app.run()
        pyglet.clock.unschedule(self.tick)
        if not self.finished:
            # The window was closed from outside (e.g. Alt+F4):
            raise ExperimentAbortException()


def run_experiment(pages, tracker=None):
    """Run the experiment: the one call at the end of an experiment
    script. `tracker` is the eye tracker object (TPx() or EyeLink());
    without one the experiment runs as a plain reading experiment.

    Takes care of everything around the pages: the session id, the
    tracker's setup, the log (also a partial one if the experiment is
    aborted with Ctrl+Q or stops with an error), and shutting the
    tracker down again whatever happens."""
    new_session()
    if tracker is not None:
        tracker.setup()
    try:
        if tracker is not None:
            app = tracker.ExperimentApp(pages)
        else:
            app = ExperimentApp(pages)
        try:
            app.run()
        except Exception as e:
            aborted = isinstance(e, ExperimentAbortException)
            print("Experiment aborted." if aborted else "An error occurred.")
            # Whatever was completed up to this point is kept:
            write_log(app.pages, partial=True)
            if not aborted:
                raise
            sys.exit(1)
    finally:
        if tracker is not None:
            tracker.shutdown()
