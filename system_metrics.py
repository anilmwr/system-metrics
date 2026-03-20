"""
System Metrics Collector
Collects CPU, memory, disk, uptime, network, and process metrics.
Supports Windows, Linux, and macOS.
"""

import argparse
import csv
import json
import logging
import os
import platform
import sys
import time
import socket
from datetime import datetime, timedelta
from pathlib import Path

try:
    import psutil
except ImportError:
    print("ERROR: 'psutil' is required. Install it with: pip install psutil")
    sys.exit(1)

try:
    from tabulate import tabulate
except ImportError:
    print("ERROR: 'tabulate' is required. Install it with: pip install tabulate")
    sys.exit(1)

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=False)
except ImportError:
    print("ERROR: 'colorama' is required. Install it with: pip install colorama")
    sys.exit(1)

from config import CRITICAL_SERVICES, DELIMITER, DNS_SERVER, LOG_DIR, LOG_RETENTION_DAYS

from constants import (
    LABEL_CRITICAL_SERVICES,
    LABEL_NETWORK_STATUS,
    LABEL_TOP_PROCS_BY_CPU,
    LABEL_TOP_PROCS_BY_MEMORY,
    METRIC_KEY_CRITICAL_SERVICES,
    METRIC_KEY_TOP_PROCS_BY_CPU,
    METRIC_KEY_TOP_PROCS_BY_MEMORY,
    NETWORK_STATUS_OK,
    NETWORK_STATUS_UNREACHABLE,
    PLATFORM_WINDOWS,
    PROC_KEY_CPU_PCT,
    PROC_KEY_MEMORY_PCT,
    PROC_KEY_MEMORY_USAGE,
    SERVICE_STATUS_RUNNING,
    SERVICE_STATUS_STOPPED,
    SORT_BY_CPU,
    SORT_BY_MEMORY,
    WINDOWS_CPU_EXCLUDED_PROCS,
)

# --- Metric Collection ---


def get_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def get_cpu_usage() -> float:
    return psutil.cpu_percent(interval=1)


def get_memory_usage() -> dict:
    mem = psutil.virtual_memory()
    return {
        "used_bytes": mem.used,
        "total_bytes": mem.total,
        "percent": mem.percent,
    }


def get_disk_usage() -> dict:
    # Determine primary/system drive based on OS
    if platform.system() == PLATFORM_WINDOWS:
        root = os.environ.get("SystemDrive", "C:") + "\\"
    else:
        root = "/"

    disk = psutil.disk_usage(root)
    return {
        "used_bytes": disk.used,
        "total_bytes": disk.total,
        "percent": disk.percent,
    }

# --- Bonus Features ---


def check_network_connectivity() -> str:
    """
    Checks network by attempting a TCP connection to a well-known host.
    Falls back to socket check to avoid DNS dependency.
    """

    try:
        socket.setdefaulttimeout(3)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((DNS_SERVER, 53))
        return NETWORK_STATUS_OK
    except OSError:
        return NETWORK_STATUS_UNREACHABLE


def _format_rss_bytes(rss_bytes: int) -> str:
    """Format RSS bytes as a human-readable MB or GB string."""
    mb = rss_bytes / (1024**2)
    if mb >= 1024:
        return f"{mb / 1024:.2f}GB"
    return f"{mb:.1f}MB"


