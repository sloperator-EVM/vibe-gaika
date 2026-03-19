#!/bin/bash

# Run GAICA local runner with example bots
# Usage: ./run_local_runner.sh [options]

python local-runner/run_local_runner.py \
  --bot-a local-runner/examples/bot_aggressive.py \
  --bot-b local-runner/examples/bot_idle.py \
  "$@"
