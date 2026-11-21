# Rust for PassNotes' keepass-rs-android module (cargo + cargo-ndk + Android
# targets), installed under ~/dynval_builds/rust only: ~/.cargo was removed,
# and nothing outside ~/dynval_builds may be changed. No shell profile edits.
set -e
export CARGO_HOME=$HOME/dynval_builds/rust/cargo RUSTUP_HOME=$HOME/dynval_builds/rust/rustup
mkdir -p $HOME/dynval_builds/rust
cd $HOME/dynval_builds/rust
if [ ! -x $CARGO_HOME/bin/cargo ]; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o rustup-init.sh
  sh rustup-init.sh -y --no-modify-path --profile minimal --default-toolchain stable
fi
export PATH=$CARGO_HOME/bin:$PATH
rustup target add armv7-linux-androideabi aarch64-linux-android x86_64-linux-android
cargo ndk --help >/dev/null 2>&1 || cargo install cargo-ndk --locked
cargo --version; rustc --version; cargo ndk --version
du -sh $HOME/dynval_builds/rust
