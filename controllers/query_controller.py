"""
Query Controller - Main business logic for JUST University Assistant.
Chatbot for Jordan University of Science and Technology (JUST)

CORE PHILOSOPHY:
================
- PRIMARY JOB: Search and answer ANY question about the university
- SECONDARY: Use cached data when available (Redis)
- RESOURCES: Helper URLs to provide context to GPT
- If no cache found → AUTO SEARCH and answer

WORKFLOW (OPTIMIZED FOR SPEED):
=========
STEP 1: Try to match query to cached data (embeddings)
STEP 2: If cache HIT → use cached data → return immediately
STEP 3: If cache MISS → AUTO SEARCH with GPT
STEP 4 (/query/stream): Extract from PDF/web first, then stream grounded answer
STEP 5: Background: aliases, embeddings, Redis cache (non-blocking)
"""
import copy
import json
import os
import sys
import threading
from typing import Dict, Any, Optional, List, Tuple, Callable

StepCallback = Callable[[int, str, str], None]
from services.redis_service import RedisService, normalize_alias
from services.openai_service import OpenAIService
from services.alias_service import AliasService
from services.extractor_service import ExtractorService
from services.embeddings_service import EmbeddingsService
from services.planner_service import PlannerService
from config import (
    SIMILARITY_THRESHOLD,
    UNCERTAINTY_GATE,
    EMBEDDING_MATCH_TOP_K,
    ALIAS_FALLBACK_CONFIDENCE_RESOLVED,
    ALIAS_FALLBACK_CONFIDENCE_DATA,
    EXTRACTION_FAILED_SUMMARY,
    EXTRACTION_FAILED_SUMMARY_AR,
)
from logger import (
    log_query_received, log_redis_check, log_redis_data_used,
    log_resource_selection, log_web_search, log_web_extraction_start,
    log_web_extraction_complete, log_json_building_start, log_json_building_complete,
    log_redis_cache_store, log_answer_generation, log_response_ready, log_error,
    log_alias_generation_start, log_alias_generation_complete, get_logger,
    log_embeddings_search, log_embeddings_result, log_canonical_key_generation,
    log_background_task_start, log_background_task_complete, log_step
)

_RESOURCES_JSON_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources.json")


