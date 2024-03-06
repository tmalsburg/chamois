#!/usr/bin/env python3

# Load Chamois:
exec(open("chamois.py").read())

# Change theme:
theme('Black')
# See here for all available themes:
# https://media.geeksforgeeks.org/wp-content/uploads/20200511200254/f19.jpg

# Stimuli:

practice_sentence = [
  [101, "practice", "This is a practice sentence.", "Was this sentence difficult?"],
  [102, "practice", "This is another practice sentence.", "Was this sentence in German?"]
]

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

# Mix and shuffle stimuli:

stimuli = next_latin_square_list(target_sentences)
stimuli += fillers
random.shuffle(stimuli)

# Structure of the experiment:

session_id = uuid.uuid4()

# An experiment consists of a series of pages:
pages = []

# A message stored in the results file along with a time stamp:
pages.append(Message("Start of session"))

# Welcome screen:
pages.append(
  CenteredInstructions("Welcome to this study!"))

# Asks user to enter subject ID:
pages.append(SubjectIDPage())

# Practice sentences:
pages.append(
  CenteredInstructions("First some practice sentences!"))

for i,c,s,q in practice_sentence:
  pages.append(ReadingTrial(i,c,s))
  pages.append(YesNoQuestionTrial(i,c,q))

# Experimental trials:
pages.append(
  CenteredInstructions("Now, on to the real experiment!"))

for i,c,s,q in stimuli:
  pages.append(ReadingTrial(i,c,s))
  if random.choice([True, False]):
    pages.append(YesNoQuestionTrial(i,c,q))

# Thank you screen:
pages.append(
  CenteredInstructions("Thank you for your participation!"))

# Another message with a time stamp:
pages.append(Message("End of session"))

# Run experiment:
run_experiment(pages, session_id)

