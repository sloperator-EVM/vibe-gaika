#!/bin/bash
set -e

# Get repository root (directory of this script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Start the webview server
echo "Starting webview server..."
(cd game/web_port && python -m main --bot-port 9001 --web-port 8080) &
WEB_PID=$!

# Wait for server to initialize
sleep 2

# Start bots
echo "Starting bots..."
python local-runner/examples/bot_aggressive.py --host 127.0.0.1 --port 9001 &
BOT_A_PID=$!

python local-runner/examples/bot_idle.py --host 127.0.0.1 --port 9001 &
BOT_B_PID=$!

echo "Webview available at: http://127.0.0.1:8080/"
echo "Press Ctrl+C to stop all processes."

# Cleanup function
cleanup() {
  echo -e "\nStopping processes..."
  kill $WEB_PID $BOT_A_PID $BOT_B_PID 2>/dev/null
  wait $WEB_PID $BOT_A_PID $BOT_B_PID 2>/dev/null || true
  exit 0
}

trap cleanup INT TERM EXIT

# Wait for web server (it runs indefinitely)
wait $WEB_PID