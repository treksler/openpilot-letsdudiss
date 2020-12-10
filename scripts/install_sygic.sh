#!/bin/sh

chmod 777 /data/openpilot/customapks/
chmod 777 /data/openpilot/customapks/com.sygic.aura17.9.4.apk
pm install -r -d "/data/openpilot/customapks/com.sygic.aura17.9.4.apk"
pm enable com.sygic.aura

pm grant com.sygic.aura android.permission.ACCESS_FINE_LOCATION
pm grant com.sygic.aura android.permission.ACCESS_COARSE_LOCATION
pm grant com.sygic.aura android.permission.READ_EXTERNAL_STORAGE
pm grant com.sygic.aura android.permission.WRITE_EXTERNAL_STORAGE
pm grant com.sygic.aura android.permission.RECORD_AUDIO

#am start -n com.sygic.aura/com.sygic.aura.activity.NaviNativeActivity
