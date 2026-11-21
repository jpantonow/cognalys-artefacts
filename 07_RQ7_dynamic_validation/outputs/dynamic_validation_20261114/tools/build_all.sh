#!/bin/bash
# Dynamic validation, BUILD step (docs/PLANO_VALIDACAO_DINAMICA.md, section 1).
# For each case, one at a time: clone into ~/dynval_builds/<case>/src/<case>,
# check out the commit the pipeline built the patch on, build the debug APK
# with the library's own build helper (cognalys.builds.apk_build: same task,
# clean first, minification off, JDK 21 as in the original builds), copy it to
# apks/<case>/base/; then `git apply` the pipeline's diff unchanged, build
# again and copy to apks/<case>/patched/. Gradle build/ dirs are deleted after
# each case. Resumable: a variant whose APK was copied is not built again.
# Never two Gradle builds of ours at once; waits while another Gradle build
# (e.g. a cognalys run's validation build) is running, kills nothing.
set -u
V=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114
B=$HOME/dynval_builds
JDK=/usr/lib/jvm/java-21-openjdk-amd64
AAPT2=$(ls -d $HOME/Android/Sdk/build-tools/*/aapt2 | sort -V | tail -1)
source ~/venv/bin/activate
mkdir -p "$B" "$V/logs" "$V/build"

free_gb() { df -BG --output=avail /mnt/c | tail -1 | tr -dc 0-9; }
room() {
  local f; f=$(free_gb)
  if [ "$f" -lt 12 ]; then echo "== $(date -Iseconds) STOP: C: has ${f} GB free (< 12)"; return 1; fi
}
other_gradle() { pgrep -f "GradleDaemon|GradleWrapperMain|gradle-launcher|GradleMain" >/dev/null; }
wait_gradle() {
  local n=0
  while other_gradle; do
    [ $((n % 10)) -eq 0 ] && echo "   $(date -Iseconds) another Gradle build is running; waiting"
    n=$((n + 1)); sleep 60
  done
}

# case|repo|commit|task|apk glob (in the build's copied APKs)
CASES="
tokn_19|https://github.com/tokn-authenticator/tokn|19d4bdf53e1a641e36d47ecd5239a23d5202ef19|assembleDebug|app-debug.apk
bitbanana_79|https://github.com/michaelWuensch/BitBanana|e813eb374cb6bc23d2436b38ecada9c47d7cda38|assembleDebug|*debug.apk
untis_29198884|https://github.com/SapuSeven/BetterUntis|dbb978db73f9549869f9aa831ad619b6d4afbcd8|assembleDebug|app-foss-debug.apk
networksurvey_114|https://github.com/christianrowlands/android-network-survey|fa9259c6c9586b306f634650ea7e588005e7ad4d|:networksurvey:assembleCdrDebug|*x86_64-debug.apk
passnotes_11700|https://github.com/aivanovski/keepassvault|77b70f8a30586252b278cf0c0d0ff142e8096008|:app:assembleAutomationDebug|app-automation-debug.apk
trafficlight_38|https://github.com/leekleak/traffic-light|e40dd7a77e24290345b0f99f4990467758d9acbd|:app:assembleFullDebug|*full-debug.apk
"
ONLY=${1:-}

build_variant() {  # case variant task glob
  local C=$1 VAR=$2 T=$3 G=$4 OUT=$B/$1/out_$2 S=$V/build/$1
  if [ -f "$S/$VAR.apk_path" ] && [ -f "$(cat "$S/$VAR.apk_path")" ]; then
    echo "   $VAR already built: $(cat "$S/$VAR.apk_path")"; return 0
  fi
  room || return 2
  wait_gradle
  rm -rf "$OUT"
  echo "   $(date -Iseconds) build $VAR: $T"
  local t0=$SECONDS
  JAVA_HOME=$JDK python -m cognalys.builds.apk_build --projects-dir "$B/$C/src" --project "$C" \
    --output-dir "$OUT" --task "$T" --clean-first --java-home "$JDK" --timeout 7200 \
    > "$V/logs/build_${C}_${VAR}.out" 2>&1
  local rc=$?
  echo "$((SECONDS - t0))" > "$S/$VAR.seconds"
  cp "$OUT"/build_report.json "$S/$VAR.build_report.json" 2>/dev/null
  cp "$OUT"/logs/projects/*.log "$V/logs/build_${C}_${VAR}.gradle.log" 2>/dev/null
  local apk
  apk=$(find "$OUT/apks" -name "$G" -type f 2>/dev/null | sort | head -1)
  if [ -z "$apk" ]; then
    echo "   $VAR build FAILED (rc $rc); see logs/build_${C}_${VAR}.out"; echo "$rc" > "$S/$VAR.failed"; return 1
  fi
  local badging pkg code
  badging=$("$AAPT2" dump badging "$apk" 2>/dev/null | head -1)
  pkg=$(echo "$badging" | sed -n "s/.*package: name='\([^']*\)'.*/\1/p")
  code=$(echo "$badging" | sed -n "s/.*versionCode='\([^']*\)'.*/\1/p")
  mkdir -p "$V/apks/$C/$VAR"
  rm -f "$V/apks/$C/$VAR"/*.apk
  cp "$apk" "$V/apks/$C/$VAR/${pkg}_${code}.apk"
  echo "$badging" > "$S/$VAR.badging"
  basename "$apk" > "$S/$VAR.built_name"
  echo "$V/apks/$C/$VAR/${pkg}_${code}.apk" > "$S/$VAR.apk_path"
  rm -f "$S/$VAR.failed"
  echo "   $VAR ok: ${pkg}_${code}.apk ($((SECONDS - t0)) s)"
}

clean_builds() {  # case: delete untracked Gradle build/, .gradle, .cxx dirs of the clone
  local R=$B/$1/src/$1 d
  [ -d "$R" ] || return
  find "$R" -type d \( -name build -o -name .gradle -o -name .cxx -o -name .kotlin \) -prune -print 2>/dev/null |
  while read -r d; do
    if [ -z "$(git -C "$R" ls-files -- "${d#$R/}" | head -1)" ]; then rm -rf "$d"; fi
  done
  rm -rf "$B/$1"/out_*/apks
}

