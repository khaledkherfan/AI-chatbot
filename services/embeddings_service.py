"""
Embeddings service for cosine similarity matching.
Uses OpenAI embeddings for semantic search across aliases.
"""
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from openai import OpenAI
from config import EMBEDDINGS_MODEL, SIMILARITY_THRESHOLD, openai_client_kwargs
from logger import get_logger


class EmbeddingsService:
    """
    Handles embeddings generation and cosine similarity matching.
    
    WORKFLOW:
    1. Convert text to embeddings using OpenAI
    2. Compare embeddings using cosine similarity
    3. Find best matching alias above threshold
    4. If uncertain, delegate to ChatGPT for final decision
    """
    
    _instance = None
    
    def __new__(cls):
        """Singleton pattern."""
        if cls._instance is None:
            cls._instance = super(EmbeddingsService, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """Initialize OpenAI client for embeddings."""
        self.logger = get_logger()
        kw = openai_client_kwargs()
        self.client = OpenAI(**kw) if kw else None
        self.model = EMBEDDINGS_MODEL
        self.threshold = SIMILARITY_THRESHOLD
        
        if self.client:
            self.logger.info(f"Embeddings service initialized with model: {self.model}")
        else:
            self.logger.warning(
                "Embeddings API key not configured — set OPENAI_API_KEY in .env"
            )
    
    def is_configured(self) -> bool:
        """Check if embeddings service is configured."""
        return self.client is not None
    
    # ========================================
    # EMBEDDINGS GENERATION
    # ========================================
    
    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding vector for text.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector as list of floats, or None if failed
        """
        if not self.client:
            self.logger.warning("OpenAI client not configured")
            return None
        
        try:
            # Clean and normalize text
            text = text.strip().lower()
            if not text:
                return None
            
            response = self.client.embeddings.create(
                model=self.model,
                input=text
            )
            
            embedding = response.data[0].embedding
            self.logger.debug(f"Generated embedding for: '{text[:50]}...' (dim: {len(embedding)})")
            return embedding
            
        except Exception as e:
            self.logger.error(f"Embedding generation failed: {e}")
            return None
    
    def generate_embeddings_batch(self, texts: List[str]) -> Dict[str, List[float]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            Dict mapping text -> embedding
        """
        if not self.client or not texts:
            return {}
        
        try:
            # Clean texts
            cleaned = [t.strip().lower() for t in texts if t.strip()]
            if not cleaned:
                return {}
            
            response = self.client.embeddings.create(
                model=self.model,
                input=cleaned
            )
            
            result = {}
            for i, data in enumerate(response.data):
                result[cleaned[i]] = data.embedding
            
            self.logger.debug(f"Generated {len(result)} embeddings in batch")
            return result
            
        except Exception as e:
            self.logger.error(f"Batch embedding generation failed: {e}")
            return {}
    
    # ========================================
    # COSINE SIMILARITY
    # ========================================
    
    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """
        Calculate cosine similarity between two vectors.
        
        Args:
            vec1: First embedding vector
            vec2: Second embedding vector
            
        Returns:
            Similarity score between -1 and 1
        """
        if not vec1 or not vec2:
            return 0.0
        
        try:
            a = np.array(vec1)
            b = np.array(vec2)
            
            # Cosine similarity = (a · b) / (||a|| * ||b||)
            dot_product = np.dot(a, b)
            norm_a = np.linalg.norm(a)
            norm_b = np.linalg.norm(b)
            
            if norm_a == 0 or norm_b == 0:
                return 0.0
            
            similarity = dot_product / (norm_a * norm_b)
            return float(similarity)
            
        except Exception as e:
            self.logger.error(f"Cosine similarity calculation failed: {e}")
            return 0.0
    
    # ========================================
    # ALIAS MATCHING
    # ========================================
    
    def find_best_match(
        self, 
        query_embedding: List[float], 
        alias_embeddings: Dict[str, Dict[str, Any]]
    ) -> Tuple[Optional[str], Optional[str], float]:
        """
        Find the best matching alias using cosine similarity.
        
        Args:
            query_embedding: Embedding of user query
            alias_embeddings: Dict of {alias: {"embedding": [...], "canonical_key": "..."}}
            
        Returns:
            Tuple of (best_alias, canonical_key, similarity_score)
        """
        if not query_embedding or not alias_embeddings:
            return None, None, 0.0
        
        best_alias = None
        best_key = None
        best_score = 0.0
        
        for alias, data in alias_embeddings.items():
            embedding = data.get('embedding')
            if not embedding:
                continue
            
            score = self.cosine_similarity(query_embedding, embedding)
            
            if score > best_score:
                best_score = score
                best_alias = alias
                best_key = data.get('canonical_key')
                # Log warning if canonical_key is missing
                if not best_key:
                    self.logger.warning(f"Embedding for alias '{alias}' missing canonical_key")
        
        self.logger.debug(f"Best match: '{best_alias}' (key: {best_key}, score: {best_score:.4f})")
        return best_alias, best_key, best_score
    
    def match_query_to_aliases(
        self, 
        query: str, 
        alias_embeddings: Dict[str, Dict[str, Any]]
    ) -> Tuple[Optional[str], Optional[str], float, bool]:
        """
        Match user query to stored aliases using embeddings.
        
        Args:
            query: User query
            alias_embeddings: Dict of stored alias embeddings
            
        Returns:
            Tuple of (best_alias, canonical_key, similarity_score, is_confident)
            is_confident = True if score >= threshold
        """
        # Generate embedding for query
        query_embedding = self.generate_embedding(query)
        if not query_embedding:
            return None, None, 0.0, False
        
        # Find best match
        best_alias, canonical_key, score = self.find_best_match(
            query_embedding, 
            alias_embeddings
        )
        
        # Check if above threshold
        is_confident = score >= self.threshold
        
        self.logger.info(
            f"Query match: '{query}' -> '{best_alias}' "
            f"(score: {score:.4f}, threshold: {self.threshold}, confident: {is_confident})"
        )
        
        return best_alias, canonical_key, score, is_confident
    
    # ========================================
    # EMBEDDING SERIALIZATION
    # ========================================
    
    def embedding_to_string(self, embedding: List[float]) -> str:
        """Convert embedding to string for Redis storage."""
        import json
        return json.dumps(embedding)
    
    def string_to_embedding(self, embedding_str: str) -> Optional[List[float]]:
        """Convert string back to embedding."""
        import json
        try:
            return json.loads(embedding_str)
        except:
            return None

