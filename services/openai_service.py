"""
OpenAI service for content generation, semantic reasoning, and alias matching.
Uses ChatGPT to provide helpful information about JUST University.
"""
import json
import re
import time
import unicodedata
from functools import lru_cache
from typing import Optional, Dict, Any, List, Tuple
from openai import OpenAI
from config import (
    OPENAI_MODEL,
    MAX_RETRIES,
    RETRY_DELAY,
    EXTRACTION_FAILED_SUMMARY,
    EXTRACTION_FAILED_SUMMARY_AR,
    openai_client_kwargs,
)
from logger import get_logger


def _classify_llm_http_error(exc: BaseException) -> str:
    """
    Rough category for user-facing notices (OpenAI API).
    Returns: rate_limit | quota | auth | model | other
    """
    raw = str(exc)
    low = raw.lower()
    if "429" in raw or "rate_limit" in low or "too many requests" in low:
        return "rate_limit"
    if "insufficient_quota" in low:
        return "quota"
    if "401" in raw or "invalid_api_key" in low or "invalid api key" in low:
        return "auth"
    if "403" in raw and ("forbidden" in low or "permission" in low):
        return "auth"
    if "404" in raw or "model_not_found" in low:
        return "model"
    return "other"


SOURCES_SECTION_TITLE = "## Information sources"
LEGACY_SOURCES_SECTION_AR = "## مصادر المعلومات"


JUST_DATA_GROUNDED_SYSTEM = """You are an assistant for the Jordan University of Science and Technology (JUST). Use a strict "fact review" style: write only what is explicitly supported by the attached JSON, and give the student a thorough, helpful answer grounded in that data.

Priority ladder (must not conflict with accuracy):
1) Allowed source only: the JSON object in the user message titled "Extracted data". Do not import from general knowledge any detail about the university (no numbers, dates, courses, links, names of people or offices) unless that exact text appears inside that JSON.
2) Literal-text rule: any fee amount, date, deadline, course code (e.g. CS101), link, phone, email — mention only if it appears literally in JSON. If the question asks for a specific item (e.g. an amount for a specific college) and it is not clearly in JSON, write exactly one sentence: "This information was not stated in the retrieved data" — do not use that sentence when `url` or `source_url` exists in JSON, and do not use an introduction implying no information while you then state numbers or a table that exists in JSON.
3) No hedging or fake generalizations: do not use "usually", "often", "might be", "it is known", "presumably", "in practice", "external sources" to add facts outside JSON.
4) Thoroughness: start with a direct answer, then expand with every relevant detail from JSON (fee amounts, program names, admission percentages, credit hours, dates, steps, requirements, contact info). When JSON contains `fees.programs`, `fee_items`, `tables`, `steps`, `requirements`, `key_points`, or `study_plan`, present them clearly — use bullet lists or markdown tables. Do not compress rich JSON into a one-line summary.
5) Scope: include all JSON fields that help answer the question; skip only fields clearly unrelated to the question. Do not apologize for "missing information" while JSON still contains enough to answer (summary, fees, table, or document link).
6) Language: use clear English for all answers unless the student's question is written entirely in Arabic, in which case you may answer in clear Modern Standard Arabic. Do not add preambles about your role or the model.
7) Length: be comprehensive when JSON is rich — use headings or bullet sections for long answers. Never give a terse reply when JSON contains tables, fee rows, or multi-step instructions.
8) Never mention: Redis, cache, embeddings, "language model", "artificial intelligence", or any system implementation details.
9) Do not add any section titled "## Information sources", "## مصادر المعلومات", "Information sources", or similar reference blocks — one is appended automatically after your reply without you writing it."""


