"""Option A (2026-11-16): appends the new phase-1 runs to order_plus.json: SP2 r3, r4 and OP r1-r4.

Same rule as the existing order (replica-major; per replica r, units shuffled and each unit's new arms shuffled)
with random.Random(seed + 5000 + r). Existing entries are kept as they are; a backup is written first.
Run (WSL): python3 port_phase/make_order_option_a.py
"""
import random
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import OUT, dump, load  # noqa: E402

NEW = {"SP2": (3, 4), "OP": (1, 2, 3, 4)}


def main():
    path = OUT / "order_plus.json"
    order = load(path)
    have = {(r["unit"], r["arm"], r["replica"]) for r in order["runs"]}
    if any(r["arm"] == "OP" for r in order["runs"]):
        raise SystemExit("order_plus.json already has OP runs")
    shutil.copy2(path, path.with_name("order_plus.json.bak_pre_option_a"))
    units = sorted(p.name for p in (OUT / "units").iterdir() if p.is_dir())
    added = []
    for rep in (1, 2, 3, 4):
        rng = random.Random(order["seed"] + 5000 + rep)
        us = units[:]
        rng.shuffle(us)
        for u in us:
            arms = [a for a, reps in NEW.items() if rep in reps]
            rng.shuffle(arms)
            for a in arms:
                if (u, a, rep) not in have:
                    added.append({"unit": u, "arm": a, "replica": rep})
    order["runs"] += added
    order["replicas"].update({"SP2": 4, "OP": 4})
    order["option_a_20261116"] = ("appended SP2 r3-r4 and OP r1-r4 with random.Random(seed + 5000 + r), "
                                  "replica-major, units then arms shuffled (port_phase/make_order_option_a.py)")
    dump(path, order)
    print(f"appended {len(added)} runs")


if __name__ == "__main__":
    main()
