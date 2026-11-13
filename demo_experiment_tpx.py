# /// script
# requires-python = ">=3.12"
# dependencies = ["pyglet>=2.0", "numpy", "pypixxlib"]
#
# [tool.uv.sources]
# pypixxlib = { path = "vendor/pypixxlib-1.12.1.tar.gz" }
# ///
"""A reading experiment with the VPixx TRACKPixx3/DATAPixx3: one
sentence per trial, on a single line; stimuli and fillers in separate
files. Calibrate in LabMaestro first, then run

    uv run demo_experiment_tpx.py

(once per machine before that: python3 setup_once.py). Without a
DATAPixx3 the experiment runs in dry-run mode, without gaze.

Reading trials end when the participant looks at the lower right
corner (space is the fallback, and the only way without a tracker). f /
j answer a question with no / yes, r on an instruction screen starts a
recalibration, Escape aborts a reading trial, Ctrl+Q the experiment.
The log, the screenshots and the eye-movement data land in data/.
"""

import random

import chamois
from TPx import TPx

# The eye tracker. All settings have defaults (listed at the top of
# TPx.py); to change one, name it here, e.g. TPx(gaze_display='both').
tracker = TPx()

# Font, sizes, colours and the texts the participant sees (e.g. for a
# translation) have defaults too, listed at the top of chamois.py;
# change them the same way: chamois.settings(fontsize=0.035, ...).

# Stimuli: targets in stimuli.tsv, fillers in fillers.tsv.
# Tab-separated, no header: item number, condition, sentence, question.

practice_sentences = [
    [101, "practice", "This is a practice sentence.", "Was this sentence difficult?"],
    [102, "practice", "This is another practice sentence.", "Was this sentence in German?"],
]

target_sentences = chamois.load_stimuli("stimuli.tsv")
fillers = chamois.load_stimuli("fillers.tsv")

# Mix and shuffle the stimuli. Each participant sees every item in one
# condition (Latin square); the next participant gets the next list.

stimuli = chamois.next_latin_square_list(target_sentences)
stimuli += fillers
random.shuffle(stimuli)

# An experiment is a series of pages:

pages = []

pages.append(chamois.SubjectIDPage())
pages.append(chamois.CenteredInstructions("Welcome to this study!"))

pages.append(chamois.CenteredInstructions("First some practice sentences!"))
for i, c, s, q in practice_sentences:
    pages.append(tracker.ReadingTrial(i, c, s, cross_position='left'))
    pages.append(chamois.YesNoQuestionTrial(i, c, q))
    pages.append(chamois.Next())

# In the experiment proper, half of the trials are followed by their
# question (percent=50):
pages.append(chamois.CenteredInstructions("Now, on to the real experiment!"))
for i, c, s, q in stimuli:
    pages.append(tracker.ReadingTrial(i, c, s, cross_position='left'))
    pages.append(chamois.YesNoQuestionTrial(i, c, q, percent=50))
    pages.append(chamois.Next())

pages.append(chamois.CenteredInstructions("Thank you for your participation!"))

chamois.run_experiment(pages, tracker)
