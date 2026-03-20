"""
Unit tests for system_metrics.py.

Covers pure logic functions (formatting, export, cleanup) and mocks all
psutil / socket / platform calls so tests run without live system data.
"""

import csv
import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

# Ensure the project root is on sys.path so the module can be imported
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import system_metrics as sm


# ---------------------------------------------------------------------------
# apply_unit
# ---------------------------------------------------------------------------

_SAMPLE_METRICS = {
    "Timestamp": "2026-01-01T00:00:00",
    "CpuPercentage": 10.0,
    "UsedMemoryBytes": 2 * 1024 ** 3,       # 2 GB
    "TotalMemoryBytes": 16 * 1024 ** 3,     # 16 GB
    "MemoryUsedPercentage": 12.5,
    "UsedDiskBytes": 100 * 1024 ** 3,       # 100 GB
    "TotalDiskBytes": 500 * 1024 ** 3,      # 500 GB
    "DiskUsedPercentage": 20.0,
    "Uptime": 3600,
}

class TestApplyUnit:
    def test_b_unit_renames_to_b_suffix_and_keeps_raw_values(self):
        result = sm.apply_unit(_SAMPLE_METRICS, "B")
        assert "UsedMemoryB" in result
        assert "TotalMemoryB" in result
        assert "UsedDiskB" in result
        assert "TotalDiskB" in result
        assert result["UsedMemoryB"] == 2 * 1024 ** 3

    def test_b_unit_no_bytes_suffix_keys(self):
        result = sm.apply_unit(_SAMPLE_METRICS, "B")
        for key in ("UsedMemoryBytes", "UsedMemoryKB", "UsedMemoryMB", "UsedMemoryGB"):
            assert key not in result

    def test_empty_unit_keeps_bytes_keys_and_raw_values(self):
        result = sm.apply_unit(_SAMPLE_METRICS, "")
        assert "UsedMemoryBytes" in result
        assert "TotalMemoryBytes" in result
        assert "UsedDiskBytes" in result
        assert "TotalDiskBytes" in result
        assert result["UsedMemoryBytes"] == 2 * 1024 ** 3
    
    def test_empty_unit_no_converted_keys(self):
        result = sm.apply_unit(_SAMPLE_METRICS, "")
        for key in ("UsedMemoryKB", "UsedMemoryMB", "UsedMemoryGB"):
            assert key not in result

    def test_kb_unit_renames_and_converts(self):
        result = sm.apply_unit(_SAMPLE_METRICS, "KB")
        assert "UsedMemoryKB" in result
        assert "UsedMemoryBytes" not in result
        assert result["UsedMemoryKB"] == round(2 * 1024 ** 3 / 1024, 2)

    def test_mb_unit_renames_and_converts(self):
        result = sm.apply_unit(_SAMPLE_METRICS, "MB")
        assert "TotalMemoryMB" in result
        assert result["TotalMemoryMB"] == round(16 * 1024 ** 3 / 1024 ** 2, 2)
    
    def test_gb_unit_renames_and_converts(self):
        result = sm.apply_unit(_SAMPLE_METRICS, "GB")
        assert "UsedDiskGB" in result
        assert result["UsedDiskGB"] == round(100 * 1024 ** 3 / 1024 ** 3, 2)
        assert "TotalDiskGB" in result
        assert result["TotalDiskGB"] == round(500 * 1024 ** 3 / 1024 ** 3, 2)

    def test_non_byte_keys_pass_through_unchanged(self):
        result = sm.apply_unit(_SAMPLE_METRICS, "GB")
        assert result["CpuPercentage"] == 10.0
        assert result["MemoryUsedPercentage"] == 12.5
        assert result["Uptime"] == 3600
        assert result["Timestamp"] == "2026-01-01T00:00:00"

    def test_all_four_byte_keys_converted(self):
        result = sm.apply_unit(_SAMPLE_METRICS, "MB")
        for key in ("UsedMemoryMB", "TotalMemoryMB", "UsedDiskMB", "TotalDiskMB"):
            assert key in result


# ---------------------------------------------------------------------------
# _colorize_row
# ---------------------------------------------------------------------------