def retry_on_error(max_retries: int = None, delay: float = None):
    """Decorator for retrying failed API calls."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            retries = max_retries or MAX_RETRIES
            wait = delay or RETRY_DELAY
            last_error = None
            
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < retries - 1:
                        time.sleep(wait * (attempt + 1))  # Exponential backoff
                    continue
            
            # Log final failure
            logger = get_logger()
            logger.error(f"{func.__name__} failed after {retries} attempts: {last_error}")
            return None
        return wrapper
    return decorator


class OpenAIService:
    """
    Handles all OpenAI operations including:
    - Knowledge-based search and information generation
    - Content generation for JUST University topics
    - Alias matching validation
    - Answer generation for JUST topics (English-first; Arabic only when the query is entirely Arabic)
    
    Uses ChatGPT's knowledge to provide helpful information.
    """
    
    _instance = None
    
    def __new__(cls):
        """Singleton pattern."""
        if cls._instance is None:
            cls._instance = super(OpenAIService, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """Initialize OpenAI client."""
        self.logger = get_logger()
        kw = openai_client_kwargs()
        self.client = OpenAI(**kw) if kw else None
        self.model = OPENAI_MODEL
        
        if self.client:
            self.logger.info(f"OpenAI service initialized with model: {self.model}")
        else:
            self.logger.warning("OpenAI API key not configured - web search disabled")
    
    def is_configured(self) -> bool:
        """Check if OpenAI is configured."""
        return self.client is not None

    def _build_grounded_answer_user_message(
        self, query: str, json_data: Dict[str, Any], source: str
    ) -> str:
        """User turn: question + single JSON evidence blob (strict grounding)."""
        origin_en = {
            "redis": "Dataset from cache for the same topic",
            "live_web": "Dataset from live extraction (web/resource) for this request",
        }.get(source, str(source))
        return (
            f"Student question:\n\"{query}\"\n\n"
            f"Dataset origin in the system: {origin_en}\n\n"
            f"Extracted data (JSON) — you may rely on this exclusively, no other source:\n"
            f"{json.dumps(json_data, ensure_ascii=False, indent=2)}\n\n"
            "Write a detailed, student-friendly answer. Include all relevant facts from the JSON "
            "(numbers, dates, programs, fees, steps, requirements). Use bullet lists or markdown "
            "tables when JSON contains structured data. Follow the system instructions exactly."
        )

    def _strip_trailing_sources_section(self, text: str) -> str:
        """Remove any model-written sources block at end of answer (English or legacy Arabic heading)."""
        if not text:
            return ""
        stripped = re.sub(
            rf"(?ms)^{re.escape(SOURCES_SECTION_TITLE)}\s*[\s\S]*$",
            "",
            text,
        )
        stripped = re.sub(
            rf"(?ms)\n{re.escape(SOURCES_SECTION_TITLE)}\s*[\s\S]*$",
            "",
            stripped,
        )
        stripped = re.sub(
            rf"(?ms)^{re.escape(LEGACY_SOURCES_SECTION_AR)}\s*[\s\S]*$",
            "",
            stripped,
        )
        stripped = re.sub(
            rf"(?ms)\n{re.escape(LEGACY_SOURCES_SECTION_AR)}\s*[\s\S]*$",
            "",
            stripped,
        )
        return stripped.rstrip()

    def _has_substantive_extracted_data(self, json_data: Dict[str, Any]) -> bool:
        """True when extraction produced usable fields (fees, tables, etc.), not just a failed summary."""
        if not json_data:
            return False
        if json_data.get("url") or json_data.get("source_url"):
            fees = json_data.get("fees")
            if isinstance(fees, dict) and fees.get("programs"):
                return True
            if json_data.get("fee_items") or json_data.get("tables"):
                return True
            for field in (
                "steps", "requirements", "key_points", "study_plan",
                "courses", "important_dates", "contact_info",
            ):
                val = json_data.get(field)
                if isinstance(val, list) and len(val) > 0:
                    return True
                if isinstance(val, dict) and val:
                    return True
            summary = (json_data.get("summary") or "").strip()
            if summary and summary not in (
                EXTRACTION_FAILED_SUMMARY,
                EXTRACTION_FAILED_SUMMARY_AR,
            ):
                if len(summary) > 120:
                    return True
        return False

    def _render_sources_section_from_json(self, json_data: Dict[str, Any]) -> str:
        """Single concise sources block — user-facing, not a debug dump."""
        url = json_data.get("url") or json_data.get("source_url") or json_data.get("university_website")
        lines: List[str] = [SOURCES_SECTION_TITLE, ""]

        if url:
            lines.append(f"Primary reference for this answer: {url}")
        else:
            lines.append("No explicit document URL was attached in the extracted data.")

        info_sources = json_data.get("information_sources")
        if isinstance(info_sources, list) and info_sources:
            lines.append("")
            lines.append("Additional source details:")
            for i, item in enumerate(info_sources, 1):
                if not isinstance(item, dict):
                    continue
                t = item.get("type")
                rk = item.get("resource_key")
                u = item.get("url")
                q_used = item.get("queries_used")
                note = item.get("note")
                segment = f"{i}. type={t} resource_key={rk} url={u} queries_used={q_used}"
                if note:
                    segment += f" note={note}"
                lines.append(segment)

        idx = json_data.get("offering_resources_index")
        if isinstance(idx, list) and idx:
            lines.append("")
            lines.append("Additional reference index:")
            for i, item in enumerate(idx, 1):
                if isinstance(item, dict):
                    rk = item.get("resource_key")
                    u = item.get("url")
                    lines.append(f"{i}. {rk} — {u}")
                elif isinstance(item, str):
                    lines.append(f"{i}. {item}")

        return "\n".join(lines).strip()

    def _ensure_sources_section(self, answer: str, json_data: Dict[str, Any]) -> str:
        """One sources block: strip any model copy, append deterministic footer."""
        body = self._strip_trailing_sources_section(answer or "")
        return (body + "\n\n" + self._render_sources_section_from_json(json_data)).strip()

    def _conversational_messages_when_extraction_failed(self, query: str) -> List[Dict[str, str]]:
        """Short reply when live extractors returned no structured data."""
        user_content = (
            f'Student question: "{query}"\n\n'
            "Situation: No verified data was extracted from university sources for this request.\n\n"
            "Requirements:\n"
            "- If the question is a greeting or very small talk: reply politely and briefly only.\n"
            "- If it is about the university: at most two to four sentences; state only that official details are on "
            "https://www.just.edu.jo — no invented fee numbers, dates, course names, extra links, "
            "or made-up program/college descriptions.\n"
            "- If the question assumes precise facts (fees, deadlines, course codes): say clearly they are not available in the retrieved data and do not guess."
        )
        return [
            {
                "role": "system",
                "content": (
                    "You are an assistant for Jordan University of Science and Technology (JUST). "
                    "Do not invent university facts when the user message lacks verified data. "
                    "Be concise and precise. If the student's question is entirely in Arabic, reply in clear Modern Standard Arabic; otherwise use English. "
                    "Do not use hedging like 'usually', 'often', or 'might be'."
                ),
            },
            {"role": "user", "content": user_content},
        ]
    
    # ========================================
    # ALIAS MATCHING VALIDATION (STEP 1)
    # ========================================
    
    def validate_alias_match(
        self, 
        query: str, 
        candidate_aliases: List[Dict[str, Any]],
        similarity_scores: List[float]
    ) -> Tuple[Optional[str], Optional[str], float]:
        """
        Use ChatGPT to validate or pick the best alias match.
        Called when cosine similarity is uncertain (below threshold).
        
        Args:
            query: User query
            candidate_aliases: List of {alias, canonical_key} candidates
            similarity_scores: Corresponding similarity scores
            
        Returns:
            Tuple of (best_alias, canonical_key, confidence)
            Returns (None, None, 0) if no match found
        """
        if not self.client or not candidate_aliases:
            return None, None, 0.0
        
        try:
            # Build candidates list for prompt
            candidates_text = "\n".join([
                f"- Alias: '{c['alias']}' → Key: '{c['canonical_key']}' (similarity: {similarity_scores[i]:.2f})"
                for i, c in enumerate(candidate_aliases)
            ])
            
            prompt = f"""You are a semantic matching assistant for a university information system.