class QueryController:
    """
    Main controller for processing student queries.
    Implements the strict 7-step workflow with embeddings.
    """

    def __init__(self):
        """Initialize all services."""
        self.redis_service = RedisService()
        self.openai_service = OpenAIService()
        # Alias service is query-driven (no static mappings); pass OpenAI for dynamic keys/aliases.
        self.alias_service = AliasService(self.openai_service)
        self.extractor_service = ExtractorService(self.openai_service)
        self.embeddings_service = EmbeddingsService()
        self.planner_service = PlannerService(self.redis_service, self.extractor_service)
        self.logger = get_logger()

        # resources.json mtime-based cache (avoids disk read on every request)
        self._resources: Dict[str, str] = {}
        self._resources_mtime: float = 0.0
        self._resources_lock = threading.Lock()

        # Background cache dedup: prevents parallel threads for the same canonical key
        self._cache_lock = threading.Lock()
        self._caching_keys: set = set()
        self._enrichment_lock = threading.Lock()
        self._enriching_queries: set = set()

    @staticmethod
    def _emit_ui_step(
        on_step: Optional[StepCallback],
        step_num: int,
        label: str,
        detail: str = "",
    ) -> None:
        """Push a pipeline step to the streaming UI (if callback provided)."""
        if on_step:
            on_step(step_num, label, detail or "")

    # ========================================
    # RESOURCES CACHE
    # ========================================

    def _get_resources(self) -> Dict[str, str]:
        """
        Return resources.json content, re-reading only when the file changes on disk.
        Thread-safe via a lock; falls back to the last-known-good dict on error.
        """
        try:
            mtime = os.path.getmtime(_RESOURCES_JSON_PATH)
        except OSError:
            return self._resources

        with self._resources_lock:
            if mtime != self._resources_mtime:
                try:
                    with open(_RESOURCES_JSON_PATH, "r", encoding="utf-8") as f:
                        self._resources = json.load(f)
                    self._resources_mtime = mtime
                except Exception as e:
                    self.logger.error(f"Failed to load resources.json: {e}")
            return self._resources

    # ========================================
    # PLANNER HELPERS
    # ========================================

    def _build_planner_json(self, query: str, planner_context: Dict[str, Any]) -> Dict[str, Any]:
        """Structured payload for academic planning with official PDF course catalog."""
        return {
            "topic": "academic_planning",
            "planner_context": planner_context,
            "user_message": (query or "").strip(),
        }

    def _prepare_planner_payload(
        self, query: str, planner_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Load official study-plan PDF for the selected program and attach courses."""
        resources = self._get_resources()
        enriched = self.planner_service.enrich_planner_context(
            planner_context, resources
        )
        official = enriched.get("official_study_plan") or {}
        if official.get("available"):
            n = len(official.get("all_courses") or [])
            self.logger.info(
                f"Planner loaded official study plan: "
                f"{enriched.get('program', {}).get('resource_key')} ({n} courses)"
            )
        else:
            self.logger.warning(
                f"Planner has no official PDF courses: {official.get('reason', 'unknown')}"
            )
        payload = self._build_planner_json(query, enriched)
        pdf_url = (
            official.get("source_url")
            or official.get("url")
            or (enriched.get("program") or {}).get("plan_pdf_url")
        )
        if pdf_url:
            payload["url"] = pdf_url
            payload["source_url"] = pdf_url
            payload["resource_key"] = official.get("resource_key")
        return payload

    def _is_valid_planner_context(self, planner_context: Optional[Dict[str, Any]]) -> bool:
        if not planner_context or not isinstance(planner_context, dict):
            return False
        prog = planner_context.get("program")
        if not isinstance(prog, dict):
            return False
        return bool(prog.get("id") or prog.get("name_ar"))

    # ========================================
    # INPUT VALIDATION
    # ========================================

    def _validate_provided_json(self, redis_json: Optional[Dict[str, Any]]) -> bool:
        """
        Basic sanity check for client-supplied redis_json.
        Rejects oversized payloads (> 500 KB) to prevent bypass of Redis/TTL story.
        """
        if redis_json is None or redis_json == {}:
            return False
        try:
            raw = json.dumps(redis_json)
            if len(raw) > 500_000:
                self.logger.warning("Provided redis_json exceeds 500 KB — ignoring")
                return False
        except (TypeError, ValueError):
            self.logger.warning("Provided redis_json is not JSON-serialisable — ignoring")
            return False
        return True

    # ========================================
    # STREAMING PATH
    # ========================================

    def process_query_for_streaming(
        self,
        query: str,
        redis_json: Optional[Dict[str, Any]] = None,
        planner_context: Optional[Dict[str, Any]] = None,
        on_step: Optional[StepCallback] = None,
    ) -> Dict[str, Any]:
        """
        Process query and prepare data for streaming.
        Returns the JSON data and metadata without generating the answer.
        The answer will be streamed separately.

        Args:
            query: Student's question
            redis_json: Optional cached JSON
            planner_context: Optional structured academic plan request (skips RAG/extraction)

        Returns:
            {
                "source": "redis" | "live_web" | "planner",
                "json": {...},
                "aliases": [...]
            }
        """
        has_provided_json = self._validate_provided_json(redis_json)

        if has_provided_json:
            self.logger.info("Using provided Redis JSON (skip workflow)")
            self._emit_ui_step(on_step, 1, "Using cached data", "Skipping search pipeline")
            original_json = copy.deepcopy(redis_json)
            aliases = original_json.get('aliases', [])
            return {
                "source": "redis",
                "json": original_json,
                "aliases": aliases,
            }

        if self._is_valid_planner_context(planner_context):
            self.logger.info(
                "Academic planner: loading official study-plan PDF for selected program"
            )
            self._emit_ui_step(
                on_step, 1, "Loading study plan", "Reading official PDF from resources.json"
            )
            payload = self._prepare_planner_payload(query, planner_context)
            program = (planner_context.get("program") or {}).get("name") or "program"
            self._emit_ui_step(
                on_step, 2, "Parsing study plan", f"Building course list for {program}"
            )
            return {
                "source": "planner",
                "json": payload,
                "aliases": [],
            }

        # ========================================
        # STEP 1: EMBEDDINGS + COSINE SIMILARITY
        # ========================================
        log_step(1, "EMBEDDINGS + COSINE SIMILARITY", "Searching for matching aliases")
        self._emit_ui_step(
            on_step, 1, "Embeddings + cosine similarity", "Searching for matching aliases"
        )
        log_embeddings_search(query)
        sys.stdout.flush()

        canonical_key, confidence = self._match_with_embeddings(query, fast_stream=True)

        if canonical_key:
            log_embeddings_result(True, canonical_key, confidence)

            # ========================================
            # STEP 2: REDIS CACHE CHECK
            # ========================================
            log_step(2, "REDIS CACHE CHECK", f"Key: {canonical_key}")
            self._emit_ui_step(
                on_step, 2, "Redis cache check", f"Key: {canonical_key}"
            )
            log_redis_check(True)

            cached_data = self.redis_service.fetch_from_redis(canonical_key)

            if cached_data and not self._is_placeholder_cache(cached_data):
                log_redis_data_used(canonical_key)
                self.logger.info(f"Cache HIT: Using cached data for {canonical_key}")
                self._emit_ui_step(
                    on_step, 3, "Cache hit", f"Loaded cached answer for {canonical_key}"
                )
                sys.stdout.flush()
                aliases = cached_data.get('aliases', [])
                return {
                    "source": "redis",
                    "json": cached_data,
                    "aliases": aliases,
                }
            elif cached_data:
                self.logger.info(
                    f"Cache HIT ignored (placeholder data) for {canonical_key} — re-extracting"
                )
            else:
                self.logger.info(f"Cache MISS: Key {canonical_key} found but no data")
                sys.stdout.flush()
        else:
            log_embeddings_result(False)
            log_redis_check(False)
            self.logger.info("No embedding match - trying quick Redis lookup")
            self._emit_ui_step(
                on_step, 2, "Redis cache check", "No alias match — checking cache"
            )
            sys.stdout.flush()

        quick_key, cached_data = self._quick_redis_lookup(query)
        if cached_data and not self._is_placeholder_cache(cached_data):
            log_redis_data_used(quick_key)
            self.logger.info(f"Quick cache HIT: {quick_key}")
            self._emit_ui_step(
                on_step, 3, "Cache hit", f"Loaded cached answer for {quick_key}"
            )
            return {
                "source": "redis",
                "json": cached_data,
                "aliases": cached_data.get("aliases", []),
            }
        if cached_data:
            self.logger.info(f"Quick cache ignored (placeholder) for {quick_key}")

        # Extract PDF/web data before streaming so answers use real fees/tables (not a placeholder)
        return self._prepare_live_web_data(query, canonical_key, on_step=on_step)

    def _is_placeholder_cache(self, data: Optional[Dict[str, Any]]) -> bool:
        """Skip Redis rows saved during fast-first (empty summary, no PDF fields)."""
        if not data:
            return False
        if data.get("fast_first"):
            return True
        summary = (data.get("summary") or "").strip()
        if summary not in (EXTRACTION_FAILED_SUMMARY, EXTRACTION_FAILED_SUMMARY_AR):
            return False
        return not (
            data.get("url")
            or data.get("source_url")
            or data.get("fees")
            or data.get("fee_items")
        )

    def _quick_redis_lookup(
        self, query: str
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """
        Local-only Redis lookup (no LLM). Used before streaming so the user is not blocked.
        """
        resolved = self.redis_service.resolve_alias(query)
        if resolved:
            data = self.redis_service.fetch_from_redis(resolved)
            if data:
                return resolved, data

        quick_key = self.alias_service.build_canonical_key(query, use_ai=False)
        data = self.redis_service.fetch_from_redis(quick_key)
        if data:
            return quick_key, data

        return quick_key, None

    def _prepare_fast_stream_response(self, query: str) -> Dict[str, Any]:
        """
        Return minimal JSON immediately so /query/stream can start the LLM answer.
        Full extraction, aliases, and Redis cache run in a background thread.
        """
        quick_key, _ = self._quick_redis_lookup(query)
        hint_key = quick_key or "university_query"
        log_step(2, "FAST RESPONSE", "Streaming answer now; enrichment in background")
        sys.stdout.flush()

        self._start_background_enrichment(query, hint_key)

        return {
            "source": "live_web",
            "json": {
                "topic": hint_key,
                "query": query,
                "summary": EXTRACTION_FAILED_SUMMARY,
                "fast_first": True,
            },
            "aliases": [query],
        }

    def _extract_live_json(
        self,
        query: str,
        canonical_key: Optional[str],
        on_step: Optional[StepCallback] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Full live pipeline: canonical key, resource selection, extraction.
        Used by sync /query and background enrichment (not before first stream chunk).
        """
        if not canonical_key or canonical_key == "general":
            # Prefer an existing Redis alias/key before generating a new AI topic id
            resolved = self.redis_service.resolve_alias(query)
            if resolved:
                canonical_key = resolved
                self.logger.info(f"Resolved canonical key from query alias: {canonical_key}")
                self._emit_ui_step(
                    on_step, 3, "Topic identifier", f"Resolved key: {canonical_key}"
                )
            else:
                log_step(2, "GENERATE CANONICAL KEY", "Creating topic identifier")
                sys.stdout.flush()
                canonical_key = self.openai_service.generate_canonical_key(query)
                log_canonical_key_generation(query, canonical_key)
                self._emit_ui_step(
                    on_step,
                    3,
                    "Generate canonical key",
                    f"Key: {canonical_key}",
                )
                sys.stdout.flush()
        elif not canonical_key or canonical_key in ("general", "university_query"):
            refined = self.openai_service.generate_canonical_key(query)
            if refined and refined.strip().lower() not in ("", "general"):
                canonical_key = refined.strip()
        elif canonical_key:
            self._emit_ui_step(
                on_step, 3, "Topic identifier", f"Using matched key: {canonical_key}"
            )

        log_step(3, "RESOURCE SELECTION", "Finding best resource URL")
        resources = self._get_resources()
        selected_key, selected_url = None, None
        if resources:
            selected_key, selected_url = self.openai_service.select_best_resource(
                query, resources
            )
            if selected_key and selected_key in resources:
                selected_url = resources.get(selected_key)
            elif selected_url and selected_url in resources.values():
                for k, v in resources.items():
                    if v == selected_url:
                        selected_key = k
                        break
            else:
                selected_key, selected_url = None, None
            log_resource_selection(query, selected_url if selected_key else None)

        if selected_key:
            resource_label = selected_key.replace("_", " ")
            self._emit_ui_step(
                on_step, 4, "Resource selection", f"Selected: {resource_label}"
            )
        else:
            self._emit_ui_step(
                on_step, 4, "Resource selection", "No direct match — using web search"
            )

        is_pdf = bool(selected_url and ".pdf" in selected_url.lower())
        extract_detail = (
            "Parsing official university PDF"
            if is_pdf
            else "Reading official university page"
        )
        log_step(4, "DATA EXTRACTION", f"Extracting from {'PDF' if is_pdf else 'Web'}")
        self._emit_ui_step(on_step, 5, "Data extraction", extract_detail)
        log_web_extraction_start(selected_url or "Web Search")
        sys.stdout.flush()

        json_data = self.extractor_service.extract_data(
            canonical_key,
            query,
            resource_url=selected_url,
            resource_key=selected_key,
        )

        if json_data:
            log_web_extraction_complete(
                selected_url or canonical_key, True, len(str(json_data))
            )
            log_json_building_complete(list(json_data.keys()))
        else:
            log_web_extraction_complete(selected_url or canonical_key, False)
            json_data = {
                "topic": canonical_key,
                "query": query,
                "source": "knowledge",
                "message": "Information was searched from Jordan University of Science and Technology (JUST) sources.",
            }

        json_data["topic"] = canonical_key
        return canonical_key, json_data

    def _prepare_live_web_data(
        self,
        query: str,
        canonical_key: Optional[str],
        on_step: Optional[StepCallback] = None,
    ) -> Dict[str, Any]:
        """Sync/blocking live path (used by non-streaming /query only)."""
        canonical_key, json_data = self._extract_live_json(
            query, canonical_key, on_step=on_step
        )
        log_background_task_start("Caching & Alias Generation")
        self._start_background_cache(canonical_key, json_data, query)
        return {
            "source": "live_web",
            "json": json_data,
            "aliases": [query],
        }

    # ========================================
    # SYNC PATH
    # ========================================

    def process_query(
        self,
        query: str,
        redis_json: Optional[Dict[str, Any]] = None,
        planner_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process a student query following the STRICT 7-step workflow.

        WORKFLOW:
        =========
        STEP 1: Embeddings + Cosine Similarity
        STEP 2: Redis Cache Check
        STEP 3: Resource Selection
        STEP 4: ChatGPT Web Search
        STEP 5: Auto-Generate Aliases
        STEP 6: Store in Redis
        STEP 7: Return Result

        Args:
            query: Student's question
            redis_json: Optional cached JSON (if provided, skip to answer generation)
            planner_context: Optional structured academic plan (skips RAG; generates plan answer only)

        Returns:
            {
                "source": "redis" | "live_web" | "planner",
                "json": {...},
                "aliases": [...],
                "answer": "..."
            }
        """
        has_provided_json = self._validate_provided_json(redis_json)
        log_query_received(query, has_provided_json)

        if has_provided_json:
            self.logger.info("Using provided Redis JSON (skip workflow)")
            return self._handle_redis_data(redis_json, query)

        if self._is_valid_planner_context(planner_context):
            self.logger.info("Academic planner (sync): loading official study-plan PDF")
            payload_json = self._prepare_planner_payload(query, planner_context)
            log_answer_generation("planner")
            answer = self.openai_service.generate_answer(payload_json, query, "planner")
            result = {
                "source": "planner",
                "json": payload_json,
                "aliases": [],
                "answer": answer,
            }
            log_response_ready("planner", len(answer), 0)
            return result

        # ========================================
        # STEP 1: EMBEDDINGS + COSINE SIMILARITY
        # ========================================
        self.logger.info("STEP 1: Embeddings + Cosine Similarity matching")

        canonical_key, confidence = self._match_with_embeddings(query)

        if canonical_key:
            # ========================================
            # STEP 2: REDIS CACHE CHECK
            # ========================================
            self.logger.info(f"STEP 2: Redis cache check for key: {canonical_key}")
            log_redis_check(True)

            cached_data = self.redis_service.fetch_from_redis(canonical_key)

            if cached_data and not self._is_placeholder_cache(cached_data):
                log_redis_data_used(canonical_key)
                self.logger.info(f"Cache HIT: Using cached data for {canonical_key}")
                return self._handle_redis_data(cached_data, query)
            elif cached_data:
                self.logger.info(
                    f"Cache HIT ignored (placeholder data) for {canonical_key} — re-extracting"
                )
            else:
                self.logger.info(f"Cache MISS: Key {canonical_key} found but no data")
        else:
            log_redis_check(False)
            self.logger.info("No matching alias found - proceeding to live extraction")

        return self._handle_live_web(query, canonical_key)

    # ========================================
    # EMBEDDING MATCHING
    # ========================================

    def _match_with_embeddings(
        self, query: str, *, fast_stream: bool = False
    ) -> Tuple[Optional[str], float]:
        """
        STEP 1: Match query to aliases using embeddings + cosine similarity.

        1. Generate embedding for query
        2. Compare with all stored alias embeddings
        3. If similarity >= threshold: use match directly
        4. If similarity is in uncertain range: ask ChatGPT to validate

        Args:
            query: User query

        Returns:
            Tuple of (canonical_key, confidence) or (None, 0)
        """
        if not self.embeddings_service.is_configured():
            self.logger.warning("Embeddings service not configured - using fallback")
            return self._fallback_alias_matching(query)

        alias_embeddings = self.redis_service.get_all_alias_embeddings()

        if not alias_embeddings:
            self.logger.info("No alias embeddings stored yet")
            return self._fallback_alias_matching(query)

        best_alias, canonical_key, score, is_confident = \
            self.embeddings_service.match_query_to_aliases(query, alias_embeddings)

        if best_alias and not canonical_key:
            resolved_key = self.redis_service.resolve_alias(best_alias)
            if resolved_key:
                canonical_key = resolved_key
                self.logger.info(f"Resolved canonical key from alias '{best_alias}': {canonical_key}")

        self.logger.info(
            f"Embedding match: alias='{best_alias}', key={canonical_key}, "
            f"score={score:.4f}, confident={is_confident}"
        )

        if is_confident and canonical_key:
            return canonical_key, score

        # Streaming skips LLM validation — still accept strong-but-uncertain matches
        if fast_stream and canonical_key and score >= UNCERTAINTY_GATE:
            self.logger.info(
                f"Fast-stream cache match (score={score:.4f} >= gate={UNCERTAINTY_GATE}): {canonical_key}"
            )
            return canonical_key, score

        elif best_alias and score > UNCERTAINTY_GATE and not fast_stream:
            # Uncertain range — ask ChatGPT to validate (skipped on /query/stream for speed)
            self.logger.info("Uncertain match - asking ChatGPT to validate")
            query_embedding = self.embeddings_service.generate_embedding(query)
            candidates = self._get_top_candidates(query_embedding, alias_embeddings, top_k=EMBEDDING_MATCH_TOP_K)

            if candidates:
                validated_alias, validated_key, confidence = \
                    self.openai_service.validate_alias_match(
                        query,
                        candidates['aliases'],
                        candidates['scores'],
                    )
                if validated_key:
                    return validated_key, confidence

        # Final fallback: try to resolve from alias
        if best_alias and not canonical_key:
            resolved_key = self.redis_service.resolve_alias(best_alias)
            if resolved_key:
                self.logger.info(f"Fallback: Resolved key '{resolved_key}' from alias '{best_alias}'")
                return resolved_key, score

        return self._fallback_alias_matching(query)

    def _get_top_candidates(
        self,
        query_embedding: Optional[List[float]],
        alias_embeddings: Dict[str, Dict],
        top_k: int = EMBEDDING_MATCH_TOP_K,
    ) -> Optional[Dict]:
        """
        Get top K candidate matches for ChatGPT validation.
        Accepts a pre-computed query_embedding to avoid a duplicate API call.
        """
        if not query_embedding:
            return None

        scores = []
        for alias, data in alias_embeddings.items():
            embedding = data.get('embedding')
            if embedding:
                score = self.embeddings_service.cosine_similarity(query_embedding, embedding)
                scores.append({
                    'alias': alias,
                    'canonical_key': data.get('canonical_key'),
                    'score': score,
                })

        scores.sort(key=lambda x: x['score'], reverse=True)
        top = scores[:top_k]

        if not top:
            return None

        return {
            'aliases': [{'alias': s['alias'], 'canonical_key': s['canonical_key']} for s in top],
            'scores': [s['score'] for s in top],
        }

    def _fallback_alias_matching(self, query: str) -> Tuple[Optional[str], float]:
        """
        Fast Redis lookup when embeddings are unavailable (no LLM before response).
        """
        canonical_key, cached = self._quick_redis_lookup(query)
        if not canonical_key:
            return None, 0.0
        if self.redis_service.resolve_alias(query):
            return canonical_key, ALIAS_FALLBACK_CONFIDENCE_RESOLVED
        if cached:
            return canonical_key, ALIAS_FALLBACK_CONFIDENCE_DATA
        return None, 0.0

    # ========================================
    # REDIS & LIVE WEB HANDLERS
    # ========================================

    def _handle_redis_data(self, redis_json: Dict[str, Any], query: str) -> Dict[str, Any]:
        """
        Handle query when Redis data is available.

        STRICT RULES:
        - Use Redis JSON exactly as-is
        - Do NOT modify the JSON
        - Do NOT fetch new data
        - Do NOT regenerate aliases
        - Pass JSON to ChatGPT for VERY DETAILED answer
        """
        original_json = copy.deepcopy(redis_json)

        topic_key = original_json.get('topic', 'unknown')
        log_redis_data_used(topic_key)

        aliases = original_json.get('aliases', [])
        clean_json = {k: v for k, v in original_json.items() if k != 'aliases'}

        log_answer_generation("redis")
        answer = self.openai_service.generate_answer(clean_json, query, "redis")

        result = {
            "source": "redis",
            "json": original_json,
            "aliases": aliases,
            "answer": answer,
        }

        log_response_ready("redis", len(answer), len(aliases))
        return result

    def _handle_live_web(self, query: str, canonical_key: Optional[str]) -> Dict[str, Any]:
        """
        Handle query with live search (sync /query endpoint).
        """
        self.logger.info("STEP 1–2: Extract live data")
        canonical_key, json_data = self._extract_live_json(query, canonical_key)

        self.logger.info("STEP 3: Generate answer")
        log_answer_generation("live_web")

        answer = self.openai_service.generate_answer(json_data, query, "live_web")

        result = {
            "source": "live_web",
            "json": json_data,
            "aliases": [query],
            "answer": answer,
        }

        log_response_ready("live_web", len(answer), 1)

        # ========================================
        # STEP 4: BACKGROUND TASKS (Non-blocking, deduplicated)
        # ========================================
        self.logger.info("STEP 4: Starting background caching tasks")
        self._start_background_cache(canonical_key, json_data, query)

        return result

    # ========================================
    # BACKGROUND ENRICHMENT + CACHE
    # ========================================

    def _start_background_enrichment(self, query: str, hint_key: str) -> None:
        """Extract, alias, embed, and cache after the user already received a streamed reply."""
        norm = " ".join((query or "").split()).lower()[:200]
        with self._enrichment_lock:
            if norm in self._enriching_queries:
                self.logger.debug("Background enrichment already running for this query")
                return
            self._enriching_queries.add(norm)

        thread = threading.Thread(
            target=self._background_enrichment_task,
            args=(query, hint_key, norm),
            daemon=True,
        )
        thread.start()
        self.logger.info("Background enrichment started (extract + cache, non-blocking)")

    def _background_enrichment_task(
        self, query: str, hint_key: str, dedup_key: str
    ) -> None:
        try:
            log_background_task_start("Live extraction + cache")
            canonical_key, json_data = self._extract_live_json(query, hint_key)
            self._start_background_cache(canonical_key, json_data, query)
            log_background_task_complete("Live extraction + cache", True)
        except Exception as e:
            self.logger.error(f"Background enrichment failed: {e}")
            log_background_task_complete("Live extraction + cache", False)
        finally:
            with self._enrichment_lock:
                self._enriching_queries.discard(dedup_key)

    def _start_background_cache(
        self, canonical_key: str, json_data: Dict[str, Any], query: str
    ) -> None:
        """
        Start background caching thread if one is not already running for this key.
        Deduplication prevents parallel writes for identical canonical keys.
        """
        with self._cache_lock:
            if canonical_key in self._caching_keys:
                self.logger.debug(f"Background cache already in progress for '{canonical_key}', skipping")
                return
            self._caching_keys.add(canonical_key)

        background_thread = threading.Thread(
            target=self._background_cache_task,
            args=(canonical_key, json_data, query),
            daemon=True,
        )
        background_thread.start()
        self.logger.info(f"Background caching task started for '{canonical_key}' (non-blocking)")

    def _background_cache_task(self, canonical_key: str, json_data: Dict[str, Any], query: str):
        """
        Background task to generate aliases, embeddings, and cache data.
        Runs in a daemon thread; does not block the user response.
        """
        try:
            self.logger.debug(f"Background: Starting cache task for {canonical_key}")

            log_alias_generation_start()
            aliases = []

            if self.openai_service.is_configured():
                ai_aliases = self.openai_service.generate_aliases_with_ai(canonical_key, query)
                if ai_aliases:
                    aliases = ai_aliases
                    self.logger.debug(f"  → Generated {len(aliases)} AI aliases")

            # Ensure the original query is included
            if query.lower() not in [a.lower() for a in aliases]:
                aliases.insert(0, query)

            log_alias_generation_complete(canonical_key, len(aliases))

            if self.redis_service.is_connected():
                alias_embeddings = {}
                if self.embeddings_service.is_configured():
                    self.logger.debug(f"  → Generating embeddings for {len(aliases)} aliases")
                    embeddings_batch = self.embeddings_service.generate_embeddings_batch(aliases)
                    for alias, embedding in embeddings_batch.items():
                        alias_embeddings[normalize_alias(alias)] = embedding

                json_data['aliases'] = aliases

                success = self.redis_service.save_to_redis(
                    canonical_key,
                    json_data,
                    aliases,
                    alias_embeddings,
                )
                log_redis_cache_store(canonical_key, success)
                log_background_task_complete("Caching & Alias Generation", success)
            else:
                self.logger.debug("  → Redis not connected, skipping cache")
                log_background_task_complete("Caching & Alias Generation", False)

        except Exception as e:
            self.logger.error(f"Background cache task failed: {e}")
            log_background_task_complete("Caching & Alias Generation", False)
        finally:
            with self._cache_lock:
                self._caching_keys.discard(canonical_key)

    # ========================================
    # API HELPER METHODS
    # ========================================

    def get_cached_data(self, topic_key: str) -> Optional[Dict[str, Any]]:
        """Get cached data for a topic."""
        return self.redis_service.fetch_from_redis(topic_key)

    def generate_aliases(self, query: str) -> Dict[str, Any]:
        """Generate aliases for a query without full processing."""
        result = self.alias_service.process_query(query)

        if self.redis_service.is_connected():
            canonical_key = result['canonical_key']
            aliases = result['aliases']

            if self.embeddings_service.is_configured():
                embeddings = self.embeddings_service.generate_embeddings_batch(aliases)
                for alias, embedding in embeddings.items():
                    self.redis_service.store_alias_embedding(alias, embedding, canonical_key)

        return {
            "canonical_key": result['canonical_key'],
            "aliases": result['aliases'],
        }

    def get_aliases(self, canonical_key: str) -> Dict[str, Any]:
        """Get aliases for a canonical key."""
        aliases = self.redis_service.get_aliases_for_key(canonical_key)
        return {
            "canonical_key": canonical_key,
            "aliases": aliases,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get system statistics."""
        redis_stats = self.redis_service.get_stats()
        return {
            "redis_connected": self.redis_service.is_connected(),
            "openai_configured": self.openai_service.is_configured(),
            "embeddings_configured": self.embeddings_service.is_configured(),
            "similarity_threshold": SIMILARITY_THRESHOLD,
            **redis_stats,
        }

    def get_study_plan_resources(self) -> Dict[str, Any]:
        """Official study-plan PDF URLs from resources.json + program id mapping."""
        from services.planner_service import (
            PROGRAM_RESOURCE_MAP,
            study_plan_urls_from_resources,
        )

        resources = self._get_resources()
        return {
            "program_map": PROGRAM_RESOURCE_MAP,
            "plans": study_plan_urls_from_resources(resources),
        }


class OutputValidator:
    """Validates that output follows the strict format requirements."""

    @staticmethod
    def validate_output(output: Dict[str, Any]) -> tuple:
        """
        Validate output format.

        Required format:
        {
            "source": "redis" | "live_web" | "planner",
            "json": {...},
            "aliases": [...],
            "answer": "..."
        }
        """
        required_keys = ["source", "json", "answer"]
        for key in required_keys:
            if key not in output:
                return False, f"Missing required key: {key}"

        if output["source"] not in ["redis", "live_web", "planner"]:
            return False, f"Invalid source: {output['source']}"

        if not isinstance(output["json"], dict):
            return False, "Field 'json' must be a dictionary"

        if not isinstance(output["answer"], str):
            return False, "Field 'answer' must be a string"

        if "aliases" in output and not isinstance(output["aliases"], list):
            return False, "Field 'aliases' must be a list"

        for src in ("live_web", "planner"):
            if output["source"] == src and "aliases" not in output:
                return False, f"Missing 'aliases' for {src} source"

        return True, ""
