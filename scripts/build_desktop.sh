#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

python=".venv/bin/python"
if [[ ! -x "$python" ]]; then
  echo "Create .venv first, or run start_localrag.sh once." >&2
  exit 1
fi

"$python" -m pip install -e '.[build,desktop,documents,images,web]'
"$python" -m PyInstaller --clean --noconfirm packaging/localrag.spec

if [[ "$(uname -s)" == "Darwin" ]] && command -v hdiutil >/dev/null 2>&1; then
  rm -f dist/LocalRAG.dmg
  hdiutil create -volname LocalRAG -srcfolder dist/LocalRAG.app \
    -ov -format UDZO dist/LocalRAG.dmg >/dev/null
  echo "Desktop build written to dist/LocalRAG.dmg"
else
  echo "Desktop build written to dist/LocalRAG"
fi
