#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILD_DIR="$REPO_ROOT/build/ns3"
mkdir -p "$BUILD_DIR"

g++ -std=c++20 -O2 \
  "$REPO_ROOT/ns3/tcp_goodput_sanity.cc" \
  -o "$BUILD_DIR/tcp_goodput_sanity" \
  $(pkg-config --cflags --libs ns3-core ns3-network ns3-internet ns3-point-to-point ns3-applications ns3-flow-monitor)

echo "$BUILD_DIR/tcp_goodput_sanity"
