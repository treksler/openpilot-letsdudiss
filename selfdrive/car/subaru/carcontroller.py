from selfdrive.car import apply_std_steer_torque_limits
from selfdrive.car.subaru import subarucan
from selfdrive.car.subaru.values import DBC, PREGLOBAL_CARS
from opendbc.can.packer import CANPacker


class CarControllerParams():
  def __init__(self):
    #@letdudiss 18 Nov 2020 Reduced max steer for new Subarus (Impreza 2021) with lower torque limit
    #Avoids LKAS and ES fault when OP apply a steer value exceed what ES allows
    self.STEER_MAX = 1439              # max_steer 4095
    self.STEER_STEP = 2                # how often we update the steer cmd
    self.STEER_DELTA_UP = 50           # torque increase per refresh, 0.8s to max
    self.STEER_DELTA_DOWN = 70         # torque decrease per refresh
    self.STEER_DRIVER_ALLOWANCE = 60   # allowed driver torque before start limiting
    self.STEER_DRIVER_MULTIPLIER = 10  # weight driver torque heavily
    self.STEER_DRIVER_FACTOR = 1       # from dbc

    #SUBARU STOP AND GO
    self.SNG_DISTANCE = 170            # distance trigger value for stop and go (0-255)
    self.THROTTLE_TAP_LIMIT = 20       # send a maximum of 20 throttle tap messages (trial and error)
    self.THROTTLE_TAP_LEVEL = 20       # send a throttle message with value of 20 (trial and error)


