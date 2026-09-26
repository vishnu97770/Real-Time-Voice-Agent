import logging

FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# These log full request URLs at INFO, and provider URLs carry call ids and phone numbers.
NOISY = ("httpx", "httpcore")


def configure_logging(level: str) -> None:
    """Readable console logging for the whole process. Safe to call more than once."""
    resolved = logging.getLevelName(level.upper())

    if not isinstance(resolved, int):
        raise ValueError(f"Invalid LOG_LEVEL: {level!r}")

    # A no-op when the host (a test runner, a server) has already set up the root logger.
    logging.basicConfig(level=resolved, format=FORMAT)
    logging.getLogger("voice_agent").setLevel(resolved)

    for name in NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
