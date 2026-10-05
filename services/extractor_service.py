"""
Extractor service for JUST University Assistant.
Data extraction for web pages and PDFs with retries, caching, and structured output.

ROBUST extraction service with:
- Advanced PDF extraction with retry and fallback
- Smart URL handling with content-type detection
- Multi-strategy data extraction
- Comprehensive error recovery
- Arabic text optimization
"""
import json
import os
import io
import re
import time
import hashlib
from collections import OrderedDict
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse, unquote
from config import (
    RESOURCES_FILE, EXTRACTION_FAILED_SUMMARY, EXTRACTION_FAILED_SUMMARY_AR,
    PDF_MAX_CHARS, PDF_LLM_MAX_TOKENS, PDF_LLM_TEMPERATURE,
)
from logger import get_logger

# PDF and HTTP imports with graceful fallback
try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    HTTP_SUPPORT = True
except ImportError:
    HTTP_SUPPORT = False

try:
    from PyPDF2 import PdfReader
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

try:
    import pdfplumber
    PDFPLUMBER_SUPPORT = True
except ImportError:
    PDFPLUMBER_SUPPORT = False

from html.parser import HTMLParser


class _HTMLToText(HTMLParser):
    """Strip HTML to plain text (keeps noscript fallback text on WAF pages)."""
    _SKIP = frozenset({"script", "style", "svg", "iframe"})

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._chunks: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        s = (data or "").strip()
        if s:
            self._chunks.append(s)

    def text(self) -> str:
        return "\n".join(self._chunks)


# Common canonical-key / query topics → resources.json keys
_RESOURCE_ALIASES = {
    "fees": "Fees",
    "tuition": "Fees",
    "tuition_fees": "Fees",
    "fee": "Fees",
    "scholarship": "Scholarships",
    "scholarships": "Scholarships",
    "rewards": "Rewards",
    "grants": "Rewards",
    "mukafaat": "Rewards",
    "academic_calendar": "Academic_Calendar",
    "calendar": "Academic_Calendar",
    "registration": "Student_Registration_System",
    "course_registration": "Student_Registration_System",
    "student_services": "Student_Services",
    "admission": "Admission_Home",
    "undergraduate_admission": "Admission_Undergraduate",
    "graduate_admission": "Admission_Graduate",
    "library": "Library_Home",
    "software_engineering": "Software_Engineering_Plan",
    "cybersecurity": "Cybersecurity_Plan",
    "computer_science": "Computer_Science_Plan",
    "artificial_intelligence": "Artificial_Intelligence_Plan",
    "ai": "Artificial_Intelligence_Plan",
    "robotics": "Robotics_Plan",
    "robo": "Robotics_Plan",
    "computer_engineering": "CPE_Plan",
    "cpe": "CPE_Plan",
    "faculty_it": "Faculty_IT",
    "faculties": "Faculties_and_Departments",
}


class _LRUCache(OrderedDict):
    """Simple bounded LRU cache backed by OrderedDict."""

    def __init__(self, maxsize: int = 50):
        super().__init__()
        self.maxsize = maxsize

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            self.popitem(last=False)

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value


