from collections.abc import AsyncIterator, Callable

from app.brains.base import BrainContext, BrainEvent
from app.profiles import get_profile
from app.session import Session, create_session, open_call, process_turn


class ScriptedBrain:
    """A brain whose behavior is a function, so tests decide what the 'LLM' does."""

    name = "scripted"

    def __init__(self, script: Callable[[BrainContext], AsyncIterator[BrainEvent]] | None = None):
        self.script = script
        self.calls: list[BrainContext] = []

    async def respond(self, context: BrainContext) -> AsyncIterator[BrainEvent]:
        self.calls.append(context)

        if self.script is None:
            return

        async for event in self.script(context):
            yield event


def start(profile_id: str, script=None, timeout: float = 5.0) -> tuple[Session, ScriptedBrain]:
    brain = ScriptedBrain(script)
    session = create_session(get_profile(profile_id), brain, timeout)
    open_call(session)

    return session, brain


async def say(session: Session, text: str) -> list[dict]:
    return [event async for event in process_turn(session, text)]


def spoken(events: list[dict]) -> str:
    return " ".join(event["text"] for event in events if event["type"] == "sentence")


def done(events: list[dict]) -> dict:
    return events[-1]


def migrate(url: str, revision: str = "head") -> None:
    """Build a file database's schema the way production does: with Alembic."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["configure_logger"] = False  # leave pytest's log capture alone
    command.upgrade(config, revision)
