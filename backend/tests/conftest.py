import os

# Before anything imports app.main, which builds a default app at import time:
# tests never touch a real database file.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["WORKFLOW_SCHEDULER_ENABLED"] = "false"  # never ticks inside a test; tests that need it build one
os.environ["AUTH_REQUIRED"] = "false"  # tests/test_auth.py switches it on explicitly