class ExtractorService:
    """
    ROBUST data extraction service for JUST University Assistant.

    Features:
    - Multi-strategy extraction (PDF, Web, Search)
    - Automatic retry with exponential backoff
    - Content-type detection and smart routing
    - Arabic text optimization
    - Comprehensive error handling
    - Performance caching
    """

    # Configuration
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0  # seconds
    REQUEST_TIMEOUT = 45  # seconds
    MAX_PDF_SIZE = 50 * 1024 * 1024  # 50 MB
    MAX_PDF_PAGES = 100
    MAX_TEXT_LENGTH = 50000  # characters
    MAX_PDF_CACHE_ENTRIES = 50  # bounded to prevent unbounded memory growth

    # HTTP Session for connection pooling
    _session = None

    def __init__(self, openai_service):
        """
        Initialize extractor service with OpenAI integration.

        Args:
            openai_service: Instance of OpenAIService for AI operations
        """
        self.openai_service = openai_service
        self.logger = get_logger()
        self.resources = self._load_resources()
        self._pdf_cache: _LRUCache = _LRUCache(maxsize=self.MAX_PDF_CACHE_ENTRIES)
        self._init_http_session()
        
        self.logger.info(
            f"ExtractorService initialized | "
            f"pdfplumber: {PDFPLUMBER_SUPPORT} | PyPDF2: {PDF_SUPPORT} | HTTP: {HTTP_SUPPORT}"
        )
    
    def _init_http_session(self):
        """Initialize HTTP session with retry strategy."""
        if not HTTP_SUPPORT:
            return
        
        if ExtractorService._session is None:
            ExtractorService._session = requests.Session()
            
            # Configure retry strategy
            retry_strategy = Retry(
                total=self.MAX_RETRIES,
                backoff_factor=self.RETRY_DELAY,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["HEAD", "GET", "OPTIONS"]
            )
            
            adapter = HTTPAdapter(max_retries=retry_strategy)
            ExtractorService._session.mount("https://", adapter)
            ExtractorService._session.mount("http://", adapter)
            
            # Set default headers
            ExtractorService._session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8',
                'Accept-Language': 'ar,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
            })
    
    def _load_resources(self) -> Dict[str, str]:
        """Load resources from JSON file with validation."""
        try:
            if os.path.exists(RESOURCES_FILE):
                with open(RESOURCES_FILE, 'r', encoding='utf-8') as f:
                    resources = json.load(f)
                    
                    # Validate URLs
                    valid_resources = {}
                    for key, url in resources.items():
                        if self._is_valid_url(url):
                            valid_resources[key] = url
                        else:
                            self.logger.warning(f"Invalid URL for {key}: {url}")
                    
                    self.logger.info(f"Loaded {len(valid_resources)} valid resources")
                    return valid_resources
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in resources.json: {e}")
        except Exception as e:
            self.logger.error(f"Error loading resources.json: {e}")
        
        return {}
    
    # ========================================
    # URL UTILITIES
    # ========================================
    
    def _normalize_url(self, url: Optional[str]) -> Optional[str]:
        """
        Normalize a URL for safer comparisons/extraction.
        
        - Trims whitespace
        - Removes surrounding quotes
        - Preserves percent-encoding (no lossy decode/re-encode)
        - Removes trailing whitespace-only fragments
        """
        if not url or not isinstance(url, str):
            return None
        u = url.strip().strip('"').strip("'").strip()
        return u or None
    
    def _decoded_url_for_detection(self, url: str) -> str:
        """Decode URL for detection only (do not use for requests)."""
        try:
            return unquote(url or "")
        except Exception:
            return url or ""
    
    def _is_valid_url(self, url: str) -> bool:
        """Validate URL format."""
        url = self._normalize_url(url)
        if not url:
            return False
        try:
            result = urlparse(url)
            return all([result.scheme in ('http', 'https'), result.netloc])
        except Exception:
            return False
    
    def _is_pdf_url(self, url: str) -> bool:
        """
        Check if URL points to a PDF file.
        Uses multiple detection methods.
        """
        url = self._normalize_url(url)
        if not url:
            return False
        
        url_lower = url.lower()
        decoded_lower = self._decoded_url_for_detection(url).lower()
        
        # Method 1: Check file extension
        if url_lower.endswith('.pdf') or decoded_lower.endswith('.pdf'):
            return True
        
        # Method 2: Check for PDF in URL path
        parsed = urlparse(url_lower)
        if '.pdf' in parsed.path:
            return True
        
        parsed_decoded = urlparse(decoded_lower)
        if '.pdf' in parsed_decoded.path:
            return True
        
        # Method 3: Check URL parameters
        if 'pdf' in parsed.query.lower():
            return True
        
        return False
    
    def _detect_content_type(self, url: str) -> str:
        """
        Detect content type of URL using HEAD request.
        
        Returns:
            'pdf', 'html', 'unknown'
        """
        if not HTTP_SUPPORT:
            return 'unknown'
        
        try:
            response = self._session.head(url, timeout=10, allow_redirects=True)
            content_type = response.headers.get('Content-Type', '').lower()
            
            if 'pdf' in content_type:
                return 'pdf'
            elif 'html' in content_type or 'text' in content_type:
                return 'html'
            else:
                return 'unknown'
        except Exception as e:
            self.logger.debug(f"Content-type detection failed: {e}")
            return 'unknown'
    
    def _get_cache_key(self, url: str) -> str:
        """Generate cache key for URL."""
        return hashlib.md5(url.encode()).hexdigest()
    
    # ========================================
    # ROBUST PDF EXTRACTION
    # ========================================
    
    def _download_with_retry(self, url: str, max_retries: int = None) -> Optional[bytes]:
        """
        Download content with retry and error handling.
        
        Args:
            url: URL to download
            max_retries: Maximum retry attempts
            
        Returns:
            Content bytes or None
        """
        if not HTTP_SUPPORT:
            self.logger.error("HTTP support not available")
            return None
        
        retries = max_retries or self.MAX_RETRIES
        last_error = None
        
        for attempt in range(retries):
            try:
                self.logger.debug(f"Download attempt {attempt + 1}/{retries}: {url[:80]}...")
                
                response = self._session.get(
                    url,
                    timeout=self.REQUEST_TIMEOUT,
                    allow_redirects=True,
                    stream=True
                )
                response.raise_for_status()
                
                # Check content length
                content_length = int(response.headers.get('Content-Length', 0))
                if content_length > self.MAX_PDF_SIZE:
                    self.logger.warning(f"File too large: {content_length} bytes")
                    return None
                
                # Download content
                content = response.content
                self.logger.info(f"Downloaded {len(content)} bytes from {url[:50]}...")
                return content
                
            except requests.exceptions.Timeout:
                last_error = "Connection timeout"
                self.logger.warning(f"Timeout on attempt {attempt + 1}")
            except requests.exceptions.HTTPError as e:
                last_error = f"HTTP error: {e.response.status_code}"
                self.logger.warning(f"HTTP error on attempt {attempt + 1}: {e}")
                if e.response.status_code in [403, 404]:
                    break  # Don't retry on these errors
            except requests.exceptions.ConnectionError as e:
                last_error = "Connection failed"
                self.logger.warning(f"Connection error on attempt {attempt + 1}: {e}")
            except Exception as e:
                last_error = str(e)
                self.logger.warning(f"Download error on attempt {attempt + 1}: {e}")
            
            # Wait before retry with exponential backoff
            if attempt < retries - 1:
                wait_time = self.RETRY_DELAY * (2 ** attempt)
                self.logger.debug(f"Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
        
        self.logger.error(f"Download failed after {retries} attempts: {last_error}")
        return None
    
    def _extract_pdf_text_pdfplumber(self, content: bytes) -> Optional[str]:
        """
        Extract text + tables from PDF using pdfplumber.
        
        pdfplumber preserves table row/column structure by rendering detected
        tables as pipe-delimited markdown-style grids before the page prose.
        Falls back to plain text for pages with no tables.
        
        Returns:
            Formatted text with tables or None
        """
        if not PDFPLUMBER_SUPPORT:
            return None
        try:
            pdf_file = io.BytesIO(content)
            text_parts = []
            with pdfplumber.open(pdf_file) as pdf:
                total_pages = min(len(pdf.pages), self.MAX_PDF_PAGES)
                self.logger.info(f"pdfplumber: processing {total_pages} pages…")
                for page_num, page in enumerate(pdf.pages[:total_pages]):
                    page_section = [f"=== Page {page_num + 1} ==="]
                    tables = page.extract_tables()
                    if tables:
                        for tbl_idx, table in enumerate(tables):
                            if not table:
                                continue
                            rows = []
                            for row in table:
                                # Replace None cells with empty string
                                cleaned_row = [
                                    (cell or "").strip().replace("\n", " ")
                                    for cell in row
                                ]
                                rows.append(" | ".join(cleaned_row))
                            page_section.append(
                                f"[Table {tbl_idx + 1}]\n" + "\n".join(rows)
                            )
                    # Also grab prose outside tables
                    prose = page.extract_text(x_tolerance=3, y_tolerance=3)
                    if prose:
                        cleaned_prose = self._clean_pdf_text(prose)
                        if cleaned_prose.strip():
                            page_section.append(cleaned_prose)
                    combined = "\n".join(page_section)
                    if combined.strip():
                        text_parts.append(combined)
            if not text_parts:
                return None
            full_text = "\n\n".join(text_parts)
            if len(full_text) > self.MAX_TEXT_LENGTH:
                full_text = (
                    full_text[:self.MAX_TEXT_LENGTH]
                    + "\n\n... [Content truncated — file is very long]"
                )
            self.logger.info(
                f"✅ pdfplumber extracted {len(full_text)} chars "
                f"from {len(text_parts)} pages"
            )
            return full_text
        except Exception as e:
            self.logger.warning(f"pdfplumber extraction failed: {e}")
            return None

    def _extract_pdf_text(self, url: str) -> Optional[str]:
        """
        Extract text from PDF with robust error handling.
        
        Tries pdfplumber first (preserves table structure), then falls back
        to PyPDF2 for plain-text PDFs or when pdfplumber is unavailable.
        
        Args:
            url: PDF URL
            
        Returns:
            Extracted text or None
        """
        if not PDF_SUPPORT and not PDFPLUMBER_SUPPORT:
            self.logger.warning("No PDF support available. Install pdfplumber or PyPDF2.")
            return None
        
        # Check cache first
        cache_key = self._get_cache_key(url)
        if cache_key in self._pdf_cache:
            self.logger.info(f"Using cached PDF content for: {url[:50]}...")
            return self._pdf_cache[cache_key]
        
        self.logger.info(f"📄 Extracting PDF: {url[:80]}...")
        
        # Download PDF
        content = self._download_with_retry(url)
        if not content:
            return None

        # ── Strategy A: pdfplumber (preserves table row/column structure) ──
        full_text = None
        if PDFPLUMBER_SUPPORT:
            full_text = self._extract_pdf_text_pdfplumber(content)

        # ── Strategy B: PyPDF2 fallback (plain text, no table awareness) ──
        if not full_text and PDF_SUPPORT:
            self.logger.info("Falling back to PyPDF2 text extraction…")
            try:
                pdf_file = io.BytesIO(content)
                reader = PdfReader(pdf_file)

                total_pages = min(len(reader.pages), self.MAX_PDF_PAGES)
                self.logger.info(f"Processing {total_pages} pages with PyPDF2…")

                text_parts = []
                failed_pages = []

                for page_num in range(total_pages):
                    try:
                        page = reader.pages[page_num]
                        page_text = page.extract_text()
                        if page_text:
                            cleaned_text = self._clean_pdf_text(page_text)
                            if cleaned_text.strip():
                                text_parts.append(
                                    f"=== Page {page_num + 1} ===\n{cleaned_text}"
                                )
                    except Exception as e:
                        failed_pages.append(page_num + 1)
                        self.logger.debug(f"Failed to extract page {page_num + 1}: {e}")

                if failed_pages:
                    self.logger.warning(f"Failed to extract pages: {failed_pages}")

                if text_parts:
                    full_text = "\n\n".join(text_parts)
                    if len(full_text) > self.MAX_TEXT_LENGTH:
                        full_text = (
                            full_text[:self.MAX_TEXT_LENGTH]
                            + "\n\n... [Content truncated — file is very long]"
                        )
                    self.logger.info(
                        f"✅ Extracted {len(full_text)} chars from {len(text_parts)} pages"
                    )
            except Exception as e:
                self.logger.error(f"PDF parsing failed: {e}")

        if not full_text:
            self.logger.warning("No text extracted from PDF (might be scanned/image-based)")
            return None

        # Cache the result
        self._pdf_cache[cache_key] = full_text
        return full_text
    
    def _clean_pdf_text(self, text: str) -> str:
        """
        Clean and normalize PDF extracted text.
        
        Handles:
        - Arabic text normalization
        - Extra whitespace
        - Special characters
        - Line breaks
        """
        if not text:
            return ""
        
        # Remove null characters
        text = text.replace('\x00', '')
        
        # Keep original Arabic for accuracy — only clean whitespace and structural artifacts below
        
        # Fix common PDF extraction issues
        # IMPORTANT: use [ \t]+ (not \s+) so newlines are preserved —
        # newlines are the only structural separator left after PyPDF2 extraction
        # and collapsing them destroys every table row/column relationship.
        text = re.sub(r'[ \t]+', ' ', text)   # Collapse runs of spaces/tabs only
        text = re.sub(r'\n{3,}', '\n\n', text)  # Trim excess blank lines
        
        # Remove excessive punctuation
        text = re.sub(r'\.{3,}', '...', text)
        text = re.sub(r'-{3,}', '---', text)
        
        # Clean up
        text = text.strip()
        
        return text
    
    def _summarize_pdf_content(self, pdf_text: str, query: str, url: str) -> Optional[Dict[str, Any]]:
        """
        Use AI to analyze and structure PDF content.
        
        Args:
            pdf_text: Extracted PDF text
            query: User's question
            url: Source URL
            
        Returns:
            Structured data dictionary
        """
        if not self.openai_service.is_configured():
            self.logger.warning("OpenAI not configured for PDF summarization")
            return self._create_basic_pdf_summary(pdf_text, url)
        
        try:
            # Prepare text (limit for API); configured via PDF_MAX_CHARS env var
            truncated = len(pdf_text) > PDF_MAX_CHARS
            text_to_analyze = pdf_text[:PDF_MAX_CHARS] if truncated else pdf_text
            
            prompt = f"""You are a document analyst for the Jordan University of Science and Technology (JUST).

The following text was extracted from an official PDF using pdfplumber:
- Tables appear as "value1 | value2 | value3" (pipe-separated columns), one line = one table row.
- Each table header is prefixed with [Table N].
- Non-table prose follows immediately after.

{text_to_analyze}

{'Note: content was truncated because the file is very long.' if truncated else ''}

Student question: "{query}"

Mandatory distinction between two different column types in "fees and admission" tables (e.g. Fees_Regular):
1) **Admission requirements / minimum admission GPA**: values shown as percentages (e.g. 65% or 85%) — these are **NOT fees** and must NOT be recorded as JOD amounts.
2) **Actual fees**: usually under headings such as "credit hour fee" or program columns (regular / parallel / …) and are **numbers in Jordanian Dinar (JOD / دينار)** per credit hour or term/admission/application fees.

Fee extraction rules:
- For each program: fill `min_admission_percent` only if an explicit admission percentage appears next to the program (e.g. 85%).
- Fill `credit_hour_fee_*_jod` only from **credit-hour fee** columns (JOD numbers). If the text shows multiple columns (regular, parallel Jordan HS, parallel other certificate), map them accurately into the separate fields below.
- **Do not** put an admission percentage (%) into any field named fee, jod, or amount.
- If a column is ambiguous, leave the field empty or null and explain in `fees.notes` instead of guessing.

Your tasks:
1. Identify the document type (fee table, study plan, policy, other).
2. Extract data relevant to the question, correctly separating admission vs fees.
3. In `summary`, explicitly state that percentages next to "admission GPA" are not fees when such a table exists.

Return JSON with the following fields (fill only what applies; use null for non-applicable):
{{
    "title": "Document title",
    "document_type": "fee_table|study_plan|policy|other",
    "summary": "Summary distinguishing admission (percentages) vs fees (JOD) if the table exists",
    "study_plan": null,
    "fees": {{
        "currency": "JOD",
        "programs": [
            {{
                "program_name_ar": "Program name as in the table (may be Arabic)",
                "min_admission_percent": "85% or null",
                "total_credit_hours": "text or null",
                "study_duration_years": "text or null",
                "credit_hour_fee_regular_jod": "number as string or null",
                "credit_hour_fee_parallel_jordan_hs_jod": "number as string or null",
                "credit_hour_fee_parallel_other_cert_jod": "number as string or null"
            }}
        ],
        "fee_items": [{{ "item": "Administrative item e.g. application or admission fee", "amount": "Amount in JOD as string" }}],
        "notes": ["Notes about ambiguous columns or naming differences"]
    }},
    "key_points": [],
    "requirements": [],
    "important_dates": [],
    "contact_info": {{ "phone": "", "email": "", "office": "" }},
    "source_url": "{url}"
}}"""

            from openai import OpenAI
            from config import OPENAI_MODEL, openai_client_kwargs

            kw = openai_client_kwargs()
            if not kw:
                return self._create_basic_pdf_summary(pdf_text, url)
            client = OpenAI(**kw)
            pdf_model = OPENAI_MODEL
            self.logger.info(f"PDF structured extraction model: {pdf_model}")
            response = client.chat.completions.create(
                model=pdf_model,
                messages=[
                    {
                        "role": "system",
                        "content": """You are a PDF table analyst for Jordan University of Science and Technology (JUST).
Golden rule: percentages (%) under admission/GPA headings are NOT fees. Fees are JOD amounts under credit-hour fee or administrative fee columns.
Do not mix the two. Do not guess uncertain columns — use null and explain in notes."""
                    },
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_tokens=PDF_LLM_MAX_TOKENS,
                temperature=PDF_LLM_TEMPERATURE,
            )
            
            data = json.loads(response.choices[0].message.content)
            data['url'] = url
            data['source_type'] = 'pdf'
            
            self.logger.info("✅ PDF content analyzed successfully")
            return data
            
        except Exception as e:
            self.logger.error(f"PDF analysis failed: {e}")
            return self._create_basic_pdf_summary(pdf_text, url)
    
    def _create_basic_pdf_summary(self, pdf_text: str, url: str) -> Dict[str, Any]:
        """Create basic summary without AI."""
        return {
            "title": "PDF content",
            "summary": pdf_text[:1000] + "..." if len(pdf_text) > 1000 else pdf_text,
            "url": url,
            "source_type": "pdf",
            "note": "Text extracted without AI analysis",
        }

    def _normalize_study_plan_pdf_text(self, text: str) -> str:
        """
        Fix RTL-mangled Latin course codes in JUST Arabic study-plan PDFs.
        pdfplumber often yields fragments like 'ب نه112' instead of 'SE112'.
        """
        if not text:
            return text

        rules = [
            (r"ب\s*نه\s*(\d{3})", r"SE\1"),
            (r"(\d{3})\s*ب\s*نه", r"SE\1"),
            (r"ح\s*ع\s*(\d{3})", r"CS\1"),
            (r"(\d{3})\s*ح\s*ع", r"CS\1"),
            (r"ب\s*ع\s*(\d{3})", r"CE\1"),
            (r"(\d{3})\s*ب\s*ع", r"CE\1"),
            (r"كه\s*(\d{3})", r"EE\1"),
            (r"(\d{3})\s*كه", r"EE\1"),
            (r"(\d{3})\s*ر\b", r"R\1"),
            (r"\bر\s*(\d{3})", r"R\1"),
            (r"140\s*ر\b", r"R140"),
            (r"\bر\s*140\b", r"R140"),
            (r"(SE|CS|CE|EE|R)(\d{3})أ\s*ع", r"\1\2"),
            (r"(SE|CS|CE|EE|R)(\d{3})\s*أ\s*ع", r"\1\2"),
            (r"\bCSCS(\d{3})\b", r"CS\1"),
            (r"\bSESE(\d{3})\b", r"SE\1"),
        ]
        out = text
        for pattern, repl in rules:
            out = re.sub(pattern, repl, out)
        return out

    def _parse_study_plan_from_tables(
        self, text: str, *, program_id: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        Parse course rows from normalized pipe/table PDF text without an LLM.
        Returns None when too few courses are detected.
        """
        normalized = self._normalize_study_plan_pdf_text(text)
        code_re = re.compile(
            r"\b(?:SE|CS|CE|EE|R|FE|NE|CYBR|AI|ROBO|IT)\d{3}\b"
        )
        prereq_code_re = re.compile(
            r"\b(?:SE|CS|CE|EE|R|FE|NE|CYBR|AI|ROBO|IT)\d{3}\b"
        )
        row_patterns = [
            re.compile(
                r"\|\s*(?:\d+\s*\|[^|]*){0,3}\|\s*"
                r"(?P<credits>[1-9])\s*\|\s*(?:\d+\s*\|[^|]*){0,2}\|\s*"
                r"(?P<level>[1-9])\s*\|\s*(?:\d+\s*\|[^|]*){0,2}\|\s*"
                r"(?P<title>[\u0600-\u06FF][^|]{2,120}?)\s*\|\s*"
                r"(?P<code>(?:SE|CS|CE|EE|R|FE|NE|CYBR|AI|ROBO|IT)\d{3})\b"
            ),
            re.compile(
                r"(?P<credits>[1-9])\s+0\s+(?P<level>[1-9])\s+"
                r"(?P<title>[\u0600-\u06FF][\u0600-\u06FF\s،\-/]{2,100}?)\s+"
                r"(?P<code>(?:SE|CS|CE|EE|R|FE|NE|CYBR|AI|ROBO|IT)\d{3})\b"
            ),
        ]

        courses_by_code: Dict[str, Dict[str, Any]] = {}
        for line in normalized.splitlines():
            line = line.strip()
            if not line or line.startswith("===") or line.startswith("[Table"):
                continue
            if not code_re.search(line):
                continue

            parsed_any = False
            for pattern in row_patterns:
                for match in pattern.finditer(line):
                    parsed_any = True
                    code = match.group("code")
                    if code in courses_by_code:
                        continue
                    title = re.sub(r"\s+", " ", match.group("title")).strip(" |")
                    level = int(match.group("level"))
                    chunk_start = max(0, match.start() - 140)
                    prefix = line[chunk_start : match.start()]
                    prereqs = [
                        c for c in prereq_code_re.findall(prefix) if c != code
                    ]
                    courses_by_code[code] = {
                        "code": code,
                        "title_ar": title,
                        "credits": int(match.group("credits")),
                        "prerequisites": list(dict.fromkeys(prereqs)),
                        "recommended_semester": level,
                    }

            if parsed_any:
                continue

            # Fallback: capture code + nearby Arabic title on unstructured lines.
            for code_match in code_re.finditer(line):
                code = code_match.group()
                if code in courses_by_code:
                    continue
                before = line[: code_match.start()]
                title_match = re.search(
                    r"([\u0600-\u06FF][\u0600-\u06FF\s،\-/]{4,80})\s*$", before
                )
                if not title_match:
                    continue
                stats = re.search(r"(\d)\s+0\s+(\d)", before)
                courses_by_code[code] = {
                    "code": code,
                    "title_ar": re.sub(r"\s+", " ", title_match.group(1)).strip(),
                    "credits": int(stats.group(1)) if stats else 3,
                    "prerequisites": list(
                        dict.fromkeys(
                            c
                            for c in prereq_code_re.findall(before)
                            if c != code
                        )
                    ),
                    "recommended_semester": int(stats.group(2))
                    if stats
                    else 1,
                }

        if len(courses_by_code) < 12:
            return None

        by_semester: Dict[int, List[Dict[str, Any]]] = {}
        for course in courses_by_code.values():
            sem = int(course.get("recommended_semester") or 1)
            by_semester.setdefault(sem, []).append(
                {
                    "code": course["code"],
                    "title_ar": course["title_ar"],
                    "credits": course["credits"],
                    "prerequisites": course.get("prerequisites") or [],
                }
            )

        semesters = []
        for sem_num in sorted(by_semester):
            block = by_semester[sem_num]
            semesters.append(
                {
                    "semester_number": sem_num,
                    "semester_label_ar": None,
                    "courses": block,
                    "semester_credits_total": sum(
                        int(c.get("credits") or 0) for c in block
                    ),
                }
            )

        all_courses = list(courses_by_code.values())
        total_credits = sum(int(c.get("credits") or 0) for c in all_courses)
        return {
            "available": True,
            "document_type": "study_plan",
            "program_id": program_id,
            "total_credit_hours": total_credits or None,
            "semesters": semesters,
            "all_courses": all_courses,
            "extraction_notes": [
                "Parsed from PDF tables with RTL course-code normalization (no LLM)."
            ],
        }

    def _flatten_semesters_to_all_courses(
        self, semesters: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Build deduplicated all_courses list from semester blocks."""
        flat: List[Dict[str, Any]] = []
        seen: set = set()
        for sem in semesters:
            if not isinstance(sem, dict):
                continue
            sn = sem.get("semester_number")
            for course in sem.get("courses") or []:
                if not isinstance(course, dict):
                    continue
                code = (course.get("code") or "").strip()
                if not code or code in seen:
                    continue
                seen.add(code)
                row = dict(course)
                row["recommended_semester"] = sn
                flat.append(row)
        return flat

    def _parse_study_plan_json(self, raw: str) -> Dict[str, Any]:
        """Parse LLM JSON; attempt minimal repair when the response was truncated."""
        if not raw or not raw.strip():
            raise json.JSONDecodeError("Empty response", raw or "", 0)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Truncated json_object: drop the broken tail and close open structures.
        trimmed = raw.rstrip()
        if trimmed.endswith(","):
            trimmed = trimmed[:-1]
        for suffix in ("}", "]}", "]}]}"):
            try:
                return json.loads(trimmed + suffix)
            except json.JSONDecodeError:
                continue
        raise json.JSONDecodeError("Could not parse study-plan JSON", raw, 0)

    def extract_study_plan_pdf(
        self,
        url: str,
        *,
        program_id: str = "",
        resource_key: Optional[str] = None,
        program_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Extract official course catalog from a study-plan PDF (planner path).
        Returns structured semesters + flat all_courses list.
        """
        unavailable = {
            "available": False,
            "program_id": program_id,
            "resource_key": resource_key,
            "semesters": [],
            "all_courses": [],
        }

        pdf_text = self._extract_pdf_text(url)
        if not pdf_text:
            unavailable["reason"] = "Could not read text from the study-plan PDF."
            return unavailable

        normalized_text = self._normalize_study_plan_pdf_text(pdf_text)
        table_parsed = self._parse_study_plan_from_tables(
            normalized_text, program_id=program_id
        )
        if table_parsed and table_parsed.get("available"):
            table_parsed["url"] = url
            table_parsed["source_url"] = url
            table_parsed["source_type"] = "pdf"
            table_parsed["resource_key"] = resource_key
            table_parsed["program_id"] = program_id
            self.logger.info(
                f"Study plan parsed from PDF tables: "
                f"{len(table_parsed.get('all_courses') or [])} courses "
                f"from {resource_key or url[:50]}"
            )
            return table_parsed

        if not self.openai_service.is_configured():
            unavailable["reason"] = "OpenAI not configured for study-plan extraction."
            return unavailable

        study_plan_char_limit = max(PDF_MAX_CHARS, 48000)
        truncated = len(normalized_text) > study_plan_char_limit
        text_to_analyze = (
            normalized_text[:study_plan_char_limit]
            if truncated
            else normalized_text
        )
        prog_label = program_name or program_id or "study plan"

        prompt = f"""You are extracting the OFFICIAL academic study plan from a Jordan University of Science and Technology (JUST) PDF.

Program: {prog_label}
PDF URL: {url}

Extracted text (RTL Arabic PDF — Latin course codes are already normalized, e.g. SE112, CS284):
---
{text_to_analyze}
---
{'Note: text was truncated — extract every course visible in the portion above.' if truncated else ''}

Return JSON ONLY with this shape:
{{
  "available": true,
  "document_type": "study_plan",
  "program_name_ar": "name as in PDF or null",
  "program_name_en": "{prog_label}",
  "total_credit_hours": 132,
  "study_duration_years": 4,
  "semesters": [
    {{
      "semester_number": 1,
      "semester_label_ar": "label from PDF or null",
      "courses": [
        {{
          "code": "CS111",
          "title_ar": "Arabic title from PDF",
          "credits": 3,
          "prerequisites": ["CS101"]
        }}
      ],
      "semester_credits_total": 15
    }}
  ],
  "extraction_notes": ["any ambiguity about semester placement"]
}}

Do NOT include an all_courses field — the server builds that from semesters.

Strict rules:
- Copy course codes EXACTLY as shown (Latin letters/digits, e.g. CS111, SE301, CYBR401).
- Copy Arabic course titles faithfully from the PDF.
- Include EVERY course row from the plan — do not skip electives or university requirements.
- Map each course to the semester/level shown in the PDF (semester_number 1–8, or 9 for summer if present).
- prerequisites: list prerequisite codes from the PDF; use [] if none stated.
- Do NOT invent courses not in the PDF text.
- Course codes appear as normalized Latin tokens (SE###, CS###, CE###, EE###, R###).
- If the PDF is unreadable, set available=false and explain in extraction_notes."""

        study_plan_max_tokens = max(PDF_LLM_MAX_TOKENS, 16384)
        compact_prompt = f"""Extract the OFFICIAL JUST study plan courses from this PDF text into compact JSON.

Program: {prog_label}

Text:
---
{text_to_analyze[:24000]}
---

Return JSON ONLY:
{{"available":true,"total_credit_hours":132,"semesters":[{{"semester_number":1,"courses":[{{"code":"CS111","title_ar":"...","credits":3,"prerequisites":[]}}]}}]}}

Rules: every course from the PDF; codes exactly as printed; semester_number 1–8 (9 for summer); prerequisites as code list; no all_courses field; no extra commentary."""

        prompts_to_try = [
            ("full", prompt, study_plan_max_tokens),
            ("compact", compact_prompt, study_plan_max_tokens),
        ]

        try:
            from openai import OpenAI
            from config import OPENAI_MODEL, openai_client_kwargs

            kw = openai_client_kwargs()
            if not kw:
                unavailable["reason"] = "OpenAI client not configured."
                return unavailable

            client = OpenAI(**kw)
            last_error: Optional[Exception] = None

            for attempt_name, user_prompt, max_tokens in prompts_to_try:
                response = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You extract official JUST university study plans into JSON. "
                                "Every course code and title must come from the PDF text. "
                                "Be exhaustive — include all semesters and all courses. "
                                "Return complete valid JSON."
                            ),
                        },
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=max_tokens,
                    temperature=0.1,
                )
                choice = response.choices[0]
                raw = choice.message.content or ""
                finish_reason = getattr(choice, "finish_reason", None)
                if finish_reason == "length":
                    self.logger.warning(
                        f"Study plan LLM response truncated ({attempt_name}); "
                        f"max_tokens={max_tokens}"
                    )
                try:
                    data = self._parse_study_plan_json(raw)
                except json.JSONDecodeError as je:
                    last_error = je
                    self.logger.warning(
                        f"Study plan JSON parse failed ({attempt_name}): {je}"
                    )
                    continue

                if not isinstance(data, dict):
                    last_error = ValueError("Study-plan extraction returned invalid JSON.")
                    continue

                data["url"] = url
                data["source_url"] = url
                data["source_type"] = "pdf"
                data["resource_key"] = resource_key
                data["program_id"] = program_id

                semesters = data.get("semesters")
                all_courses = data.get("all_courses")
                has_courses = (
                    (isinstance(semesters, list) and len(semesters) > 0)
                    or (isinstance(all_courses, list) and len(all_courses) > 0)
                )
                if not data.get("available", True) or not has_courses:
                    data["available"] = False
                    data.setdefault(
                        "reason",
                        "No structured courses could be parsed from the study-plan PDF.",
                    )
                    return data

                data["available"] = True
                if isinstance(semesters, list):
                    data["all_courses"] = self._flatten_semesters_to_all_courses(
                        semesters
                    )
                elif isinstance(all_courses, list):
                    data["all_courses"] = all_courses
                else:
                    data["all_courses"] = []

                self.logger.info(
                    f"Study plan extracted ({attempt_name}): "
                    f"{len(data.get('all_courses') or [])} courses "
                    f"from {resource_key or url[:50]}"
                )
                return data

            if last_error:
                raise last_error
            unavailable["reason"] = "Study-plan extraction returned no usable JSON."
            return unavailable
        except Exception as e:
            self.logger.error(f"Study plan PDF extraction failed: {e}")
            unavailable["reason"] = f"Study plan extraction failed: {e}"
            return unavailable

    def _extract_and_process_pdf(self, url: str, query: str, canonical_key: str) -> Optional[Dict[str, Any]]:
        """
        Complete PDF processing pipeline.
        
        Args:
            url: PDF URL
            query: User query
            canonical_key: Topic key
            
        Returns:
            Structured data or None
        """
        self.logger.info(f"🔄 Processing PDF: {url[:60]}...")
        
        # Step 1: Extract text
        pdf_text = self._extract_pdf_text(url)
        
        if not pdf_text:
            self.logger.warning(f"Could not extract text from PDF")
            return None
        
        # Step 2: Analyze and structure
        data = self._summarize_pdf_content(pdf_text, query, url)
        
        if data:
            data['topic'] = canonical_key
            data['source_type'] = 'pdf'
            return self._clean_dataset(data, query, canonical_key)
        
        return None
    
    # ========================================
    # WEB PAGE EXTRACTION
    # ========================================

    def _regex_html_fallback(self, html: str) -> str:
        if not html:
            return ""
        t = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
        t = re.sub(r"<style[\s\S]*?</style>", " ", t, flags=re.I)
        t = re.sub(r"<[^>]+>", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    def _html_to_plain_text(self, html: str) -> str:
        parser = _HTMLToText()
        try:
            parser.feed(html)
            parser.close()
        except Exception:
            return self._regex_html_fallback(html)
        out = parser.text().strip()
        if len(out) < 50:
            fb = self._regex_html_fallback(html)
            if len(fb) > len(out):
                out = fb.strip()
        return out

    def _fetch_web_page_html(self, url: str) -> Optional[str]:
        if not HTTP_SUPPORT:
            return None
        url = self._normalize_url(url)
        if not url:
            return None
        try:
            response = self._session.get(
                url, timeout=self.REQUEST_TIMEOUT, allow_redirects=True
            )
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            self.logger.info(
                f"Fetched web page: status={response.status_code}, chars={len(response.text or '')}"
            )
            return response.text
        except Exception as e:
            self.logger.warning(f"Web page fetch failed for {url[:60]}: {e}")
            return None

    def _summarize_web_content(
        self, plain_text: str, query: str, url: str
    ) -> Optional[Dict[str, Any]]:
        """Structure fetched web page text into JSON (grounded on page content)."""
        if not self.openai_service.is_configured():
            return {
                "title": "Web page content",
                "summary": plain_text[:2000] + ("..." if len(plain_text) > 2000 else ""),
                "url": url,
                "source_type": "web",
            }

        truncated = len(plain_text) > PDF_MAX_CHARS
        text_to_analyze = plain_text[:PDF_MAX_CHARS] if truncated else plain_text

        prompt = f"""You are a web page analyst for Jordan University of Science and Technology (JUST).

The following plain text was extracted from an official JUST web page:
---
{text_to_analyze}
---
{'Note: content was truncated because the page is very long.' if truncated else ''}

Page URL: {url}
Student question: "{query}"

Extract ALL information from the text that helps answer the question. Do not invent facts not present in the text.

Return JSON with applicable fields only:
{{
    "title": "Page title or topic",
    "summary": "Detailed summary covering the main facts from the page",
    "key_points": ["important point 1", "..."],
    "requirements": ["requirement if listed"],
    "steps": ["step if listed"],
    "fees": {{ "currency": "JOD if applicable", "programs": [], "fee_items": [], "notes": [] }},
    "important_dates": ["date or deadline from page"],
    "contact_info": {{ "phone": "", "email": "", "office": "" }},
    "tables": [],
    "source_url": "{url}"
}}

Rules:
- Copy numbers, dates, and names exactly as they appear in the text.
- If fees or percentages appear, put them in the appropriate fields.
- If the page has lists or steps, include every item in key_points, steps, or requirements.
- Do not use general knowledge — only the text above."""

        try:
            from openai import OpenAI
            from config import OPENAI_MODEL, openai_client_kwargs

            kw = openai_client_kwargs()
            if not kw:
                return None
            client = OpenAI(**kw)
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You extract structured JSON from JUST university web pages. "
                            "Be thorough — include all relevant details from the text. "
                            "Do not invent information."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=PDF_LLM_MAX_TOKENS,
                temperature=PDF_LLM_TEMPERATURE,
            )
            data = json.loads(response.choices[0].message.content)
            data["url"] = url
            data["source_type"] = "web"
            self.logger.info("Web page content structured successfully")
            return data
        except Exception as e:
            self.logger.error(f"Web content structuring failed: {e}")
            return None

    def _extract_web_page(self, url: str, query: str) -> Optional[Dict[str, Any]]:
        """
        Extract data from a web page: fetch HTML, parse text, structure with AI.
        Falls back to legacy LLM-only extraction if fetch fails.
        """
        html = self._fetch_web_page_html(url)
        if html:
            plain = self._html_to_plain_text(html)
            if plain and len(plain.strip()) > 50:
                data = self._summarize_web_content(plain, query, url)
                if data and (data.get("title") or data.get("summary")):
                    return data
            self.logger.warning(
                f"Web page yielded little text ({len(plain or '')} chars) for {url[:60]}"
            )

        self.logger.warning("Web fetch/structure failed — falling back to LLM context extraction")
        return self.openai_service.extract_page_data(url, query)
    
    # ========================================
    # SMART RESOURCE SELECTION
    # ========================================
    
    def _resource_key_tokens(self, key: str) -> set:
        return set(re.sub(r"[^a-z0-9]", " ", key.lower()).split())

    def select_resource(self, canonical_key: str, query: str) -> Optional[str]:
        """
        Select the best resource URL for a canonical key.

        Strategy:
        1. Direct key lookup in resources dict.
        2. Known alias map (tuition_fees → Fees, etc.).
        3. Token overlap between canonical_key and resource key names.
        4. Keyword overlap between query words and resource key tokens.
        """
        if not self.resources:
            return None

        # Strategy 1: Direct lookup
        if canonical_key in self.resources:
            url = self.resources[canonical_key]
            self.logger.info(f"Direct match: {canonical_key} -> {url[:50]}...")
            return url

        # Strategy 2: Known aliases
        norm_key = (canonical_key or "").strip().lower()
        alias_target = _RESOURCE_ALIASES.get(norm_key)
        if alias_target and alias_target in self.resources:
            url = self.resources[alias_target]
            self.logger.info(f"Alias match: {canonical_key} -> {alias_target} -> {url[:50]}...")
            return url

        # Strategy 3: Token overlap — canonical_key tokens vs resource keys
        ck_tokens = self._resource_key_tokens(canonical_key or "")
        if ck_tokens:
            best_key, best_score = None, 0
            for k in self.resources:
                score = len(ck_tokens & self._resource_key_tokens(k))
                if score > best_score:
                    best_key, best_score = k, score
            if best_key and best_score >= 1:
                url = self.resources[best_key]
                self.logger.info(
                    f"Canonical token match: '{best_key}' (score={best_score}) -> {url[:50]}..."
                )
                return url

        # Strategy 4: Query word overlap
        query_words = set(
            re.sub(r"[^\w\u0600-\u06FF]", " ", (query or "").lower()).split()
        )
        # Also check alias map values against query
        for alias, target in _RESOURCE_ALIASES.items():
            if alias in query_words and target in self.resources:
                url = self.resources[target]
                self.logger.info(f"Query alias match: '{alias}' -> {target} -> {url[:50]}...")
                return url

        if query_words:
            best_key, best_score = None, 0
            for k in self.resources:
                k_words = self._resource_key_tokens(k)
                score = len(query_words & k_words)
                if score > best_score:
                    best_key, best_score = k, score
            if best_key and best_score > 0:
                url = self.resources[best_key]
                self.logger.info(
                    f"Keyword overlap match: '{best_key}' (score={best_score}) -> {url[:50]}..."
                )
                return url

        self.logger.debug(f"No resource match for: {canonical_key}")
        return None
    
    def get_all_resources(self) -> Dict[str, str]:
        """Get all available resources."""
        return self.resources.copy()
    
    # ========================================
    # MAIN EXTRACTION PIPELINE
    # ========================================
    
    def _attach_resource_metadata(
        self,
        data: Dict[str, Any],
        resource_key: Optional[str],
        url: Optional[str],
    ) -> Dict[str, Any]:
        """Tag extracted JSON with resources.json key and source URL."""
        if not data:
            return data
        key = resource_key
        norm_url = self._normalize_url(url or data.get("url") or data.get("source_url"))
        if not key and norm_url:
            for k, v in self.resources.items():
                if self._normalize_url(v) == norm_url:
                    key = k
                    break
        if key:
            data["resource_key"] = key
        if norm_url:
            data.setdefault("url", norm_url)
            data.setdefault("source_url", norm_url)
        if key or norm_url:
            data["information_sources"] = [{
                "type": "official_resource",
                "resource_key": key,
                "url": norm_url,
            }]
        return data

    def extract_data(
        self,
        canonical_key: str,
        query: str,
        resource_url: Optional[str] = None,
        resource_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        ROBUST data extraction with multiple fallback strategies.
        
        Pipeline:
        1. Try provided URL (detect PDF vs HTML)
        2. Try resources.json URL (detect PDF vs HTML)
        3. Try web search as fallback
        4. Return helpful default if all fail
        
        Args:
            canonical_key: Topic identifier
            query: User's question
            resource_url: Optional direct URL
            
        Returns:
            Structured data (always returns something useful)
        """
        self.logger.info(f"🔍 Extracting data for: {query[:50]}... (key: {canonical_key})")
        
        extraction_attempts = []
        
        # ==========================================
        # STRATEGY 1: Use provided resource URL
        # ==========================================
        resource_url = self._normalize_url(resource_url)
        if resource_url and self._is_valid_url(resource_url):
            self.logger.info(f"Strategy 1: Trying provided URL")
            
            data = self._try_extract_from_url(resource_url, query, canonical_key)
            if data:
                return self._attach_resource_metadata(data, resource_key, resource_url)
            extraction_attempts.append(f"Provided URL failed: {resource_url[:50]}")
        
        # ==========================================
        # STRATEGY 2: Select from resources.json
        # ==========================================
        fallback_key = resource_key
        if not fallback_key and resource_url:
            for k, v in self.resources.items():
                if self._normalize_url(v) == resource_url:
                    fallback_key = k
                    break
        selected_url = self._normalize_url(self.select_resource(canonical_key, query))
        if selected_url and not fallback_key:
            for k, v in self.resources.items():
                if self._normalize_url(v) == selected_url:
                    fallback_key = k
                    break
        
        if selected_url and selected_url != resource_url:
            self.logger.info(f"Strategy 2: Trying resources.json URL")
            
            data = self._try_extract_from_url(selected_url, query, canonical_key)
            if data:
                return self._attach_resource_metadata(data, fallback_key, selected_url)
            extraction_attempts.append(f"Resources URL failed: {selected_url[:50]}")
        
        # ==========================================
        # STRATEGY 3: Web search fallback
        # ==========================================
        self.logger.info(f"Strategy 3: Web search fallback")
        
        data = self._try_web_search(query, canonical_key)
        if data:
            return data
        extraction_attempts.append("Web search failed")
        
        # ==========================================
        # STRATEGY 4: Return helpful default
        # ==========================================
        self.logger.warning(f"All strategies failed: {extraction_attempts}")
        
        return self._create_fallback_response(canonical_key, query, extraction_attempts)
    
    def _try_extract_from_url(self, url: str, query: str, canonical_key: str) -> Optional[Dict[str, Any]]:
        """
        Try to extract data from URL with automatic type detection.
        
        Args:
            url: URL to extract from
            query: User query
            canonical_key: Topic key
            
        Returns:
            Extracted data or None
        """
        url = self._normalize_url(url)
        if not url or not self._is_valid_url(url):
            return None

        # Check if PDF by URL pattern (also considers decoded path)
        is_pdf = self._is_pdf_url(url)
        
        # If not obvious, check content type
        if not is_pdf:
            content_type = self._detect_content_type(url)
            is_pdf = (content_type == 'pdf')
            # If HEAD is blocked/unknown, fall back to decoded-path detection
            if not is_pdf and content_type == 'unknown':
                is_pdf = self._is_pdf_url(url)
        
        if is_pdf:
            self.logger.info(f"📄 Detected PDF, using PDF extractor")
            data = self._extract_and_process_pdf(url, query, canonical_key)
        else:
            self.logger.info(f"🌐 Detected web page, using web extractor")
            data = self._extract_web_page(url, query)
            if data and data.get('title'):
                data['topic'] = canonical_key
                data = self._clean_dataset(data, query, canonical_key)
        
        return data if data and (data.get('title') or data.get('summary')) else None
    
    def _try_web_search(self, query: str, canonical_key: str) -> Optional[Dict[str, Any]]:
        """
        Try web search as fallback.
        
        Args:
            query: Search query
            canonical_key: Topic key
            
        Returns:
            Search results or None
        """
        search_queries = [
            f"Jordan University of Science and Technology JUST {query}",
            f"JUST Jordan University {query}",
        ]
        
        for search_query in search_queries:
            result = self.openai_service.perform_web_search(search_query)
            
            if result and result != "Information not found" and len(result) > 50:
                data = self._parse_search_result(result, query, canonical_key)
                if data and (data.get('summary') or data.get('title')):
                    return self._clean_dataset(data, query, canonical_key)
        
        return None
    
    def _create_fallback_response(self, canonical_key: str, query: str, attempts: List[str]) -> Dict[str, Any]:
        """Create helpful fallback response."""
        return {
            "topic": canonical_key,
            "query": query,
            "title": f"Information about {canonical_key.replace('_', ' ')}",
            "summary": EXTRACTION_FAILED_SUMMARY,
            "suggestion": "Please visit the official Jordan University of Science and Technology website",
            "university_website": "https://www.just.edu.jo",
            "helpful_links": [
                "https://www.just.edu.jo/FacultiesandDepartments",
                "https://www.just.edu.jo/Admission",
                "https://www.just.edu.jo/StudentServices"
            ],
            "contact": "You can contact student services for assistance",
            "_debug_attempts": attempts if self.logger.level <= 10 else None
        }
    
    def _parse_search_result(self, search_result: str, query: str, canonical_key: str) -> Optional[Dict[str, Any]]:
        """Parse web search result into structured data."""
        if not search_result or search_result == "Information not found":
            return None
        
        if self.openai_service.is_configured():
            try:
                prompt = f"""Convert this search result into structured JSON.

Search result:
{search_result[:4000]}

Original query: "{query}"

Return JSON with relevant fields:
- title: Main topic
- summary: Key information
- requirements: List if applicable
- fees: Fee information if applicable
- steps: Steps if applicable
- contact_info: Contact details if found
- key_points: Main points

Only include fields with actual data. Do NOT invent information."""

                from openai import OpenAI
                from config import OPENAI_MODEL, openai_client_kwargs

                kw = openai_client_kwargs()
                if not kw:
                    raise RuntimeError("LLM not configured")
                client = OpenAI(**kw)
                response = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": "Extract structured data from search results. Be accurate and factual."},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=2000
                )
                
                return json.loads(response.choices[0].message.content)
                
            except Exception as e:
                self.logger.error(f"Failed to parse search result: {e}")
        
        return {
            "summary": search_result[:500],
            "source_type": "web_search"
        }
    
    # ========================================
    # DATA CLEANING
    # ========================================
    
    def _clean_dataset(self, data: Dict[str, Any], query: str, canonical_key: str) -> Dict[str, Any]:
        """Clean and normalize extracted data."""
        cleaned = {'topic': canonical_key}
        
        # All possible fields
        all_fields = [
            # Standard fields
            'url', 'title', 'summary', 'requirements', 'fees',
            'deadlines', 'steps', 'tables', 'lists', 'contact_info',
            'departments', 'dates', 'descriptions', 'source_type',
            'key_points', 'source_url', 'document_type',
            # PDF/Study plan fields
            'study_plan', 'courses', 'semesters', 'total_hours',
            'required_courses', 'elective_courses', 'total_credit_hours',
            'program_name', 'graduation_requirements', 'courses_by_semester',
            # Additional fields
            'important_dates', 'helpful_links', 'suggestion', 'note',
            'university_website', 'contact', 'fee_items'
        ]
        
        array_fields = {
            'requirements', 'deadlines', 'steps', 'dates', 'descriptions',
            'departments', 'key_points', 'required_courses', 'elective_courses',
            'courses', 'semesters', 'graduation_requirements', 'important_dates',
            'helpful_links', 'courses_by_semester', 'fee_items'
        }
        
        object_fields = {'fees', 'contact_info', 'study_plan', 'contact'}
        
        for field in all_fields:
            if data.get(field) is not None:
                value = data[field]
                
                if field in array_fields:
                    cleaned[field] = self._ensure_array(value)
                elif field in object_fields:
                    cleaned[field] = self._ensure_object(value)
                elif field in ('tables', 'lists'):
                    cleaned[field] = value
                else:
                    cleaned[field] = value
        
        # Remove empty values
        cleaned = {k: v for k, v in cleaned.items() 
                   if v is not None and v != "" and v != [] and v != {}}
        
        return cleaned
    
    def _ensure_array(self, value: Any) -> List:
        """Ensure value is a list."""
        if isinstance(value, list):
            return value
        elif isinstance(value, str):
            return [value] if value.strip() else []
        elif value is None:
            return []
        else:
            return [str(value)]
    
    def _ensure_object(self, value: Any) -> Dict:
        """Ensure value is a dictionary."""
        if isinstance(value, dict):
            return value
        elif isinstance(value, str):
            return {"value": value} if value.strip() else {}
        else:
            return {}
    
    # ========================================
    # CACHE MANAGEMENT
    # ========================================
    
    def clear_cache(self):
        """Clear PDF cache."""
        self._pdf_cache.clear()
        self.logger.info("PDF cache cleared")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            "pdf_cache_size": len(self._pdf_cache),
            "pdf_cache_keys": list(self._pdf_cache.keys())[:10]
        }
