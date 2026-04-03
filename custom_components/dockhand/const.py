"""Constants for the Dockhand integration."""

from datetime import timedelta

DOMAIN = "dockhand"
DEFAULT_PORT = 3000
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_VERIFY_SSL = False

CONF_URL = "url"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_VERIFY_SSL = "verify_ssl"
CONF_ENVIRONMENTS = "environments"
CONF_SCAN_INTERVAL = "scan_interval"

ATTR_CONTAINER_ID = "container_id"
ATTR_CONTAINER_IMAGE = "image"
ATTR_CONTAINER_STATUS = "status"
ATTR_CPU_PERCENT = "cpu_percent"
ATTR_MEMORY_USAGE = "memory_usage"
ATTR_MEMORY_LIMIT = "memory_limit"
ATTR_MEMORY_PERCENT = "memory_percent"
ATTR_NETWORK_RX = "network_rx"
ATTR_NETWORK_TX = "network_tx"
ATTR_BLOCK_READ = "block_read"
ATTR_BLOCK_WRITE = "block_write"
ATTR_ENVIRONMENT = "environment"
ATTR_ENVIRONMENT_ID = "environment_id"
ATTR_STARTED_AT = "started_at"