class TestColorizeRow:
    def test_each_token_wrapped_in_color_and_reset(self):
        from colorama import Style
        result = sm._colorize_row(["A", "B"], ",")
        # Every token must end with a reset sequence
        assert result.count(Style.RESET_ALL) == 2

    def test_delimiter_separates_tokens(self):
        result = sm._colorize_row(["X", "Y", "Z"], "|")
        # Strip ANSI codes and check plain structure
        import re
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result)
        assert plain == "X|Y|Z"
    
    def test_color_cycles_after_all_colors_used(self):
        from colorama import Style
        # Generate more tokens than there are colors
        tokens = [str(i) for i in range(len(sm._COLUMN_COLORS) + 2)]
        result = sm._colorize_row(tokens, ",")
        # First and (len+1)-th token should share the same color prefix
        first_color = sm._COLUMN_COLORS[0]
        cycle_color = sm._COLUMN_COLORS[0]  # wraps back to index 0
        assert result.startswith(first_color)
        # Should complete without error and contain all tokens
        import re
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result)
        assert plain == ",".join(tokens)

    def test_single_token(self):
        from colorama import Style
        import re
        result = sm._colorize_row(["only"], "-")
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result)
        assert plain == "only"
    

# ---------------------------------------------------------------------------
# _display_value
# ---------------------------------------------------------------------------

class TestDisplayValue:
    def test_cpu_percentage_formatting(self):
        assert sm._display_value("CpuPercentage", 45.678) == "45.7"

    def test_memory_percentage_formatting(self):
        assert sm._display_value("MemoryUsedPercentage", 72.0) == "72.0"

    def test_disk_percentage_formatting(self):
        assert sm._display_value("DiskUsedPercentage", 10.1) == "10.1"

    def test_uptime_formatting_full(self):
        # 1 day + 2 hours + 3 minutes + 4 seconds
        total = 86400 + 7200 + 180 + 4
        assert sm._display_value("Uptime", total) == "1d 2h 3m 4s"

    def test_uptime_formatting_no_days(self):
        total = 3661  # 1h 1m 1s
        assert sm._display_value("Uptime", total) == "0d 1h 1m 1s"

    def test_unknown_key_returns_str(self):
        assert sm._display_value("Timestamp", "2026-01-01T00:00:00") == "2026-01-01T00:00:00"


# ---------------------------------------------------------------------------
# _format_rss_bytes
# ---------------------------------------------------------------------------

class TestFormatRssBytes:
    def test_mb_output(self):
        assert sm._format_rss_bytes(200 * 1024 ** 2) == "200.0MB"

    def test_gb_output(self):
        assert sm._format_rss_bytes(2 * 1024 ** 3) == "2.00GB"

    def test_boundary_exactly_1024mb(self):
        # 1024 MB == 1 GB, should switch to GB
        assert sm._format_rss_bytes(1024 * 1024 ** 2) == "1.00GB"


# ---------------------------------------------------------------------------
# check_network_connectivity
# ---------------------------------------------------------------------------

class TestCheckNetworkConnectivity:
    @patch("system_metrics.socket")
    def test_returns_ok_on_successful_connect(self, mock_socket_module):
        mock_sock = MagicMock()
        mock_socket_module.AF_INET = 2
        mock_socket_module.SOCK_STREAM = 1
        mock_socket_module.socket.return_value = mock_sock
        mock_sock.connect.return_value = None

        result = sm.check_network_connectivity()
        assert result == sm.NETWORK_STATUS_OK

    @patch("system_metrics.socket")
    def test_returns_unreachable_on_os_error(self, mock_socket_module):
        mock_sock = MagicMock()
        mock_socket_module.AF_INET = 2
        mock_socket_module.SOCK_STREAM = 1
        mock_socket_module.socket.return_value = mock_sock
        mock_sock.connect.side_effect = OSError("connection refused")

        result = sm.check_network_connectivity()
        assert result == sm.NETWORK_STATUS_UNREACHABLE


# ---------------------------------------------------------------------------
# check_critical_services
# ---------------------------------------------------------------------------

