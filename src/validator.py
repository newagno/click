import os
import re

def validate_config():
    """Validates required environment variables."""
    email = os.getenv("INCRYPTED_EMAIL")
    if not email or not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        raise ValueError(f"Invalid or missing INCRYPTED_EMAIL: {email}")
    
    proxy = os.getenv("RESIDENTIAL_PROXY")
    # Proxy is optional, but if provided, it must match basic format
    if proxy and not re.match(r'^(https?://)?[^:]+:[^@]+@[^:]+:\d+$', proxy):
        raise ValueError("Invalid RESIDENTIAL_PROXY format. Expected: http://user:pass@host:port")
    
    if not os.getenv("GITHUB_TOKEN") and not os.getenv("GH_PAT"):
        print("WARNING: Neither GITHUB_TOKEN nor GH_PAT is set. State will not be saved to Gist!")
        
    return True
