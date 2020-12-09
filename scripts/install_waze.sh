#!/bin/sh

chmod 777 /data/openpilot/apk/
chmod 777 /data/openpilot/apk/com.waze.apk
pm install -r -d "/data/openpilot/apk/com.waze.apk"
pm enable com.waze

pm grant com.waze android.permission.ACCESS_FINE_LOCATION
pm grant com.waze android.permission.ACCESS_COARSE_LOCATION
pm grant com.waze android.permission.READ_EXTERNAL_STORAGE
pm grant com.waze android.permission.WRITE_EXTERNAL_STORAGE
pm grant com.waze android.permission.RECORD_AUDIO

#am start -n com.waze/com.waze.MainActivity