User Query: "{query}"

Candidate Aliases (with similarity scores):
{candidates_text}

TASK:
1. Analyze the user's query semantically
2. Determine which alias (if any) is the best match
3. Consider both semantic meaning and the similarity score

RULES:
- If a candidate clearly matches the query's intent, select it
- If no candidate matches, respond with "NO_MATCH"
- Be strict - only match if the semantic meaning aligns

Respond in JSON format:
{{
    "match": true/false,
    "selected_alias": "alias text or null",
    "canonical_key": "key or null",
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation"
}}"""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a semantic matching expert. Analyze queries and determine the best alias match."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)

            if result.get('match') and result.get('canonical_key'):
                # Constrain returned key to known candidates to prevent hallucinated keys
                known_keys = {
                    c.get('canonical_key')
                    for c in candidate_aliases
                    if c.get('canonical_key')
                }
                returned_key = result.get('canonical_key')
                if returned_key not in known_keys:
                    self.logger.warning(
                        f"validate_alias_match: LLM returned unknown key '{returned_key}'; ignoring"
                    )
                    return None, None, 0.0

                self.logger.info(
                    f"ChatGPT validated match: '{query}' -> '{result['selected_alias']}' "
                    f"(key: {returned_key}, confidence: {result.get('confidence', 0.8)})"
                )
                return (
                    result.get('selected_alias'),
                    returned_key,
                    result.get('confidence', 0.8),
                )
            else:
                self.logger.info(f"ChatGPT found no match for: '{query}'")
                return None, None, 0.0

        except Exception as e:
            self.logger.error(f"Alias validation failed: {e}")
            return None, None, 0.0
    
    def _format_resources_catalog(self, resources: Dict[str, str]) -> str:
        """Group resources.json keys by category for clearer LLM selection."""
        categories: Dict[str, List[str]] = {
            "Fees, scholarships & financial aid": [],
            "Study plans (official PDF plans by major)": [],
            "Admission (undergraduate & graduate)": [],
            "Academic calendar & schedules": [],
            "Student services & portals": [],
            "Faculties & departments": [],
            "About the university (vision, regulations, ranking)": [],
            "Deanships, centers & institutes": [],
            "Library & e-learning": [],
            "Wellness clinics": [],
            "Other official pages": [],
        }
        for key, url in resources.items():
            kl = key.lower()
            line = f"  - {key}: {url}"
            if kl in ("fees", "rewards", "scholarships") or "fee" in kl:
                categories["Fees, scholarships & financial aid"].append(line)
            elif kl.endswith("_plan") or "studyplan" in url.lower() or "study plan" in url.lower():
                categories["Study plans (official PDF plans by major)"].append(line)
            elif kl.startswith("admission"):
                categories["Admission (undergraduate & graduate)"].append(line)
            elif "calendar" in kl or "schedule" in kl or "exams" in kl:
                categories["Academic calendar & schedules"].append(line)
            elif kl.startswith("student") or kl in (
                "e_learning", "office365_email", "course_schedule", "exams_schedule"
            ):
                categories["Student services & portals"].append(line)
            elif kl.startswith("faculty_") or kl.startswith("institute_") or kl == "software_engineering":
                categories["Faculties & departments"].append(line)
            elif kl.startswith("about_"):
                categories["About the university (vision, regulations, ranking)"].append(line)
            elif kl.startswith("deanship_") or kl.startswith("center_"):
                categories["Deanships, centers & institutes"].append(line)
            elif kl.startswith("library") or kl == "e_learning":
                categories["Library & e-learning"].append(line)
            elif kl.startswith("wellness_"):
                categories["Wellness clinics"].append(line)
            else:
                categories["Other official pages"].append(line)

        parts = []
        for title, lines in categories.items():
            if lines:
                parts.append(f"### {title}\n" + "\n".join(lines))
        return "\n\n".join(parts)

    def select_best_resource(
        self, 
        query: str, 
        resources: Dict[str, str]
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Use ChatGPT to select the best resource URL for a query.
        
        Args:
            query: User query
            resources: Dict of {key: url}
            
        Returns:
            Tuple of (resource_key, url) or (None, None)
        """
        if not self.client or not resources:
            return None, None
        
        try:
            def _norm_url(u: Optional[str]) -> str:
                if not u or not isinstance(u, str):
                    return ""
                return u.strip()

            resources_text = self._format_resources_catalog(resources)
            
            prompt = f"""You are a resource selector for Jordan University of Science and Technology (JUST).

User Query: "{query}"

Official resources catalog (from resources.json — pick exactly one key whose URL best answers the query):
{resources_text}

TASK:
1. Read the query carefully (Arabic or English).
2. Match to the MOST SPECIFIC resource key — prefer study-plan PDFs for curriculum questions, Fees for tuition, Scholarships for grants, faculty pages for college info, Academic_Calendar for semester dates, Admission_* for applying.
3. If the query mentions a major (Software Engineering, Cybersecurity, AI, Robotics, Computer Science), pick the matching *_Plan PDF.
4. If no resource clearly fits, respond with null.

Respond in JSON:
{{
    "selected_key": "key or null",
    "selected_url": "url or null",
    "reasoning": "brief explanation"
}}"""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a resource selector for JUST. Pick at most one key from the "
                            "categorized catalog whose URL best matches the user query. Prefer specific "
                            "PDF study plans for curriculum questions, Fees for tuition, Scholarships for "
                            "grants, faculty/deanship pages for college info. If none clearly fits, return "
                            "null for both selected_key and selected_url. Do not invent URLs."
                        )
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)

            selected_key = result.get("selected_key")
            selected_url = _norm_url(result.get("selected_url"))

            # STRICT VALIDATION:
            # - Never trust a URL that is not exactly one of the provided resources.
            # - If the key is valid but URL is missing/invalid, recover using resources[key].
            # - If URL matches a known value, recover key from that mapping if needed.
            if selected_key in resources:
                canonical_url = _norm_url(resources.get(selected_key))
                if selected_url and selected_url == canonical_url:
                    self.logger.info(f"Selected resource: {selected_key} for query: '{query}'")
                    return selected_key, canonical_url
                if not selected_url or selected_url not in resources.values():
                    # Recover: key is known, URL returned by LLM is unusable/shortened.
                    if selected_url and selected_url != canonical_url:
                        self.logger.warning(
                            f"LLM returned non-listed URL for key '{selected_key}'. "
                            f"Using canonical resources.json URL instead."
                        )
                    return selected_key, canonical_url

            if selected_url:
                # If the LLM returned a URL, only accept it if it exactly matches one of the provided URLs.
                for k, v in resources.items():
                    if _norm_url(v) == selected_url:
                        self.logger.info(f"Selected resource: {k} for query: '{query}'")
                        return k, _norm_url(v)

                self.logger.warning(
                    "LLM returned a URL not present in resources.json; ignoring it."
                )

            return None, None
            
        except Exception as e:
            self.logger.error(f"Resource selection failed: {e}")
            return None, None
    
    # ========================================
    # WEB SEARCH OPERATIONS
    # ========================================
    
    def perform_web_search(self, query: str) -> Optional[str]:
        """
        Perform a search about JUST university using ChatGPT.
        Uses model's knowledge and provides helpful information.
        
        Args:
            query: The search query
            
        Returns:
            Search results as text, or None if failed
        """
        if not self.client:
            self.logger.warning("OpenAI client not configured")
            return None
        
        try:
            self.logger.info(f"Performing search for: {query}")
            
            # Use chat completion with specialized prompt for JUST
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": """You are an assistant focused on Jordan University of Science and Technology (JUST).

