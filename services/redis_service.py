"""
Redis service for caching university query results and alias embeddings.
Handles all Redis operations including embedding storage for cosine similarity.
"""
import json
import time as _time
import unicodedata
import redis
from typing import Optional, Dict, Any, List, Tuple
from config import (
    REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD, CACHE_TTL,
    EMBEDDING_CACHE_TTL,
)
from logger import log_redis_connection, get_logger


def normalize_alias(text: str) -> str:
    """Shared NFC normalizer for consistent alias key comparison across all storage/lookup paths."""
    return unicodedata.normalize('NFC', text).lower().strip()


# Backward-compatible alias used inside this module
_normalize_alias = normalize_alias


class RedisService:
    """
    Handles all Redis operations:
    - JSON dataset caching
    - Alias -> canonical key mappings
    - Alias embeddings for cosine similarity
    """

    _instance = None

    # Redis key prefixes
    PREFIX_DATA = "data:"           # data:<canonical_key> -> JSON dataset
    PREFIX_ALIAS = "alias:"         # alias:<alias_text> -> canonical_key
    PREFIX_EMBEDDING = "emb:"       # emb:<alias_text> -> embedding vector
    PREFIX_CANONICAL = "canonical:" # canonical:<key>:aliases -> list of aliases

    def __new__(cls):
        """Singleton pattern to ensure one Redis connection."""
        if cls._instance is None:
            cls._instance = super(RedisService, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """Initialize Redis connection."""
        self.logger = get_logger()
        # In-process embedding corpus cache
        self._emb_cache: Dict[str, Dict[str, Any]] = {}
        self._emb_cache_ts: float = 0.0
        try:
            self.logger.debug(f"Attempting Redis connection to {REDIS_HOST}:{REDIS_PORT}")
            self.client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                password=REDIS_PASSWORD,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            self.client.ping()
            self.connected = True
            log_redis_connection(True)
            self.logger.info(f"Redis connected successfully to {REDIS_HOST}:{REDIS_PORT}")
        except Exception as e:
            self.logger.warning(f"Redis connection failed: {e}")
            log_redis_connection(False)
            self.connected = False
            self.client = None

    def is_connected(self) -> bool:
        """Check if Redis is connected, attempting a ping to detect dropped connections."""
        if not self.connected or not self.client:
            return False
        try:
            self.client.ping()
            return True
        except Exception:
            self.connected = False
            return False

    # ========================================
    # DATA OPERATIONS
    # ========================================

    def fetch_from_redis(self, canonical_key: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached data for a canonical key.

        Args:
            canonical_key: The canonical key

        Returns:
            Cached JSON data if found, None otherwise
        """
        if not self.is_connected():
            return None

        try:
            key = f"{self.PREFIX_DATA}{canonical_key}"
            cached = self.client.get(key)
            if cached:
                self.logger.debug(f"Cache HIT for key: {canonical_key}")
                return json.loads(cached)
            self.logger.debug(f"Cache MISS for key: {canonical_key}")
        except Exception as e:
            self.logger.error(f"Error retrieving from Redis: {e}")

        return None

    def save_to_redis(
        self,
        canonical_key: str,
        data: Dict[str, Any],
        aliases: List[str] = None,
        alias_embeddings: Dict[str, List[float]] = None,
        ttl: int = None
    ) -> bool:
        """
        Cache data, aliases, and embeddings for a canonical key.

        Args:
            canonical_key: The canonical key
            data: The JSON data to cache
            aliases: List of aliases
            alias_embeddings: Dict of {alias: embedding_vector}
            ttl: Time to live in seconds

        Returns:
            True if successful
        """
        if not self.is_connected():
            return False

        try:
            ttl = ttl or CACHE_TTL

            # Store main data
            data_key = f"{self.PREFIX_DATA}{canonical_key}"
            data_to_store = data.copy()
            if aliases:
                data_to_store['aliases'] = aliases

            self.client.setex(
                data_key,
                ttl,
                json.dumps(data_to_store, ensure_ascii=False)
            )

            # Store alias mappings and embeddings (with matching TTL)
            if aliases:
                self._store_alias_mappings(canonical_key, aliases, alias_embeddings, ttl=ttl)

            # Invalidate in-process embedding cache so new aliases are visible immediately
            self.invalidate_embedding_cache()

            self.logger.info(
                f"Cached data for key: {canonical_key} "
                f"(TTL: {ttl}s, Aliases: {len(aliases) if aliases else 0})"
            )
            return True

        except Exception as e:
            self.logger.error(f"Error caching to Redis: {e}")
            return False

    # ========================================
    # ALIAS OPERATIONS
    # ========================================

    def resolve_alias(self, alias: str) -> Optional[str]:
        """
        Resolve an alias to its canonical key.

        Args:
            alias: The alias text

        Returns:
            Canonical key or None
        """
        if not self.is_connected():
            return None

        try:
            alias_key = f"{self.PREFIX_ALIAS}{_normalize_alias(alias)}"
            canonical_key = self.client.get(alias_key)
            if canonical_key:
                self.logger.debug(f"Alias resolved: '{alias}' -> {canonical_key}")
            return canonical_key
        except Exception as e:
            self.logger.error(f"Error resolving alias: {e}")
            return None

    def _store_alias_mappings(
        self,
        canonical_key: str,
        aliases: List[str],
        alias_embeddings: Dict[str, List[float]] = None,
        ttl: int = None,
    ):
        """
        Store alias -> canonical_key mappings and embeddings with TTL matching the data key.

        Args:
            canonical_key: The canonical key
            aliases: List of aliases
            alias_embeddings: Dict of {alias: embedding_vector}
            ttl: Time to live in seconds (matches data key TTL to avoid orphan keys)
        """
        if not self.is_connected():
            return

        ttl = ttl or CACHE_TTL

        try:
            for alias in aliases:
                alias_normalized = _normalize_alias(alias)
                if not alias_normalized:
                    continue

                # Store alias -> canonical_key mapping (expires with data key)
                alias_key = f"{self.PREFIX_ALIAS}{alias_normalized}"
                self.client.setex(alias_key, ttl, canonical_key)

                # Store embedding if provided (expires with data key)
                if alias_embeddings:
                    embedding = alias_embeddings.get(alias_normalized)
                    if embedding is None:
                        embedding = alias_embeddings.get(alias.strip().lower())
                    if embedding is not None:
                        emb_key = f"{self.PREFIX_EMBEDDING}{alias_normalized}"
                        self.client.setex(emb_key, ttl, json.dumps({
                            'embedding': embedding,
                            'canonical_key': canonical_key
                        }))

            # Store reverse mapping: canonical:<key>:aliases -> list (expires with data key)
            canonical_aliases_key = f"{self.PREFIX_CANONICAL}{canonical_key}:aliases"
            self.client.setex(
                canonical_aliases_key, ttl,
                json.dumps(aliases, ensure_ascii=False)
            )

            self.logger.debug(f"Stored {len(aliases)} alias mappings for {canonical_key} (TTL: {ttl}s)")

        except Exception as e:
            self.logger.error(f"Failed to store alias mappings: {e}")

    def get_aliases_for_key(self, canonical_key: str) -> List[str]:
        """
        Get all aliases for a canonical key.

        Args:
            canonical_key: The canonical key

        Returns:
            List of aliases
        """
        if not self.is_connected():
            return []

        try:
            key = f"{self.PREFIX_CANONICAL}{canonical_key}:aliases"
            aliases_json = self.client.get(key)
            if aliases_json:
                return json.loads(aliases_json)
        except Exception as e:
            self.logger.error(f"Error getting aliases: {e}")

        return []

    # ========================================
    # EMBEDDINGS OPERATIONS
    # ========================================

    def invalidate_embedding_cache(self) -> None:
        """Invalidate the in-process embedding corpus cache so the next query re-fetches from Redis."""
        self._emb_cache = {}
        self._emb_cache_ts = 0.0

    def get_all_alias_embeddings(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all stored alias embeddings for cosine similarity matching.
        Results are cached in-process for EMBEDDING_CACHE_TTL seconds to avoid O(n) Redis
        scans on every query.

        Returns:
            Dict of {alias: {"embedding": [...], "canonical_key": "..."}}
        """
        now = _time.monotonic()
        if self._emb_cache and (now - self._emb_cache_ts) < EMBEDDING_CACHE_TTL:
            return self._emb_cache

        if not self.is_connected():
            return {}

        result = self._fetch_all_alias_embeddings_from_redis()
        self._emb_cache = result
        self._emb_cache_ts = now
        return result

    def _fetch_all_alias_embeddings_from_redis(self) -> Dict[str, Dict[str, Any]]:
        """Perform the actual O(n) Redis SCAN to load all embedding vectors."""
        try:
            result = {}
            cursor = 0
            pattern = f"{self.PREFIX_EMBEDDING}*"

            while True:
                cursor, keys = self.client.scan(cursor, match=pattern, count=100)

                for key in keys:
                    try:
                        data = self.client.get(key)
                        if data:
                            parsed = json.loads(data)
                            alias = key.replace(self.PREFIX_EMBEDDING, '', 1)
                            result[alias] = parsed
                    except (json.JSONDecodeError, KeyError):
                        continue

                if cursor == 0:
                    break

            self.logger.debug(f"Retrieved {len(result)} alias embeddings from Redis")
            return result

        except Exception as e:
            self.logger.error(f"Error getting alias embeddings: {e}")
            return {}

    def store_alias_embedding(
        self,
        alias: str,
        embedding: List[float],
        canonical_key: str,
        ttl: int = None,
    ) -> bool:
        """
        Store a single alias embedding with TTL.

        Args:
            alias: The alias text
            embedding: The embedding vector
            canonical_key: The canonical key
            ttl: Time to live in seconds

        Returns:
            True if successful
        """
        if not self.is_connected():
            return False

        ttl = ttl or CACHE_TTL

        try:
            alias_normalized = _normalize_alias(alias)

            # Store embedding (expires to avoid orphan keys)
            emb_key = f"{self.PREFIX_EMBEDDING}{alias_normalized}"
            self.client.setex(emb_key, ttl, json.dumps({
                'embedding': embedding,
                'canonical_key': canonical_key
            }))

            # Also store alias mapping (expires to avoid orphan keys)
            alias_key = f"{self.PREFIX_ALIAS}{alias_normalized}"
            self.client.setex(alias_key, ttl, canonical_key)

            # Invalidate in-process cache so new embedding is visible immediately
            self.invalidate_embedding_cache()

            return True

        except Exception as e:
            self.logger.error(f"Error storing alias embedding: {e}")
            return False

    def get_embedding(self, alias: str) -> Optional[Dict[str, Any]]:
        """
        Get embedding for a specific alias.

        Args:
            alias: The alias text

        Returns:
            Dict with embedding and canonical_key, or None
        """
        if not self.is_connected():
            return None

        try:
            key = f"{self.PREFIX_EMBEDDING}{_normalize_alias(alias)}"
            data = self.client.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            self.logger.error(f"Error getting embedding: {e}")

        return None

    # ========================================
    # UTILITY OPERATIONS
    # ========================================

    def delete_key(self, canonical_key: str) -> bool:
        """
        Delete a canonical key and its aliases.

        Args:
            canonical_key: The key to delete

        Returns:
            True if deleted
        """
        if not self.is_connected():
            return False

        try:
            # Get aliases first
            aliases = self.get_aliases_for_key(canonical_key)

            # Delete main data
            self.client.delete(f"{self.PREFIX_DATA}{canonical_key}")

            # Delete alias mappings and embeddings
            for alias in aliases:
                alias_normalized = _normalize_alias(alias)
                self.client.delete(f"{self.PREFIX_ALIAS}{alias_normalized}")
                self.client.delete(f"{self.PREFIX_EMBEDDING}{alias_normalized}")

            # Delete aliases list
            self.client.delete(f"{self.PREFIX_CANONICAL}{canonical_key}:aliases")

            # Invalidate in-process cache
            self.invalidate_embedding_cache()

            self.logger.info(f"Deleted key: {canonical_key} and {len(aliases)} aliases")
            return True

        except Exception as e:
            self.logger.error(f"Error deleting key: {e}")
            return False

    def get_stats(self) -> Dict[str, int]:
        """Get Redis statistics."""
        if not self.is_connected():
            return {}

        try:
            stats = {
                'total_data_keys': 0,
                'total_aliases': 0,
                'total_embeddings': 0
            }

            for pattern, stat_key in [
                (f"{self.PREFIX_DATA}*", 'total_data_keys'),
                (f"{self.PREFIX_ALIAS}*", 'total_aliases'),
                (f"{self.PREFIX_EMBEDDING}*", 'total_embeddings')
            ]:
                cursor = 0
                count = 0
                while True:
                    cursor, keys = self.client.scan(cursor, match=pattern, count=100)
                    count += len(keys)
                    if cursor == 0:
                        break
                stats[stat_key] = count

            return stats

        except Exception as e:
            self.logger.error(f"Error getting stats: {e}")
            return {}
