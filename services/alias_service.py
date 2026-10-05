"""
Alias service for JUST University Assistant.
خدمة الأسماء المستعارة لمساعد جامعة العلوم والتكنولوجيا

Handles normalization, multilingual support, alias generation and validation.
NEVER uses 'general' - always generates specific keys.
"""
import re
import unicodedata
from typing import Dict, List, Optional, Tuple, Any
from logger import get_logger


def normalize_for_storage(text: str) -> str:
    """
    Shared NFC normalizer used for all alias storage and lookup paths.
    Must match the normalizer in redis_service._normalize_alias to guarantee
    that stored keys are always found by queried keys.
    """
    return unicodedata.normalize('NFC', text).lower().strip()


class AliasService:
    """
    Maps student queries to canonical Redis keys and generates aliases.

    PHILOSOPHY:
    - NEVER use 'general' as a key
    - Generate specific, descriptive keys
    - Support Arabic and English
    """

    def __init__(self, openai_service: Optional[Any] = None):
        """
        Initialize alias service.

        IMPORTANT DESIGN NOTE:
        This service intentionally avoids static keyword/topic mappings.
        Canonical key + aliases are generated dynamically from the query,
        using the LLM when configured, with a generic fallback otherwise.
        """
        self.logger = get_logger()
        self.openai_service = openai_service

    # ========================================
    # NORMALIZATION
    # ========================================

    def normalize_input(self, query: str) -> Tuple[str, str]:
        """
        Normalize user input.

        Args:
            query: Raw user query

        Returns:
            Tuple of (normalized_query, detected_language)
        """
        if not query:
            return '', 'unknown'

        # Remove extra whitespace
        normalized = ' '.join(query.split())

        # NFC normalization — consistent with Redis storage keys
        normalized = unicodedata.normalize('NFC', normalized)

        # Detect language
        language = self._detect_language(normalized)

        # Lowercase English parts while preserving Arabic
        if language in ('english', 'mixed'):
            words = normalized.split()
            normalized_words = []
            for word in words:
                if self._is_english(word):
                    normalized_words.append(word.lower())
                else:
                    normalized_words.append(word)
            normalized = ' '.join(normalized_words)

        return normalized, language

    def _detect_language(self, text: str) -> str:
        """Detect if text is Arabic, English, or mixed."""
        arabic_chars = sum(1 for char in text if '\u0600' <= char <= '\u06FF')
        total_chars = len([c for c in text if c.isalpha()])

        if total_chars == 0:
            return 'unknown'

        arabic_ratio = arabic_chars / total_chars

        if arabic_ratio > 0.5:
            return 'arabic'
        elif arabic_ratio > 0:
            return 'mixed'
        else:
            return 'english'

    def _is_english(self, word: str) -> bool:
        """Check if word is primarily English."""
        english_chars = sum(1 for char in word if char.isalpha() and ord(char) < 128)
        return english_chars > len(word) * 0.5

    # ========================================
    # CANONICAL KEY MAPPING
    # ========================================

    def build_canonical_key(self, query: str, *, use_ai: bool = True) -> str:
        """
        Build canonical key from query.

        Args:
            query: User query
            use_ai: When False, skip LLM (fast path for immediate user response).

        Returns:
            Canonical key string
        """
        normalized, language = self.normalize_input(query)
        return self.map_to_canonical_key(normalized, language, use_ai=use_ai)

    def map_to_canonical_key(
        self, normalized_query: str, language: str, *, use_ai: bool = True
    ) -> str:
        """
        Map normalized input to canonical Redis key.
        NEVER returns 'general' - always generates a specific key.

        Args:
            normalized_query: Normalized query
            language: Detected language

        Returns:
            Canonical key (never 'general')
        """
        # Prefer AI-generated canonical key when available (skipped on fast path).
        if use_ai:
            try:
                if self.openai_service and getattr(self.openai_service, "is_configured", None):
                    if self.openai_service.is_configured():
                        key = self.openai_service.generate_canonical_key(normalized_query)
                        if key and isinstance(key, str) and key.strip() and key.strip().lower() != "general":
                            return key.strip()
            except Exception as e:
                self.logger.debug(f"AI canonical key generation unavailable: {e}")

        # Generic fallback: generate a key from the query (no topic dictionary).
        generated_key = self._generate_key_from_query(normalized_query, language)
        self.logger.debug(f"Generated key '{generated_key}' for '{normalized_query}'")
        return generated_key

    def _generate_key_from_query(self, query: str, language: str) -> str:
        """
        Generate a canonical key from the query when no keyword matches.

        Args:
            query: The normalized query
            language: Detected language — used to select which stop-word set to apply

        Returns:
            A snake_case canonical key
        """
        # Stop-word sets: apply Arabic set for Arabic queries, English set for English,
        # both for mixed-language queries.
        arabic_stop_words = {
            'في', 'من', 'على', 'إلى', 'عن', 'مع', 'هل', 'ما', 'كيف', 'متى',
            'أين', 'لماذا', 'هذا', 'هذه', 'التي', 'الذي', 'أن', 'ان', 'كان',
            'يكون', 'هي', 'هو', 'انا', 'انت', 'نحن', 'شو', 'وين', 'ليش',
        }
        english_stop_words = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
            'ought', 'used', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by',
            'from', 'as', 'into', 'through', 'during', 'before', 'after', 'above',
            'below', 'between', 'under', 'again', 'further', 'then', 'once',
            'here', 'there', 'when', 'where', 'why', 'how', 'all', 'each', 'few',
            'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only',
            'own', 'same', 'so', 'than', 'too', 'very', 'just', 'what', 'which',
            'who', 'whom', 'this', 'that', 'these', 'those', 'am', 'i', 'me',
            'my', 'myself', 'we', 'our', 'ours', 'ourselves', 'you', 'your',
            'yours', 'yourself', 'yourselves', 'he', 'him', 'his', 'himself',
            'she', 'her', 'hers', 'herself', 'it', 'its', 'itself', 'they',
            'them', 'their', 'theirs', 'themselves',
        }

        if language == 'arabic':
            stop_words = arabic_stop_words
        elif language == 'english':
            stop_words = english_stop_words
        else:
            stop_words = arabic_stop_words | english_stop_words

        words = query.lower().split()
        meaningful_words = []

        for word in words:
            clean_word = re.sub(r'[^\w\s]', '', word)
            if clean_word and clean_word not in stop_words and len(clean_word) > 1:
                meaningful_words.append(clean_word)

        key_words = meaningful_words[:3]

        if not key_words:
            for word in words:
                clean = re.sub(r'[^\w]', '', word)
                if clean and len(clean) > 2:
                    return clean.lower()[:20]
            return 'university_query'

        key = '_'.join(key_words)
        key = re.sub(r'[^a-z0-9_\u0600-\u06FF]', '', key.lower())
        key = re.sub(r'_+', '_', key).strip('_')

        if any('\u0600' <= c <= '\u06FF' for c in key):
            key = self._transliterate_arabic(key)

        return key[:30] if key else 'university_query'

    def _transliterate_arabic(self, text: str) -> str:
        """Simple Arabic to Latin transliteration for keys."""
        trans_map = {
            'ا': 'a', 'أ': 'a', 'إ': 'i', 'آ': 'a', 'ب': 'b', 'ت': 't', 'ث': 'th',
            'ج': 'j', 'ح': 'h', 'خ': 'kh', 'د': 'd', 'ذ': 'th', 'ر': 'r', 'ز': 'z',
            'س': 's', 'ش': 'sh', 'ص': 's', 'ض': 'd', 'ط': 't', 'ظ': 'z', 'ع': 'a',
            'غ': 'gh', 'ف': 'f', 'ق': 'q', 'ك': 'k', 'ل': 'l', 'م': 'm', 'ن': 'n',
            'ه': 'h', 'و': 'w', 'ي': 'y', 'ى': 'a', 'ة': 'a', 'ء': '', 'ئ': 'y',
            'ؤ': 'w', 'ـ': '',
        }
        result = ''
        for char in text:
            if char in trans_map:
                result += trans_map[char]
            elif char.isalnum() or char == '_':
                result += char
        return result

    # ========================================
    # ALIAS GENERATION
    # ========================================

    def generate_aliases(
        self,
        canonical_key: str,
        original_query: str,
        language: str = None,
        *,
        use_ai: bool = True,
    ) -> List[str]:
        """
        Generate array of plausible aliases for canonical key.

        Args:
            canonical_key: The canonical Redis key
            original_query: Original user query
            language: Detected language (optional, will detect if not provided)

        Returns:
            List of aliases
        """
        if not language:
            _, language = self.normalize_input(original_query)

        aliases: List[str] = []

        # Always include the original query
        if original_query:
            aliases.append(original_query)

        # Prefer AI aliases when available (skipped on fast path).
        if use_ai:
            try:
                if self.openai_service and getattr(self.openai_service, "is_configured", None):
                    if self.openai_service.is_configured():
                        ai_aliases = self.openai_service.generate_aliases_with_ai(
                            canonical_key, original_query
                        )
                        if isinstance(ai_aliases, list) and ai_aliases:
                            aliases.extend([a for a in ai_aliases if isinstance(a, str)])
            except Exception as e:
                self.logger.debug(f"AI alias generation unavailable: {e}")

        # Generic fallback aliases (non-topic, no static mappings)
        norm = (original_query or "").strip()
        if norm:
            aliases.append(re.sub(r"\s+", " ", norm))
            aliases.append(re.sub(r"[^\w\s\u0600-\u06FF]", " ", norm))
            aliases.append(re.sub(r"\s+", " ", re.sub(r"[^\w\s\u0600-\u06FF]", " ", norm)).strip())

        # Remove duplicates using NFC-normalized keys (consistent with Redis storage)
        seen: set = set()
        unique_aliases: List[str] = []
        for alias in aliases:
            norm_key = normalize_for_storage(alias or "")
            if norm_key and norm_key not in seen:
                seen.add(norm_key)
                unique_aliases.append(alias)

        return unique_aliases

    # ========================================
    # ALIAS VALIDATION
    # ========================================

    def validate_aliases(self, canonical_key: str, aliases: List[str]) -> List[str]:
        """
        Deduplicate and clean aliases, ensuring at least one is kept.
        Uses NFC normalization for consistent comparison.

        Args:
            canonical_key: Canonical key (unused, kept for API compatibility)
            aliases: List of aliases to clean

        Returns:
            Deduplicated, non-empty alias list
        """
        if not aliases:
            return []

        validated: List[str] = []
        seen: set = set()
        for alias in aliases:
            a = (alias or "").strip()
            if not a:
                continue
            key = normalize_for_storage(a)
            if key in seen:
                continue
            seen.add(key)
            validated.append(a)

        if not validated and aliases:
            first = (aliases[0] or "").strip()
            if first:
                validated = [first]
        return validated

    # ========================================
    # COMPLETE WORKFLOW
    # ========================================

    def process_query(self, query: str, *, use_ai: bool = True) -> Dict[str, Any]:
        """
        Complete workflow: Normalize, map, generate aliases, validate.
        NEVER returns 'general' as canonical key.

        Args:
            query: Student query

        Returns:
            {
                "canonical_key": "...",  # Always specific, never 'general'
                "aliases": [...],
                "language": "..."
            }
        """
        normalized, language = self.normalize_input(query)
        self.logger.debug(f"Normalized query: '{normalized}', Language: {language}")

        canonical_key = self.map_to_canonical_key(normalized, language, use_ai=use_ai)
        self.logger.debug(f"Mapped to canonical key: {canonical_key}")

        aliases = self.generate_aliases(canonical_key, query, language, use_ai=use_ai)
        validated_aliases = self.validate_aliases(canonical_key, aliases)

        self.logger.info(f"Generated {len(validated_aliases)} validated aliases for key: {canonical_key}")

        return {
            "canonical_key": canonical_key,
            "aliases": validated_aliases,
            "language": language,
        }