def get_top_processes(
    by: str = SORT_BY_MEMORY,
    count: int = 5,
    exclude_names: set[str] | None = None,
) -> list[dict]:
    """Returns top N processes sorted by memory or CPU usage.

    exclude_names: lowercase process names to omit from results.
    """
    procs = []
    for proc in psutil.process_iter(
        ["pid", "name", "memory_percent", "cpu_percent", "memory_info"]
    ):
        try:
            info = proc.info
            name = info["name"] or ""
            if exclude_names and name.lower() in exclude_names:
                continue
            rss = info["memory_info"].rss if info["memory_info"] else 0
            procs.append(
                {
                    "pid": info["pid"],
                    "name": name,
                    PROC_KEY_MEMORY_PCT: round(info["memory_percent"] or 0, 2),
                    PROC_KEY_CPU_PCT: round(info["cpu_percent"] or 0, 2),
                    PROC_KEY_MEMORY_USAGE: rss,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    sort_key = PROC_KEY_MEMORY_PCT if by == SORT_BY_MEMORY else PROC_KEY_CPU_PCT
    return sorted(procs, key=lambda p: p[sort_key], reverse=True)[:count]


def check_critical_services(services: list[str]) -> dict[str, str]:
    """Returns running/stopped status for each listed service name."""
    running_names = {p.name().lower() for p in psutil.process_iter(["name"])}
    return {
        svc: (
            SERVICE_STATUS_RUNNING
            if svc.lower() in running_names
            else SERVICE_STATUS_STOPPED
        )
        for svc in services
    }


def build_metrics(proc_count: int = 0, include_services: bool = False) -> dict:
    """Collect all metrics and return as a structured dict with absolute numeric values."""
    mem = get_memory_usage()
    disk = get_disk_usage()

    metrics = {
        "Timestamp": get_timestamp(),
        "CpuPercentage": get_cpu_usage(),
        "UsedMemoryBytes": mem["used_bytes"],
        "TotalMemoryBytes": mem["total_bytes"],
        "MemoryUsedPercentage": mem["percent"],
        "UsedDiskBytes": disk["used_bytes"],
        "TotalDiskBytes": disk["total_bytes"],
        "DiskUsedPercentage": disk["percent"],
        "Uptime": int(
            (
                datetime.now() - datetime.fromtimestamp(psutil.boot_time())
            ).total_seconds()
        ),
    }

    if proc_count > 0:
        cpu_excludes = (
            WINDOWS_CPU_EXCLUDED_PROCS
            if platform.system() == PLATFORM_WINDOWS
            else None
        )
        metrics[METRIC_KEY_TOP_PROCS_BY_MEMORY] = get_top_processes(
            by=SORT_BY_MEMORY, count=proc_count
        )
        metrics[METRIC_KEY_TOP_PROCS_BY_CPU] = get_top_processes(
            by=SORT_BY_CPU, count=proc_count, exclude_names=cpu_excludes
        )

    if include_services:
        metrics[METRIC_KEY_CRITICAL_SERVICES] = check_critical_services(
            CRITICAL_SERVICES
        )

    return metrics

# Base names for the four byte-valued fields, without any unit suffix.
_BYTE_KEY_BASE_NAMES = {
    "UsedMemoryBytes": "UsedMemory",
    "TotalMemoryBytes": "TotalMemory",
    "UsedDiskBytes": "UsedDisk",
    "TotalDiskBytes": "TotalDisk",
}

_UNIT_DIVISORS = {"KB": 1024, "MB": 1024**2, "GB": 1024**3}


def apply_unit(metrics: dict, unit: str) -> dict:
    """Return a copy of metrics with byte fields converted and renamed to reflect unit.

    unit="B" keeps raw bytes and renames keys to *B.
    unit="KB"/"MB"/"GB" converts values and renames keys to *KB/*MB/*GB.
    unit="" keeps raw bytes and the original *Bytes key names (legacy behaviour).
    """
    result = {}
    for key, value in metrics.items():
        if key in _BYTE_KEY_BASE_NAMES:
            base = _BYTE_KEY_BASE_NAMES[key]
            if unit in _UNIT_DIVISORS:
                result[f"{base}{unit}"] = round(value / _UNIT_DIVISORS[unit], 2)
            elif unit == "B":
                result[f"{base}B"] = value  # keep raw bytes, rename to *B
            else:
                result[key] = value  # keep original *Bytes name and raw value
        else:
            result[key] = value
    return result


def _display_value(key: str, value) -> str:
    """Convert a raw metric value to a display string for console output."""
    if key in ("CpuPercentage", "MemoryUsedPercentage", "DiskUsedPercentage"):
        return f"{value:.1f}"
    if key == "Uptime":
        total = int(value)
        days, remainder = divmod(total, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{days}d {hours}h {minutes}m {seconds}s"
    return str(value)


# Colors applied per-column in the console header and value rows, cycling if
# there are more columns than colors.
_COLUMN_COLORS = [
    Fore.CYAN,
    Fore.YELLOW,
    Fore.GREEN,
    Fore.MAGENTA,
    Fore.BLUE,
    Fore.RED,
    Fore.WHITE,
]

def _colorize_row(tokens: list[str], delimiter: str) -> str:
    """Return a single string where each token is wrapped in its column color."""
    parts = []
    for i, token in enumerate(tokens):
        color = _COLUMN_COLORS[i % len(_COLUMN_COLORS)]
        parts.append(f"{color}{token}{Style.RESET_ALL}")
    return delimiter.join(parts)


def print_delimited(
    metrics: dict,
    delimiter: str = DELIMITER,
    network_status: str | None = None,
    print_header: bool = True,
) -> None:
    """Print metrics as a single delimited line with a header row.

    Expects metrics to have already been processed by apply_unit so key names
    match what is written to CSV/JSON. Each column is printed in a distinct
    color on the console for readability.
    """
    flat_metrics = {k: v for k, v in metrics.items() if not isinstance(v, (dict, list))}
    header_tokens = list(flat_metrics.keys())
    value_tokens = [_display_value(k, v) for k, v in flat_metrics.items()]
    if print_header:
        print(_colorize_row(header_tokens, delimiter))
    print(_colorize_row(value_tokens, delimiter))

    if network_status is not None:
        print(f"\n{LABEL_NETWORK_STATUS}")
        print(
            tabulate([[network_status]], headers=["Status"], tablefmt="rounded_outline")
        )
    
    if METRIC_KEY_TOP_PROCS_BY_MEMORY in metrics:
        rows = [
            [
                p["pid"],
                p["name"],
                _format_rss_bytes(p[PROC_KEY_MEMORY_USAGE]),
                p[PROC_KEY_MEMORY_PCT],
                p[PROC_KEY_CPU_PCT],
            ]
            for p in metrics[METRIC_KEY_TOP_PROCS_BY_MEMORY]
        ]
        print(f"\n{LABEL_TOP_PROCS_BY_MEMORY}")
        print(
            tabulate(
                rows,
                headers=["PID", "Name", "MemoryUsage", "Memory (%)", "CPU (%)"],
                tablefmt="rounded_outline",
            )
        )
    
    if METRIC_KEY_TOP_PROCS_BY_CPU in metrics:
        rows = [
            [
                p["pid"],
                p["name"],
                _format_rss_bytes(p[PROC_KEY_MEMORY_USAGE]),
                p[PROC_KEY_MEMORY_PCT],
                p[PROC_KEY_CPU_PCT],
            ]
            for p in metrics[METRIC_KEY_TOP_PROCS_BY_CPU]
        ]
        print(f"\n{LABEL_TOP_PROCS_BY_CPU}")
        print(
            tabulate(
                rows,
                headers=["PID", "Name", "MemoryUsage", "Memory (%)", "CPU (%)"],
                tablefmt="rounded_outline",
            )
        )

    if METRIC_KEY_CRITICAL_SERVICES in metrics:
        rows = list(metrics[METRIC_KEY_CRITICAL_SERVICES].items())
        print(f"\n{LABEL_CRITICAL_SERVICES}")
        print(tabulate(rows, headers=["Service", "Status"], tablefmt="rounded_outline"))



# --- Export ---


def export_json(metrics: dict, path: Path) -> None:
    # Each call appends one compact JSON line (NDJSON) so the file grows
    # without re-parsing existing content.
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(metrics, separators=(",", ":")) + "\n")


def export_csv(metrics: dict, path: Path) -> None:
    flat = {k: v for k, v in metrics.items() if not isinstance(v, (dict, list))}
    # Check existence before opening so the "w"-equivalent append open doesn't
    # create the file before the header decision is made.
    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=flat.keys(), delimiter=",")
        if write_header:
            writer.writeheader()
        writer.writerow(flat)


# --- Periodic Logging with Cleanup ---


def cleanup_old_logs(log_dir: Path, retention_days: int) -> None:
    """Remove log files older than retention_days."""
    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = 0
    for f in log_dir.glob("metrics_*.csv"):
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        if mtime < cutoff:
            f.unlink()
            removed += 1
    if removed:
        logging.info("Cleaned up %d old log file(s).", removed)



def enforce_max_files(log_dir: Path, max_files: int) -> None:
    """Delete the oldest metrics files in log_dir so at most max_files of each type remain.

    CSV and JSON counts are tracked independently so each type gets its own quota.
    Files are sorted by modification time; the oldest beyond the limit are removed.
    """
    for pattern in ("metrics_*.csv", "metrics_*.json"):
        files = sorted(log_dir.glob(pattern), key=lambda f: f.stat().st_mtime)
        excess = files[: max(0, len(files) - max_files)]
        for f in excess:
            f.unlink()
            print(f"Deleted old log file: {f}")


def _confirm_exit() -> bool:
    """Prompt the user to confirm exiting after a KeyboardInterrupt. Returns True if the user confirms exit."""
    try:
        answer = input("\n Interrupted. Terminate script execution? (y/N): ").strip().lower()
        return answer == "y"
    except EOFError:
        return True  # Treat EOF (e.g. Ctrl+D) as confirmation to exit


def run_periodic(interval_seconds: int, count: int, args: argparse.Namespace) -> None:
    """Collect metrics periodically, exporting to timestamped files in LOG_DIR."""
    LOG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_file = LOG_DIR / f"metrics_{timestamp}.csv"
    json_file = LOG_DIR / f"metrics_{timestamp}.json"

    print(f"Logging every {interval_seconds}s ({count} iterations)")
    cleanup_old_logs(LOG_DIR, LOG_RETENTION_DAYS)

    completed = 0
    i = 0
    while i < count:
        try:
            metrics = apply_unit(
                build_metrics(
                    proc_count=args.procs or 0,
                    include_services=args.services,
                ),
                args.default_unit,
            )
            network_status = check_network_connectivity() if args.network else None
            print_delimited(
                metrics,
                delimiter=args.delimiter,
                network_status=network_status,
                print_header=(i == 0),
            )
            export_csv(metrics, csv_file)
            export_json(metrics, json_file)
            enforce_max_files(LOG_DIR, args.max_files)
            completed += 1
            if i < count - 1:
                time.sleep(interval_seconds)
            i += 1
        except KeyboardInterrupt:
            if _confirm_exit():
                break
            # 
            i += 1
    
    print(f"\nCompleted {completed}/{count} iteration(s).")
    print(f"Exported CSV: {csv_file}")
    print(f"Exported JSON: {json_file}")


# --- CLI ---


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="System metrics collector (Windows/Linux/macOS)"
    )
    parser.add_argument(
        "--network", action="store_true", help="Include network connectivity check"
    )
    parser.add_argument(
        "--procs",
        nargs="?",
        const=5,
        type=int,
        metavar="N",
        help="Show top N processes by memory and CPU (default: 5)",
    )
    parser.add_argument(
        "--services", action="store_true", help="Check critical services/processes"
    )
    parser.add_argument(
        "--delimiter",
        default=DELIMITER,
        metavar="CHAR",
        help=f"Delimiter for console output (default: '{DELIMITER}')",
    )
    parser.add_argument("--json", action="store_true", help="Export metrics to JSON file in metrics_logs directory")
    parser.add_argument("--csv", action="store_true", help="Export metrics to CSV file in metrics_logs directory")
    parser.add_argument(
        "--default-unit",
        dest="default_unit",
        default="B",
        choices=["B", "KB", "MB", "GB", ""],
        metavar="UNIT",
        help="Unit for memory/disk values: B (raw bytes), KB, MB, GB (default: B)",
    )
    parser.add_argument(
        "--max-files",
        dest="max_files",
        type=int,
        default=10,
        metavar="N",
        help="Maximum number of CSV and JSON files to keep in metrics_logs (default: 10 each)",
    )
    parser.add_argument(
        "--periodic",
        nargs=2,
        metavar=("INTERVAL_SECS", "COUNT"),
        type=int,
        help="Collect metrics periodically: interval and total count",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()

    if args.periodic:
        interval, count = args.periodic
        run_periodic(interval, count, args)
        return

    metrics = apply_unit(
        build_metrics(
            proc_count=args.procs or 0,
            include_services=args.services,
        ),
        args.default_unit,
    )
    network_status = check_network_connectivity() if args.network else None
    print_delimited(metrics, delimiter=args.delimiter, network_status=network_status)

    if args.json or args.csv:
        LOG_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if args.json:
            json_path = LOG_DIR / f"metrics_{timestamp}.json"
            export_json(metrics, json_path)
            print(f"Exported JSON: {json_path}")
        if args.csv:
            csv_path = LOG_DIR / f"metrics_{timestamp}.csv"
            export_csv(metrics, csv_path)
            print(f"Exported CSV: {csv_path}")
        enforce_max_files(LOG_DIR, args.max_files)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)
