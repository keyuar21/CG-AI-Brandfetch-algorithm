"""LangChain LLM with automatic provider fallback (Groq -> OpenAI -> Gemini by default)."""
import json
import re
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage

from . import config


def _build(provider: str):
    if provider in ("groq", "groq_small") and config.GROQ_API_KEY:
        from langchain_groq import ChatGroq
        model = config.GROQ_MODEL if provider == "groq" else config.GROQ_FALLBACK_MODEL
        return ChatGroq(model=model, api_key=config.GROQ_API_KEY, temperature=0.4, max_retries=1, timeout=90)
    if provider == "openai" and config.OPENAI_API_KEY:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=config.OPENAI_MODEL, api_key=config.OPENAI_API_KEY, temperature=0.4, max_retries=1, timeout=90)
    if provider == "gemini" and config.GOOGLE_API_KEY:
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=config.GEMINI_MODEL, google_api_key=config.GOOGLE_API_KEY, temperature=0.4, max_retries=1)
    return None


@lru_cache(maxsize=1)
def chain():
    models = [(p, m) for p in config.LLM_ORDER if (m := _build(p))]
    if not models:
        raise RuntimeError("No LLM provider configured")
    return models


def ask(system: str, user: str) -> tuple[str, str]:
    """Return (text, provider_label). Tries each provider in order."""
    errors = []
    for provider, model in chain():
        try:
            res = model.invoke([SystemMessage(system), HumanMessage(user)])
            label = {"groq": config.GROQ_MODEL, "groq_small": config.GROQ_FALLBACK_MODEL,
                     "openai": config.OPENAI_MODEL, "gemini": config.GEMINI_MODEL}[provider]
            return _text(res.content), f"{provider}:{label}"
        except Exception as e:  # quota / auth / network — try the next provider
            errors.append(f"{provider}: {str(e)[:120]}")
    raise RuntimeError("All LLM providers failed: " + " | ".join(errors))


def _text(content) -> str:
    # Gemini/OpenAI can return a list of content blocks; keep only the text parts.
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)


def parse_json(text: str):
    text = re.sub(r"```(?:json)?", "", text)
    for open_, close in (("[", "]"), ("{", "}")):
        s, e = text.find(open_), text.rfind(close)
        if s != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except json.JSONDecodeError:
                continue
    return None
