import json
d=json.load(open('/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/exp_final_20261019/consolidated/findings.json'))
recs = d if isinstance(d,list) else d.get('findings', d)
print(type(d), len(recs))
if isinstance(recs, dict): recs=list(recs.values())
keys=['bitbanana_79','networksurvey_114','passnotes_11700','trafficlight_38','untis_29198884','tokn_19']
want={'bitbanana':'encodePbkdf2','networksurvey':'getOrCreateKey','passnotes':'createHttpClient','trafficlight':'getSecretKey','untis':'getCacheFile','tokn':'getOrCreateKey'}
print(list(recs[0].keys()))
for r in recs:
    s=json.dumps(r)
    for k,m in want.items():
        if k in s.lower() and m in s:
            print('==',k, {x:r.get(x) for x in r if not isinstance(r.get(x),(dict,list)) and len(str(r.get(x)))<300})