About the university:
- Location: Irbid, Jordan
- Founded: 1986
- Official website: https://www.just.edu.jo
- One of Jordan's largest universities, strong in science and technology

Main colleges (examples):
- Medicine, Engineering, Information Technology & Computer Science
- Pharmacy, Dentistry, Nursing, Science, Agriculture, Architecture & Design

Student-facing services (examples):
- Registration and admissions, housing, library, scholarships, student affairs

Answer rules:
1. Be helpful and reasonably concise based on reliable general knowledge.
2. For specific operational facts (fees, deadlines, numbered regulations), do not invent numbers or exact dates unless you are very sure they are correct; otherwise say details change and must be confirmed on https://www.just.edu.jo only.
3. Be friendly without fabricating precise operational details.
4. Prefer clear English; if the question is entirely in Arabic, you may answer in clear Modern Standard Arabic."""
                    },
                    {
                        "role": "user",
                        "content": f"Answer this question about Jordan University of Science and Technology (JUST):\n\n{query}"
                    }
                ]
            )
            
            result = response.choices[0].message.content
            self.logger.debug(f"Search completed, result length: {len(result) if result else 0}")
            return result
            
        except Exception as e:
            self.logger.error(f"Search failed: {e}")
            return None
    
    def extract_page_data(self, url: str, query: str) -> Optional[Dict[str, Any]]:
        """
        Generate structured data for a topic using ChatGPT.
        
        Args:
            url: Context URL (may not be directly accessible)
            query: The original query for context
            
        Returns:
            Structured JSON dataset or None if failed
        """
        if not self.client:
            self.logger.warning("OpenAI client not configured")
            return None
        
        try:
            self.logger.info(f"Generating data for query: {query}")
            
            extraction_prompt = f"""Return a single JSON object only (no text outside JSON) for a student question in the context of Jordan University of Science and Technology (JUST).

Question: "{query}"
Reference URL (context only; the system may not have the page body): {url}

Optional fields (include a field only if you have clear, specific content; otherwise omit it):
- title, summary, key_points, steps, tips, website, contact

