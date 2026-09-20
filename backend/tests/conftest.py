import os

# Before anything imports app.main, which builds a default app at import time:
# tests never touch a real database file.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["AUTH_REQUIRED"] = "false"  # tests/test_auth.py switches it on explicitly
