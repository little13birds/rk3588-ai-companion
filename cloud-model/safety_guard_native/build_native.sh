#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${ROOT_DIR}/build"

mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"
cmake ..
make -j"$(nproc)"

echo "built: ${BUILD_DIR}/libsafety_rknn.so"
