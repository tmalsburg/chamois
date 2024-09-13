

from pypixxlib import _libdpx as dp
import pandas as pd

class TPx:
  def calibrate(self, skipCameraSetup=False):
    while not TPxSimpleCalibration(skipCameraSetup):
      continue
  def start_recording(self):
    dp.DPxOpen()
    dp.DPxSetTPxAwake()
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
    dp.DPxSetTPxSleep()
    dp.DPxUpdateRegCache()
    dp.DPxClose()
    return pd.DataFrame(data, columns=columns)

# Chamois ReadingTrial but with TRACKPixx3 recording:

class TPxReadingTrial(ReadingTrial):
  def __init__(self, item, condition, s, tpx, trigger_radius=200):
    super().__init__(item, condition, s)
    self.tpx = tpx
    self.trigger_radius = trigger_radius
  def prelude(self, window):
    super().prelude(window)
    self.tpx.start_recording()
  def deactivate(self):
    self.tpx.stop_recording()
    super().deactivate()
    data_frame = self.tpx.retrieve_data()
    filename = "data/%s_%03d_%s_%03d_%s.csv" % (session_id, self.pno, self.type, self.item, self.condition)
    data_frame.to_csv(filename, index=False)
    self.metadata2 = filename
  def handle_event(self, window):
    # If we start sampling gaze too early there's no data yet:
    time.sleep(0.05)
    w, h = window.size
    while True:
      # Checking keyboard events:
      self.event, self.values = window.read(timeout=1)
      if self.event=="__TIMEOUT__":
        pass
      # Weird but sometimes event is None when the window is closed.
      # Even with the None check in pace we sometimes get a messy
      # abort and stack trace.
      elif not self.event or self.event == WIN_CLOSED:
        dp.DPxUpdateRegCache()
        self.tpx.stop_recording()
        raise ExperimentAbortException()
      elif self.event.startswith('space:'):
        break
      elif self.event.startswith('Escape:'):
        self.response = "ABORTED"
        print("  Page aborted.")
        break
      # Checking if participant is looking at corner of screen:
      dp.DPxUpdateRegCache()
      eyeData = dp.TPxGetEyePosition()
      lx, ly, rx, ry = eyeData[0:4]
      # TP3 uses center as origin:
      x = (lx+rx)/2 + w/2
      y = (ly+ry)/2 + h/2
      if math.sqrt((x-w)**2 + y**2) < self.trigger_radius:
        break
    dp.DPxUpdateRegCache()
    self.deactivate()

# Shares an interface with Page but is not itself a page since
# it is not itself part of the GUI.
class TPxCalibration():
  def __init__(self, tpx):
    self.pno       = None
    self.type      = type(self).__name__
    self.tpx       = tpx
    self.starttime = None
    self.completed = None
  def activate(self, _, pno):
    self.pno = pno
    self.starttime = round(time.time() - exp_starttime, 3)
    self.tpx.calibrate()
    self.completed = True
  def get_data(self):
    return (self.pno, self.type, self.starttime, None, None, None, None, None, None, None, None)

class TPxQuickCalibration(TPxCalibration):
  def activate(self, _, pno):
    self.pno = pno
    self.starttime = round(time.time() - exp_starttime, 3)
    self.tpx.calibrate(True)
    self.completed = True

class TPxNext(Instructions):
  def __init__(self, tpx):
    layout = [[VPush()],
              [Text("Press space bar to continue.")],
              [VPush()],
              [Text("[r] key for eye-tracker recalibration", font=f"{font} {int(fontsize*0.7)}", text_color="grey79")]]
    super().__init__(layout, element_justification="center")
    self.tpx = tpx
  def handle_event(self, window):
    while True:
      self.event, self.values = window.read()
      if self.event == WIN_CLOSED:
        raise ExperimentAbortException()
      if self.event.startswith('space:'):
        break
      if self.event.startswith('r:'):
        self.tpx.calibrate(True)
    self.deactivate()

#
# TPxSimpleCalibration.py
# 

from pypixxlib import _libdpx as dp
from psychopy import visual, iohub, core
import numpy as np
import PIL

