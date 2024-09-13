
from FreeSimpleGUI import *
theme('Default1')

import time, random, re, math, os, sys, re, csv, subprocess
from collections import Counter
from datetime import datetime

font = "Courier"
fontsize = 22
wordspacing = 18
latin_square_list_label = None

class ExperimentAbortException(Exception):
  pass

class Page:
  def __init__(self, layout, **kwargs):
    self.layout     = layout
    self.column     = Column(layout, visible=False, expand_x=True, expand_y=True, **kwargs)
    self.event      = None
    self.values     = None
    self.completed  = False
    self.pno        = None
    self.type       = type(self).__name__
    self.starttime  = None
    self.endtime    = None
    self.item       = None
    self.condition  = None
    self.stimulus   = None
    self.response   = None
    self.screenshot = None
    self.metadata1  = None
    self.metadata2  = None
  # Activate the page as defined at creation.
  def activate(self, window, pno):
    self.column.update(visible=True)
    self.pno = pno
    self.starttime = round(time.time() - exp_starttime, 3)
    # Print message in terminal:
    stim_abbrev = self.stimulus or ""
    stim_abbrev = stim_abbrev.replace("\n", "")
    if len(stim_abbrev)>50:
      stim_abbrev = stim_abbrev[:49] + "…"
    if self.item and self.condition:
      print("%d, %s (%s, %s): %s" % (self.pno, self.type, self.item, self.condition, stim_abbrev))
    elif stim_abbrev:
      print("%d, %s: %s" % (self.pno, self.type, stim_abbrev))
    else:
      print("%d, %s" % (self.pno, self.type))
    # Let's go:
    self.prelude(window)
    self.handle_event(window)
  # Optional stage-setting that can only be performed once the page is
  # displayed:
  def prelude(self, window):
    window.refresh()
  def handle_event(self, window):
    while True:
      self.event, self.values = window.read()
      if self.event == WIN_CLOSED:
        raise ExperimentAbortException()
      if self.event.startswith('space:'):
        break
    self.deactivate()
  def deactivate(self):
    self.endtime = round(time.time() - exp_starttime, 3)
    self.column.update(visible=False)
    self.completed = True
  def get_data(self):
    if not self.completed:
      raise RuntimeError("Trying to retrieve results from a page that hasn't completed.")
    return (self.pno, self.type, f"{self.starttime:.3f}", f"{self.endtime:.3f}", self.item, self.condition, self.stimulus, self.response, self.screenshot, self.metadata1, self.metadata2)

# Message shares an interface with Page but is not itself a page since
# it is not part of the GUI.
class Message:
  def __init__(self, message):
    self.pno       = None
    self.type      = type(self).__name__
    self.metadata1 = message
    self.starttime = None
  def activate(self, _, pno):
    self.pno = pno
    self.starttime = round(time.time() - exp_starttime, 3)
  def get_data(self):
    # TODO: Make sure to show 3 digits in start and endtime.
    return (self.pno, self.type, f"{self.starttime:.3f}", None, None, None, None, None, None, self.metadata1, None)

# Separate class to make a page show up in the results as
# "Instructions".
class Instructions(Page):
  pass

class CenteredInstructions(Instructions): 
  def __init__(self, instructions, **kwargs):
    layout = [[VPush()],
              [Text(instructions, pad=50)],
              [Text("Press space bar to continue.", pad=50)],
              [VPush()]]
    super().__init__(layout, element_justification="center")
    instructions = re.sub('[\n\r\t ]+', ' ', instructions)
    self.stimulus = instructions
    if len(instructions) > 40:
      self.stimulus = instructions[:40] + ' …'

class Next(Instructions):
  def __init__(self, **kwargs):
    layout = [[VPush()],
              [Text("Press space bar to continue.")],
              [VPush()]]
    super().__init__(layout, element_justification="center")

class ConsentForm(Instructions):
  def __init__(self, text, **kwargs):
    layout = [[VPush()],
              [Text(text, pad=50)],
              [Text("Press space bar to consent.", pad=50)],
              [VPush()]]
    super().__init__(layout, element_justification="center")
    text = re.sub('[\n\r\t ]+', ' ', text)
    self.stimulus = text
    if len(text) > 40:
      self.stimulus = text[:40] + ' …'

# Takes a screenshot before handling an event:
class ExperimentalTrial(Page):
  def deactivate(self):
    self.screenshot = "%s_%03d_%s_%03d_%s.png" % (session_id, self.pno, self.type, self.item, self.condition)
    try:
      # Fails on wayland and is not accurate (some pixels horizontally
      # offset):
      # window.save_window_screenshot_to_disk(self.screenshot)

      # Scrot also doesn't work on wayland but it's accurate:
      subprocess.run(["scrot", "data/" + self.screenshot])
    except:
      sys.stderr.write(f"Warning: Screenshot failed: {self.screenshot}\n")
    super().deactivate()

