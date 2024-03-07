

from pypixxlib import _libdpx as dp
import pandas as pd

class TPx:
  def activate(self):
    dp.DPxOpen()
    dp.DPxSetTPxAwake()
  def start_recording(self):
    self.TPxSetupSchedule = dp.TPxSetupSchedule()
    dp.TPxStartSchedule()
    dp.DPxUpdateRegCache()
  def stop_recording(self):
    dp.TPxStopSchedule()
    dp.DPxUpdateRegCache()
    dp.TPxGetStatus(self.TPxSetupSchedule)
  def retrieve_data(self):
    data = dp.TPxReadData(self.TPxSetupSchedule, self.TPxSetupSchedule['newBufferFrames'])
    columns = ['TimeTag', 'LeftEyeX', 'LeftEyeY',
               'LeftPupilDiameter', 'RightEyeX', 'RightEyeY',
               'RightPupilDiameter', 'DigitalIn', 'LeftBlink', 'RightBlink',
               'DigitalOut', 'LeftEyeFixationFlag', 'RightEyeFixationFlag',
               'LeftEyeSaccadeFlag', 'RightEyeSaccadeFlag', 'MessageCode',
               'LeftEyeRawX', 'LeftEyeRawY', 'RightEyeRawX', 'RightEyeRawY']
    return pd.DataFrame(data, columns=columns)
  def shut_down(self):
    dp.DPxSetTPxSleep()
    dp.DPxUpdateRegCache()
    dp.DPxClose()

# Chamois ReadingTrial but with TRACKPixx3 recording:

class TPxReadingTrial(ReadingTrial):
  def __init__(self, item, condition, s, tpx):
    super().__init__(item, condition, s)
    self.tpx = tpx
  def prelude(self, window):
    super().prelude(window)
    self.tpx.start_recording()
  def deactivate(self):
    global session_id
    self.tpx.stop_recording()
    super().deactivate()
    data_frame = self.tpx.retrieve_data()
    filename = "%s_%s_%03d_%s.tsv" % (session_id, self.type, self.item, self.condition)
    data_frame.to_csv(filename, index=False)
    self.metadata2 = filename

