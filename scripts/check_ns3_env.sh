#!/usr/bin/env bash
set -euo pipefail

echo "== toolchain =="
python3 --version
g++ --version | head -n 1
cmake --version | head -n 1
pkg-config --version

echo
echo "== ns-3 packages =="
dpkg-query -W ns3 libns3-dev || true

echo
echo "== ns-3 source/pkg-config =="
NS3_ROOT="${NS3_ROOT:-/home/rzy/shared/ns-3.43}"
echo "NS3_ROOT=$NS3_ROOT"
if [ -d "$NS3_ROOT/cmake-cache/pkgconfig" ]; then
  export PKG_CONFIG_PATH="$NS3_ROOT/cmake-cache/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
fi
pkg-config --modversion ns3-core || true

echo
echo "== ns-3 command =="
if command -v ns3 >/dev/null 2>&1; then
  ns3 --help | head -n 1 || true
elif [ -x "$NS3_ROOT/ns3" ]; then
  "$NS3_ROOT/ns3" --help | head -n 1 || true
else
  echo "ns3 command not found"
fi
