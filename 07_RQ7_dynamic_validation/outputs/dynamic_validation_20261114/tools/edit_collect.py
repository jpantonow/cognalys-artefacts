import pathlib
p = pathlib.Path('/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114/tools/collect_builds.py')
s = p.read_text()
old = '            "The patch also keeps SSLContext.getInstance(\\"TLS\\") and hostnameVerifier { _, _ -> true }, and changes a log banner."]},'
assert s.count(old) == 1
s = s.replace(old, old[:-3] + ',\n            "The keepass-rs-android module builds Rust JNI libraries with cargo + cargo-ndk. ~/.cargo no longer exists (the pipeline\'s 2026-10-19 build had it), so the first attempt of both variants failed (\\"Required command is not installed: cargo\\"); rustup stable (rustc 1.100.0), the three Android targets and cargo-ndk 4.1.2 were installed under ~/dynval_builds/rust (CARGO_HOME/RUSTUP_HOME, no profile edits) and both variants rebuilt with them. The Rust toolchain version of the pipeline\'s build is unknown; base and patched here share the same one."]},')
old2 = '    entry["problems"] = problems'
s = s.replace(old2, '''    if entry["pipeline_patched_apk_sha256"] and entry["patched_sha256"]:
        entry["patched_identical_to_pipeline_apk"] = entry["pipeline_patched_apk_sha256"] == entry["patched_sha256"]
''' + old2)
p.write_text(s)
