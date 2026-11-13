# /// script
# requires-python = ">=3.12"
# dependencies = ["pyglet>=2.0", "numpy", "sr-research-pylink"]
# ///
"""A reading experiment with the SR Research EyeLink: like
demo_experiment_eyelink.py, but with longer texts that run over several
lines. Run it with

    uv run demo_experiment_eyelink_multiline.py

The session starts with the calibration, which is run from the Host PC
(Escape there continues with the experiment). Without a reachable Host
PC the experiment runs in dummy mode, without gaze.

Reading trials end when the participant looks at the lower right
corner (space is the fallback, and the only way without a tracker). f /
j answer a question with no / yes, r on an instruction screen starts a
recalibration, Escape aborts a reading trial, Ctrl+Q the experiment.
The log, the screenshots and the eye-movement data land in data/.
"""

import random

import chamois
from EyeLink import EyeLink

# The eye tracker. All settings have defaults (listed at the top of
# EyeLink.py); to change some, name them here, e.g.
# EyeLink(host_ip="102.1.1.1", drift_check=True).
tracker = EyeLink()

# Font, sizes, colours and the texts the participant sees (e.g. for a
# translation) have defaults too, listed at the top of chamois.py;
# change them the same way: chamois.settings(fontsize=0.035, ...).

# Stimuli: targets in stimuli_multiline.tsv, fillers in
# fillers_multiline.tsv.
# Tab-separated, no header: item number, condition, sentence, question.

practice_sentences = [
    [101, "practice",
     "The weather was wonderful over the weekend. The sun shone without a "
     "break and it was pleasantly warm. Thomas seized the chance and went "
     "for a walk in the park. Along the way he met an old schoolfriend he "
     "had not seen for many years.",
     "Did Thomas meet an old friend in the park?"],
    [102, "practice",
     "Julia loves to cook in her free time. Last night she tried out a new "
     "recipe from Italy and spent two hours in the kitchen. Unfortunately "
     "it turned out in the end that she had used far too much salt.",
     "Was Julia happy with her finished meal?"],
]

target_sentences = chamois.load_stimuli("stimuli_multiline.tsv")
fillers = chamois.load_stimuli("fillers_multiline.tsv")

# Mix and shuffle the stimuli. Each participant sees every item in one
# condition (Latin square); the next participant gets the next list.

stimuli = chamois.next_latin_square_list(target_sentences)
stimuli += fillers
random.shuffle(stimuli)

# An experiment is a series of pages:

pages = []

pages.append(chamois.SubjectIDPage())
pages.append(tracker.CalibrationPage())
pages.append(chamois.CenteredInstructions("Welcome to this study!"))

# Line breaks are set per reading trial with wrap: the maximum number
# of characters per line (the default, None, means no automatic
# breaks). A text that contains "\n" (typed like that in the stimulus
# file) is always shown with exactly those breaks, whatever wrap says:
# the target items here. The fillers and the practice texts have none,
# so they are broken automatically.

pages.append(chamois.CenteredInstructions("First some practice sentences!"))
for i, c, s, q in practice_sentences:
    pages.append(tracker.ReadingTrial(i, c, s, wrap=70,
                                      cross_position='left'))
    pages.append(chamois.YesNoQuestionTrial(i, c, q))
    pages.append(chamois.Next())

# In the experiment proper, half of the trials are followed by their
# question (percent=50):
pages.append(chamois.CenteredInstructions("Now, on to the real experiment!"))
for i, c, s, q in stimuli:
    pages.append(tracker.ReadingTrial(i, c, s, wrap=70,
                                      cross_position='left'))
    pages.append(chamois.YesNoQuestionTrial(i, c, q, percent=50))
    pages.append(chamois.Next())

pages.append(chamois.CenteredInstructions("Thank you for your participation!"))

chamois.run_experiment(pages, tracker)
