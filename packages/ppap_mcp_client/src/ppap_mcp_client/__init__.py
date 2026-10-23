"""Client-side file adapter. No server database or engine dependencies."""


class ClientError(ValueError):
    """Stable, secret-free code safe to return across the local MCP boundary."""


MAX_UPLOAD_BYTES = 200 * 1024 * 1024
