#!/bin/bash
# FMHY Overnight Fetcher — just calls the Python script
# Usage:  ./run_overnight.sh
# Or:     nohup ./run_overnight.sh > /dev/null 2>&1 &

cd "$(dirname "$0")"
exec python3 run_overnight.py
