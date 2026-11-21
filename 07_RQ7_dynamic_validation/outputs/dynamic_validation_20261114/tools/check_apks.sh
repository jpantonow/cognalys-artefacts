V=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114
A=$(ls -d ~/Android/Sdk/build-tools/*/aapt2 | sort -V | tail -1)
for f in $V/apks/*/*/*.apk; do
  echo "$(echo $f | sed "s#$V/apks/##") $(stat -c %s $f) $($A dump badging $f 2>/dev/null | grep -E "^(sdkVersion|launchable-activity)" | tr '\n' ' ' | cut -c1-160) $(unzip -l $f | grep -c 'lib/.*\.so')so"
done
