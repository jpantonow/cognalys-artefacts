set -e
V=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114
TMP=$(mktemp -d)
eval "$(sed -n '/^keep_results()/,/^}/p' $V/queue.sh)"
keep_results ~/rvsec_runs/rvsec_20261114_150915 $TMP/run/rvsec
touch $TMP/run/.done; echo x > $TMP/run/rvsec_run.out
du -sh $TMP/run/rvsec; find $TMP/run/rvsec -type f | sed "s#$TMP##" | head
source ~/venv/bin/activate
cat > $TMP/t.py <<PY
import sys, json
sys.path.insert(0, '$V')
import classify
case = {"spec": "SSLContextSpec", "code": "SSLCONTEXT-NOBS-03", "message": "zzz", "packages": ["com.sysadmindoc.callshield"],
        "base_sites": [("okhttp3.internal.platform.Platform", "newSslSocketFactory")],
        "patched_sites": [("com.sysadmindoc.callshield.CallShieldApp", "<clinit>")]}
from pathlib import Path
r = classify.read_run(Path('$TMP/run'), case, 'base'); r['tasks']=[{k:v for k,v in t.items() if k!='site_methods_reached'} for t in r['tasks']]
print(json.dumps(r, indent=0)[:1500])
r = classify.read_run(Path('$TMP/run'), case, 'patched')
print(r['violation'], r['covered'], r['covered_by'], r['app_methods_reached'])
print(classify.site_matches('a.B\$getSecretKey\$1','invoke',[('a.B','getSecretKey')]), classify.site_matches('a.B','getSecretKey\$lambda\$0',[('a.B','getSecretKey')]), classify.site_matches('a.BC','getSecretKey',[('a.B','getSecretKey')]), classify.site_matches('a.B','getKey-Ab12Cd3',[('a.B','getKey')]))
PY
python $TMP/t.py
rm -rf $TMP
