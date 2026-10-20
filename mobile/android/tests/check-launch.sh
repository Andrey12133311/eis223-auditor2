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
if rg -q 'Process: ru.eis223.auditor' "$result_dir/crash.txt"; then
  echo 'FAIL: Android Runtime recorded a startup crash'
  exit 1
fi
python3 - "$result_dir/ui.xml" <<'PY'
import sys,xml.etree.ElementTree as E
root=E.parse(sys.argv[1]).getroot()
assert any(n.get('class')=='android.webkit.WebView' for n in root.iter('node')), 'WebView is not visible'
assert any(n.get('text')=='ЕИС Аудитор' for n in root.iter('node')), 'Application toolbar is not visible'
print('PASS: native toolbar and WebView are visible')
PY
python3 - "$result_dir/ui.xml" <<'PY'
import sys,xml.etree.ElementTree as E,re,subprocess
root=E.parse(sys.argv[1]).getroot()
nodes=[n for n in root.iter('node') if n.get('content-desc')=='Меню приложения']
assert len(nodes)==1, 'Application menu button is missing'
x1,y1,x2,y2=map(int,re.findall(r'\d+',nodes[0].get('bounds')))
subprocess.run(['adb','shell','input','tap',str((x1+x2)//2),str((y1+y2)//2)],check=True)
PY
adb shell uiautomator dump /sdcard/eis-menu.xml > /dev/null
adb pull /sdcard/eis-menu.xml "$result_dir/menu.xml" > /dev/null
python3 - "$result_dir/menu.xml" <<'PY'
import sys,xml.etree.ElementTree as E
root=E.parse(sys.argv[1]).getroot()
assert any(n.get('text')=='Личный кабинет' for n in root.iter('node')), 'Account menu did not open'
print('PASS: application menu opens')
PY
adb shell input keyevent 4
adb shell pidof ru.eis223.auditor > "$result_dir/pid-after-menu.txt"
adb shell am force-stop ru.eis223.auditor
adb shell am start -W -n ru.eis223.auditor/.MainActivity > "$result_dir/relaunch.txt" 2>&1
sleep 8
adb shell pidof ru.eis223.auditor > "$result_dir/pid-after-relaunch.txt"
adb logcat -d -b crash > "$result_dir/crash-after-relaunch.txt"
if rg -q 'Process: ru.eis223.auditor' "$result_dir/crash-after-relaunch.txt"; then
  cat "$result_dir/crash-after-relaunch.txt"
  exit 1
fi
adb exec-out screencap -p > "$result_dir/relaunch.png"
echo 'PASS: APK starts, displays the page, opens its menu and survives a force-stop/relaunch'