# TODO Specify size in visual field degrees (adapts to screen size,
# distance, ...).
class FixationCross(Graph):
  def __init__(self, width=38, height=38, radius1=17, radius2=4):
    self.width   = width
    self.height  = height
    self.radius1 = radius1
    self.radius2 = radius2
    theme_properties = LOOK_AND_FEEL_TABLE[theme()]
    self.background_color = theme_properties['BACKGROUND']
    self.foreground_color = theme_properties['TEXT']
    super().__init__(
      canvas_size       = (width, height),
      graph_bottom_left = (-width/2, -height/2), 
      graph_top_right   = (width/2, height/2),
      background_color  = self.background_color)
  def draw(self):
    r1 = self.radius1
    r2 = self.radius2
    # Draw the inner oval:
    self.draw_oval((r1, r1), (-r1, -r1),
                   line_color=self.foreground_color,
                   fill_color=self.foreground_color)
    # Draw the cross using lines:
    self.draw_line((r1*1.1, 0), (-r1*1.1, 0), width=7, color=self.background_color)
    self.draw_line((0, r1*1.1), (0, -r1*1.1), width=7, color=self.background_color)
    # Draw the inner oval:
    self.draw_oval((r2, r2), (-r2, -r2),
                   line_color="red",
                   fill_color="red")
  def blink(self, window):
    t = 200
    while t > 10:
      self.draw()
      window.read(timeout=30+t)
      self.erase()
      window.read(timeout=30+t/2)
      t *= 0.55

class ReadingTrial(ExperimentalTrial):
  def __init__(self, item, condition, text):
    self.fixation_cross = fc = FixationCross()
    self.fixation_cross2 = fc2 = FixationCross()
    self.words = [Text(w, pad=int(wordspacing/2), visible=False) for w in s.split()]
    layout = [[VPush()],
              [fc] + self.words,
              [VPush()],
              [Push(), fc2]]
    super().__init__(layout, vertical_alignment="center")
    self.item      = item
    self.condition = condition
    self.stimulus  = text
  def prelude(self, window):
    # Blink fixation cross:
    self.fixation_cross.blink(window)
    # Show words:
    for w in self.words:
      w.update(visible=True)
    self.fixation_cross2.draw()
    window.refresh()
    # Calculate AOIs:
    self.aois = []
    for i,word in enumerate(self.words):
      x, y = word.widget.winfo_rootx(), word.widget.winfo_rooty()
      w, h = word.get_size()
      self.aois.append(f'{x},{y},{x+w},{y+h}')
    # Check whether text extends beyond screen (no exact science):
    window_width, _ = window.size
    first_word_length = len(self.words[0].get())
    first_word_width = self.words[0].widget.winfo_width()
    first_word_start = self.words[0].widget.winfo_rootx()
    char_width = (first_word_width - 4) / first_word_length
    last_word_end = first_word_start + char_width * len(''.join(self.stimulus.split())) + 4 * len(self.words) + wordspacing * (len(self.words)-1)
    if (window_width < last_word_end):
      time.sleep(1)
      raise RuntimeError("Text extends beyond window boundaries: " + self.stimulus)
    self.metadata1 = ";".join(self.aois)
    super().prelude(window)
  def handle_event(self, window):
    while True:
      self.event, self.values = window.read()
      if self.event == WIN_CLOSED:
        raise ExperimentAbortException()
      if self.event.startswith('space:'):
        break
      if self.event.startswith('Escape:'):
        self.response = "ABORTED"
        print("  Page aborted.")
        break
    self.deactivate()

class YesNoQuestionTrial(ExperimentalTrial):
  def __init__(self, item, condition, question):
    layout = [[VPush()],
              [Text(q, pad=50)],
              [VPush()],
              [Text("[f] key for “no” — [j] key for “yes”", font=f"{font} {int(fontsize*0.7)}", text_color="grey79")]]
    super().__init__(layout, element_justification="center")
    self.item      = item
    self.condition = condition
    self.stimulus  = question
    self.response  = None
  def handle_event(self, window):
    while True:
      self.event, self.values = window.read()
      if self.event == WIN_CLOSED:
        raise ExperimentAbortException()
      if self.event.startswith("f:") or self.event.startswith("j:"):
        break
    self.deactivate()
    if self.event.startswith("f:"):
      self.response = "no"
    elif self.event.startswith("j:"):
      self.response = "yes"
    else:
      raise RuntimeError()

class ComprehensionTrial(YesNoQuestionTrial):
  def __init__(self, item, condition, s, q):
    layout = [[VPush()],
              [Text(s)],
              [Text(q)],
              random.sample([Yes(bind_return_key=False), No(bind_return_key=False)], 2),
              [VPush()]]
    super().__init__(layout)
    self.item      = item
    self.condition = condition
    self.s         = s
    self.q         = q
    self.stimulus  = f'{self.s} : {self.q}'
    self.response  = None

class SubjectIDPage(Page):
  def __init__(self):
    layout = [[VPush()],
              [Text("Please enter participant ID:")],
              [Input("", key="-SUBJECTID-")],
              [VPush()]]
    super().__init__(layout, vertical_alignment="center")
  def handle_event(self, window):
    while True:
      self.event, self.values = window.read()
      if self.event == WIN_CLOSED:
        raise ExperimentAbortException()
      if self.event.startswith("Return:"):
        break
    self.deactivate()
    self.response = self.values["-SUBJECTID-"]

