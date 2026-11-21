import csv
f='/mnt/c/Users/Antonow/Documents/tcc_v3/data/processed/exp_final/findings_exp_final.csv'
rows=list(csv.DictReader(open(f,encoding='utf-8')))
print(list(rows[0].keys()))
want=[('networksurvey_114','getOrCreateKey','KeyStoreSpec'),('passnotes_11700','createHttpClient','SSLContextSpec'),('trafficlight_38','getSecretKey','KeyStoreSpec'),('untis_29198884','getCacheFile','MessageDigestSpec'),('tokn_19','getOrCreateKey','KeyStoreSpec'),('bitbanana_79','encodePbkdf2','PBEKeySpecSpec')]
for a,m,s in want:
    for r in rows:
        if a in r.get('apk','') and m in r.get('method','') and s in r.get('spec',''):
            print('==',a,{k:v[:250] for k,v in r.items() if v and k not in ('stack',)})
