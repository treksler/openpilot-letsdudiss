#!/usr/bin/env python
import time
import subprocess
import cereal
import cereal.messaging as messaging
ThermalStatus = cereal.log.ThermalData.ThermalStatus
from selfdrive.swaglog import cloudlog
from common.params import Params, put_nonblocking
params = Params()
from math import floor
import re
import os
from common.realtime import sec_since_boot
import psutil

class App():

  # app type
  TYPE_GPS = 0
  TYPE_SERVICE = 1
  TYPE_FULLSCREEN = 2
  TYPE_UTIL = 3
  TYPE_ANDROID_AUTO = 4

  # manual switch stats
  MANUAL_OFF = -1
  MANUAL_IDLE = 0
  MANUAL_ON = 1

  def appops_set(self, package, op, mode):
    self.system(f"LD_LIBRARY_PATH= appops set {package} {op} {mode}")

  def pm_grant(self, package, permission):
    self.system(f"pm grant {package} {permission}")

  def set_package_permissions(self):
    if self.permissions is not None:
      for permission in self.permissions:
        self.pm_grant(self.app, permission)
    if self.opts is not None:
      for opt in self.opts:
        self.appops_set(self.app, opt, "allow")

  def __init__(self, app, start_cmd, enable_param, auto_run_param, manual_ctrl_param, app_type, check_crash, permissions, opts):
    self.app = app
    # main activity
    self.start_cmd = start_cmd
    # read enable param
    self.enable_param = enable_param
    # read manual run param
    self.manual_ctrl_param = manual_ctrl_param if manual_ctrl_param is not None else None
    # if it's a service app, we do not kill if device is too hot
    self.app_type = app_type
    # app permissions
    self.permissions = permissions
    # app options
    self.opts = opts

    self.own_apk = "/sdcard/apks/" + self.app + ".apk"
    self.has_own_apk = os.path.exists(self.own_apk)
    self.is_installed = False
    self.is_enabled = False
    self.last_is_enabled = False
    self.is_auto_runnable = False
    self.is_running = False
    self.manual_ctrl_status = self.MANUAL_IDLE
    self.manually_ctrled = False
    self.init = False
    self.check_crash = check_crash

  def is_crashed(self):
    return getattr(self, self.enable_param + "_is_crashed")()

  def dp_app_hr_is_crashed(self):
    try:
      result = subprocess.check_output(["dumpsys", "activity", "gb.xxy.hr"], encoding='utf8')
      print("is_crash = %s" % "ACTIVITY" in result)
      return "ACTIVITY" not in result
    except (subprocess.CalledProcessError, IndexError) as e:
      return False

  def get_remote_version(self):
    apk = self.app + ".apk"
    try:
      url = "https://raw.githubusercontent.com/dragonpilot-community/apps/%s/VERSION" % apk
      return subprocess.check_output(["curl", "-H", "'Cache-Control: no-cache'", "-s", url]).decode('utf8').rstrip()
    except subprocess.CalledProcessError as e:
      pass
    return None

  def uninstall_app(self):
    try:
      local_version = self.get_local_version()
      if local_version is not None:
        subprocess.check_output(["pm","uninstall", self.app])
        self.is_installed = False
    except subprocess.CalledProcessError as e:
      pass

  def update_app(self):
    put_nonblocking('dp_is_updating', '1')
    if self.has_own_apk:
      try:
        subprocess.check_output(["pm","install","-r",self.own_apk])
        self.is_installed = True
      except subprocess.CalledProcessError as e:
        self.is_installed = False
    else:
      apk = self.app + ".apk"
      apk_path = "/sdcard/" + apk
      try:
        os.remove(apk_path)
      except (OSError, FileNotFoundError) as e:
        pass

      self.uninstall_app()
      # if local_version is not None:
      #   try:
      #     subprocess.check_output(["pm","uninstall", self.app], stderr=subprocess.STDOUT, shell=True)
      #   except subprocess.CalledProcessError as e:
      #     pass
      try:
        url = "https://raw.githubusercontent.com/dragonpilot-community/apps/%s/%s" % (apk, apk)
        subprocess.check_output(["curl","-o", apk_path,"-LJO", url])
        subprocess.check_output(["pm","install","-r",apk_path])
        self.is_installed = True
      except subprocess.CalledProcessError as e:
        self.is_installed = False
      try:
        os.remove(apk_path)
      except (OSError, FileNotFoundError) as e:
        pass
    put_nonblocking('dp_is_updating', '0')

  def get_local_version(self):
    try:
      result = subprocess.check_output(["dumpsys", "package", self.app, "|", "grep", "versionName"], encoding='utf8')
      if len(result) > 12:
        return re.findall(r"versionName=(.*)", result)[0]
    except (subprocess.CalledProcessError, IndexError) as e:
      pass
    return None

  def init_vars(self, dragonconf):
    self.is_enabled = getattr(dragonconf, self.enable_struct)

    if self.is_enabled:
      local_version = self.get_local_version()
      if local_version is not None:
        self.is_installed = True

      if self.has_own_apk and not self.is_installed:
        self.update_app()

      if self.is_installed:
        self.set_package_permissions()
    else:
      self.uninstall_app()

    if self.manual_ctrl_param is not None and getattr(dragonconf, self.manual_ctrl_struct) != self.MANUAL_IDLE:
      put_nonblocking(self.manual_ctrl_param, str(self.MANUAL_IDLE))
    self.init = True

  def read_params(self, dragonconf):
    if not self.init:
      self.init_vars(dragonconf)

    self.last_is_enabled = self.is_enabled
    self.is_enabled = False if self.enable_struct is None else getattr(dragonconf, self.enable_struct)

    if self.is_installed:
      if self.is_enabled:
        # a service app should run automatically and not manual controllable.
        if self.app_type in [App.TYPE_SERVICE]:
          self.is_auto_runnable = True
          self.manual_ctrl_status = self.MANUAL_IDLE
        else:
          self.manual_ctrl_status = self.MANUAL_IDLE if self.manual_ctrl_param is None else getattr(dragonconf, self.manual_ctrl_struct)
          if self.manual_ctrl_status == self.MANUAL_IDLE:
            self.is_auto_runnable = False if self.auto_run_struct is None else getattr(dragonconf, self.auto_run_struct)
      else:
        if self.last_is_enabled:
          self.uninstall_app()
        self.is_auto_runnable = False
        self.manual_ctrl_status = self.MANUAL_IDLE
        self.manually_ctrled = False
    else:
      if not self.last_is_enabled and self.is_enabled:
        self.update_app()

  def run(self, force=False):
    self.system("pm enable %s" % self.app)  
    self.system(self.start_cmd)
    self.is_running = True

  def kill(self):
    self.system("killall %s" % self.app)
    self.is_running = False

  def system(self, cmd):
    try:
      subprocess.check_output(cmd, stderr=subprocess.STDOUT, shell=True)
    except subprocess.CalledProcessError as e:
      cloudlog.event("running failed",
                     cmd=e.cmd,
                     output=e.output[-1024:],
                     returncode=e.returncode)

  def isRunning(self):
    #Check if this app is running  
    #Iterate over the all the running process
    for proc in psutil.process_iter():
      try:
        # Check if process name contains the given name string.
        if self.app.lower() in proc.name().lower():
          return True
      except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass
    return False;

