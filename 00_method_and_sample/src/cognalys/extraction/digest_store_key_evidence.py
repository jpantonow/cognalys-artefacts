"""Whether a reported digest's value is the key stored entries are kept under.

EinkBro's ad filter (rvsec_einkbro_20261022) identifies each filter by
`url.sha1`: the filter map saved in SharedPreferences is keyed by it, and
each filter's processed data is kept under it, `binaryDataStore.loadData(id)`.
A SHA-256 patch was refused three times by STORED_DERIVED_VALUE_CHANGED and
had no migration to offer, because the policy only knew digests that stay in
memory or that name a file. Data stored under a digest key has a known
migration too -- re-key the entries -- and this records when it applies.

Flagged when every persisted consumer derived_value_consumers found uses the
value as a key into a store (value_consumers._STORE_KEYED_CALL) and none
sends it out of the app.
"""
from __future__ import annotations

from typing import Dict

STORE_KEY_BASIS = "key into a store"


def digest_store_key_evidence(error: Dict) -> Dict:
    """``{"keys_stored_entries": True, ...}`` when the digest keys stored entries."""
    consumers = error.get("derived_value_consumers") or {}
    if consumers.get("transmitted"):
        return {}
    persisted = [item for item in consumers.get("consumers") or []
                 if isinstance(item, dict) and item.get("kind") == "persistence"]
    keyed = [item for item in persisted if STORE_KEY_BASIS in str(item.get("basis", ""))]
    # Every stored use must be a key: a digest also written into a file or a
    # preference value is another contract, and re-keying would not cover it.
    if not keyed or len(keyed) != len(persisted):
        return {}
    first = keyed[0]
    return {
        "keys_stored_entries": True,
        "store_key_sites": [
            {key: item.get(key) for key in ("path", "line", "statement", "reached_through")}
            for item in keyed[:8]
        ],
        "store_files": sorted({str(item.get("path", "")) for item in keyed if item.get("path")}),
        "maintainer_facts": {
            "stored_entry_key": (
                f"The digest is the key stored data is kept under "
                f"({first.get('path', '')}:{first.get('line', '')}: "
                f"{str(first.get('statement', ''))[:120]}), so the change moves existing "
                "entries to the new key when they are loaded instead of leaving them behind."
            ),
        },
    }