class TestCheckCriticalServices:
    def _make_proc(self, name: str) -> MagicMock:
        p = MagicMock()
        p.name.return_value = name
        return p

    @patch("system_metrics.psutil.process_iter")
    def test_running_service_detected(self, mock_iter):
        mock_iter.return_value = [self._make_proc("nginx")]
        result = sm.check_critical_services(["nginx"])
        assert result["nginx"] == sm.SERVICE_STATUS_RUNNING

    @patch("system_metrics.psutil.process_iter")
    def test_stopped_service_detected(self, mock_iter):
        mock_iter.return_value = [self._make_proc("python")]
        result = sm.check_critical_services(["nginx"])
        assert result["nginx"] == sm.SERVICE_STATUS_STOPPED
    
    @patch("system_metrics.psutil.process_iter")
    def test_case_insensitive_matching(self, mock_iter):
        mock_iter.return_value = [self._make_proc("NGINX")]
        result = sm.check_critical_services(["nginx"])
        assert result["nginx"] == sm.SERVICE_STATUS_RUNNING

    @patch("system_metrics.psutil.process_iter")
    def test_mixed_running_and_stopped(self, mock_iter):
        mock_iter.return_value = [self._make_proc("sshd"), self._make_proc("python")]
        result = sm.check_critical_services(["sshd", "nginx"])
        assert result["sshd"] == sm.SERVICE_STATUS_RUNNING
        assert result["nginx"] == sm.SERVICE_STATUS_STOPPED


# ---------------------------------------------------------------------------
# get_top_processes
# ---------------------------------------------------------------------------

class TestGetTopProcesses:
    def _make_proc(self, pid, name, mem_pct, cpu_pct, rss):
        proc = MagicMock()
        mem_info = MagicMock()
        mem_info.rss = rss
        proc.info = {
            "pid": pid,
            "name": name,
            "memory_percent": mem_pct,
            "cpu_percent": cpu_pct,
            "memory_info": mem_info,
        }
        return proc

    @patch("system_metrics.psutil.process_iter")
    def test_sorted_by_memory(self, mock_iter):
        mock_iter.return_value = [
            self._make_proc(1, "low",  1.0, 0.5, 1024),
            self._make_proc(2, "high", 9.0, 0.1, 9 * 1024),
        ]
        result = sm.get_top_processes(by=sm.SORT_BY_MEMORY, count=2)
        assert result[0]["name"] == "high"
        assert result[1]["name"] == "low"
    
    @patch("system_metrics.psutil.process_iter")
    def test_sorted_by_cpu(self, mock_iter):
        mock_iter.return_value = [
            self._make_proc(1, "idle",   0.1, 0.5, 1024),
            self._make_proc(2, "worker", 0.5, 8.0, 2048),
        ]
        result = sm.get_top_processes(by=sm.SORT_BY_CPU, count=2)
        assert result[0]["name"] == "worker"

    @patch("system_metrics.psutil.process_iter")
    def test_count_limits_results(self, mock_iter):
        mock_iter.return_value = [
            self._make_proc(i, f"proc{i}", float(i), float(i), i * 1024)
            for i in range(10)
        ]
        result = sm.get_top_processes(count=3)
        assert len(result) == 3

    @patch("system_metrics.psutil.process_iter")
    def test_exclude_names_filters_process(self, mock_iter):
        mock_iter.return_value = [
            self._make_proc(1, "system idle process", 0.0, 99.0, 0),
            self._make_proc(2, "python", 5.0, 5.0, 5000),
        ]
        result = sm.get_top_processes(exclude_names={"system idle process"}, count=5)
        names = [p["name"] for p in result]
        assert "system idle process" not in names
        assert "python" in names


# ---------------------------------------------------------------------------
# export_json
# ---------------------------------------------------------------------------

class TestExportJson:
    def test_appends_ndjson_line(self, tmp_path):
        path = tmp_path / "metrics.json"
        metrics = {"Timestamp": "2026-01-01T00:00:00", "CpuPercentage": 10.0}
        sm.export_json(metrics, path)
        sm.export_json(metrics, path)

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            parsed = json.loads(line)
            assert parsed["CpuPercentage"] == 10.0

    def test_creates_file_if_not_exists(self, tmp_path):
        path = tmp_path / "new.json"
        sm.export_json({"key": "value"}, path)
        assert path.exists()