def init_apps(apps):
  apps.append(App(
    "com.sygic.aura",
    "am start -n com.sygic.aura/com.sygic.aura.activity.NaviNativeActivity",
    "dp_app_sygic",
    None,
    "dp_app_sygic_manual",
    App.TYPE_FULLSCREEN,
    False,
    [
      "android.permission.ACCESS_FINE_LOCATION",
      "android.permission.ACCESS_COARSE_LOCATION",
      "android.permission.READ_EXTERNAL_STORAGE",
      "android.permission.WRITE_EXTERNAL_STORAGE",
      "android.permission.RECORD_AUDIO",
    ],
    [],
  ))

def main():
  apps = []
  frame = 0
  init_done = False
  is_onroad_prev = False

  while 1:
    if not init_done:
      if frame >= 10:
        init_done = True
        init_apps(apps)
        #reset frame count after initialisation
        frame = 0
    else:
      #check if we are on road, run waze if on road, i.e ignition detected, kill app if not on road
      is_onroad = params.get("IsOffroad") != b"1"
      #start GPS app right away if ignition change is detected
      if is_onroad and not is_onroad_prev:
        for app in apps:
          #Only start app if it is not running
          #if not app.isRunning():
          app.run()

      #Close all apps when we are offroad      
      if not is_onroad:
        for app in apps:
          if app.is_running:
            app.kill()

      #check if app is running and restart it every 30 seconds (.i.e every 30 frames)
      if frame >= 30:
        frame = 0 #reset frame count when it exceeds 30
        for app in apps:
          #if app is detected to be dead and we are on-road, restart the app  
          if is_onroad and is_onroad_prev and not app.isRunning():
          #if is_onroad and is_onroad_prev:
            app.run()
      
      #update is_onroad_prev    
      is_onroad_prev = is_onroad      

    frame += 1
    time.sleep(1) #just sleep 1 second

def system(cmd):
  try:
    cloudlog.info("running %s" % cmd)
    subprocess.check_output(cmd, stderr=subprocess.STDOUT, shell=True)
  except subprocess.CalledProcessError as e:
    cloudlog.event("running failed",
                   cmd=e.cmd,
                   output=e.output[-1024:],
                   returncode=e.returncode)

if __name__ == "__main__":
  main()
