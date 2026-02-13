#!/usr/bin/env python3
"""
internet_blocker.py

Disable and re-enable networking between two times (24-hour HH:MM).
- Linux: uses `nmcli networking off/on` by default (NetworkManager required)
- macOS: uses `networksetup` to disable a network service (default: "Wi-Fi")
- Windows: uses `netsh` to disable a network interface (default: "Wi-Fi")

Run with admin privileges:
- Linux/macOS: sudo python3 internet_blocker.py --start 22:00 --end 06:00
- Windows (Admin PowerShell): python .\internet_blocker.py --start 22:00 --end 06:00

Press Ctrl+C to stop. If you stop while blocked, re-run with --unblock-now.
"""

from __future__ import annotations

import argparse
import datetime as dt
import platform
import subprocess
import sys
import time


def parse_hhmm(s: str) -> dt.time:
    try:
        hour, minute = s.split(":")
        hour_i = int(hour)
        minute_i = int(minute)
        if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
            raise ValueError
        return dt.time(hour=hour_i, minute=minute_i)
    except Exception:
        raise argparse.ArgumentTypeError("Time must be in HH:MM (24-hour) format, e.g., 22:30")


def run_cmd(cmd: list[str]) -> None:
    # Use check=True so failures are loud.
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def block_network(os_name: str, name: str | None) -> None:
    if os_name == "Linux":
        # NetworkManager
        run_cmd(["nmcli", "networking", "off"])
    elif os_name == "Darwin":
        service = name or "Wi-Fi"
        run_cmd(["networksetup", "-setnetworkserviceenabled", service, "off"])
    elif os_name == "Windows":
        iface = name or "Wi-Fi"
        run_cmd(["netsh", "interface", "set", "interface", f'name={iface}', "admin=disabled"])
    else:
        raise RuntimeError(f"Unsupported OS: {os_name}")


def unblock_network(os_name: str, name: str | None) -> None:
    if os_name == "Linux":
        run_cmd(["nmcli", "networking", "on"])
    elif os_name == "Darwin":
        service = name or "Wi-Fi"
        run_cmd(["networksetup", "-setnetworkserviceenabled", service, "on"])
    elif os_name == "Windows":
        iface = name or "Wi-Fi"
        run_cmd(["netsh", "interface", "set", "interface", f'name={iface}', "admin=enabled"])
    else:
        raise RuntimeError(f"Unsupported OS: {os_name}")


def next_occurrence(now: dt.datetime, t: dt.time) -> dt.datetime:
    candidate = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += dt.timedelta(days=1)
    return candidate


def compute_window(now: dt.datetime, start: dt.time, end: dt.time) -> tuple[dt.datetime, dt.datetime]:
    """
    Returns (start_dt, end_dt) for the *next* window relative to now.
    If end <= start, the window crosses midnight.
    """
    start_dt = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    end_dt = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)

    if end <= start:
        # crosses midnight: end is on the next day relative to start
        if now.time() < end:
            # We're after midnight but before end -> start was yesterday
            start_dt -= dt.timedelta(days=1)
        else:
            # start is today, end is tomorrow
            end_dt += dt.timedelta(days=1)
    else:
        # same-day window
        if now > end_dt:
            # window for today already passed -> move to tomorrow
            start_dt += dt.timedelta(days=1)
            end_dt += dt.timedelta(days=1)

    # If we're before start, ensure end matches the same window as start
    if now < start_dt:
        if end <= start:
            end_dt = (start_dt + dt.timedelta(days=1)).replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
        else:
            end_dt = start_dt.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)

    return start_dt, end_dt


def within_window(now: dt.datetime, start_dt: dt.datetime, end_dt: dt.datetime) -> bool:
    return start_dt <= now < end_dt


def sleep_until(target: dt.datetime) -> None:
    while True:
        now = dt.datetime.now()
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 30))  # wake up periodically


def main() -> int:
    ap = argparse.ArgumentParser(description="Block network access between two times (24h HH:MM).")
    ap.add_argument("--start", type=parse_hhmm, required=True, help="Start time (HH:MM, 24h)")
    ap.add_argument("--end", type=parse_hhmm, required=True, help="End time (HH:MM, 24h)")
    ap.add_argument("--name", default=None, help='Interface/service name (macOS/Windows). Defaults to "Wi-Fi".')
    ap.add_argument("--once", action="store_true", help="Run for just the next block/unblock cycle, then exit.")
    ap.add_argument("--unblock-now", action="store_true", help="Immediately re-enable networking and exit.")
    args = ap.parse_args()

    os_name = platform.system()

    if args.unblock_now:
        try:
            unblock_network(os_name, args.name)
            print("Networking enabled.")
            return 0
        except Exception as e:
            print(f"Failed to enable networking: {e}")
            return 2

    print(f"OS detected: {os_name}")
    print(f"Schedule: {args.start.strftime('%H:%M')} -> {args.end.strftime('%H:%M')} (24h)")
    if os_name in ("Darwin", "Windows"):
        print(f'Using interface/service name: {args.name or "Wi-Fi"}')
    if os_name == "Linux":
        print("Linux uses: nmcli networking off/on (requires NetworkManager)")

    blocked = False

    try:
        while True:
            now = dt.datetime.now()
            start_dt, end_dt = compute_window(now, args.start, args.end)

            # If we're currently inside the window and not blocked yet, block now.
            if within_window(now, start_dt, end_dt):
                if not blocked:
                    print(f"[{now:%Y-%m-%d %H:%M:%S}] Blocking networking until {end_dt:%Y-%m-%d %H:%M}...")
                    block_network(os_name, args.name)
                    blocked = True
                sleep_until(end_dt)
                now2 = dt.datetime.now()
                print(f"[{now2:%Y-%m-%d %H:%M:%S}] Unblocking networking...")
                unblock_network(os_name, args.name)
                blocked = False
                if args.once:
                    return 0
                continue

            # Otherwise, wait until the next start time
            next_start = start_dt if start_dt > now else next_occurrence(now, args.start)
            print(f"[{now:%Y-%m-%d %H:%M:%S}] Waiting until {next_start:%Y-%m-%d %H:%M} to block...")
            sleep_until(next_start)

    except KeyboardInterrupt:
        print("\nStopping.")
        # Best effort: if we were blocked, re-enable networking
        if blocked:
            try:
                unblock_network(os_name, args.name)
                print("Networking enabled.")
            except Exception:
                print("Could not auto-enable networking. Run with --unblock-now (as admin).")
        return 0
    except subprocess.CalledProcessError:
        print("A system command failed. Make sure you are running as admin and the interface/service name is correct.")
        return 3
    except Exception as e:
        print(f"Error: {e}")
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
