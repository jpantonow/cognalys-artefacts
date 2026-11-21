A=$(ls -d ~/Android/Sdk/build-tools/*/aapt2 | sort -V | tail -1)
for f in /mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114/apks/*/base/*.apk; do
  echo "$(basename $f) $($A dump badging $f | grep -iE 'sdkVersion|launchable' | head -3 | tr '\n' ' ')"
done
