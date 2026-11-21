import re, pathlib
p = pathlib.Path('/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114/classify.py')
s = p.read_text()
pk = {"tokn_19": ["me.diamondforge.tokn"], "bitbanana_79": ["app.michaelwuensch.bitbanana"],
      "untis_29198884": ["com.sapuseven.untis"], "networksurvey_114": ["com.craxiom.networksurvey", "com.craxiom"],
      "passnotes_11700": ["com.ivanovsky.passnotes"], "trafficlight_38": ["com.leekleak.trafficlight"]}
for case, pkgs in pk.items():
    old = f'    "{case}": {{\n'
    assert s.count(old) == 1, case
    s = s.replace(old, old + f'        "packages": {pkgs!r},\n'.replace("'", '"'))
old = '''        prefix = sites[0][0].rsplit(".", 2)[0]
        reached_app.update((cls, m) for cls, m in task["reached"] if cls.startswith(prefix))'''
new = '''        reached_app.update((cls, m) for cls, m in task["reached"]
                           if rvsec.code_origin(cls, case["packages"]) == "app")'''
assert s.count(old) == 1
s = s.replace(old, new)
p.write_text(s)
