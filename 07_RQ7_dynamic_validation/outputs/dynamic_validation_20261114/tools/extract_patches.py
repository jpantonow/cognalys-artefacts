"""Write each case's pipeline diff, unchanged, to patches/<case>.diff."""
import hashlib, json, pathlib
R = pathlib.Path('/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/exp_final_20261019')
OUT = pathlib.Path('/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114/patches')
PR = {
 'bitbanana_79': R/'subset_bitbanana_scope6/pull_requests/app.michaelwuensch.bitbanana_79.apk_encodePbkdf2_PBEKeySpecSpec_pr.json',
 'networksurvey_114': R/'run2/pull_requests/com.craxiom.networksurvey_114.apk_getOrCreateKey_KeyStoreSpec_pr.json',
 'passnotes_11700': R/'run2/pull_requests/com.ivanovsky.passnotes_11700.apk_createHttpClient_SSLContextSpec_pr.json',
 'trafficlight_38': R/'run2/pull_requests/com.leekleak.trafficlight_38.apk_getSecretKey_KeyStoreSpec_pr.json',
 'untis_29198884': R/'run2/pull_requests/com.sapuseven.untis_29198884.apk_getCacheFile_MessageDigestSpec_pr.json',
 'tokn_19': R/'run2/pull_requests/me.diamondforge.tokn_19.apk_getOrCreateKey_KeyStoreSpec_pr.json',
}
for case, f in PR.items():
    d = json.loads(f.read_text(encoding='utf-8'))
    diff = d['diff']
    if case == 'bitbanana_79':
        p = (R/'subset_bitbanana_scope6/bitbanana_migration.patch').read_bytes()
        print('  bitbanana .patch == pr diff:', p == diff.encode('utf-8'))
        data = p
    else:
        data = diff.encode('utf-8')
    if not data.endswith(b'\n'):
        print('  WARNING no trailing newline', case)
    (OUT/f'{case}.diff').write_bytes(data)
    files = [l[6:] for l in data.decode().splitlines() if l.startswith('+++ b/')]
    print(case, hashlib.sha256(data).hexdigest()[:16], len(data), files,
          'base:', d.get('base_branch') or d.get('base'), 'head:', d.get('head_commit') or d.get('commit'))