echo "$CASES" | while IFS='|' read -r C REPO COMMIT T G; do
  [ -n "$C" ] || continue
  [ -n "$ONLY" ] && [ "$ONLY" != "$C" ] && continue
  S=$V/build/$C; mkdir -p "$S"
  echo "== $(date -Iseconds) $C"
  room || exit 2
  R=$B/$C/src/$C
  if [ ! -d "$R/.git" ]; then
    mkdir -p "$B/$C/src"
    git clone -q "$REPO" "$R" > "$V/logs/clone_$C.out" 2>&1 || { echo "   clone failed"; continue; }
  fi
  PATCH=$V/patches/$C.diff
  if [ ! -f "$S/base.apk_path" ] || [ ! -f "$(cat "$S/base.apk_path" 2>/dev/null)" ]; then
    # Base: the commit, no patch.
    git -C "$R" checkout -q -f --detach "$COMMIT" || { echo "   checkout failed"; continue; }
    git -C "$R" reset -q --hard "$COMMIT"
    git -C "$R" rev-parse HEAD > "$S/base.commit"
    build_variant "$C" base "$T" "$G"; rc=$?
    [ $rc -eq 2 ] && exit 2
  fi
  if [ ! -f "$S/patched.apk_path" ] || [ ! -f "$(cat "$S/patched.apk_path" 2>/dev/null)" ]; then
    git -C "$R" checkout -q -f --detach "$COMMIT"
    # The build helper restores what it changed (minification); anything
    # else tracked and modified would not be the pipeline's diff.
    git -C "$R" status --porcelain --untracked-files=no > "$S/tracked_changes_before_apply.txt"
    if [ -s "$S/tracked_changes_before_apply.txt" ]; then
      echo "   tracked files changed by the base build; reset before applying:"; cat "$S/tracked_changes_before_apply.txt"
      git -C "$R" reset -q --hard "$COMMIT"
    fi
    if git -C "$R" apply --check "$PATCH" 2> "$S/apply_check.err"; then
      git -C "$R" apply "$PATCH"
      git -C "$R" diff --stat > "$S/applied.stat"
      sha256sum "$PATCH" | cut -d' ' -f1 > "$S/patch.sha256"
      build_variant "$C" patched "$T" "$G"; rc=$?
      [ $rc -eq 2 ] && exit 2
    else
      echo "   patch does not apply:"; cat "$S/apply_check.err"; touch "$S/patched.apply_failed"
    fi
  fi
  clean_builds "$C"
  echo "   $(date -Iseconds) $C done; C: $(free_gb) GB free"
done
echo "== $(date -Iseconds) builds finished"
