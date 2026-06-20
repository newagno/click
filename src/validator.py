import os
import re

def validate_config():
    """Validates required environment variables for claim mode."""
    email = os.getenv("INCRYPTED_EMAIL")
    if not email or not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        raise ValueError(f"Invalid or missing INCRYPTED_EMAIL: {email}")

    proxy = os.getenv("RESIDENTIAL_PROXY")
    if proxy and not re.match(r'^(https?://)?[^:]+:[^@]+@[^:]+:\d+$', proxy):
        raise ValueError("Invalid RESIDENTIAL_PROXY format. Expected: http://user:pass@host:port")

    return True
