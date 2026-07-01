#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILD_DIR="${NS3_BUILD_DIR:-$REPO_ROOT/build/ns3}"
if [ -d "$BUILD_DIR" ] && [ ! -w "$BUILD_DIR" ]; then
  BUILD_DIR="/tmp/neura-ns3-build-${USER:-user}"
fi
mkdir -p "$BUILD_DIR"

NS3_ROOT="${NS3_ROOT:-/home/rzy/shared/ns-3.43}"
if ! pkg-config --exists ns3-core && [ -d "$NS3_ROOT/cmake-cache/pkgconfig" ]; then
  export PKG_CONFIG_PATH="$NS3_ROOT/cmake-cache/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
  export LD_LIBRARY_PATH="$NS3_ROOT/build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

NS3_CFLAGS="$(pkg-config --cflags ns3-core ns3-network ns3-internet ns3-point-to-point ns3-applications ns3-flow-monitor)"
NS3_LIBS="$(pkg-config --libs ns3-core ns3-network ns3-internet ns3-point-to-point ns3-applications ns3-flow-monitor)"
if [ -d "$NS3_ROOT/build/include" ]; then
  NS3_CFLAGS="${NS3_CFLAGS//\/usr\/local\/include/$NS3_ROOT\/build\/include}"
fi
if [ -d "$NS3_ROOT/build/lib" ]; then
  NS3_LIBS="${NS3_LIBS//\/usr\/local\/lib/$NS3_ROOT\/build\/lib} -Wl,-rpath,$NS3_ROOT/build/lib"
fi

g++ -std=c++20 -O2 \
  "$REPO_ROOT/ns3/queue_signal_sanity.cc" \
  -o "$BUILD_DIR/queue_signal_sanity" \
  $NS3_CFLAGS $NS3_LIBS

echo "$BUILD_DIR/queue_signal_sanity"
