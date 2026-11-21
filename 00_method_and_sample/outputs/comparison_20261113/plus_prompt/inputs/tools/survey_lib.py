"""Survey the maintainer-response sections in every recorded request of the C arm."""
import json, os, glob, re, sys, hashlib
ROOT = "/mnt/c/Users/Antonow/Documents/tcc_v3/frozen_runs_20261113/c_arm_8be9f78"
UNITS = "/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/comparison_20261113/units"
KEYS = ("maintainer_responses_to_this_rule_in_this_repository",
        "maintainer_responses_to_this_rule_elsewhere",
        "maintainer_responses_in_this_repository")
dec = json.JSONDecoder()


def find_values(text, key):
    """Every JSON value following "key": in text (text is the raw input string)."""
    out = []
    pat = '"%s":' % key
    i = 0
    while True:
        j = text.find(pat, i)
        if j < 0:
            break
        k = j + len(pat)
        while text[k] in " \n":
            k += 1
        try:
            v, end = dec.raw_decode(text, k)
            out.append(v)
        except Exception as e:
            out.append({"_parse_error": str(e)})
            end = k + 1
        i = end
    return out


