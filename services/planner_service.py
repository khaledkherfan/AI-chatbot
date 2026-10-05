"""
Academic planner — load official study-plan PDFs from resources.json and enrich planner_context.
"""
import copy
from typing import Any, Dict, Optional, Tuple

from logger import get_logger

# One official study-plan PDF per program (resources.json keys, lines 6–10)
PROGRAM_RESOURCE_MAP: Dict[str, str] = {
    "se": "Software_Engineering_Plan",
    "robotics": "Robotics_Plan",
    "cyber": "Cybersecurity_Plan",
    "cs": "Computer_Science_Plan",
    "ai": "Artificial_Intelligence_Plan",
}

# resources.json keys → used for direct resource_key hints from the UI
STUDY_PLAN_RESOURCE_KEYS = frozenset(PROGRAM_RESOURCE_MAP.values())


def expected_resource_key_for_program(program_id: str) -> Optional[str]:
    """Canonical resources.json key for a planner program id."""
    return PROGRAM_RESOURCE_MAP.get((program_id or "").strip().lower())


def study_plan_urls_from_resources(resources: Dict[str, str]) -> Dict[str, str]:
    """Return study-plan PDF URLs from resources.json (keys lines 6–10)."""
    return {k: resources[k] for k in STUDY_PLAN_RESOURCE_KEYS if k in resources}


class PlannerService:
    """Resolve program → PDF, extract/cache official course catalog for the planner."""

    CACHE_KEY_PREFIX = "study_plan_"

    def __init__(self, redis_service, extractor_service):
        self.redis = redis_service
        self.extractor = extractor_service
        self.logger = get_logger()

    def resolve_resource(
        self,
        program_id: str,
        resources: Dict[str, str],
        *,
        resource_key_hint: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Return (resource_key, pdf_url) strictly from program id → resources.json."""
        if not resources:
            return None, None

        pid = (program_id or "").strip().lower()
        key = expected_resource_key_for_program(pid)

        if resource_key_hint and key and resource_key_hint != key:
            self.logger.warning(
                f"Ignoring client resource_key '{resource_key_hint}' for program '{pid}'; "
                f"using '{key}' from PROGRAM_RESOURCE_MAP"
            )

        if key and key in resources:
            return key, resources[key]

        return None, None

    def _cache_key(self, program_id: str, resource_key: Optional[str] = None) -> str:
        pid = (program_id or "unknown").strip().lower()
        if resource_key:
            return f"{self.CACHE_KEY_PREFIX}{pid}_{resource_key}"
        return f"{self.CACHE_KEY_PREFIX}{pid}"

    def _unavailable(
        self,
        program_id: str,
        reason: str,
        *,
        resource_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "available": False,
            "program_id": program_id,
            "resource_key": resource_key,
            "reason": reason,
            "semesters": [],
            "all_courses": [],
        }

    def load_official_study_plan(
        self,
        program_id: str,
        resources: Dict[str, str],
        *,
        resource_key_hint: Optional[str] = None,
        program_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Load structured courses from the official PDF (Redis cache → live extract).
        """
        program_id = (program_id or "").strip().lower()
        expected_key = expected_resource_key_for_program(program_id)
        if not expected_key:
            return self._unavailable(
                program_id,
                "This program is not mapped to a study-plan PDF in resources.json.",
            )

        if resource_key_hint and resource_key_hint != expected_key:
            self.logger.warning(
                f"Client sent resource_key '{resource_key_hint}' for '{program_id}'; "
                f"using '{expected_key}'"
            )

        resolved_key = expected_key
        cache_key = self._cache_key(program_id, resolved_key)

        if self.redis.is_connected():
            cached = self.redis.fetch_from_redis(cache_key)
            if cached and cached.get("available") and (
                cached.get("semesters") or cached.get("all_courses")
            ):
                if cached.get("resource_key") == resolved_key:
                    self.logger.info(f"Study plan cache HIT: {cache_key}")
                    return cached
            # Drop legacy/wrong cache entries for this program
            legacy = self._cache_key(program_id)
            if legacy != cache_key:
                self.redis.delete_key(legacy)

        resource_key, pdf_url = self.resolve_resource(
            program_id,
            resources,
            resource_key_hint=resolved_key,
        )
        if not pdf_url:
            return self._unavailable(
                program_id,
                "No official study-plan PDF is mapped for this program in resources.json.",
            )

        self.logger.info(
            f"Extracting official study plan: program={program_id} resource={resource_key}"
        )
        extracted = self.extractor.extract_study_plan_pdf(
            pdf_url,
            program_id=program_id,
            resource_key=resource_key,
            program_name=program_name,
        )
        if not extracted or not extracted.get("available"):
            return extracted or self._unavailable(
                program_id,
                "Could not extract course data from the official study-plan PDF.",
                resource_key=resource_key,
            )

        if self.redis.is_connected():
            self.redis.save_to_redis(
                cache_key,
                extracted,
                aliases=[],
                alias_embeddings=None,
            )

        return extracted

    def enrich_planner_context(
        self,
        planner_context: Dict[str, Any],
        resources: Dict[str, str],
    ) -> Dict[str, Any]:
        """Attach official_study_plan + PDF metadata to planner_context."""
        ctx = copy.deepcopy(planner_context)
        prog = ctx.get("program") or {}
        program_id = (prog.get("id") or "").strip().lower()
        resource_hint = prog.get("resource_key")

        official = self.load_official_study_plan(
            program_id,
            resources,
            resource_key_hint=resource_hint,
            program_name=prog.get("name_en") or prog.get("name_ar"),
        )
        ctx["official_study_plan"] = official

        if official.get("available"):
            prog["resource_key"] = official.get("resource_key")
            pdf_url = official.get("source_url") or official.get("url")
            prog["plan_pdf_url"] = pdf_url
            prog["plan_url"] = pdf_url
            if official.get("total_credit_hours"):
                profile = ctx.setdefault("profile", {})
                if not profile.get("target_hours"):
                    try:
                        profile["target_hours"] = int(official["total_credit_hours"])
                    except (TypeError, ValueError):
                        pass
        else:
            prog["official_plan_note"] = official.get("reason")

        ctx["program"] = prog
        return ctx
