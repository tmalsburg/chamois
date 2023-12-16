
from PySimpleGUI import *
theme('Default1')

import time, random, re, math, uuid
from collections import Counter

class Page:
  def __init__(self, layout, **kwargs):
    self.layout     = layout
    self.column     = Column(layout, visible=False, expand_x=True, expand_y=True, **kwargs)
    self.event      = None
    self.values     = None
    self.started    = False
    self.completed  = False
    self.type       = type(self).__name__
    self.starttime  = None
    self.endtime    = None
    self.item       = None
    self.condition  = None
    self.stimulus   = None
    self.response   = None
    self.screenshot = None
    self.metadata   = None
  # Activate the page as defined at creation.
  def activate(self, window):
    self.column.update(visible=True)
    self.started = True
    self.starttime = time.time()
    self.stage_setting(window)
    self.handle_event(window)
  # Optional stage-setting that can only be performed once the page is
  # displayed:
  def stage_setting(self, window):
    window.refresh()
  def deactivate(self):
    if not self.started:
      raise RuntimeError()
    self.endtime = time.time()
    self.column.update(visible=False)
    self.completed = True
  def handle_event(self, window):
    if not self.started:
      raise RuntimeError()
    self.event, self.values = window.read()
    self.deactivate()
  def get_data(self):
    if not self.completed:
      raise RuntimeError()
    return (self.type, self.starttime, self.endtime, self.item, self.condition, self.stimulus, self.response, self.screenshot, self.metadata)

# Message shares an interface with Page but is not itself a page since
# it is not part of the GUI.
class Message:
  def __init__(self, message):
    self.type      = type(self).__name__
    self.metadata  = message
    self.starttime = None
  def activate(self, _):
    self.starttime = time.time()
  def get_data(self):
    return (self.type, self.starttime, None, None, None, None, None, None, self.metadata)

# Separate class to make a page show up in the results as
# "Instructions".
class Instructions(Page):
  pass

class CenteredInstructions(Instructions): 
  def __init__(self, layout, **kwargs):
    layout = list(layout)
    layout.insert(0, [VPush()])
    layout.append([VPush()])
    super().__init__(layout, element_justification="center")

# Takes a screenshot before handling an event:
class ExperimentalTrial(Page):
  def handle_event(self, window):
    self.screenshot = f'/tmp/{self.item}_{self.condition}_{self.type}.png'
    window.save_window_screenshot_to_disk(self.screenshot)
    super().handle_event(window)
  
class ComprehensionTrial(ExperimentalTrial):
  def __init__(self, item, condition, s, q):
    layout = [[VPush()],
              [Text(s)],
              [Text(q)],
              random.sample([Yes(), No()], 2),
              [VPush()]]
    super().__init__(layout)
    self.item      = item
    self.condition = condition
    self.s         = s
    self.q         = q
    self.stimulus = f'{self.s} : {self.q}'
    self.response  = None
  def handle_event(self, window):
    super().handle_event(window)
    self.response = self.event

# TODO Specify size in visual field degrees (adapts to screen size,
# distance, ...).
class FixationCross(Graph):
  def __init__(self, width=38, height=38, radius1=17, radius2=4, blinks=3):
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

class ReadingTrial(ExperimentalTrial):
  def __init__(self, item, condition, s):
    self.fixation_cross = fc = FixationCross()
    self.words = [Text(w, visible=False) for w in s.split()]
    layout = [[VPush()],
              [fc] + self.words,
              [VPush()],
              [Push(), Button("Next")]]
    super().__init__(layout, vertical_alignment="center")
    self.item      = item
    self.condition = condition
    self.stimulus  = s
  def stage_setting(self, window):
    t = 450
    while t > 40:
      self.fixation_cross.draw()
      window.read(timeout=30+t)
      self.fixation_cross.erase()
      window.read(timeout=30+t/2)
      t *= 0.65
    for w in self.words:
      w.update(visible=True)
    window.refresh()
    # Calculate AOIs:
    self.aois = []
    for w in self.words:
      x, y = w.widget.winfo_rootx(), w.widget.winfo_rooty()
      w, h = w.get_size()
      self.aois.append(f'{x},{y},{x+w},{y+h}')
    self.metadata = ";".join(self.aois)

class YesNoQuestionTrial(ExperimentalTrial):
  def __init__(self, item, condition, q):
    layout = [[VPush()],
              [Text(q)],
              random.sample([Yes(), No()], 2),
              [VPush()]]
    super().__init__(layout, element_justification="center")
    self.item      = item
    self.condition = condition
    self.stimulus  = q
    self.response  = None
  def handle_event(self, window):
    super().handle_event(window)
    self.response = re.sub(r'\d+$', '', self.event)

class SubjectIDPage(Page):
  def __init__(self):
    layout = [[VPush()],
              [Text("Please enter participant ID:")],
              [Input("", key="-SUBJECTID-")],
              [Button("Next")],
              [VPush()]]
    super().__init__(layout, vertical_alignment="center")
  def handle_event(self, window):
    super().handle_event(window)
    self.response = self.values["-SUBJECTID-"]

def run_experiment(pages):
  # Set up window:
  layout = [[p.column for p in pages if isinstance(p, Page)]]
  wrapper_layout = [[ProgressBar(len(layout[0])-1, orientation='h', expand_x=True, size=(20, 20), key='-PBAR-')],
                    [Column(layout, expand_x=True, expand_y=True)]]
  window = Window('Experiment', wrapper_layout, keep_on_top=True, resizable=True, font="Courier 17").Finalize()
  window.Maximize()
  # Run experiment:
  i = 0
  for p in pages:
    p.activate(window)
    if isinstance(p, Page):
      window['-PBAR-'].update(current_count=i+1)
      i += 1
  window.close()
  # Save data:
  filename = f"/tmp/results_{uuid.uuid4()}.tsv"
  with open(filename, "w") as f:
    f.write('\t'.join(["type", "starttime", "endtime", "item", "condition", "stimulus", "response", "screenshot", "metadata\n"]))
    for t in [p.get_data() for p in pages]:
      t = tuple(str(v) if v!=None else '' for v in t)
      print(','.join(t))
      f.write("\t".join(t))
      f.write("\n")

def check_latin_square(stimuli):
  # Checks that all items have the same number of sentence:
  if len(set(Counter([x[0] for x in stimuli]).values())) > 1:
    RuntimeError("All items need to have the same number of sentences.")
  # Checks that all items have the same conditions:
  d = dict()
  for k,v in [(x[0], x[1]) for x in stimuli]:
    d.setdefault(k, []).append(v)
  items = list(d.keys())
  if len(set([tuple(l) for l in d.values()])) != 1:
    RuntimeError("Latin square design looks unbalanced.")
  # Checks that each condition is present only once:
  item, conditions = d.popitem()
  if len(set(conditions)) < len(conditions):
    RuntimeError("At least one condition appears multiple times per item.")
  return items, conditions

def latin_square_lists(stimuli):
  items, conditions = check_latin_square(stimuli)
  stimuli.sort(key=lambda s:s[1])
  stimuli.sort(key=lambda s:s[0])
  d = {}
  for s in stimuli:
    d.setdefault(s[0], []).append(s)
  lists = [list() for _ in conditions]
  offset = 0
  for i in d.keys():
    for j,l in enumerate(lists):
      l.append(d[i][(j + offset) % len(conditions)])
    offset += 1
  return lists

