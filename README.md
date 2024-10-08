
Chamois is a Python application that aims to make state-of-the-art eye-tracking experiments on reading behavior as easy as possible.  The focus is on stimulus presentation and recording of user responses.  Chamois doesn’t support any particular eye-tracker but is designed to be used with any eye-tracker that has a Python API.  A demo experiment for TRACKPixx3 trackers is linked below.  Using PyGaze most other eye-trackers could be used as well.

**Status:** Chamois is work in progress.  I wrote it for my own lab and share it without any warranty.  It seems to work pretty well, but definitely has some rough edges.  If you’re interested in using it, feel free to contact me to discuss it.

**Key features:**
- Chamois is inspired by [Ibex](https://github.com/addrummond/ibex): Configuration and data format are similar but simpler.
- Implements most of the usual best-practices, so that users don’t have to reinvent the wheel.  E.g:
  - Blinking fixation target at the beginning of a trial.
  - Trial ends when participant looks at lower-right corner of screen.
- Sanity checking of stimuli and support for Latin square designs.  Chamois automatically picks the most underrepresented list.
- Chamois is eye-tracker agnostic and can be combined with almost any eye-tracking system via [PyGaze](https://www.pygaze.org/) or manufacturer APIs such as pylink and pypixx.
- Runs on Linux, MacOS, and Windows.  Only dependency is [FreeSimpleGui](https://github.com/spyoungtech/FreeSimpleGUI).
- With only a small amount of code, Chamois is relatively easy to hack and extend even for users with only limited Python knowledge.

**A non-feature:** Chamois was not designed for timing-sensitive experiments, e.g. those using the boundary paradigm.  Eye-movement contingent display-changes could be implemented but may be too slow to be executed during saccades.

# Demo experiment

Below is the code for a very simple experiment with 3 items, 2 conditions, 2 fillers, but no practice sentences.

Two slightly more elaborate examples:
- [demo_experiment.py](https://github.com/tmalsburg/chamois/blob/main/demo_experiment.py): As below but with practice sentences.
- [demo_experiment_tpx.py](https://github.com/tmalsburg/chamois/blob/main/demo_experiment_tpx.py): As below but with practice sentences and recording of eye movement using a [TRACKPixx3](https://vpixx.com/products/trackpixx/) eye-tracker.  Note how the code for this experiment differs only minimally from the code for an experiment without eye-tracker.

## Stimuli

Stimuli can be loaded from `.tsv` files (via `load_stimuli(…)`) or be defined directly in Python.

``` python
target_sentences = [
  [1, "a", "While Bill hunted the deer was hunted by Bill.", "Did Bill hunt the deer?"],
  [1, "b", "While Bill hunted the deer that was brown and nimble was hunted by Bill.", "Did Bill hunt the deer?"],
  [2, "a", "While Mary bathed the baby bathed Mary.", "Did Mary bath the baby?"],
  [2, "b", "While Mary bathed the baby that was small and cute bathed Mary.", "Did Mary bath the baby?"],
  [3, "a", "Anna scolded the chef of the aristocrats who was routinely letting food go to waste.", "Did food go to waste?"],
  [3, "b", "Anna studied with the chef of the aristocrats who was routinely letting food go to waste.", "Did food go to waste?"],
]

fillers = [
  [20, "filler", "Colorless green ideas sleep furiously.", "Does this sentence make any sense at all?"],
  [21, "filler", "No head injury is too trivial to be ignored.", "Did that sentence make your brain hurt?"],
]

# Select most underrepresented Latin square list:
stimuli = next_latin_square_list(target_sentences)

# Mix with fillers and shuffle :
stimuli += fillers
random.shuffle(stimuli)
```

## Experiment structure

An experiment consists of a series of “pages” that are displayed one by one.  Each page produces one line in the results file.  Various types of pages are predefined (reading trials, acceptability judgment trials), but it’s really easy to add new types of pages.

``` python
# Load Chamois:
exec(open("chamois.py").read())

# Set the visual theme:
theme('Black')
font = "Courier"
fontsize = 22
wordspacing = 18

# Structure of experiment:
pages = []

# Welcome screen:
pages.append(
  CenteredInstructions("Welcome to this study!"))

# Asks user to enter subject ID:
pages.append(SubjectIDPage())

# Experimental trials with comprehension question after 50% of the
# sentences:
for item, condition, sentence, question in stimuli:
  pages.append(ReadingTrial(item, condition, sentence))
  if random.choice([True, False]):
    pages.append(YesNoQuestionTrial(item, condition, question))

# Thank-you screen:
pages.append(
  CenteredInstructions("Thank you for your participation!"))

# Run experiment:
run_experiment(pages)
```

## Screenshots

![Screenshot 1](images/screenshot_1.png)

![Screenshot 2](images/screenshot_2.png)

![Screenshot 3](images/screenshot_3.png)

![Screenshot 4](images/screenshot_4.png)

![Screenshot 5](images/screenshot_5.png)

![Screenshot 6](images/screenshot_6.png)

## Session log

The log of the session comes in tab-separated values format (`.tsv`) and, among other things, includes the AOIs of the individual words.  Screenshots of the stimuli are stored on disk.  All filenames belonging to a session start with the session ID which is composed of the date and time at which the experiment was started, e.g. `20240912_082813`.

Results in table format:

| pno | type                 | starttime | endtime | item | condition | stimulus                                                                                  | response  | screenshot                                     | metadata1                                                       | metadata2 |
|-----| ---------------------|-----------|---------|------|-----------|-------------------------------------------------------------------------------------------|-----------|------------------------------------------------|-----------------------------------------------------------------|-----------|
|   0 | CenteredInstructions | 0.000     | 14.055  |      |           | Welcome to this study!                                                                    |           |                                                |                                                                 |           |
|   1 | SubjectIDPage        | 14.070    | 18.509  |      |           |                                                                                           | subject01 |                                                |                                                                 |           |
|   2 | ReadingTrial         | 40.271    | 43.455  | 1    | b         | While Bill hunted the deer that was brown and nimble was hunted by Bill.                  |           | `20240912_082813_ReadingTrial_001_b.png`       | 77,704,221,761;239,704,355,761;373,704,545,761;563,704,651,761; |           |
|   3 | YesNoQuestionTrial   | 43.470    | 48.545  | 1    | b         | Did Bill hunt the deer?                                                                   | no        | `20240912_082813_YesNoQuestionTrial_001_b.png` |                                                                 |           |
|   4 | ReadingTrial         | 48.560    | 52.726  | 21   | filler    | No head injury is too trivial to be ignored.                                              |           | `20240912_082813_ReadingTrial_021_filler.png`  | 77,704,137,761;155,704,271,761;289,704,461,761;479,704,539,761; |           |
|   5 | ReadingTrial         | 53.390    | 55.383  | 3    | b         | Anna studied with the chef of the aristocrats who was routinely letting food go to waste. |           | `20240912_082813_ReadingTrial_003_b.png`       | 77,704,193,761;211,704,411,761;429,704,545,761;563,704,651,761; |           |
|   6 | YesNoQuestionTrial   | 55.396    | 56.436  | 3    | b         | Did food go to waste?                                                                     | no        | `20240912_082813_YesNoQuestionTrial_003_b.png` |                                                                 |           |
|   7 | ReadingTrial         | 56.451    | 58.252  | 20   | filler    | Colorless green ideas sleep furiously.                                                    |           | `20240912_082813_ReadingTrial_020_filler.png`  | 77,704,333,761;351,704,495,761;513,704,657,761;675,704,819,761; |           |
|   8 | ReadingTrial         | 59.073    | 60.640  | 2    | a         | While Mary bathed the baby bathed Mary.                                                   |           | `20240912_082813_ReadingTrial_002_a.png`       | 77,704,221,761;239,704,355,761;373,704,545,761;563,704,651,761; |           |
|   9 | CenteredInstructions | 60.655    | 66.013  |      |           | Thank you for your participation!                                                         |           |                                                |                                                                 |           |

Note that each column contains only one type of data.  This makes it easy to work with this format: just `read.csv` the results file in R and you’re ready to go.

Columns:

1. `pno`: The number of the page in the sequence of all pages.
2. `type`: The type of page that was displayed (or “Message” which appears only in the results file, not on screen during the experiment).
3. `starttime`: The time at which the page was displayed, in seconds with precision down to milliseconds.  The clock starts at the beginning of the experiment (`0.000`).
4. `endtime`: The time at which the page was left.
5. `item`: The item number of the displayed stimulus (if any).
6. `condition`: The condition of the displayed stimulus (if any).
7. `stimulus`: The displayed stimulus (if any).  Could also be the instructions that were displayed (potentially abbreviated).
8. `response`: The response (if any).
9. `screenshot`: Filename of screenshot of the page if a stimulus was displayed.
10. `metadata1`: Meta data depending on page type.  For ReadingTrials, this column contains the screen coordinates of the AOIs.
11. `metadata2`: More meta data depending on page type.  For TPxReadingTrials, this column contains the file name of the recorded eye-tracking data.

The data format of the eye-tracking data depends on the eye-tracker and the user will have to take care of combining Chamois data from the session log (above) with the eye-tracking data.  Initial support for TRACKPixx3 eye-trackers is included in this repository (see [demo_experiment_tpx.py](https://github.com/tmalsburg/chamois/blob/main/demo_experiment_tpx.py)).

## Details on individual page types

### `ReadingTrial` and `TPxReadingTrials`
There are 4 ways to end a TPxReadingTrial (with eye-tracking):
1. Looking at the lower right corner of the screen is the normal way to end a TPxReadingTrial.  The trigger region is a circle around the corner that has a radius of 200 pixels by default.  The radius can be adjusted using the `trigger_radius` parameter of the constructor of TPxReadingTrial, e.g: `TPxReadingTrial(item, condition, sentence, tpx, trigger_radius=250)`
2. Alternatively the trial can be ended by pressing space bar.  This can be a plan B when the calibration is so poor that looking at the corner doesn’t work.
3. Pressing escape aborts the trial.  In this case, the response field in the session log will show `ABORTED`.  This is useful in cases such as when the participant was distracted.  Note that a comprehension question for this trial may still be shown.  In this case, the participant can give a random response.
4. It’s always an option to just close the window which aborts the whole experiment and stores the session log so far.

`ReadingTrial` (no eye-tracking) supports options 2–4 in the above list.

### `Next` and `TPxNext`

These page types can be used between trials to give participants a chance to take a break and, in the case of `TPxNext`, to give the experimenter a chance to recalibrate the eye-tracker (press `r` key).

