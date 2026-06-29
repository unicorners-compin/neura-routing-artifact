#!/usr/bin/env bash
set -euo pipefail

echo "== toolchain =="
python3 --version
g++ --version | head -n 1
cmake --version | head -n 1
pkg-config --version

echo
echo "== ns-3 packages =="
dpkg-query -W ns3 libns3-dev

echo
echo "== ns-3 command =="
if command -v ns3 >/dev/null 2>&1; then
  ns3 --version || true
else
  echo "ns3 command not found"
fi