Strict constraints:
- Do not invent fee amounts, deadlines, phone numbers, emails, or specific course codes; if needed, state in summary that they are not available in the current context instead of fabricating them.
- If unsure about any numeric fact, omit it and point to the official site https://www.just.edu.jo
- Return valid JSON only."""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": """You output JSON fields for a JUST university assistant.
Mandatory rules:
- Return valid JSON only (response_format json_object).
- Do not invent numeric facts (fees, dates, deadlines) or fake contact details.
- If precise details are missing, say in summary that official details are on the website only, without invented numbers.
- Keep fields concise; no padding."""
                    },
                    {
                        "role": "user",
                        "content": extraction_prompt
                    }
                ],
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            
            if not content:
                self.logger.warning("Empty response from extraction")
                return None
            
            # Parse JSON response
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                data = self._extract_json_from_text(content)
            
            if data:
                data['url'] = url
                data['source_query'] = query
                data = {k: v for k, v in data.items() if v is not None and v != "" and v != [] and v != {}}
                self.logger.info(f"Generated {len(data)} fields for: {query}")
                return data
            
            return None
            
        except Exception as e:
            self.logger.error(f"Data generation failed for {query}: {e}")
            return None

    def extract_academic_calendar_events(
        self, page_plain_text: str, source_url: str
    ) -> Optional[Dict[str, Any]]:
        """
        Parse plain text from the JUST academic calendar page into structured events.
        Caller is responsible for fetching HTML and stripping to text.
        """
        if not self.client:
            self.logger.warning("OpenAI client not configured for calendar extraction")
            return None
        try:
            max_chars = 48000
            text = page_plain_text[:max_chars]
            if len(page_plain_text) > max_chars:
                text += "\n\n[... part of the text was truncated for the model limit ...]"

            extraction_prompt = f"""You extract academic-calendar events from the following plain text taken from a Jordan University of Science and Technology (JUST) page.
Page URL (reference): {source_url}

Plain text from the page (may include noise from menus and footers):
---
{text}
---

Return JSON in exactly this shape:
{{
  "academic_year_hint": "short text about the academic year if present in the text, else null",
  "events": [
    {{
      "id": "slug_in_english_only_underscores",
      "title_ar": "event title as in the source (often Arabic; copy faithfully)",
      "date_note_ar": "dates as shown in the source (free text from the page)",
      "start_date": "YYYY-MM-DD or null",
      "end_date": "YYYY-MM-DD or null",
      "category": "registration|exams|holiday|semester|general|other",
      "details_ar": "short extra sentence or null"
    }}
  ]
}}

