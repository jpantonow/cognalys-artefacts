#!/bin/bash
# Dynamic validation of the published PRs, BUILD step
# (docs/preregistro_validacao_prs_20261115.md; mirrors tools/build_all.sh).
#
# Cases and order: build/prs_build_cases.txt, written by tools/resolve_pr_cases.py
# from pr_cases.json (accepted, open, closed_by_maintainer; pr_cases.json order
# within a group). For each case, one at a time: clone into
# ~/dynval_builds/<case>/src/<case> (WSL disk), check out build_commit, build with
# the library's helper (cognalys.builds.apk_build: the case's Gradle task, clean
# first, minification off by the helper's init script, JDK chosen by the helper
# from the Gradle wrapper and the declared Java version, as in the pipeline's
# builds), copy the APK to apks/<case>/base/; then `git apply` patch_to_apply
# unchanged, build again, copy to apks/<case>/patched/.
#
# Same recipe in both variants. The only environment addition is CARGO_HOME/
# RUSTUP_HOME pointing at ~/dynval_builds/rust (cargo + cargo-ndk, installed for
# passnotes_11700 on 2026-11-15; ~/.cargo no longer exists), exported for every
# build of both variants. No change to any app's source or build files.
#
# A failed variant build is retried once with the same recipe; a second failure is
# recorded (build/<case>/<variant>.failed, status "não compilou", log tail in
# build_status_prs.json). A base that does not compile skips the patched build.
# After each case the Gradle build/ dirs of the clone are deleted, the resolver
# is run again (packages from the base APK's badging) and build_status_prs.json is
# rewritten (tools/collect_builds_prs.py).
#
# Resumable: a variant whose APK was copied is not built again; a variant with
# two recorded failed attempts is not retried. Never two Gradle builds at once:
# waits while another Gradle build (a client process, or a daemon using CPU) runs,
# kills nothing. Stops when C: has less than 10 GB free.
#
# Run detached (see tools/start_build_prs.sh):
#   tr -d '\r' < tools/build_prs.sh > /tmp/dynval_build_prs.sh
#   setsid nohup bash /tmp/dynval_build_prs.sh >> logs/build_prs.log 2>&1 < /dev/null &
set -u
V=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114
T=/mnt/c/Users/Antonow/Documents/tcc_v3
B=$HOME/dynval_builds
MIN_FREE_GB=${MIN_FREE_GB:-10}
AAPT2=$(ls -d $HOME/Android/Sdk/build-tools/*/aapt2 | sort -V | tail -1)
export CARGO_HOME=$B/rust/cargo RUSTUP_HOME=$B/rust/rustup
unset JAVA_HOME
source ~/venv/bin/activate
mkdir -p "$B" "$V/logs" "$V/build"
cd "$T" || exit 1
ONLY=${1:-}

free_gb() { df -BG --output=avail /mnt/c | tail -1 | tr -dc 0-9; }
room() {
  local f; f=$(free_gb)
  if [ -z "$f" ] || [ "$f" -lt "$MIN_FREE_GB" ]; then
    echo "== $(date -Iseconds) STOP: C: has ${f:-unknown} GB free (< $MIN_FREE_GB)"; return 1
  fi
}

# Another Gradle build is running: a Gradle client (it lives as long as its
# build), or a Gradle/Kotlin daemon that used more than 2 s of CPU in 10 s (an
# idle daemon left by an earlier build does not count).
gradle_busy() {
  pgrep -f "GradleWrapperMain|gradle-launcher|org.gradle.launcher.GradleMain" >/dev/null && return 0
  local pids p t0 t1 sum0=0 sum1=0
  pids=$(pgrep -f "GradleDaemon|KotlinCompileDaemon")
  [ -n "$pids" ] || return 1
  for p in $pids; do t0=$(awk '{print $14+$15}' /proc/$p/stat 2>/dev/null); sum0=$((sum0 + ${t0:-0})); done
  sleep 10
  for p in $pids; do t1=$(awk '{print $14+$15}' /proc/$p/stat 2>/dev/null); sum1=$((sum1 + ${t1:-0})); done
  [ $((sum1 - sum0)) -gt 200 ]
}
wait_gradle() {
  local n=0
  while gradle_busy; do
    [ $((n % 10)) -eq 0 ] && echo "   $(date -Iseconds) another Gradle build is running; waiting"
    n=$((n + 1)); sleep 60
  done
}

pick_apk() {  # out dir, glob -> prints "path|how"
  local OUT=$1 G=$2 apk loose n
  apk=$(find "$OUT/apks" -name "$G" -type f 2>/dev/null | sort | head -1)
  [ -n "$apk" ] && { echo "$apk|apk_glob $G"; return; }
  # The recorded name can carry a version, date or commit that differs at this
  # commit: the glob with every digit run made a wildcard.
  loose=$(echo "$G" | sed -E 's/[0-9]+/*/g; s/\*(\.\*)+/*/g')
  if [ "$loose" != "$G" ]; then
    n=$(find "$OUT/apks" -name "$loose" -type f 2>/dev/null | wc -l)
    if [ "$n" -eq 1 ]; then
      apk=$(find "$OUT/apks" -name "$loose" -type f); echo "$apk|apk_glob $G not found; digit-wildcard glob $loose matched one APK"; return
    fi
  fi
  n=$(find "$OUT/apks" -name '*.apk' -type f 2>/dev/null | wc -l)
  if [ "$n" -eq 1 ]; then
    apk=$(find "$OUT/apks" -name '*.apk' -type f); echo "$apk|apk_glob $G not found; the build produced exactly one APK"; return
  fi
  echo ""
}

build_once() {  # case variant task glob attempt -> 0 ok, 1 failed
  local C=$1 VAR=$2 TASK=$3 G=$4 A=$5 OUT=$B/$1/out_$2 S=$V/build/$1 rc t0 picked apk how
  wait_gradle
  rm -rf "$OUT"
  echo "   $(date -Iseconds) build $VAR (attempt $A): $TASK"
  t0=$SECONDS
  python -m cognalys.builds.apk_build --projects-dir "$B/$C/src" --project "$C" \
    --output-dir "$OUT" --task "$TASK" --clean-first --timeout 7200 \
    > "$V/logs/build_${C}_${VAR}.a$A.out" 2>&1
  rc=$?
  echo "$((SECONDS - t0))" > "$S/$VAR.a$A.seconds"
  cp "$OUT"/build_report.json "$S/$VAR.a$A.build_report.json" 2>/dev/null
  cp "$OUT"/logs/projects/*.log "$V/logs/build_${C}_${VAR}.a$A.gradle.log" 2>/dev/null
  picked=$(pick_apk "$OUT" "$G")
  if [ -z "$picked" ]; then
    echo "$rc" > "$S/$VAR.a$A.failed"
    find "$OUT/apks" -name '*.apk' -type f -printf '%f\n' 2>/dev/null > "$S/$VAR.a$A.apks_found"
    echo "   $VAR attempt $A FAILED (rc $rc, $((SECONDS - t0)) s; APKs found: $(tr '\n' ' ' < "$S/$VAR.a$A.apks_found"))"
    tail -5 "$V/logs/build_${C}_${VAR}.a$A.gradle.log" 2>/dev/null | sed 's/^/      | /'
    return 1
  fi
  apk=${picked%%|*}; how=${picked#*|}
  local badging pkg code
  badging=$("$AAPT2" dump badging "$apk" 2>/dev/null | head -1)
  pkg=$(echo "$badging" | sed -n "s/.*package: name='\([^']*\)'.*/\1/p")
  code=$(echo "$badging" | sed -n "s/.*versionCode='\([^']*\)'.*/\1/p")
  mkdir -p "$V/apks/$C/$VAR"
  rm -f "$V/apks/$C/$VAR"/*.apk
  cp "$apk" "$V/apks/$C/$VAR/${pkg}_${code}.apk"
  echo "$badging" > "$S/$VAR.badging"
  basename "$apk" > "$S/$VAR.built_name"
  echo "$how" > "$S/$VAR.apk_pick"
  echo "$A" > "$S/$VAR.attempt_ok"
  echo "$((SECONDS - t0))" > "$S/$VAR.seconds"
  # net.bytebuddy in the dex: RVSec's dexlib2 instrumenter failed on it for networksurvey_114.
  : > "$S/$VAR.bytebuddy"
  for d in $(unzip -Z1 "$apk" 2>/dev/null | grep -E '^classes[0-9]*\.dex$'); do
    unzip -p "$apk" "$d" | grep -aq 'Lnet/bytebuddy/' && echo -n "$d " >> "$S/$VAR.bytebuddy"
  done
  echo "$V/apks/$C/$VAR/${pkg}_${code}.apk" > "$S/$VAR.apk_path"
  echo "   $VAR ok: ${pkg}_${code}.apk from $(basename "$apk") ($((SECONDS - t0)) s; $how)"
}

build_variant() {  # case variant task glob -> 0 ok, 1 "não compilou" or patch does not apply, 2 stop
  local C=$1 VAR=$2 S=$V/build/$1 A
  if [ -f "$S/$VAR.apk_path" ] && [ -f "$(cat "$S/$VAR.apk_path")" ]; then
    echo "   $VAR already built: $(cat "$S/$VAR.apk_path")"; return 0
  fi
  for A in 1 2; do
    if [ -f "$S/$VAR.a$A.failed" ]; then
      [ "$A" = 2 ] && { echo "   $VAR: two failed attempts recorded (não compilou)"; touch "$S/$VAR.failed"; return 1; }
      continue
    fi
    room || return 2
    # Each attempt starts from the same tree: the commit, plus the patch for patched.
    prepare_$VAR || return 1
    if build_once "$@" "$A"; then rm -f "$S/$VAR.failed"; return 0; fi
    [ "$A" = 2 ] && { touch "$S/$VAR.failed"; echo "   $VAR não compilou (2 attempts, same recipe)"; return 1; }
  done
}

prepare_base() {
  checkout_clean "$R" "$COMMIT" > "$S/base.commit"
}
prepare_patched() {
  checkout_clean "$R" "$COMMIT" > "$S/patched.commit"
  if git -C "$R" apply --check "$PATCH" 2> "$S/apply_check.err"; then
    git -C "$R" apply "$PATCH"
    git -C "$R" diff --stat > "$S/applied.stat"
    sha256sum "$PATCH" | cut -d' ' -f1 > "$S/patch.sha256"
    return 0
  fi
  echo "   patch does not apply:"; sed 's/^/      | /' "$S/apply_check.err"; touch "$S/patched.apply_failed"
  return 1
}

clean_builds() {  # case: delete untracked Gradle build/, .gradle, .cxx, .kotlin dirs of the clone
  local R=$B/$1/src/$1 d
  [ -d "$R" ] || return
  find "$R" -type d \( -name build -o -name .gradle -o -name .cxx -o -name .kotlin \) -prune -print 2>/dev/null |
  while read -r d; do
    if [ -z "$(git -C "$R" ls-files -- "${d#$R/}" | head -1)" ]; then rm -rf "$d"; fi
  done
  rm -rf "$B/$1"/out_*/apks
}

