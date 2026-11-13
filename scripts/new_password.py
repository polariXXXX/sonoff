"""Generate a Basic Auth password locally; do not commit its output."""

import secrets

print(secrets.token_urlsafe(32))