Strict rules:
- Do not invent dates or events not mentioned in the text.
- If you cannot parse a date to YYYY-MM-DD, set start_date and end_date to null and keep date_note_ar from the text.
- Focus on what matters to students: semester start/end, registration windows, exams, official holidays, clear administrative dates.
- id must be unique and stable (English letters, digits, underscores only)."""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an academic-calendar data analyst for JUST. "
                            "Extract JSON only from the given text without inventing information."
                        ),
                    },
                    {"role": "user", "content": extraction_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.15,
                max_tokens=8000,
            )
            content = response.choices[0].message.content
            if not content:
                return None
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                data = self._extract_json_from_text(content)
            if not data or not isinstance(data, dict):
                return None
            events = data.get("events")
            if not isinstance(events, list):
                return None
            return data
        except Exception as e:
            self.logger.error(f"Calendar extraction failed: {e}")
            return None

    def _extract_json_from_text(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract JSON from text that might contain markdown or other content."""
        import re
        
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        
        return None
    
    # ========================================
    # ANSWER GENERATION
    # ========================================

    def _is_academic_planner(self, json_data: Dict[str, Any], source: str) -> bool:
        if json_data.get("topic") == "academic_planning":
            return True
        return source == "planner"

    ACADEMIC_PLANNER_SYSTEM = """You are an academic planning assistant for students at Jordan University of Science and Technology (JUST).
Your task: suggest advisory study schedules using the OFFICIAL course catalog when provided — not a substitute for the approved plan, the academic advisor's decision, or actual registration.

Mandatory rules (high precision):
1) Allowed sources: `planner_context` fields, `official_study_plan` (when available=true), and `user_message`. Do not add fee amounts, registration deadlines, phones, or URLs not in that JSON.
2) Official courses: when `official_study_plan.available` is true and `official_study_plan.semesters` or `official_study_plan.all_courses` exists, every course in PLAN_JSON and the Markdown tables MUST use codes and titles from that list only. Respect prerequisites listed there. Order semesters by prerequisite chains and the recommended_semester / semester_number fields from the PDF.
3) When `official_study_plan.available` is false, state clearly that no official PDF course list was loaded. Use only advisory placeholder suggestions and label them as "planning suggestion — confirm with advisor"; never invent realistic-looking official codes.
4) Follow program, credit hours, and duration in JSON; if the student's question conflicts with JSON, gently remind them that the UI-sent fields take priority.
5) If `schedule_preferences` exists, follow it literally: use_summer_semester_in_plan (if false, no sem 9 and no "summer" in PLAN_JSON), target_credits_per_regular_semester, and summer_max_credits_if_used as the summer cap when enabled.
6) State clearly that the schedule is advisory and that real registration is with the department/advisor.
7) Markdown tables per semester with fixed columns: Semester | Course code | Course title | Credits | Note (prerequisite if any).
8) Summarize total credits per semester and cumulatively; totals must match your tables and stay within official total_credit_hours when known.
9) PLAN_JSON block (no ```) exactly between these lines:
PLAN_JSON_START
[ {"sem":1,"code":"CS111","cr":3,"title":"example","kind":"regular"} ]
PLAN_JSON_END
where sem is 1–8 for regular terms, or 9 or "summer" for summer only if schedule_preferences allows; summer total credits ≤ limit in JSON and ≤ 10 always. kind optional: regular | elective | graduation. code Latin, cr integer.
10) Do not mention Redis, cache, embeddings, or any implementation tech.
11) At the end of the reply (after tables and PLAN_JSON): a section titled ## Information sources — one line for the official study-plan PDF (program.plan_pdf_url or official_study_plan.source_url); one line stating placement is advisory."""

    def _academic_planner_user_prompt(self, json_data: Dict[str, Any], query: str) -> str:
        pc = json_data.get("planner_context") or {}
        mode = pc.get("mode") or "full_plan"
        mode_en = (
            "Generate a full suggested plan from scratch according to the program and profile."
            if mode == "full_plan"
            else "Review the student's current draft and suggest improvements and redistribution where needed."
        )
        return f"""Operating mode: {mode_en}

Structured data (from the student UI + official study-plan PDF when loaded):
{json.dumps(json_data, ensure_ascii=False, indent=2)}

Extra request or note from the student (may be empty):
{(query or '').strip() or '(none)'}

Execution instructions:
- If official_study_plan.available is true, build the schedule ONLY from those course codes/titles.
- Respect prerequisites from official_study_plan when placing courses across semesters.
- Apply schedule_preferences exactly when present.
- Include the ## Information sources section as in the system instructions.

Respond in clear English. No AI self-introductions."""

    def generate_answer(self, json_data: Dict[str, Any], query: str, source: str) -> str:
        """
        Generate a student-facing answer from structured JSON (strictly grounded
        on json_data for non-planner paths; planner uses planner_context rules).
        
        Args:
            json_data: The structured JSON dataset
            query: Original student query
            source: Data source ("redis" or "live_web")
            
        Returns:
            Student-facing answer text (English-first; Arabic only when the query is entirely Arabic on grounded paths).
        """
        if not self.client:
            base = self._generate_fallback_answer(json_data, query, no_client=True)
            return self._ensure_sources_section(base, json_data)

        if self._is_academic_planner(json_data, source):
            try:
                user_content = self._academic_planner_user_prompt(json_data, query)
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.ACADEMIC_PLANNER_SYSTEM},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0.32,
                    max_tokens=6000,
                )
                return self._ensure_sources_section(
                    response.choices[0].message.content.strip(), json_data
                )
            except Exception as e:
                self.logger.error(f"Academic planner answer failed: {e}")
                return self._ensure_sources_section(
                    self._generate_fallback_answer(json_data, query, error=e), json_data
                )

        if (
            source == "live_web"
            and json_data.get("summary")
            in (EXTRACTION_FAILED_SUMMARY, EXTRACTION_FAILED_SUMMARY_AR)
            and not self._has_substantive_extracted_data(json_data)
        ):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self._conversational_messages_when_extraction_failed(query),
                    temperature=0.4,
                    max_tokens=900,
                )
                return self._ensure_sources_section(
                    response.choices[0].message.content.strip(), json_data
                )
            except Exception as e:
                self.logger.error(f"Conversational answer (no extraction) failed: {e}")
                return self._ensure_sources_section(
                    self._generate_fallback_answer(json_data, query, error=e), json_data
                )
        
        try:
            user_content = self._build_grounded_answer_user_message(
                query, json_data, source
            )
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": JUST_DATA_GROUNDED_SYSTEM},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.28,
                max_tokens=6000,
            )

            return self._ensure_sources_section(
                response.choices[0].message.content.strip(), json_data
            )

        except Exception as e:
            self.logger.error(f"Answer generation failed: {e}")
            return self._ensure_sources_section(
                self._generate_fallback_answer(json_data, query, error=e), json_data
            )
    
    def generate_answer_stream(self, json_data: Dict[str, Any], query: str, source: str):
        """
        Generate a streaming answer from JSON data.
        Yields chunks of text as they are generated.
        
        Args:
            json_data: The structured JSON dataset
            query: Original student query
            source: Data source ("redis" or "live_web")
            
        Yields:
            Text chunks as they are generated
        """
        if not self.client:
            # Fallback: yield the fallback answer all at once
            fallback = self._generate_fallback_answer(json_data, query, no_client=True)
            yield self._ensure_sources_section(fallback, json_data)
            return

        if self._is_academic_planner(json_data, source):
            try:
                user_content = self._academic_planner_user_prompt(json_data, query)
                stream = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.ACADEMIC_PLANNER_SYSTEM},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0.32,
                    max_tokens=6000,
                    stream=True,
                )
                acc = ""
                for chunk in stream:
                    if chunk.choices[0].delta.content:
                        piece = chunk.choices[0].delta.content
                        acc += piece
                        yield piece
                if (
                    SOURCES_SECTION_TITLE not in acc
                    and LEGACY_SOURCES_SECTION_AR not in acc
                ):
                    yield "\n\n" + self._render_sources_section_from_json(json_data)
            except Exception as e:
                self.logger.error(f"Academic planner stream failed: {e}")
                fallback = self._generate_fallback_answer(json_data, query, error=e)
                yield self._ensure_sources_section(fallback, json_data)
            return

        # Fast-first stream or empty extraction — answer immediately from the model (no wait for cache)
        if (
            source == "live_web"
            and json_data.get("summary")
            in (EXTRACTION_FAILED_SUMMARY, EXTRACTION_FAILED_SUMMARY_AR)
            and not self._has_substantive_extracted_data(json_data)
        ):
            try:
                stream = self.client.chat.completions.create(
                    model=self.model,
                    messages=self._conversational_messages_when_extraction_failed(query),
                    temperature=0.4,
                    max_tokens=900,
                    stream=True,
                )
                acc = ""
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        piece = chunk.choices[0].delta.content
                        acc += piece
                        yield piece
                if (
                    SOURCES_SECTION_TITLE not in acc
                    and LEGACY_SOURCES_SECTION_AR not in acc
                ):
                    yield "\n\n" + self._render_sources_section_from_json(json_data)
            except Exception as e:
                self.logger.error(f"Conversational stream (no extraction) failed: {e}")
                fallback = self._generate_fallback_answer(json_data, query, error=e)
                yield self._ensure_sources_section(fallback, json_data)
            return

        try:
            user_content = self._build_grounded_answer_user_message(
                query, json_data, source
            )
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": JUST_DATA_GROUNDED_SYSTEM},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.28,
                max_tokens=6000,
                stream=True,
            )

            acc = ""
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    piece = chunk.choices[0].delta.content
                    acc += piece
                    yield piece
            if (
                SOURCES_SECTION_TITLE not in acc
                and LEGACY_SOURCES_SECTION_AR not in acc
            ):
                yield "\n\n" + self._render_sources_section_from_json(json_data)

        except Exception as e:
            self.logger.error(f"Streaming answer generation failed: {e}")
            # Fallback: yield the fallback answer
            fallback = self._generate_fallback_answer(json_data, query, error=e)
            yield self._ensure_sources_section(fallback, json_data)
    
    def _ai_unavailable_notice(
        self,
        *,
        no_client: bool = False,
        error: Optional[BaseException] = None,
    ) -> str:
        """English notice when chat completion is skipped or failed."""
        if no_client:
            return (
                "**Note:** No OpenAI API key is configured. Add `OPENAI_API_KEY` to `.env` at the project root and restart the server.\n\n"
            )
        if error is None:
            return (
                "**Note:** Could not get a reply from the chat model. Check `OPENAI_API_KEY`, `OPENAI_MODEL`, and your internet connection, then try again.\n\n"
            )
        kind = _classify_llm_http_error(error)
        if kind == "rate_limit":
            return (
                "**Note:** OpenAI rate limit reached (429). Wait a moment or check usage at platform.openai.com.\n\n"
            )
        if kind == "quota":
            return (
                "**Note:** OpenAI quota or billing limit reached. Check your account balance at platform.openai.com.\n\n"
            )
        if kind == "auth":
            return (
                "**Note:** The OpenAI API key was rejected (invalid or expired). Update `OPENAI_API_KEY` in `.env` and restart the server.\n\n"
            )
        if kind == "model":
            return (
                "**Note:** The model in `OPENAI_MODEL` is not available on your OpenAI account. Try `gpt-4o` or `gpt-4o-mini`.\n\n"
            )
        err_text = str(error).lower()
        if "missing an 'http" in err_text or "unsupportedprotocol" in err_text:
            return (
                "**Note:** OpenAI API URL misconfigured. Remove any empty `OPENAI_BASE_URL=` line from `.env` and restart the server.\n\n"
            )
        return (
            "**Note:** Generation via OpenAI failed. Check `OPENAI_API_KEY`, `OPENAI_MODEL`, and connectivity; see server logs for details.\n\n"
        )

    def _generate_fallback_answer(
        self,
        json_data: Dict[str, Any],
        query: str,
        *,
        no_client: bool = False,
        error: Optional[BaseException] = None,
    ) -> str:
        """Generate a basic answer without AI (used when API key missing or stream errors)."""
        parts = [
            self._ai_unavailable_notice(no_client=no_client, error=error),
            "Here is a summary based on the extracted or default data:\n",
        ]

        if json_data.get("title"):
            parts.append(f"**{json_data['title']}**\n")

        if json_data.get("summary"):
            parts.append(f"{json_data['summary']}\n")

        if json_data.get("suggestion"):
            parts.append(f"\n{json_data['suggestion']}\n")

        if json_data.get("requirements"):
            parts.append("\n**Requirements:**")
            for req in json_data["requirements"]:
                parts.append(f"• {req}")

        if json_data.get("fees"):
            parts.append("\n**Fees:**")
            for key, value in json_data["fees"].items():
                parts.append(f"• {key}: {value}")

        if json_data.get("steps"):
            parts.append("\n**Steps:**")
            for i, step in enumerate(json_data["steps"], 1):
                parts.append(f"{i}. {step}")

        if json_data.get("deadlines"):
            parts.append("\n**Deadlines:**")
            for deadline in json_data["deadlines"]:
                parts.append(f"• {deadline}")

        if json_data.get("university_website"):
            parts.append(f"\nOfficial website: {json_data['university_website']}")

        if json_data.get("url"):
            parts.append(f"\nMore details: {json_data['url']}")

        return "\n".join(parts)
    
    # ========================================
    # ALIAS GENERATION
    # ========================================
    
    def generate_aliases_with_ai(self, canonical_key: str, query: str) -> List[str]:
        """
        Generate exactly 10 English + 10 Arabic aliases for a canonical key.
        
        Args:
            canonical_key: The canonical key
            query: The original query
            
        Returns:
            List of 20 aliases (10 Arabic + 10 English)
        """
        if not self.client:
            return []
        
        try:
            prompt = f"""You generate aliases for a Jordan University of Science and Technology (JUST) information system.

Topic: {canonical_key}
Original query: "{query}"

Generate exactly 20 aliases:

10 Arabic aliases:
- 3 in Modern Standard Arabic
- 3 in Jordanian colloquial Arabic
- 2 in Arabizi (Arabic in Latin letters, e.g. "kif asajel")
- 2 abbreviations or common typos

10 English aliases:
- 3 formal phrasings
- 3 casual/short forms
- 2 common misspellings
- 2 abbreviations

Strict rules:
- Each alias must relate directly to the topic
- No duplicates
- Make them realistic (things a student might actually type)
- Focus on Jordan University of Science and Technology

Return JSON in exactly this shape:
{{
    "arabic_aliases": ["name1", "name2", "name3", "name4", "name5", "name6", "name7", "name8", "name9", "name10"],
    "english_aliases": ["alias1", "alias2", "alias3", "alias4", "alias5", "alias6", "alias7", "alias8", "alias9", "alias10"]
}}"""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": """You are an expert at generating aliases for Jordan University of Science and Technology.
You understand Jordanian dialect, Modern Standard Arabic, and English.
You produce realistic aliases students might actually use."""
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            
            aliases = []
            
            # Collect Arabic aliases
            if 'arabic_aliases' in result:
                aliases.extend(result['arabic_aliases'][:10])
            
            # Collect English aliases  
            if 'english_aliases' in result:
                aliases.extend(result['english_aliases'][:10])
            
            # Fallback for different response formats
            if not aliases:
                if isinstance(result, list):
                    aliases = result[:20]
                elif 'aliases' in result:
                    aliases = result['aliases'][:20]
                else:
                    for value in result.values():
                        if isinstance(value, list):
                            aliases.extend(value)
                    aliases = aliases[:20]
            
            self.logger.info(f"Generated {len(aliases)} aliases for {canonical_key}")
            return aliases
            
        except Exception as e:
            self.logger.error(f"AI alias generation failed: {e}")
            return []
    
    def generate_canonical_key(self, query: str) -> str:
        """
        Generate a professional canonical key for a query using AI.
        Results are cached by NFC-normalized query text (up to 256 entries) to avoid
        a redundant LLM call for repeated or near-identical queries.

        Args:
            query: The user query

        Returns:
            A professional canonical key (snake_case, English)
        """
        if not self.client:
            return "general"

        # Normalize before caching so variant spellings/whitespace hit the same entry
        norm_query = unicodedata.normalize('NFC', (query or "")).lower().strip()
        return self._generate_canonical_key_cached(norm_query, query)

    @lru_cache(maxsize=256)
    def _generate_canonical_key_cached(self, norm_query: str, original_query: str) -> str:
        """LRU-cached inner implementation keyed on the normalized query."""
        try:
            prompt = f"""Generate a canonical key for this university query.