# ---------------------------------------------------------------------------
# export_csv
# ---------------------------------------------------------------------------

class TestExportCsv:
    def test_writes_header_on_new_file(self, tmp_path):
        path = tmp_path / "metrics.csv"
        metrics = {"Timestamp": "2026-01-01T00:00:00", "CpuPercentage": 5.0}
        sm.export_csv(metrics, path)

        rows = list(csv.DictReader(path.open(encoding="utf-8")))
        assert rows[0]["CpuPercentage"] == "5.0"

    def test_no_duplicate_header_on_append(self, tmp_path):
        path = tmp_path / "metrics.csv"
        metrics = {"Timestamp": "2026-01-01T00:00:00", "CpuPercentage": 5.0}
        sm.export_csv(metrics, path)
        sm.export_csv(metrics, path)
    
        lines = path.read_text(encoding="utf-8").splitlines()
        header_count = sum(1 for line in lines if "Timestamp" in line and "CpuPercentage" in line)
        assert header_count == 1

    def test_skips_nested_values(self, tmp_path):
        path = tmp_path / "metrics.csv"
        metrics = {
            "CpuPercentage": 20.0,
            "TopProcessesByMemory": [{"pid": 1}],  # should be excluded
        }
        sm.export_csv(metrics, path)
        content = path.read_text(encoding="utf-8")
        assert "TopProcessesByMemory" not in content


# ---------------------------------------------------------------------------
# cleanup_old_logs
# ---------------------------------------------------------------------------

class TestCleanupOldLogs:
    def test_removes_files_older_than_retention(self, tmp_path):
        old_file = tmp_path / "metrics_20250101.csv"
        old_file.write_text("old data")
        # Set mtime to 10 days ago
        old_mtime = (datetime.now() - timedelta(days=10)).timestamp()
        os.utime(old_file, (old_mtime, old_mtime))

        sm.cleanup_old_logs(tmp_path, retention_days=7)
        assert not old_file.exists()

    def test_keeps_recent_files(self, tmp_path):
        recent_file = tmp_path / "metrics_20260319.csv"
        recent_file.write_text("recent data")

        sm.cleanup_old_logs(tmp_path, retention_days=7)
        assert recent_file.exists()

    def test_ignores_non_metrics_files(self, tmp_path):
        other_file = tmp_path / "other_old.csv"
        other_file.write_text("should not be deleted")
        old_mtime = (datetime.now() - timedelta(days=10)).timestamp()
        os.utime(other_file, (old_mtime, old_mtime))

        sm.cleanup_old_logs(tmp_path, retention_days=7)
        assert other_file.exists()


# ---------------------------------------------------------------------------
# enforce_max_files
# ---------------------------------------------------------------------------

