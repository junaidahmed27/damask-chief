#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$repo_dir"
python_cmd="${DAMASK_PYTHON:-python3}"
"$python_cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else "Python 3.12+ is required in this runtime")'
venv_dir=.venv-wsl
if [[ ! -x "$venv_dir/bin/python" ]]; then "$python_cmd" -m venv "$venv_dir"; fi
"$venv_dir/bin/python" -m pip install --require-hashes -r requirements.lock
"$venv_dir/bin/python" -m pip install --no-deps .
"$venv_dir/bin/python" -m damask_chief_of_staff.cli init
if [[ "${1:-}" != '--no-start' ]]; then "$venv_dir/bin/python" -m damask_chief_of_staff.cli start; fi