ensure_commit() {  # repo dir, commit, pr number
  git -C "$1" cat-file -e "$2^{commit}" 2>/dev/null && return 0
  git -C "$1" fetch -q origin "$2" 2>/dev/null && git -C "$1" cat-file -e "$2^{commit}" 2>/dev/null && return 0
  git -C "$1" fetch -q origin "pull/$3/head" 2>/dev/null && git -C "$1" cat-file -e "$2^{commit}" 2>/dev/null && return 0
  return 1
}

checkout_clean() {  # repo dir, commit
  # 2026-11-17: also drop untracked files (not ignored ones): an interrupted patched build left a file the
  # patch adds, and the next `git apply --check` failed with "already exists in working directory".
  git -C "$1" checkout -q -f --detach "$2" && git -C "$1" reset -q --hard "$2" && git -C "$1" clean -fdq &&
    git -C "$1" submodule update -q --init --recursive --force 2>/dev/null
  git -C "$1" rev-parse HEAD
}

# 2026-11-18 (deviation 6): also resolve the deviation-6 cases (app packages from the new base badging).
collect() { python "$V/tools/resolve_pr_cases.py" > /dev/null; python "$V/tools/resolve_oldmsg_cases.py" > /dev/null; python "$V/tools/resolve_desvio6_cases.py" > /dev/null; python "$V/tools/collect_builds_prs.py" > /dev/null; }

