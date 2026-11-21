R=~/dynval_builds/passnotes_11700/src/passnotes_11700
[ -d $R/keepass-rs/target ] && [ -z "$(git -C $R ls-files keepass-rs/target | head -1)" ] && rm -rf $R/keepass-rs/target
du -sh ~/dynval_builds/* | tr '\n' ' '; echo
unzip -l /mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114/apks/passnotes_11700/patched/*.apk | grep keepass
for c in ~/dynval_builds/*/src/*; do echo "$(basename $c): $(git -C $c log -1 --format=%h) $(git -C $c diff --shortstat)"; done
source ~/venv/bin/activate
python /mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114/tools/collect_builds.py
