"""Domain models. Import every model module here: Alembic and Base.metadata only know about
models whose module has been imported."""

from app.models.agent import Agent
from app.models.base import Base, JsonType
from app.models.contact import Contact
from app.models.organization import Organization
from app.models.workflow import Workflow

__all__ = ["Agent", "Base", "Contact", "JsonType", "Organization", "Workflow"]

# Organization.users refers to UserRow, which lives in app.db (that module imports this one, so
# the import must come last). This makes `import app.models` alone enough to configure every mapper.
import app.db  # noqa: E402, F401
