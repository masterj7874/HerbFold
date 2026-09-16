#!/usr/bin/env bash
# Exact HMMER recipe and sequence-limit patch from the pinned official AF3 Dockerfile.
set -euo pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
af3_checkout=${AF3_REPO_DIR:-"$project_root/external/alphafold3"}
af3_revision=85c4d20505fd5cef05eac22b534d4e793971ae69
source_sha256=ca70d94fd0cf271bd7063423aabb116d42de533117343a9b27a65c17ff06fbf3
source_url=http://eddylab.org/software/hmmer/hmmer-3.4.tar.gz
hmmer_prefix="$project_root/external/tools/hmmer-3.4-af3"
patch_path="$af3_checkout/docker/jackhmmer_seq_limit.patch"

for required in gcc make patch tar sha256sum curl git; do
  command -v "$required" >/dev/null || { printf 'Required command is missing: %s\n' "$required" >&2; exit 1; }
done
[[ $(git -C "$af3_checkout" rev-parse HEAD) == "$af3_revision" ]] || {
  printf 'HMMER patch requires the reviewed official AF3 v3.0.4 checkout.\n' >&2; exit 1;
}
origin=$(git -C "$af3_checkout" remote get-url origin)
[[ "$origin" == https://github.com/google-deepmind/alphafold3.git || "$origin" == https://github.com/google-deepmind/alphafold3 ]] || {
  printf 'AF3 patch origin is not the official repository; stopped.\n' >&2; exit 1;
}
git -C "$af3_checkout" diff --quiet HEAD -- docker/Dockerfile docker/jackhmmer_seq_limit.patch || {
  printf 'Official HMMER recipe or patch has local modifications; stopped.\n' >&2; exit 1;
}
patch_sha256=$(sha256sum "$patch_path")
patch_sha256=${patch_sha256%% *}

if [[ -e "$hmmer_prefix" ]]; then
  if [[ -f "$hmmer_prefix/VERIFIED_SOURCE" && -x "$hmmer_prefix/bin/jackhmmer" ]] &&
     [[ $(cat "$hmmer_prefix/VERIFIED_SOURCE") == "$source_sha256 $patch_sha256" ]]; then
    help_text=$("$hmmer_prefix/bin/jackhmmer" -h)
    if [[ "$help_text" == *"HMMER 3.4"* && "$help_text" == *"--seq_limit"* ]]; then
      printf 'Verified patched HMMER is already installed: %s\n' "$hmmer_prefix/bin"
      exit 0
    fi
  fi
  printf 'Existing HMMER destination was not verified; it will not be overwritten: %s\n' "$hmmer_prefix" >&2
  exit 1
fi

mkdir -p -- "$project_root/external/tools"
build_tmp=$(mktemp -d "$project_root/external/tools/.hmmer-build.XXXXXXXX")
created_prefix=false
completed=false
cleanup() {
  rm -rf -- "$build_tmp"
  if [[ "$created_prefix" == true && "$completed" == false ]]; then
    rm -rf -- "$hmmer_prefix"
  fi
}
trap cleanup EXIT

# Upstream distributes this release over HTTP; the pinned SHA-256 is checked
# before any archive extraction or build code executes. No alternative archive.
curl --fail --location --connect-timeout 15 --max-time 90 --output "$build_tmp/hmmer-3.4.tar.gz" "$source_url"
printf '%s  %s\n' "$source_sha256" "$build_tmp/hmmer-3.4.tar.gz" | sha256sum --check
tar -xzf "$build_tmp/hmmer-3.4.tar.gz" -C "$build_tmp"
(
  cd -- "$build_tmp"
  patch --batch --forward -p0 < "$patch_path"
)
mkdir -- "$hmmer_prefix"
created_prefix=true
build_jobs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '2')
if (( build_jobs > 8 )); then build_jobs=8; fi
(
  cd -- "$build_tmp/hmmer-3.4"
  ./configure --prefix="$hmmer_prefix"
  make -j "$build_jobs"
  make install
  make -C easel install
)
for binary in jackhmmer nhmmer hmmsearch hmmalign hmmbuild esl-reformat; do
  [[ -x "$hmmer_prefix/bin/$binary" ]] || { printf 'Expected tool missing: %s\n' "$binary" >&2; exit 1; }
done
help_text=$("$hmmer_prefix/bin/jackhmmer" -h)
[[ "$help_text" == *"HMMER 3.4"* && "$help_text" == *"--seq_limit"* ]] || {
  printf 'HMMER version/sequence-limit patch verification failed.\n' >&2; exit 1;
}
printf '%s %s\n' "$source_sha256" "$patch_sha256" > "$hmmer_prefix/VERIFIED_SOURCE"
completed=true
printf 'Installed patched HMMER without system changes. Set AF3_HMMER_DIR=%s\n' "$hmmer_prefix/bin"