Query: "{original_query}"

RULES:
1. Return a single snake_case key in English
2. Key should be specific and descriptive
3. Key should be 2-4 words max
4. Examples: "course_registration", "tuition_fees", "admission_requirements", "academic_calendar"
5. DO NOT use "general" - always be specific

Return JSON:
{{"canonical_key": "your_key_here"}}"""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You generate canonical keys for a university information system. Keys must be specific, descriptive, and in snake_case English.",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                response_format={"type": "json_object"},
            )

            result = json.loads(response.choices[0].message.content)
            key = result.get('canonical_key', 'general')

            # Sanitize: lowercase, underscores only, no special chars
            key = re.sub(r'[^a-z0-9_]', '_', key.lower().replace('-', '_').replace(' ', '_'))
            key = re.sub(r'_+', '_', key).strip('_')

            if not key or key == 'general':
                words = original_query.lower().split()[:3]
                key = '_'.join(w for w in words if w.isalnum())[:30] or 'query'

            self.logger.info(f"Generated canonical key: {key} for query: {original_query[:50]}...")
            return key

        except Exception as e:
            self.logger.error(f"Canonical key generation failed: {e}")
            return "general"

    VISION_ASSISTANT_SYSTEM = """You are a helpful assistant for Jordan University of Science and Technology (JUST) students.
