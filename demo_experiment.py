# /// script
# requires-python = ">=3.12"
# dependencies = ["pyglet>=2.0"]
# ///
"""Hello world: the smallest reading experiment, without eye-tracking
and with the sentences right here in the script. Run it with

    uv run demo_experiment.py

Space continues and ends a reading trial, f / j answer a question with
no / yes, Ctrl+Q aborts the experiment. The log, and a screenshot of
every trial, land in data/.

For a real experiment (practice trials, conditions, fillers, stimuli
in files, an eye tracker) see demo_experiment_tpx.py (VPixx
TRACKPixx3) and demo_experiment_eyelink.py (SR Research EyeLink).
"""

import chamois

# Settings. All of them have defaults (listed at the top of
# chamois.py); to change some, name them here. For example the font
# size (a fraction of the screen height) and the text below every
# instruction screen, e.g. to translate it:
chamois.settings(fontsize=0.03,
                 continue_text="Press the space bar to continue.",
                 subject_id_prompt="what is your participant ID?")

# The sentences: item number, condition, sentence, question. The
# first two come with a yes/no question, the last two with a question
# that is answered by typing.

yesno_sentences = [
    [1, "hello", "Hello world!", "Did the sentence say hello?"],
    [2, "hello", "This is a sentence.", "Was this a sentence?"],
]

typed_sentences = [
    [3, "hello", "The cat sat on the mat.", "Where did the cat sit?"],
    [4, "hello", "The dog chased the ball.", "What did the dog chase?"],
]

# An experiment is a series of pages:

pages = []

pages.append(chamois.SubjectIDPage())
pages.append(chamois.CenteredInstructions("Welcome to this study!"))

# Where the sentence appears:
#   align     'left', 'center' or 'right'
#   y_offset  height on the screen: 0.0 = middle, 0.3 = higher up,
#             -0.3 = lower down (1.0 is the full screen height)
# How often a question follows:
#   percent   50 = after half of the sentences (chosen at random for
#             every participant), 100 = after every sentence

# Yes/no questions, answered with the keys f (no) and j (yes):
for i, c, s, q in yesno_sentences:
    pages.append(chamois.ReadingTrial(i, c, s, align='center', y_offset=0.0))
    pages.append(chamois.YesNoQuestionTrial(i, c, q, percent=50))

# Typed answers: the participant types and confirms with Enter:
for i, c, s, q in typed_sentences:
    pages.append(chamois.ReadingTrial(i, c, s, align='center', y_offset=0.0))
    pages.append(chamois.ComprehensionTrial(i, c, q, percent=50))

pages.append(chamois.CenteredInstructions("Thank you for your participation!"))

chamois.run_experiment(pages)
