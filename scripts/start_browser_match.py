#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
from pathlib import Path


def _build_cmd(template: str, host: str, port: int) -> list[str]:
    rendered = template.format(host=host, port=port, python=sys.executable)
    return shlex.split(rendered)


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    default_bot_a = "{python} gaica_bot_v5/main.py {host} {port}"
    default_bot_b = "{python} gaica_bot_v5/main.py {host} {port}"

    parser = argparse.ArgumentParser(description="Start GAICA web UI and launch two socket bots")
    parser.add_argument("--bot-host", default="127.0.0.1")
    parser.add_argument("--bot-port", type=int, default=9001)
    parser.add_argument("--web-host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8080)
    parser.add_argument("--mode", choices=("bot-vs-bot", "bot-vs-human"), default="bot-vs-bot")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--level-index", type=int, default=None)
    parser.add_argument("--round-time-limit", type=float, default=None)
    parser.add_argument(
        "--bot-a-cmd",
        default=default_bot_a,
        help="Command template for bot A. Placeholders: {python}, {host}, {port}",
    )
    parser.add_argument(
        "--bot-b-cmd",
        default=default_bot_b,
        help="Command template for bot B. Placeholders: {python}, {host}, {port}",
    )
    parser.add_argument(
        "--server-cmd",
        default="{python} game/web_port/main.py --bot-host {host} --bot-port {port}",
        help="Optional override for the server command. Placeholders: {python}, {host}, {port}",
    )
    parser.add_argument("--delay-before-bots", type=float, default=1.2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workdir", default=str(repo_root))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workdir = Path(args.workdir).resolve()

    server_template = args.server_cmd
    if "--web-port" not in server_template:
        server_template += f" --web-port {args.web_port}"
    if "--web-host" not in server_template:
        server_template += f" --web-host {args.web_host}"
    if "--mode" not in server_template:
        server_template += f" --mode {args.mode}"
    if "--seed" not in server_template:
        server_template += f" --seed {args.seed}"
    if args.level_index is not None and "--level-index" not in server_template:
        server_template += f" --level-index {args.level_index}"
    if args.round_time_limit is not None and "--round-time-limit" not in server_template:
        server_template += f" --round-time-limit {args.round_time_limit}"

    server_cmd = _build_cmd(server_template, args.bot_host, args.bot_port)
    bot_a_cmd = _build_cmd(args.bot_a_cmd, args.bot_host, args.bot_port)
    bot_b_cmd = _build_cmd(args.bot_b_cmd, args.bot_host, args.bot_port) if args.mode == "bot-vs-bot" else None

    print("Server:", shlex.join(server_cmd))
    print("Bot A :", shlex.join(bot_a_cmd))
    print("Bot B :", shlex.join(bot_b_cmd) if bot_b_cmd is not None else "<human player #2 via browser>")
    print(f"Web UI: http://{args.web_host}:{args.web_port}/")

    if args.dry_run:
        return 0

    processes: list[subprocess.Popen[bytes]] = []
    try:
        server = subprocess.Popen(server_cmd, cwd=workdir)
        processes.append(server)
        time.sleep(max(0.2, args.delay_before_bots))

        bot_a = subprocess.Popen(bot_a_cmd, cwd=workdir)
        processes.append(bot_a)
        if bot_b_cmd is not None:
            bot_b = subprocess.Popen(bot_b_cmd, cwd=workdir)
            processes.append(bot_b)

        print("Match started. Press Ctrl+C to stop all processes.")
        while True:
            time.sleep(0.5)
            if server.poll() is not None:
                return int(server.returncode or 0)
    except KeyboardInterrupt:
        print("Stopping match...")
        return 0
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            if process.poll() is None:
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