The student uploaded an image and may ask a question about it. Describe relevant details you see (schedules, forms, notices, screenshots, documents).
Answer clearly in English unless the student's message is entirely in Arabic, in which case you may reply in Modern Standard Arabic.
Give practical guidance; when deadlines or fees are unclear from the image alone, say to confirm on the official university website or with their department.
Do not claim to be an official university authority."""

    def generate_vision_answer_stream(
        self, query: str, image_base64: str, mime_type: str
    ):
        """Stream an answer about an uploaded image (vision-capable model required)."""
        if not self.client:
            yield (
                "Image analysis is not available because the language model API is not configured."
            )
            return

        model = self.model
        prompt = (query or "").strip() or (
            "What can you tell me about this image? If it relates to university life at JUST, "
            "explain it in that context."
        )
        data_url = f"data:{mime_type};base64,{image_base64}"

        try:
            stream = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": self.VISION_ASSISTANT_SYSTEM},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url},
                            },
                        ],
                    },
                ],
                temperature=0.35,
                max_tokens=2000,
                stream=True,
            )
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            self.logger.error(f"Vision answer stream failed: {e}")
            kind = _classify_llm_http_error(e)
            if kind == "model":
                yield (
                    "This image could not be analyzed. Set OPENAI_MODEL to a vision-capable model "
                    "(e.g. gpt-4o) in `.env` and restart the server."
                )
            else:
                yield (
                    "Sorry, I could not analyze this image right now. Try again or type your question without the image."
                )
