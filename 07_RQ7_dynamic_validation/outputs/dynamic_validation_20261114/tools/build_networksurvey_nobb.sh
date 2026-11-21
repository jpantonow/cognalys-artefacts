#!/bin/bash
# Dynamic validation, networksurvey_114 only: RVSec's dexlib2 instrumenter
# fails on a net.bytebuddy method in the debug APK built by build_all.sh
# (base and patched alike, 3 of 3 repetitions: logs/queue.log). This rebuilds
# both variants from the same commit with the same task, adding one init
# script that excludes the net.bytebuddy group from every configuration
# (the app's own code does not import it; it arrives transitively). Base and
# patched get the identical build; only the pipeline's diff differs.
# Output: apks/networksurvey_114/{base,patched}_nobb/ (the earlier APKs stay).
set -u
V=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114
C=networksurvey_114
R=$HOME/dynval_builds/$C/src/$C
COMMIT=fa9259c6c9586b306f634650ea7e588005e7ad4d
PATCH=$V/patches/$C.diff
JDK=/usr/lib/jvm/java-21-openjdk-amd64
AAPT2=$(ls -d $HOME/Android/Sdk/build-tools/*/aapt2 | sort -V | tail -1)
S=$V/build/${C}_nobb
mkdir -p "$S" "$V/logs"
INIT=$S/exclude_bytebuddy.init.gradle
cat > "$INIT" <<'EOF'
allprojects {
    configurations.configureEach {
        exclude group: 'net.bytebuddy'
    }
}
EOF
# The pipeline's own minification-off init script (same one build_all.sh used through apk_build).
PG=$(ls /mnt/c/Users/Antonow/Documents/tcc_v3/outputs/exp_final_20261019/run2/application_results/build_validation/ac1a8dd4d680/production/disable_proguard.init.gradle 2>/dev/null)
echo "== $(date -Iseconds) $C no-bytebuddy builds (proguard-off init: ${PG:-none})"
cd "$R" || exit 1
[ -f local.properties ] || echo "sdk.dir=$HOME/Android/Sdk" > local.properties

build() {  # variant
  local VAR=$1 t0=$SECONDS out apk badging pkg code
  echo "   $(date -Iseconds) build $VAR"
  JAVA_HOME=$JDK ./gradlew --no-daemon --stacktrace -I "$INIT" ${PG:+-I "$PG"} clean :networksurvey:assembleCdrDebug \
    > "$V/logs/build_${C}_nobb_${VAR}.gradle.log" 2>&1
  local rc=$?
  echo "$((SECONDS - t0))" > "$S/$VAR.seconds"
  apk=$(find networksurvey/build/outputs/apk -name '*x86_64-debug.apk' -type f | head -1)
  if [ $rc -ne 0 ] || [ -z "$apk" ]; then echo "   $VAR FAILED (rc $rc)"; tail -30 "$V/logs/build_${C}_nobb_${VAR}.gradle.log"; return 1; fi
  badging=$("$AAPT2" dump badging "$apk" 2>/dev/null | head -1)
  pkg=$(echo "$badging" | sed -n "s/.*package: name='\([^']*\)'.*/\1/p")
  code=$(echo "$badging" | sed -n "s/.*versionCode='\([^']*\)'.*/\1/p")
  mkdir -p "$V/apks/$C/${VAR}_nobb"; rm -f "$V/apks/$C/${VAR}_nobb"/*.apk
  cp "$apk" "$V/apks/$C/${VAR}_nobb/${pkg}_${code}.apk"
  echo "$badging" > "$S/$VAR.badging"
  echo -n "bytebuddy dex: " > "$S/$VAR.bytebuddy"
  for d in $(unzip -Z1 "$apk" | grep -E '^classes[0-9]*\.dex$'); do unzip -p "$apk" "$d" | grep -aq 'Lnet/bytebuddy/' && echo -n "$d " >> "$S/$VAR.bytebuddy"; done
  sha256sum "$V/apks/$C/${VAR}_nobb/${pkg}_${code}.apk" | cut -d' ' -f1 > "$S/$VAR.sha256"
  echo "   $VAR ok ($((SECONDS - t0)) s): ${pkg}_${code}.apk; $(cat "$S/$VAR.bytebuddy")"
}

git checkout -q -f --detach "$COMMIT" && git reset -q --hard "$COMMIT" && git rev-parse HEAD > "$S/base.commit"
build base || exit 1
git reset -q --hard "$COMMIT"
git apply --check "$PATCH" && git apply "$PATCH" && git diff --stat > "$S/applied.stat" || { echo "patch does not apply"; exit 1; }
sha256sum "$PATCH" | cut -d' ' -f1 > "$S/patch.sha256"
build patched || exit 1
git reset -q --hard "$COMMIT"
find . -type d -name build -prune -exec rm -rf {} + 2>/dev/null
echo "== $(date -Iseconds) done"
