"""Configuration parameters for the system metrics collector."""

from pathlib import Path

DELIMITER = ","
CRITICAL_SERVICES = [
    "CSFalconService.exe",
    "sshd",
    "nginx",
    "apache2",
    "httpd",
    "mysqld",
    "postgres",
]  # customize as needed
LOG_RETENTION_DAYS = 7
LOG_DIR = Path("metrics_logs")
DNS_SERVER = "8.8.8.8"
