#!/usr/bin/env bash
# Cancello pre-«fatto» di DA-Proxcheck: gira sul Mac/PC. Esito aggregato.
set -u
cd "$(dirname "$0")/.."
# il .venv (con pytest e ruff) si crea su ogni macchina: python3 -m venv .venv && .venv/bin/pip install pytest ruff
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
esito=0
echo "→ compilazione"
$PY -m py_compile audit-nodo.py || esito=1
echo "→ sintassi dei collector incorporati"
$PY - <<'EOF' || esito=1
import ast, importlib.util, sys
spec = importlib.util.spec_from_file_location("audit_nodo", "audit-nodo.py")
m = importlib.util.module_from_spec(spec); sys.modules["audit_nodo"] = m; spec.loader.exec_module(m)
for nome, src in (("nodo", m.COLLECTOR_NODO), ("cluster", m.COLLECTOR_CLUSTER)):
    ast.parse(src.replace("__SOLO_ACCESE__", "False").replace("__PERFORMANCE__", "False").replace("__MAX_VM__", "0")
              .replace("__TUTTO_CLUSTER__", "True").replace("__SRC_B64__", "AA==").replace("__VER__", "x"))
    print("  collector", nome, "ok")
EOF
if $PY -m ruff --version >/dev/null 2>&1; then
  echo "→ ruff"; $PY -m ruff check audit-nodo.py tests --quiet || esito=1
fi
echo "→ pytest"
$PY -m pytest -q tests || esito=1
[ $esito -eq 0 ] && echo "✔ tutto verde" || echo "✘ qualcosa non va"
exit $esito
