
# Load Chamois:
exec(open("chamois.py").read())

# Stimuli:

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

stimuli = random.choice(latin_square_lists(target_sentences))
stimuli += fillers
random.shuffle(stimuli)

# Structure of the experiment:

pages = []
pages.append(Message("Start of session"))
pages.append(
  CenteredInstructions([[Text("Welcome to this study!", pad=50)], [Button("Continue")]]))
pages.append(SubjectIDPage())
for i,c,s,q in stimuli:
  pages.append(ReadingTrial(i,c,s))
  if random.choice([True, False]):
    pages.append(YesNoQuestionTrial(i,c,q))
pages.append(
  CenteredInstructions([[Text("Thank you for your participation!", pad=50)], [Exit()]]))
pages.append(Message("End of session"))

# Run it:

run_experiment(pages)
