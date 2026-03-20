"""String constants for the system metrics collector."""

# Network status
NETWORK_STATUS_OK = "OK"
NETWORK_STATUS_UNREACHABLE = "UNREACHABLE"

# Service status
SERVICE_STATUS_RUNNING = "RUNNING"
SERVICE_STATUS_STOPPED = "STOPPED"

# Process sort keys
SORT_BY_MEMORY = "memory"
SORT_BY_CPU = "cpu"
PROC_KEY_MEMORY_PCT = "memory_percentage"
PROC_KEY_CPU_PCT = "cpu_percentage"
PROC_KEY_MEMORY_USAGE = "memory_usage"

# Processes excluded from CPU table on Windows (skews results)
WINDOWS_CPU_EXCLUDED_PROCS = {"system idle process"}

# Platform names
PLATFORM_WINDOWS = "Windows"

# Metrics dict keys
METRIC_KEY_NETWORK_STATUS = "NetworkStatus"
METRIC_KEY_TOP_PROCS_BY_MEMORY = "TopProcessesByMemory"
METRIC_KEY_TOP_PROCS_BY_CPU = "TopProcessesByCPU"
METRIC_KEY_CRITICAL_SERVICES = "CriticalServices"

# Display labels
LABEL_NETWORK_STATUS = "Network Status:"
LABEL_TOP_PROCS_BY_MEMORY = "Top Processes by Memory:"
LABEL_TOP_PROCS_BY_CPU = "Top Processes by CPU:"
LABEL_CRITICAL_SERVICES = "Critical Services:"
