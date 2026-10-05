"""
Comprehensive logging system for University Assistant.
Tracks all operations, queries, and system flow with clear terminal output.
"""
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


# ANSI Color codes for terminal
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    
    # Colors
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    
    # Background
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'


class FlushingStreamHandler(logging.StreamHandler):
    """StreamHandler that flushes after every log."""
    
    def emit(self, record):
        # Windows consoles can raise UnicodeEncodeError (cp1252) on emojis/Arabic.
        # Never let logging crash request handling—fallback to a safe representation.
        try:
            super().emit(record)
            self.flush()
        except UnicodeEncodeError:
            try:
                msg = self.format(record)
                # Replace any non-encodable chars for the target stream encoding
                enc = getattr(self.stream, "encoding", None) or "utf-8"
                safe = msg.encode(enc, errors="replace").decode(enc, errors="replace")
                stream = self.stream
                stream.write(safe + self.terminator)
                self.flush()
            except Exception:
                # Last resort: swallow logging errors
                return


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for terminal output."""
    
    LEVEL_COLORS = {
        logging.DEBUG: Colors.CYAN,
        logging.INFO: Colors.GREEN,
        logging.WARNING: Colors.YELLOW,
        logging.ERROR: Colors.RED,
        logging.CRITICAL: Colors.BG_RED + Colors.WHITE,
    }
    
    def format(self, record):
        # Add color based on level
        color = self.LEVEL_COLORS.get(record.levelno, Colors.WHITE)
        
        # Format the message
        record.levelname = f"{color}{record.levelname:8}{Colors.RESET}"
        
        # Add special formatting for flow messages
        if hasattr(record, 'flow_type'):
            if record.flow_type == 'start':
                record.msg = f"{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.RESET}\n{record.msg}"
            elif record.flow_type == 'end':
                record.msg = f"{record.msg}\n{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.RESET}"
            elif record.flow_type == 'step':
                record.msg = f"{Colors.BOLD}{Colors.MAGENTA}▶{Colors.RESET} {record.msg}"
            elif record.flow_type == 'success':
                record.msg = f"{Colors.GREEN}✓{Colors.RESET} {record.msg}"
            elif record.flow_type == 'fail':
                record.msg = f"{Colors.RED}✗{Colors.RESET} {record.msg}"
            elif record.flow_type == 'data':
                record.msg = f"{Colors.CYAN}📦{Colors.RESET} {record.msg}"
        
        formatted = super().format(record)
        # Ensure output is always safe for Windows console encodings.
        # (Prevents internal logging from crashing before StreamHandler fallback runs.)
        try:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            formatted = formatted.encode(enc, errors="replace").decode(enc, errors="replace")
        except Exception:
            pass
        return formatted


class UniversityAssistantLogger:
    """Centralized logging system for University Assistant."""
    
    _instance = None
    _logger = None
    
    def __new__(cls):
        """Singleton pattern to ensure one logger instance."""
        if cls._instance is None:
            cls._instance = super(UniversityAssistantLogger, cls).__new__(cls)
            cls._instance._initialize_logger()
        return cls._instance
    
    def _initialize_logger(self):
        """Initialize the logger with file and console handlers."""
        # Create logs directory if it doesn't exist
        log_dir = Path('logs')
        log_dir.mkdir(exist_ok=True)
        
        # Create logger
        self._logger = logging.getLogger('UniversityAssistant')
        self._logger.setLevel(logging.DEBUG)
        
        # Prevent duplicate handlers
        if self._logger.handlers:
            return
        
        # Create formatters
        detailed_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Colored console formatter
        console_formatter = ColoredFormatter(
            '%(asctime)s | %(levelname)s | %(message)s',
            datefmt='%H:%M:%S'
        )
        
        # File handler - All logs (rotating, max 10MB, keep 5 backups)
        file_handler = RotatingFileHandler(
            log_dir / 'assistant.log',
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(detailed_formatter)
        
        # Error file handler - Errors only
        error_handler = RotatingFileHandler(
            log_dir / 'errors.log',
            maxBytes=10*1024*1024,
            backupCount=5,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(detailed_formatter)
        
        # Console handler - ALL logs (DEBUG and above) with auto-flush
        console_handler = FlushingStreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)  # Show ALL logs in terminal
        console_handler.setFormatter(console_formatter)
        
        # Add handlers
        self._logger.addHandler(file_handler)
        self._logger.addHandler(error_handler)
        self._logger.addHandler(console_handler)
    
    def get_logger(self):
        """Get the logger instance."""
        return self._logger


# Convenience functions for easy logging
def get_logger():
    """Get the logger instance."""
    return UniversityAssistantLogger().get_logger()


# ========================================
# FLOW TRACKING FUNCTIONS
# ========================================

def log_flow_start(title: str):
    """Log the start of a flow."""
    logger = get_logger()
    record = logger.makeRecord(
        logger.name, logging.INFO, '', 0,
        f"\n{'='*60}\n🚀 {title}\n{'='*60}",
        (), None
    )
    record.flow_type = 'start'
    logger.handle(record)


def log_flow_end(title: str):
    """Log the end of a flow."""
    logger = get_logger()
    record = logger.makeRecord(
        logger.name, logging.INFO, '', 0,
        f"✅ {title}\n{'='*60}\n",
        (), None
    )
    record.flow_type = 'end'
    logger.handle(record)


def log_step(step_num: int, step_name: str, details: str = ""):
    """Log a step in the flow."""
    logger = get_logger()
    msg = f"STEP {step_num}: {step_name}"
    if details:
        msg += f" | {details}"
    logger.info(f"▶ {msg}")


def log_substep(name: str, details: str = ""):
    """Log a substep."""
    logger = get_logger()
    msg = f"  → {name}"
    if details:
        msg += f": {details}"
    logger.debug(msg)


# ========================================
# QUERY FLOW LOGGING
# ========================================

def log_query_received(query: str, redis_json_provided: bool = False):
    """Log when a query is received."""
    logger = get_logger()
    logger.info(f"\n{'='*60}")
    logger.info(f"📥 NEW QUERY RECEIVED")
    logger.info(f"{'='*60}")
    logger.info(f"📝 Query: {query[:100]}{'...' if len(query) > 100 else ''}")
    logger.info(f"📦 Redis JSON Provided: {'Yes' if redis_json_provided else 'No'}")
    logger.info(f"{'-'*60}")


def log_redis_check(has_redis_data: bool):
    """Log Redis cache check result."""
    logger = get_logger()
    if has_redis_data:
        logger.info(f"✓ Redis cache HIT - Using cached data")
    else:
        logger.info(f"✗ Redis cache MISS - Proceeding to next step")


def log_redis_data_used(topic_key: str):
    """Log when Redis data is used."""
    logger = get_logger()
    logger.info(f"📦 Using Redis data for topic: {topic_key}")
    logger.debug(f"  → Redis JSON used exactly as-is (no modifications)")


def log_embeddings_search(query: str):
    """Log embeddings search."""
    logger = get_logger()
    logger.info(f"🔍 STEP 1: Embeddings + Cosine Similarity Search")
    logger.debug(f"  → Query: {query[:50]}...")


def log_embeddings_result(found: bool, canonical_key: str = None, confidence: float = 0.0):
    """Log embeddings search result."""
    logger = get_logger()
    if found:
        logger.info(f"✓ Match found: {canonical_key} (confidence: {confidence:.2f})")
    else:
        logger.info(f"✗ No matching alias found")


def log_canonical_key_generation(query: str, key: str):
    """Log canonical key generation."""
    logger = get_logger()
    logger.info(f"🔑 Generated canonical key: {key}")
    logger.debug(f"  → From query: {query[:50]}...")


def log_resource_selection(query: str, selected_url: str = None):
    """Log resource selection process."""
    logger = get_logger()
    logger.info(f"📚 STEP 2: Resource Selection")
    if selected_url:
        logger.info(f"✓ Selected resource: {selected_url[:60]}...")
    else:
        logger.info(f"✗ No matching resource - Will use web search")


def log_pdf_detection(url: str):
    """Log PDF detection."""
    logger = get_logger()
    logger.info(f"📄 PDF Detected: {url[:60]}...")


def log_pdf_download_start(url: str):
    """Log PDF download start."""
    logger = get_logger()
    logger.info(f"⬇️ Downloading PDF...")
    logger.debug(f"  → URL: {url}")


def log_pdf_download_complete(success: bool, size: int = 0, pages: int = 0):
    """Log PDF download completion."""
    logger = get_logger()
    if success:
        logger.info(f"✓ PDF downloaded: {size} bytes, {pages} pages")
    else:
        logger.warning(f"✗ PDF download failed")


def log_pdf_extraction_start():
    """Log PDF text extraction start."""
    logger = get_logger()
    logger.info(f"📝 Extracting text from PDF...")


def log_pdf_extraction_complete(success: bool, chars: int = 0):
    """Log PDF text extraction completion."""
    logger = get_logger()
    if success:
        logger.info(f"✓ PDF text extracted: {chars} characters")
    else:
        logger.warning(f"✗ PDF text extraction failed")


def log_web_search(query: str):
    """Log web search operation."""
    logger = get_logger()
    logger.info(f"🌐 STEP 3: Web Search")
    logger.debug(f"  → Query: {query[:50]}...")


def log_web_extraction_start(url: str):
    """Log start of web extraction."""
    logger = get_logger()
    logger.info(f"🔄 Extracting data...")
    logger.debug(f"  → Source: {url[:60] if url else 'Web Search'}...")


def log_web_extraction_complete(url: str, success: bool, data_size: int = 0):
    """Log completion of web extraction."""
    logger = get_logger()
    if success:
        logger.info(f"✓ Data extracted: {data_size} bytes")
    else:
        logger.warning(f"✗ Data extraction failed")


def log_json_building_start():
    """Log start of JSON building."""
    logger = get_logger()
    logger.debug(f"📦 Building structured JSON dataset...")


def log_json_building_complete(json_keys: list):
    """Log completion of JSON building."""
    logger = get_logger()
    logger.info(f"✓ JSON dataset built with {len(json_keys)} keys")
    logger.debug(f"  → Keys: {', '.join(json_keys[:5])}{'...' if len(json_keys) > 5 else ''}")


def log_answer_generation(source: str):
    """Log answer generation."""
    logger = get_logger()
    source_emoji = "📦" if source == "redis" else "🌐"
    logger.info(f"{source_emoji} STEP 4: Generating Answer (source: {source})")


def log_answer_streaming_start():
    """Log start of answer streaming."""
    logger = get_logger()
    logger.info(f"📡 Streaming answer to client...")


def log_answer_streaming_complete(chars: int):
    """Log completion of answer streaming."""
    logger = get_logger()
    logger.info(f"✓ Answer streamed: {chars} characters")


def log_response_ready(source: str, answer_length: int, aliases_count: int = 0):
    """Log when response is ready."""
    logger = get_logger()
    logger.info(f"\n{'-'*60}")
    logger.info(f"✅ RESPONSE READY")
    logger.info(f"  → Source: {source}")
    logger.info(f"  → Answer: {answer_length} chars")
    if aliases_count > 0:
        logger.info(f"  → Aliases: {aliases_count}")
    logger.info(f"{'='*60}\n")


# ========================================
# BACKGROUND TASK LOGGING
# ========================================

def log_background_task_start(task_name: str):
    """Log background task start."""
    logger = get_logger()
    logger.debug(f"🔄 Background: Starting {task_name}...")


def log_background_task_complete(task_name: str, success: bool = True):
    """Log background task completion."""
    logger = get_logger()
    if success:
        logger.debug(f"✓ Background: {task_name} completed")
    else:
        logger.warning(f"✗ Background: {task_name} failed")


def log_alias_generation_start():
    """Log start of alias generation."""
    logger = get_logger()
    logger.debug(f"🏷️ Background: Generating aliases...")


def log_alias_generation_complete(canonical_key: str, aliases_count: int):
    """Log completion of alias generation."""
    logger = get_logger()
    logger.debug(f"✓ Background: Generated {aliases_count} aliases for: {canonical_key}")


def log_redis_cache_store(topic_key: str, success: bool):
    """Log Redis cache storage attempt."""
    logger = get_logger()
    if success:
        logger.debug(f"✓ Background: Cached in Redis - {topic_key}")
    else:
        logger.warning(f"✗ Background: Failed to cache - {topic_key}")


# ========================================
# ERROR & SYSTEM LOGGING
# ========================================

def log_error(operation: str, error: Exception):
    """Log errors."""
    logger = get_logger()
    logger.error(f"❌ ERROR in {operation}: {str(error)}")
    logger.debug(f"  → Full traceback:", exc_info=True)


def log_warning(message: str):
    """Log warnings."""
    logger = get_logger()
    logger.warning(f"⚠️ {message}")


def log_api_request(method: str, endpoint: str, status_code: int = None):
    """Log API requests."""
    logger = get_logger()
    if status_code:
        status_color = "✓" if status_code < 400 else "✗"
        logger.info(f"🌐 API {method} {endpoint} → {status_color} {status_code}")
    else:
        logger.debug(f"🌐 API {method} {endpoint}")


def log_redis_connection(status: bool):
    """Log Redis connection status."""
    logger = get_logger()
    if status:
        logger.info(f"✓ Redis connection established")
    else:
        logger.warning(f"✗ Redis connection failed - System will work without caching")


def log_openai_call(operation: str):
    """Log OpenAI API call."""
    logger = get_logger()
    logger.debug(f"🤖 OpenAI: {operation}")


def log_openai_response(operation: str, tokens: int = 0):
    """Log OpenAI API response."""
    logger = get_logger()
    if tokens:
        logger.debug(f"✓ OpenAI {operation}: {tokens} tokens")
    else:
        logger.debug(f"✓ OpenAI {operation} completed")


def log_validation_result(is_valid: bool, error_msg: str = ""):
    """Log output validation result."""
    logger = get_logger()
    if is_valid:
        logger.debug(f"✓ Output validation passed")
    else:
        logger.error(f"✗ Output validation failed: {error_msg}")


def log_system_start():
    """Log system startup."""
    logger = get_logger()
    logger.info(f"\n{'='*60}")
    logger.info(f"🚀 UNIVERSITY ASSISTANT SYSTEM STARTING")
    logger.info(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"{'='*60}")


def log_system_config(redis_connected: bool, openai_configured: bool):
    """Log system configuration."""
    logger = get_logger()
    logger.info(f"⚙️ System Configuration:")
    redis_status = "✓ Connected" if redis_connected else "✗ Not Connected"
    openai_status = "✓ Configured" if openai_configured else "✗ Not Configured"
    logger.info(f"  → Redis: {redis_status}")
    logger.info(f"  → OpenAI: {openai_status}")
    logger.info(f"{'='*60}\n")


def log_system_ready(host: str, port: int):
    """Log system ready."""
    logger = get_logger()
    logger.info(f"\n{'='*60}")
    logger.info(f"✅ SERVER READY")
    logger.info(f"🌐 URL: http://{host}:{port}")
    logger.info(f"{'='*60}\n")