def TPxSimpleCalibration(skipCameraSetup=False):

    calibrationSuccess = False # Return whether the camera is calibrated
    screenNumber = 0 # Screen number of monitor to be used for experiment. Minus one from the number assigned by the OS (e.g., 1 is actually screen 2).
    ledIntensity = 8 # Intensity of infrared illuminator
    approximateIrisSize = 140 # There is no rule of thumb for this value. Set this in PyPixx to be sure.

    # Set up the hardware
    dp.DPxOpen()
    dp.TPxHideOverlay()
    dp.TPxClearDeviceCalibration()
    dp.DPxSetTPxAwake()
    dp.TPxSetLEDIntensity(ledIntensity)
    dp.TPxSetIrisExpectedSize(approximateIrisSize)
    dp.DPxWriteRegCache()

    # Set up PsychoPy
    windowPtr = visual.Window(fullscr=True,color=-1,screen=screenNumber) # Open a black PsychoPy window
    windowPtr.setMouseVisible(False)  # FIXME: Doesn't hide cursor.
    windowRect = windowPtr.size # Get window dimensions
    io = iohub.launchHubServer() # Record key strokes with PsychoPy

    t = dp.DPxGetTime() # Time stamps
    t2 = dp.DPxGetTime()

    # Create text stimulus
    textStim = visual.TextStim(windowPtr,text='Instructions:\n\n 1- Focus the eyes.'+
                            '\n\n 2- Press Enter when ready to calibrate '+
                            'or Escape to exit.',anchorHoriz='center',anchorVert='bottom',units='pix')
    textStim.size = 24
    textStim.pos = (0,-windowRect[1]/2)

    ######################### SCREEN 1: Start screen ##########################
    if not skipCameraSetup:
      while True:
          if ((t2 - t) > 1/60): # Just refresh at 60 Hz
              # Get static image of eye from TPx. Draw to screen.
              dp.DPxUpdateRegCache()
              imageStim = visual.SimpleImageStim(windowPtr,image=PIL.Image.fromarray(dp.TPxGetEyeImage()),units='pix',pos=(0,0))
              drawCollection( [imageStim, textStim] ) # See below for definition
              windowPtr.flip()
              t = t2
          else:
              dp.DPxUpdateRegCache()
              t2 = dp.DPxGetTime() # Get most recent time stamp

          # Check for key presses
          events = io.devices.keyboard.state
          if events:
              keys = list(events.keys())
              if 'escape' in keys:
                  escape(windowPtr,io)
                  return False
              elif 'return' in keys:
                  break

    ###################### SCREEN 2: Calibration routine ######################
    cx = windowRect[0]/2 # Screen center x coordinate
    cy = windowRect[1]/2 # Screen center y coordinate
    windowRect[1]/windowRect[0] # Aspect ratio
    dx = 600 # How big of a range to cover in X (center +/- 600 pixels)
    dy = windowRect[1]/windowRect[0]*dx # How big of a range to cover in Y  (same as x, scaled by AR)

    # Define (x,y) target positions for a 13-point calibration grid
    xy = np.array(
         [  [cx, cy],
            [cx, cy+dy],
            [cx+dx, cy],
            [cx, cy-dy],
            [cx-dx, cy],
            [cx+dx, cy+dy],
            [cx-dx, cy+dy],
            [cx+dx, cy-dy],
            [cx-dx, cy-dy],
            [cx+dx/2, cy+dy/2],
            [cx-dx/2, cy+dy/2],
            [cx-dx/2, cy-dy/2],
            [cx+dx/2, cy-dy/2] ])
    xyCartesian = np.array( dp.TPxConvertCoordSysToCartesian(xy, offsetX=-cx, offsetY=-cy) )
    npts = xy.size//2

    # Define calibration targets
    outerCircle = visual.Circle(windowPtr,units='pix',color=(0,1,0),colorSpace='rgb',radius=30)
    innerCircle = visual.Circle(windowPtr,units='pix',color=(1,0,0),colorSpace='rgb',radius=8)
    outerCircle.color = (0, 1, 0)
    innerCircle.color = (1, 0, 0)

    i = 0 # Calibration target iterator
    raw_vector = np.zeros( (npts,4) ) # Get the pupil-center-to-corneal-reflection-vector data
    showing_dot = 0 # Flag
    t = 0
    t2 = 0
    while i < npts:
        # Present current dot. Calibrate .95 seconds after dot appears. Display dot for an additional 1.05 seconds. Repeat with next dot.
        if (t2 - t) > 2: # Calibration targets each presented for 2 sec total
            Sx = xyCartesian[i,0] # Get screen coordinates of current calibration target
            Sy = xyCartesian[i,1]
            outerCircle.pos = innerCircle.pos = (Sx,Sy) # Update the position of the calibration targets (dots)
            drawCollection( [outerCircle,innerCircle] ) # Draw dots
            windowPtr.flip()
            t = t2
            showing_dot = 1
        else:
            dp.DPxUpdateRegCache() # Get most recent time stamp
            t2 = dp.DPxGetTime()

        if showing_dot and (t2 - t) > 0.95:
            print('calibrating point %d...' % (i+1) )
            raw_vector[i,:] = dp.TPxGetEyePositionDuringCalib_returnsRaw(Sx, Sy, 3) # Get raw values from TPx
            print('done!\n')
            i += 1 # Next point
            showing_dot = 0

    ##### Check for calibration parameters in hardware
    dp.TPxBestPolyFinishCalibration() # Compute affine tranformations
    dp.DPxUpdateRegCache()
    if dp.TPxIsDeviceCalibrated(): # Flag any issues
        calibrationSuccess = True
    else:
        escape(windowPtr,io)
        raise("Could not successfully finish calibration process... exiting now")
    core.wait(2)
    #####


    ###### SCREEN 3: Free viewing of calibration grid with gaze follower ######
    textStim = visual.TextStim(windowPtr,text='Following your gaze now!',
                               anchorHoriz='center',anchorVert='bottom',units='pix')
    textStim.size = 24
    textStim.pos = (0,-windowRect[1]/2)

    targDots = []
    for i in range(xy.shape[0]):
        targDots.append( visual.Circle(windowPtr,units='pix',color=(1,1,1),colorSpace='rgb',radius=20) )
        targDots[i].color = (1,1,1) # these seem to get ignored in the constructor... i know not why psychopy does the things it does... or doesn't
        targDots[i].pos = (xyCartesian[i,0],xyCartesian[i,1])

    rightEye = visual.Circle(windowPtr,units='pix',color=(1,1,1),colorSpace='rgb',radius=15)
    leftEye = visual.Circle(windowPtr,units='pix',color=(1,1,1),colorSpace='rgb',radius=15)
    rightEye.color = (1,0,0)
    leftEye.color = (0,0,1)

    while True:
        dp.DPxUpdateRegCache()
        eyeData = dp.TPxGetEyePosition()

        leftEye.pos = tuple(eyeData[0:2])
        rightEye.pos = tuple(eyeData[2:4])
        
        drawCollection( [textStim,targDots,rightEye,leftEye] )
        windowPtr.flip()

        events = io.devices.keyboard.state
        if events:
            keys = list(events.keys())
            if 'escape' in keys:
                dp.TPxClearDeviceCalibration()
                escape(windowPtr,io)
                return False
            elif 'return' in keys:
                break

    escape(windowPtr,io)
    return calibrationSuccess

###############################################################################
############################### HELPER FUNCTIONS ##############################
###############################################################################

def escape(wp,io):
    wp.close()
    dp.TPxUninitialize()
    dp.DPxClose()
    io.quit()

def drawCollection(collection):
    """
    Draws all items contained within 'collection' onto the back buffer. Assumes every item in
    'collection' is an object of type psychopy.visual.*. If an element in 'collection' is itself
    a collection, then this function will just recursively draw that sub-collection also.

    Args:
        collection (list and/or tuple): some iteratable collection of psychopy.visual.* objects
    """
    for item in collection:
        if type(item) is list or type(item) is tuple:
            drawCollection(item)
        else:
            item.draw()