echo "== $(date -Iseconds) build_prs.sh started (pid $$, C: $(free_gb) GB free)"
python "$V/tools/resolve_pr_cases.py" > /dev/null || { echo "resolver failed"; exit 1; }
CASEFILE=${CASEFILE:-$V/build/prs_build_cases.txt}   # deviation 5 (2026-11-17): CASEFILE=$V/build/prs_build_cases_oldmsg.txt; deviation 6 (2026-11-18): prs_build_cases_desvio6.txt
while IFS='|' read -r -u 3 C STATUS REPO COMMIT PATCH TASK G PRN; do
  [ -n "$C" ] || continue
  [ -n "$ONLY" ] && [ "$ONLY" != "$C" ] && continue
  S=$V/build/$C; mkdir -p "$S"
  base_ok() { [ -f "$S/base.apk_path" ] && [ -f "$(cat "$S/base.apk_path" 2>/dev/null)" ]; }
  patched_ok() { [ -f "$S/patched.apk_path" ] && [ -f "$(cat "$S/patched.apk_path" 2>/dev/null)" ]; }
  if base_ok && patched_ok; then continue; fi
  if [ -f "$S/base.failed" ] || [ -f "$S/patched.failed" ] || [ -f "$S/patched.apply_failed" ] || [ -f "$S/clone.failed" ]; then
    continue
  fi
  echo "== $(date -Iseconds) $C ($STATUS) $REPO @ ${COMMIT:0:12}, task $TASK, apk $G"
  room || break
  t_case=$SECONDS
  R=$B/$C/src/$C
  if [ ! -d "$R/.git" ]; then
    mkdir -p "$B/$C/src"
    # A repository already cloned for another case of this run is cloned locally
    # (hard-linked objects), then pointed at GitHub.
    SIB=$(for d in "$B"/*/src/*/.git; do [ -d "$d" ] || continue
            u=$(git -C "${d%/.git}" remote get-url origin 2>/dev/null); [ "$u" = "$REPO" ] && { echo "${d%/.git}"; break; }; done)
    if [ -n "$SIB" ]; then
      git clone -q --no-checkout "$SIB" "$R" > "$V/logs/clone_$C.out" 2>&1 && git -C "$R" remote set-url origin "$REPO"
    else
      git clone -q --no-checkout "$REPO" "$R" > "$V/logs/clone_$C.out" 2>&1
    fi
    if [ ! -d "$R/.git" ]; then echo "   clone failed"; tail -3 "$V/logs/clone_$C.out"; touch "$S/clone.failed"; continue; fi
  fi
  if ! ensure_commit "$R" "$COMMIT" "$PRN"; then
    echo "   commit $COMMIT not found in $REPO (nor its PR #$PRN head)"; echo "$COMMIT" > "$S/clone.failed"; continue
  fi
  stop=0
  if ! base_ok; then
    build_variant "$C" base "$TASK" "$G" < /dev/null; rc=$?
    [ $rc -eq 2 ] && stop=1
  fi
  if [ $stop -eq 0 ] && base_ok && ! patched_ok; then
    build_variant "$C" patched "$TASK" "$G" < /dev/null; rc=$?
    [ $rc -eq 2 ] && stop=1
  fi
  checkout_clean "$R" "$COMMIT" > /dev/null
  clean_builds "$C"
  echo "$((SECONDS - t_case))" > "$S/case.seconds"
  collect
  echo "   $(date -Iseconds) $C done in $((SECONDS - t_case)) s; C: $(free_gb) GB free"
  [ $stop -eq 1 ] && break
done 3< "$CASEFILE"
collect
echo "== $(date -Iseconds) build_prs.sh finished"
