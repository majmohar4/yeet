#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "Installing test dependencies…"
pip3 install -q -r tests/requirements.txt

echo "Starting automated test suite…"
python3 tests/run_all.py

echo ""
echo "Detailed report:  cat test_results/test_report.md"
echo "Container logs:   cat test_results/container_logs.txt"
