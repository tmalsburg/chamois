
Chamois is a Python app that aims to make eye-tracking experiments on reading behaviour as painless as possible.

Current state: It largely works, but is in very early stages of developement.  I advise against using it for serious work.

Key features:
- Chamois is inspired by Ibex: Configuration and data format are similar but much simpler.
- Support for Latin square designs.  Just drop your sentences and start collecting data.
- Can be combined with most eye-trackers via PyGaze and manufacturer APIs such as pylink and pypixx.  But that’s the user’s job.
- Runs on Linux, MacOS, Windows.  Only dependency is [PySimpleGui](https://www.pysimplegui.org).
- With only 300 lines of code Chamois quite hackable.
- I wrote Chamois for my own lab and share without any warranty.  Use at your own risk.

## Sample experiment:

Stimuli:

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

# Mix and shuffle :
stimuli = random.choice(latin_square_lists(target_sentences))
stimuli += fillers
random.shuffle(stimuli)
```

Storyboard:

``` python
pages = []
pages.append(Message("Start of session"))

pages.append(
  CenteredInstructions([[Text("Welcome to this study!", pad=50)], [Button("Continue")]]))

pages.append(SubjectIDPage())

for i,c,s,q in stimuli[0:3]:
  pages.append(ReadingTrial(i,c,s))
  if random.choice([True, False]):
    pages.append(YesNoQuestionTrial(i,c,q))

pages.append(
  CenteredInstructions([[Text("Thank you for your participation!", pad=50)], [Exit()]]))

pages.append(Message("End of session"))
```

## Sample output

Output comes in tab-separated values format (`.tsv`) and includes the AOIs of the individual words.  Screenshots of the stimulus screens are separately stored on disk.

``` 
type,starttime,endtime,item,condition,stimulus,response,screenshot,metadata
Message	1702732592.0449314							Start of session
CenteredInstructions	1702732592.0449767	1702732592.8204858						
SubjectIDPage	1702732592.8210366	1702732595.677696				001		
ReadingTrial	1702732595.6792064	1702732598.4286556	21	filler	No head injury is too trivial to be ignored.		/tmp/21_filler_ReadingTrial.png	75	680	121	725;131	680	219	725;229	680	359	725;369	680	415	725;425	680	492	725;502	680	653	725;663	680	709	725;719	680	765	725;775	680	947	725
ReadingTrial	1702732598.4291706	1702732600.9246044	1	b	While Bill hunted the deer that was brown and nimble was hunted by Bill.		/tmp/1_b_ReadingTrial.png	75	680	184	725;194	680	282	725;292	680	422	725;432	680	499	725;509	680	597	725;607	680	695	725;705	680	772	725;782	680	891	725;901	680	968	725;978	680	1108	725;1118	680	1185	725;1195	680	1325	725;1335	680	1381	725;1391	680	1500	725
YesNoQuestionTrial	1702732600.9249854	1702732602.166412	1	b	Did Bill hunt the deer?	Yes	/tmp/1_b_YesNoQuestionTrial.png	
ReadingTrial	1702732602.167954	1702732604.7166016	20	filler	Colorless green ideas sleep furiously.		/tmp/20_filler_ReadingTrial.png	75	680	268	725;278	680	387	725;397	680	506	725;516	680	625	725;635	680	849	725
YesNoQuestionTrial	1702732604.716971	1702732606.0231833	20	filler	Does this sentence make any sense at all?	Yes	/tmp/20_filler_YesNoQuestionTrial.png	
CenteredInstructions	1702732606.024852	1702732607.0927289						
Message	1702732607.0930743							End of session
``` 

Note that each column contains only one type of data.  This makes it easy to work with this format: just `read.csv` the file in R and you’re ready to go.

The data format of the eye-tracking data depends on the eye-tracker and the user will have to take care of combining Chamois and eye-tracking data.