class CarController():
  def __init__(self, dbc_name, CP, VM):

    #
    #Set below to False to disable feature that turn off Engine Auto Stop Start when car starts
    #
    self.feature_no_engine_stop_start = True

    self.apply_steer_last = 0
    self.es_distance_cnt = -1
    self.es_accel_cnt = -1
    self.es_lkas_cnt = -1
    self.dashlights_cnt = -1
    self.throttle_cnt = -1
    self.fake_button_prev = 0
    self.steer_rate_limited = False
    self.has_set_auto_ss = False

    self.params = CarControllerParams()
    self.packer = CANPacker(DBC[CP.carFingerprint]['pt'])
    self.frame = 0

    #STOP AND GO flags and vars
    self.manual_hold = False
    self.prev_close_distance = 0
    self.prev_cruise_state = 0
    self.throttle_tap_cnt = 0
    self.sng_resume_acc = False

  def update(self, enabled, CS, frame, actuators, pcm_cancel_cmd, visual_alert, left_line, right_line):

    P = self.params
    can_sends = []

    # *** steering ***
    if (frame % self.params.STEER_STEP) == 0:

      apply_steer = int(round(actuators.steer * self.params.STEER_MAX))

      # limits due to driver torque

      new_steer = int(round(apply_steer))
      apply_steer = apply_std_steer_torque_limits(new_steer, self.apply_steer_last, CS.out.steeringTorque, self.params)
      self.steer_rate_limited = new_steer != apply_steer

      #@letdudiss 18 Nov 2020 Work around for steerWarning to
      #Avoids LKAS and ES fault when OP apply a steer value exceed what ES allows
      #set Steering value to 0 when a steer Warning is present
      if not enabled or CS.out.steerWarning:
        apply_steer = 0

      if CS.CP.carFingerprint in PREGLOBAL_CARS:
        can_sends.append(subarucan.create_preglobal_steering_control(self.packer, apply_steer, frame, self.params.STEER_STEP))
      else:
        can_sends.append(subarucan.create_steering_control(self.packer, apply_steer, frame, self.params.STEER_STEP))

      self.apply_steer_last = apply_steer


    # Record manual hold set while in standstill while no car in front
    if CS.out.standstill and self.prev_cruise_state == 1 and CS.cruise_state == 3 and CS.car_follow == 0:
      self.manual_hold = True

    # Cancel manual hold when car starts moving
    if not CS.out.standstill:
      self.manual_hold = False
      self.throttle_tap_cnt = 0         #Reset throttle tap message count when car starts moving
      self.sng_resume_acc = False  #Cancel throttle tap when car starts moving

    #Subaru STOP AND GO
    #Resume when not in MANUAL HOLD and lead car has moved forward
    # Trigger THROTTLE TAP when in hold and close_distance increases > SNG_DISTANCE
    # Ignore when hold has been set in standstill (eg at traffic lights) to avoid 
    # false positives caused by pedestrians/cyclists crossing the street in front of car
    self.sng_resume_acc = False
    if (enabled
        and CS.cruise_state == 3 #cruise state == 3 => ACC HOLD state
        and CS.close_distance > P.SNG_DISTANCE
        and CS.close_distance < 255
        and CS.out.standstill
        and self.prev_close_distance < CS.close_distance
        and CS.car_follow == 1
        and not self.manual_hold):
      self.sng_resume_acc = True

    #Send a throttle tap to resume ACC
    throttle_cmd = -1 #normally, just forward throttle msg from ECU
    if self.sng_resume_acc:
      #Send Maximum <THROTTLE_TAP_LIMIT> to get car out of HOLD
      if self.throttle_tap_cnt < P.THROTTLE_TAP_LIMIT:
        throttle_cmd = P.THROTTLE_TAP_LEVEL
        self.throttle_tap_cnt += 1
      else:
        self.throttle_tap_cnt = -1
        self.sng_resume_acc = False

    #if self.has_set_auto_ss and not CS.autoStopStartDisabled:
    #  throttle_cmd = 20

    # *** alerts and pcm cancel ***

    if CS.CP.carFingerprint in PREGLOBAL_CARS:
      if self.es_accel_cnt != CS.es_accel_msg["Counter"]:
        # 1 = main, 2 = set shallow, 3 = set deep, 4 = resume shallow, 5 = resume deep
        # disengage ACC when OP is disengaged
        if pcm_cancel_cmd:
          fake_button = 1
        # turn main on if off and past start-up state
        elif not CS.out.cruiseState.available and CS.ready:
          fake_button = 1
        else:
          fake_button = CS.button

        # unstick previous mocked button press
        if fake_button == 1 and self.fake_button_prev == 1:
          fake_button = 0
        self.fake_button_prev = fake_button

        can_sends.append(subarucan.create_es_throttle_control(self.packer, fake_button, CS.es_accel_msg))
        self.es_accel_cnt = CS.es_accel_msg["Counter"]

    else:
      if self.es_distance_cnt != CS.es_distance_msg["Counter"]:
        can_sends.append(subarucan.create_es_distance(self.packer, CS.es_distance_msg, pcm_cancel_cmd))
        self.es_distance_cnt = CS.es_distance_msg["Counter"]

      if self.es_lkas_cnt != CS.es_lkas_msg["Counter"]:
        can_sends.append(subarucan.create_es_lkas(self.packer, CS.es_lkas_msg, visual_alert, left_line, right_line))
        self.es_lkas_cnt = CS.es_lkas_msg["Counter"]

      #If Auto Stop Start has gone to state 3 at least once, it means either we have successfully turn off autoStopStart
      #or driver manually turn it off before we got to it
      if CS.autoStopStartDisabled:
        self.has_set_auto_ss = True

      #Send message to press AutoSS button, only do it once, when car starts up, after that, driver can turn it back on if they want
      if self.dashlights_cnt != CS.dashlights_msg["Counter"] and not self.has_set_auto_ss and self.feature_no_engine_stop_start:
        can_sends.append(subarucan.create_dashlights(self.packer, CS.dashlights_msg, True))
        self.dashlights_cnt = CS.dashlights_msg["Counter"]

      #Subaru STOP AND GO: Send throttle message  
      if self.throttle_cnt != CS.throttle_msg["Counter"]:
        can_sends.append(subarucan.create_throttle(self.packer, CS.throttle_msg, throttle_cmd))
        self.throttle_cnt = CS.throttle_msg["Counter"]

    return can_sends
