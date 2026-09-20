from app.brains.base import Brain
from app.config import Settings


def build_brain(settings: Settings) -> Brain | None:
    """The configured brain, or None if no LLM is available.

    With no brain the API still answers /health; the browser then falls back to
    its built-in local runtime.
    """
    if not settings.gemini_api_key:
        return None

    from google import genai

    from app.brains.gemini import GeminiBrain

    return GeminiBrain(
        client=genai.Client(api_key=settings.gemini_api_key),
        model=settings.gemini_model,
        thinking_budget=settings.gemini_thinking_budget,
    )
