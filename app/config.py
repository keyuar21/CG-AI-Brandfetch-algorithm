import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip().strip('"')


GOOGLE_API_KEY = env("GOOGLE_API_KEY")
OPENAI_API_KEY = env("OPENAI_API_KEY")
GROQ_API_KEY = env("GROQ_API_KEY")
APOLLO_API_KEY = env("APOLLO_API_KEY") or env("APPOLO_API_KEY")
BRANDFETCH_CLIENT_ID = env("BRANDFETCH_CLIENT_ID", "1idSD5yNUdB2DUhTtKo")
SERPAPI_API_KEY = env("SERPAPI_API_KEY")
BUILTWITH_API_KEY = env("BUILTWITH_API_KEY")

SERVICE_ACCOUNT_JSON = str(ROOT / env("GOOGLE_SERVICE_ACCOUNT_JSON", "credentials.json"))
SPREADSHEET_URL = env("SPREADSHEET_URL")
RESULTS_TAB = env("RESULTS_TAB", "tool_search")

# LLM provider order for the LangChain fallback chain (first that works wins).
LLM_ORDER = [p.strip() for p in env("LLM_ORDER", "groq,groq_small,gemini,openai").split(",") if p.strip()]
GROQ_MODEL = env("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_FALLBACK_MODEL = env("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b")  # separate daily quota
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-4.1-mini")
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-3.5-flash")
# Live web search through Groq; uses lots of tokens (free tier: 200k/day), so off by default.
GROQ_WEB_SEARCH = env("GROQ_WEB_SEARCH", "0") == "1"

# Outreach sender details used in AI drafts.
SENDER_NAME = env("SENDER_NAME", "Partnerships Team")
SENDER_COMPANY = "CouponzGuru"
SENDER_WEBSITE = "https://www.couponzguru.com"

# Optional SMTP so the "Send" button can send directly instead of opening Gmail.
SMTP_HOST = env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_USER = env("SMTP_USER")
SMTP_PASSWORD = env("SMTP_PASSWORD")

COUNTRIES = ["India", "USA", "UAE", "Singapore", "Malaysia", "Philippines"]
COUNTRY_GL = {"India": "in", "USA": "us", "UAE": "ae", "Singapore": "sg", "Malaysia": "my", "Philippines": "ph"}

CATEGORIES = [
    "Fashion & Apparel", "Beauty & Personal Care", "Electronics & Gadgets", "Travel & Hotels",
    "Food & Dining", "Grocery", "Health & Pharmacy", "Home & Furniture", "Jewelry & Watches",
    "Kids & Baby", "Sports & Fitness", "Education & Courses", "Finance & Fintech",
    "Entertainment & Streaming", "Automotive", "Pets", "Software & SaaS", "Footwear",
    "Eyewear", "Gifts & Flowers",
]
