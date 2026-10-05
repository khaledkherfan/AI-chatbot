"""
Configuration file for the University Assistant system.

SYSTEM ARCHITECTURE:
- Embeddings + Cosine Similarity for alias matching
- ChatGPT Web Search for data extraction (NO manual scraping)
- Redis for caching JSON datasets and alias embeddings
"""
import os
from dotenv import load_dotenv

# Load .env from the project root (this file's directory), not the shell CWD,
# so OPENAI_API_KEY and other vars work when the server is started from elsewhere.
_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_CONFIG_DIR, ".env"), encoding="utf-8-sig")


def _sanitize_openai_env() -> None:
    """
    Remove empty or invalid OPENAI_BASE_URL / OPENAI_API_BASE from the process env.
    A blank OPENAI_BASE_URL= line in .env breaks the OpenAI SDK (Connection error).
    """
    for name in ("OPENAI_BASE_URL", "OPENAI_API_BASE"):
        val = (os.environ.get(name) or "").strip()
        if not val or not val.lower().startswith(("http://", "https://")):
            os.environ.pop(name, None)


_sanitize_openai_env()

# Official OpenAI API (chat, vision, embeddings, PDF extraction)
OPENAI_API_BASE = "https://api.openai.com/v1"

# Redis Configuration
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_DB = int(os.getenv('REDIS_DB', 0))
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', None)

# OpenAI — single provider (set OPENAI_API_KEY in .env)
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()

# Chat, vision, extraction, planner (gpt-4o supports vision)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Max decoded image size for /query/stream uploads (bytes)
MAX_IMAGE_UPLOAD_BYTES = int(os.getenv("MAX_IMAGE_UPLOAD_BYTES", str(4 * 1024 * 1024)))

ALLOWED_IMAGE_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    }
)

# Suggested questions shown on the assistant chat page
FAQ_SUGGESTIONS = [
    "What are the main registration deadlines this semester?",
    "How do I register for courses online?",
    "What are undergraduate tuition fees?",
    "Where can I find the academic calendar?",
    "What programs does the Faculty of Information Technology offer?",
]

# Embeddings model for cosine similarity (OpenAI)
EMBEDDINGS_MODEL = os.getenv('EMBEDDINGS_MODEL', 'text-embedding-3-small')

# Cosine similarity threshold for confident matches
# If score >= threshold, use the match directly
# If score < threshold, ask ChatGPT to validate
SIMILARITY_THRESHOLD = float(os.getenv('SIMILARITY_THRESHOLD', '0.70'))

# Minimum similarity to consider as a candidate
MIN_SIMILARITY = float(os.getenv('MIN_SIMILARITY', '0.50'))

# Gate below which an uncertain match is sent to LLM for validation
UNCERTAINTY_GATE = float(os.getenv('UNCERTAINTY_GATE', '0.50'))

# Number of top candidates to pass to LLM for alias validation
EMBEDDING_MATCH_TOP_K = int(os.getenv('EMBEDDING_MATCH_TOP_K', '5'))

# Seconds to keep the in-process embedding corpus cache valid (avoids O(n) Redis scan per query)
EMBEDDING_CACHE_TTL = int(os.getenv('EMBEDDING_CACHE_TTL', '60'))

# Seconds to cache analytics payload in memory (reduces repeated JSONL reads on admin requests)
ANALYTICS_CACHE_TTL = int(os.getenv('ANALYTICS_CACHE_TTL', '30'))

# PDF extraction LLM parameters
PDF_MAX_CHARS       = int(os.getenv('PDF_MAX_CHARS', '32000'))
PDF_LLM_MAX_TOKENS  = int(os.getenv('PDF_LLM_MAX_TOKENS', '4000'))
PDF_LLM_TEMPERATURE = float(os.getenv('PDF_LLM_TEMPERATURE', '0.1'))

# Confidence scores returned by the fallback alias matcher
ALIAS_FALLBACK_CONFIDENCE_RESOLVED = float(os.getenv('ALIAS_FALLBACK_CONFIDENCE_RESOLVED', '0.7'))
ALIAS_FALLBACK_CONFIDENCE_DATA     = float(os.getenv('ALIAS_FALLBACK_CONFIDENCE_DATA', '0.6'))

# API retry settings
MAX_RETRIES = int(os.getenv('MAX_RETRIES', '3'))
RETRY_DELAY = float(os.getenv('RETRY_DELAY', '1.0'))

# Server Configuration
SERVER_HOST = os.getenv('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.getenv('SERVER_PORT', 8000))

# Resources file path
RESOURCES_FILE = os.getenv('RESOURCES_FILE', 'resources.json')

# Cache TTL (Time To Live) in seconds
CACHE_TTL = int(os.getenv('CACHE_TTL', 86400))  # 24 hours default

# Academic calendar page (structured events + reminders feature)
CALENDAR_PAGE_URL = os.getenv(
    'CALENDAR_PAGE_URL',
    'https://www.just.edu.jo/calendar/Pages/default.aspx',
)
# Optional: full Cookie header from browser/curl when the server gets a WAF shell without it.
CALENDAR_REQUEST_COOKIE = (os.getenv("CALENDAR_REQUEST_COOKIE") or "").strip() or None
CALENDAR_REQUEST_REFERER = (os.getenv("CALENDAR_REQUEST_REFERER") or "").strip() or None
# Optional: read HTML from this file path (absolute or relative to project root) instead of HTTP —
# useful with a browser-saved snapshot (e.g. test.html) when cookies expire.
CALENDAR_HTML_FILE = (os.getenv("CALENDAR_HTML_FILE") or "").strip() or None
# How long to keep parsed calendar JSON in Redis (default 7 days)
CALENDAR_CACHE_TTL_SECONDS = int(os.getenv('CALENDAR_CACHE_TTL_SECONDS', str(7 * 86400)))

# Stored reminders per authenticated user (Redis key TTL)
CALENDAR_REMINDERS_TTL_SECONDS = int(os.getenv('CALENDAR_REMINDERS_TTL_SECONDS', str(90 * 86400)))

# Auth (JWT + SQLite) — set JWT_SECRET in production
JWT_SECRET = (os.getenv('JWT_SECRET') or '').strip()
if not JWT_SECRET:
    JWT_SECRET = 'dev-insecure-change-me'
JWT_EXPIRY_DAYS = int(os.getenv('JWT_EXPIRY_DAYS', '30'))
DATABASE_PATH = os.getenv(
    'DATABASE_PATH',
    os.path.join(_CONFIG_DIR, 'instance', 'just_app.db'),
)

# When all extractors + web search fail; must stay in sync with fallback JSON "summary"
EXTRACTION_FAILED_SUMMARY = "We could not extract detailed information at this time."
# Backward compatibility for cached payloads / older code
EXTRACTION_FAILED_SUMMARY_AR = EXTRACTION_FAILED_SUMMARY


def openai_client_kwargs():
    """Arguments for OpenAI() — always uses the official OpenAI API."""
    if not OPENAI_API_KEY:
        return None
    return {"api_key": OPENAI_API_KEY, "base_url": OPENAI_API_BASE}


# Backward-compatible aliases
llm_openai_client_kwargs = openai_client_kwargs
embeddings_openai_client_kwargs = openai_client_kwargs
