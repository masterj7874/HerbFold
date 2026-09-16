#!/usr/bin/env bash
# Reproducible source/runtime setup; never obtains restricted model parameters.
set -euo pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
af3_checkout=${AF3_REPO_DIR:-"$project_root/external/alphafold3"}
af3_revision=85c4d20505fd5cef05eac22b534d4e793971ae69
af3_tag=v3.0.4
af3_upstream=https://github.com/google-deepmind/alphafold3.git

for required in uv git; do
  command -v "$required" >/dev/null || { printf 'Required command is missing: %s\n' "$required" >&2; exit 1; }
done
if [[ ! -e "$af3_checkout" ]]; then
  mkdir -p -- "$(dirname -- "$af3_checkout")"
  clone_tmp=$(mktemp -d "$(dirname -- "$af3_checkout")/.af3-clone.XXXXXXXX")
  trap 'if [[ -n ${clone_tmp:-} && -d "$clone_tmp" ]]; then rm -rf -- "$clone_tmp"; fi' EXIT
  git clone --depth 1 --branch "$af3_tag" "$af3_upstream" "$clone_tmp"
  [[ $(git -C "$clone_tmp" rev-parse HEAD) == "$af3_revision" ]] || {
    printf 'Official tag did not resolve to the reviewed commit; stopped.\n' >&2; exit 1;
  }
  mv -T -- "$clone_tmp" "$af3_checkout"
  clone_tmp=
fi
[[ -d "$af3_checkout/.git" ]] || { printf 'Destination is not an AF3 Git checkout; it will not be overwritten.\n' >&2; exit 1; }
origin=$(git -C "$af3_checkout" remote get-url origin)
[[ "$origin" == "$af3_upstream" || "$origin" == "${af3_upstream%.git}" ]] || {
  printf 'AF3 origin is not the reviewed official repository; stopped without changing it.\n' >&2; exit 1;
}
[[ $(git -C "$af3_checkout" rev-parse HEAD) == "$af3_revision" ]] || {
  printf 'AF3 checkout must be v3.0.4 at %s. Existing source will not be changed.\n' "$af3_revision" >&2; exit 1;
}
git -C "$af3_checkout" diff --quiet && git -C "$af3_checkout" diff --cached --quiet || {
  printf 'AF3 has tracked local changes; preserve them before using this pinned setup.\n' >&2; exit 1;
}

# --locked rejects any pyproject/lock inconsistency. Existing .venv stays local.
UV_LINK_MODE=copy uv sync --project "$af3_checkout" --locked --all-groups --python 3.12
if uv run --project "$af3_checkout" --no-sync python - <<'PY'
from importlib import resources
from pathlib import Path
from alphafold3.constants import converters

root = resources.files(converters)
paths = [Path(str(root.joinpath(name))) for name in ("ccd.pickle", "chemical_component_sets.pickle")]
raise SystemExit(0 if all(path.is_file() and path.stat().st_size > 0 for path in paths) else 1)
PY
then
  printf 'AF3 chemical-component data already exists; build_data skipped.\n'
else
  uv run --project "$af3_checkout" --no-sync build_data
fi
uv run --project "$af3_checkout" --no-sync python - <<'PY'
import importlib.metadata
import sys
from alphafold3.cpp import cif_dict

assert cif_dict is not None
print(f"AlphaFold {importlib.metadata.version('alphafold3')} runtime ready: {sys.executable}")
print("Model parameters and MSA databases must be supplied separately through the official process.")
PY
