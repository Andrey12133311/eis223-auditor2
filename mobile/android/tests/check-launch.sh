#!/usr/bin/env bash
set -euo pipefail
apk_path="$1"
result_dir="$PWD/android-runtime-results"
mkdir -p "$result_dir"
adb shell input keyevent 82
adb logcat -c
adb install -r "$apk_path" > "$result_dir/install.txt"
adb shell am start -W -n ru.eis223.auditor/.MainActivity > "$result_dir/start.txt" 2>&1 || true
sleep 8
adb shell pidof ru.eis223.auditor > "$result_dir/pid.txt" || true
adb logcat -d -b crash > "$result_dir/crash.txt"
adb logcat -d -s AndroidRuntime chromium WebViewFactory > "$result_dir/startup-logcat.txt"
adb shell dumpsys activity activities > "$result_dir/activities.txt"
adb shell uiautomator dump /sdcard/eis-startup.xml > /dev/null 2>&1 || true
adb pull /sdcard/eis-startup.xml "$result_dir/ui.xml" > /dev/null 2>&1 || true
adb exec-out screencap -p > "$result_dir/screen.png"
cat "$result_dir/start.txt"
cat "$result_dir/crash.txt"
if [ ! -s "$result_dir/pid.txt" ]; then
  echo 'FAIL: application process exited during startup'
  exit 1
fi
if rg -q 'FATAL EXCEPTION|Process: ru.eis223.auditor' "$result_dir/crash.txt"; then
  echo 'FAIL: Android Runtime recorded a startup crash'
  exit 1
fi
echo 'PASS: application stayed alive after startup'