def run_experiment(pages):
  global window, session_id, exp_starttime
  session_id = datetime.today().strftime('%Y%m%d_%H%M%S')
  # Create data subdirectory if necessary:
  if not os.path.exists("data"):
      os.makedirs("data")
  try:
    # Set up window:
    layout = [[p.column for p in pages if isinstance(p, Page)]]
    wrapper_layout = [[ProgressBar(len(layout[0])-1, orientation='h', expand_x=True, size=(20, 20), key='-PBAR-')],
                      [Column(layout, expand_x=True, expand_y=True)]]
    window = Window('Experiment', wrapper_layout, keep_on_top=False, resizable=True, font=f"{font} {fontsize}", return_keyboard_events=True).Finalize()
    window.Maximize()
    window.TKroot["cursor"] = "none"
    # Run experiment:
    exp_starttime = time.time()
    i = 0
    for p in pages:
      p.activate(window, i)
      if isinstance(p, Page):
        window['-PBAR-'].update(current_count=i+1)
        i += 1
    window.close()
  except Exception as e:
    if type(e) == ExperimentAbortException:
      print("Experiment aborted.")
    else:
      print("An error occurred.")

    # Save data (emergency):
    filename = f"{session_id}_log.tsv"
    with open("data/" + filename, "w") as f:
      f.write('\t'.join(["pno", "type", "starttime", "endtime", "item", "condition", "stimulus", "response", "screenshot", "metadata1", "metadata2"]))
      f.write('\n')
      for p in pages:
        if not p.completed:
          break
        t = tuple(str(v) if v!=None else '' for v in p.get_data())
        if t[0]=="Next":
          continue
        f.write("\t".join(t))
        f.write("\n")
    print("Session log stored in: data/" + filename)
    # Raise exception again to complete.
    if type(e) != ExperimentAbortException:
      raise e
    else:
      sys.exit(1)

  # Save data (normal):
  filename = f"{session_id}_log.tsv"
  with open("data/" + filename, "w") as f:
    f.write('\t'.join(["pno", "type", "starttime", "endtime", "item", "condition", "stimulus", "response", "screenshot", "metadata1", "metadata2"]))
    f.write('\n')
    for t in [p.get_data() for p in pages]:
      t = tuple(str(v) if v!=None else '' for v in t)
      if t[0]=="Next":
        continue
      f.write("\t".join(t))
      f.write("\n")

  # If a Latin square was used, update our on-disk memory of completed
  # lists:
  if latin_square_list_label:
    with open('tested_latin_square_lists.txt', 'a') as file:
      file.write(f'{latin_square_list_label}\n')

  print(f"Experiment finished.\nSession log stored in: data/{filename}")

def check_latin_square(target_sentences):
  # Checks that all items have the same number of sentence:
  if len(set(Counter([x[0] for x in target_sentences]).values())) > 1:
    raise RuntimeError("All items need to have the same number of sentences.")
  # Checks that all items have the same conditions:
  d = dict()
  for k,v in [(x[0], x[1]) for x in target_sentences]:
    d.setdefault(k, []).append(v)
  items = list(d.keys())
  if len(set([tuple(l) for l in d.values()])) != 1:
    raise RuntimeError("Latin square design looks unbalanced.")
  # Checks that each condition is present only once: (Yes, it's enough
  # to check one item, because we've already established that all items
  # have the same number of sentences and the same conditions.)
  item, conditions = d.popitem()
  if len(set(conditions)) < len(conditions):
    raise RuntimeError("At least one condition appears multiple times per item.")
  return items, conditions

def latin_square_lists(target_sentences):
  items, conditions = check_latin_square(target_sentences)
  target_sentences.sort(key=lambda s:s[1])
  target_sentences.sort(key=lambda s:s[0])
  d = {}
  for s in target_sentences:
    d.setdefault(s[0], []).append(s)
  lists = [list() for _ in conditions]
  offset = 0
  for i in d.keys():
    for j,l in enumerate(lists):
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
    with open(filename, 'r') as file:
      previous_lists = [line.strip() for line in file if line.strip()]
    c = Counter(previous_lists)
    x = [(cond, c[cond]) for cond in conditions]
    x.sort(key=lambda s:s[1])
    latin_square_list_label = x[0][0]
  return latin_square_list_label

def next_latin_square_list(target_sentences):
  next_list_label = next_latin_square_list_label(target_sentences)
  print(f'Next Latin square list: {next_list_label}')
  return latin_square_lists(target_sentences)[next_list_label]

def load_stimuli(filename):
  stimuli = []
  with open(filename, 'r') as file:
    tsv_reader = csv.reader(file, delimiter='\t')
    for row in tsv_reader:
      assert len(row)==4
      assert len(row[1])>0
      assert len(row[2])>0
      assert len(row[3])>0
      row = (int(row[0]), row[1], row[2], row[3])
      stimuli.append(row)
  return stimuli

