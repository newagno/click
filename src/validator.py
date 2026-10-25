import os
import re

def validate_config():
    """Validates required environment variables for claim mode."""
    email = os.getenv("INCRYPTED_EMAIL")
    if not email or not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        raise ValueError(f"Invalid or missing INCRYPTED_EMAIL: {email}")

    proxy = os.getenv("RESIDENTIAL_PROXY") or os.getenv("BACKUP_PROXY") or os.getenv("PROXY")
    if proxy:
        s = proxy.strip()
        for prefix in ("http://", "https://", "socks5://", "socks4://"):
            if s.startswith(prefix):
                s = s[len(prefix):]
        s = s.split("/")[0].strip()
        
        # Format 1: host:port:user:pass
        is_colon_format = ("@" not in s and s.count(":") == 3)
        # Format 2: user:pass@host:port
        is_at_format = ("@" in s and s.count(":") >= 2)
        # Format 3: host:port
        is_simple_host_port = (":" in s and s.count(":") == 1)

        if not (is_colon_format or is_at_format or is_simple_host_port):
            raise ValueError("Invalid RESIDENTIAL_PROXY format. Expected: http://user:pass@host:port or host:port:user:pass")

    return True