class TestEnforceMaxFiles:
    def _make_file(self, path: Path, age_seconds: int) -> Path:
        """Create a file and backdate its mtime by age_seconds."""
        path.write_text("data")
        mtime = (datetime.now() - timedelta(seconds=age_seconds)).timestamp()
        os.utime(path, (mtime, mtime))
        return path
    
    def test_deletes_oldest_csv_beyond_limit(self, tmp_path): # 3 CSV files; keep only 2 oldest should be deleted
        self._make_file(tmp_path / "metrics_old.csv", age_seconds=300)
        self._make_file(tmp_path / "metrics_mid.csv", age_seconds=200)
        self._make_file(tmp_path / "metrics_new.csv", age_seconds=100)

        sm.enforce_max_files(tmp_path, max_files=2)

        assert not (tmp_path / "metrics_old.csv").exists()
        assert (tmp_path / "metrics_mid.csv").exists()
        assert (tmp_path / "metrics_new.csv").exists()

    def test_deletes_oldest_json_beyond_limit(self, tmp_path):
        self._make_file(tmp_path / "metrics_old.json", age_seconds=300)
        self._make_file(tmp_path / "metrics_mid.json", age_seconds=200)
        self._make_file(tmp_path / "metrics_new.json", age_seconds=100)

        sm.enforce_max_files(tmp_path, max_files=2)

        assert not (tmp_path / "metrics_old.json").exists()
        assert (tmp_path / "metrics_mid.json").exists()
        assert (tmp_path / "metrics_new.json").exists()

    def test_csv_and_json_quotas_are_independent(self, tmp_path): # 3 CSV, 1 JSON only excess CSV should be pruned
        self._make_file(tmp_path / "metrics_old.csv", age_seconds=300)
        self._make_file(tmp_path / "metrics_mid.csv", age_seconds=200)
        self._make_file(tmp_path / "metrics_new.csv", age_seconds=100)
        self._make_file(tmp_path / "metrics_only.json", age_seconds=300)

        sm.enforce_max_files(tmp_path, max_files=2)

        assert not (tmp_path / "metrics_old.csv").exists()
        assert (tmp_path / "metrics_only.json").exists()

    def test_no_deletion_when_within_limit(self, tmp_path):
        self._make_file(tmp_path / "metrics_a.csv", age_seconds=200)
        self._make_file(tmp_path / "metrics_b.csv", age_seconds=100)

        sm.enforce_max_files(tmp_path, max_files=5)

        assert (tmp_path / "metrics_a.csv").exists()
        assert (tmp_path / "metrics_b.csv").exists()

    def test_prints_deleted_filenames(self, tmp_path, capsys):
        self._make_file(tmp_path / "metrics_old.csv", age_seconds=300)
        self._make_file(tmp_path / "metrics_new.csv", age_seconds=100)

        sm.enforce_max_files(tmp_path, max_files=1)

        output = capsys.readouterr().out
        assert "metrics_old.csv" in output

    def test_ignores_non_metrics_files(self, tmp_path):
        self._make_file(tmp_path / "other_file.csv", age_seconds=300)
        self._make_file(tmp_path / "metrics_a.csv", age_seconds=200)
        self._make_file(tmp_path / "metrics_b.csv", age_seconds=100)

        # Limit of 1 should only remove metrics_a.csv, not other_file.csv
        sm.enforce_max_files(tmp_path, max_files=1)

        assert (tmp_path / "other_file.csv").exists()
        assert not (tmp_path / "metrics_a.csv").exists()
        assert (tmp_path / "metrics_b.csv").exists()


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------

class TestParseArgs:
    def _parse(self, argv):
        with patch("sys.argv", ["system_metrics.py"] + argv):
            return sm.parse_args()

    def test_defaults(self):
        args = self._parse([])
        assert args.network is False
        assert args.procs is None
        assert args.services is False
        assert args.periodic is None
        assert args.json is False
        assert args.csv is False
        assert args.max_files == 10

    def test_max_files_custom(self):
        args = self._parse(["--max-files", "5"])
        assert args.max_files == 5

    def test_json_flag(self):
        args = self._parse(["--json"])
        assert args.json is True

    def test_csv_flag(self):
        args = self._parse(["--csv"])
        assert args.csv is True

    def test_network_flag(self):
        args = self._parse(["--network"])
        assert args.network is True

    def test_procs_default_const(self):
        args = self._parse(["--procs"])
        assert args.procs == 5

    def test_procs_explicit_value(self):
        args = self._parse(["--procs", "10"])
        assert args.procs == 10

    def test_services_flag(self):
        args = self._parse(["--services"])
        assert args.services is True

    def test_periodic_two_values(self):
        args = self._parse(["--periodic", "30", "10"])
        assert args.periodic == [30, 10]

    def test_delimiter_custom(self):
        args = self._parse(["--delimiter", "|"])
        assert args.delimiter == "|"

    def test_default_unit_defaults_to_b(self):
        args = self._parse([])
        assert args.default_unit == "B"
        args = self._parse(["--default-unit", "B"])
        assert args.default_unit == "B"

    def test_default_unit_kb(self):
        args = self._parse(["--default-unit", "KB"])
        assert args.default_unit == "KB"

    def test_default_unit_mb(self):
        args = self._parse(["--default-unit", "MB"])
        assert args.default_unit == "MB"

    def test_default_unit_gb(self):
        args = self._parse(["--default-unit", "GB"])
        assert args.default_unit == "GB"
