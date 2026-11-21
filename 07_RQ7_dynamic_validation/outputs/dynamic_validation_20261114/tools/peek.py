import json,sys,glob
for f in sorted(glob.glob('/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114/locate/*.json')):
    print('==',f.split('/')[-1])
    d=json.load(open(f))
    def walk(d,p=''):
        if isinstance(d,dict):
            for k,v in d.items(): walk(v,p+'.'+k)
        elif isinstance(d,list): print(p,'[list',len(d),']', str(d)[:200])
        else: print(p, str(d)[:200].replace('\n','\n'))
    walk(d)
