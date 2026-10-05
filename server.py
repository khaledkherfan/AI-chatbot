"""
Flask server for the JUST University Assistant API.
Chatbot for Jordan University of Science and Technology (JUST)

SYSTEM ARCHITECTURE:
- Primary: ChatGPT Web Search (auto-search for any university question)
- Secondary: Redis Cache with Embeddings (for faster repeated queries)
- Aliases: 10 Arabic + 10 English per topic
- Resources: Helper URLs passed to GPT for context
"""
from flask import Flask, request, jsonify, send_file, Response, stream_with_context
from flask_cors import CORS
from controllers.query_controller import QueryController, OutputValidator
from services.redis_service import RedisService
from services.calendar_reminder_service import CalendarReminderService
from services.user_store import UserStore
from services.auth_service import AuthService
from admin_api import register_admin_dashboard
from logger import (
    log_api_request, log_system_start, log_system_config,
    log_validation_result, log_error, get_logger
)
from config import (
    OPENAI_API_KEY,
    SIMILARITY_THRESHOLD,
    FAQ_SUGGESTIONS,
    MAX_IMAGE_UPLOAD_BYTES,
    ALLOWED_IMAGE_MIME_TYPES,
)
import base64
import os
import sys
import json
import logging
import queue
import threading

# Configure Flask/Werkzeug logging to not interfere with our logs
logging.getLogger('werkzeug').setLevel(logging.WARNING)

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)

# Disable Flask's default logger to avoid duplicate logs
app.logger.disabled = True

# Initialize controller and services
query_controller = QueryController()
redis_service = RedisService()
calendar_reminder_service = CalendarReminderService(
    query_controller.redis_service,
    query_controller.openai_service,
)
user_store = UserStore()
auth_service = AuthService(user_store)

register_admin_dashboard(app, query_controller)


def _current_user():
    return auth_service.user_from_authorization_header(
        request.headers.get("Authorization")
    )


def _parse_image_upload(data: dict):
    """
    Validate optional image_base64 + image_mime_type from JSON body.
    Returns ((base64_str, mime), None) or (None, error_message).
    """
    raw_b64 = data.get("image_base64")
    if not raw_b64:
        return None, None
    if not isinstance(raw_b64, str):
        return None, "Invalid image data"
    mime = (data.get("image_mime_type") or "image/jpeg").strip().lower()
    if mime not in ALLOWED_IMAGE_MIME_TYPES:
        return None, "Unsupported image type. Use JPEG, PNG, WebP, or GIF."
    try:
        decoded = base64.b64decode(raw_b64, validate=True)
    except Exception:
        return None, "Could not read the image file."
    if len(decoded) > MAX_IMAGE_UPLOAD_BYTES:
        mb = MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)
        return None, f"Image is too large (max {mb} MB)."
    return (raw_b64, mime), None


@app.route('/', methods=['GET'])
@app.route('/assistant.html', methods=['GET'])
@app.route('/test', methods=['GET'])
@app.route('/test.html', methods=['GET'])
def serve_assistant():
    """Serve the main student assistant (chat) interface."""
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assistant.html')
    if os.path.exists(file_path):
        return send_file(file_path, mimetype='text/html')
    return jsonify({'error': 'assistant.html file not found'}), 404


@app.route('/calendar-reminders', methods=['GET'])
@app.route('/calendar-reminders.html', methods=['GET'])
def serve_calendar_reminders():
    """Academic calendar search + reminders UI."""
    file_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'calendar-reminders.html'
    )
    if os.path.exists(file_path):
        return send_file(file_path, mimetype='text/html')
    return jsonify({'error': 'calendar-reminders.html not found'}), 404


@app.route('/login.html', methods=['GET'])
def serve_login_page():
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'login.html')
    if os.path.exists(file_path):
        return send_file(file_path, mimetype='text/html')
    return jsonify({'error': 'login.html not found'}), 404


@app.route('/register.html', methods=['GET'])
def serve_register_page():
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'register.html')
    if os.path.exists(file_path):
        return send_file(file_path, mimetype='text/html')
    return jsonify({'error': 'register.html not found'}), 404


@app.route('/planner.html', methods=['GET'])
def serve_planner_page():
    """Academic study plan (planner) UI."""
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'planner.html')
    if os.path.exists(file_path):
        return send_file(file_path, mimetype='text/html')
    return jsonify({'error': 'planner.html not found'}), 404


@app.route('/api/planner/study-plans', methods=['GET'])
def api_planner_study_plans():
    """Official study-plan PDF URLs from resources.json (lines 6–10) for the planner UI."""
    try:
        return jsonify(query_controller.get_study_plan_resources()), 200
    except Exception as e:
        log_error('api_planner_study_plans', e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/calendar/events', methods=['GET'])
def api_calendar_events():
    """Cached (7d) structured events from the official academic calendar page."""
    refresh = request.args.get('refresh', '').lower() in ('1', 'true', 'yes')
    get_logger().info(
        "[calendar] API GET /api/calendar/events refresh=%s remote=%s",
        refresh,
        request.remote_addr,
    )
    try:
        data = calendar_reminder_service.get_events(force_refresh=refresh)
        get_logger().info(
            "[calendar] API OK events=%s cached=%s",
            len(data.get("events") or []),
            data.get("cached"),
        )
        return jsonify(data), 200
    except Exception as e:
        log_error('api_calendar_events', e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/calendar/reminders', methods=['GET'])
def api_calendar_reminders_list():
    u = _current_user()
    if not u:
        return jsonify({'error': 'Unauthorized'}), 401
    items = calendar_reminder_service.list_reminders(u['id'])
    return jsonify({'reminders': items}), 200


@app.route('/api/calendar/reminders', methods=['POST'])
def api_calendar_reminders_create():
    u = _current_user()
    if not u:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        body = request.get_json(silent=True) or {}
        row = calendar_reminder_service.add_reminder(u['id'], body)
        return jsonify({'ok': True, 'reminder': row}), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        log_error('api_calendar_reminders_create', e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/calendar/reminders/<reminder_id>', methods=['DELETE'])
def api_calendar_reminders_delete(reminder_id):
    u = _current_user()
    if not u:
        return jsonify({'error': 'Unauthorized'}), 401
    ok = calendar_reminder_service.delete_reminder(u['id'], reminder_id)
    if not ok:
        return jsonify({'error': 'Reminder not found.'}), 404
    return jsonify({'ok': True}), 200


@app.route('/api/auth/register', methods=['POST'])
def api_auth_register():
    try:
        body = request.get_json(silent=True) or {}
        email = body.get('email', '')
        password = body.get('password', '')
        user, token = auth_service.register(email, password)
        return jsonify({
            'ok': True,
            'token': token,
            'user': {'id': user['id'], 'email': user['email']},
        }), 201
    except ValueError as e:
        msg = str(e)
        code = 409 if 'already registered' in msg.lower() else 400
        return jsonify({'error': msg}), code
    except Exception as e:
        log_error('api_auth_register', e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    try:
        body = request.get_json(silent=True) or {}
        email = body.get('email', '')
        password = body.get('password', '')
        user, token = auth_service.login(email, password)
        return jsonify({
            'ok': True,
            'token': token,
            'user': {'id': user['id'], 'email': user['email']},
        }), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 401
    except Exception as e:
        log_error('api_auth_login', e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/auth/me', methods=['GET'])
def api_auth_me():
    u = _current_user()
    if not u:
        return jsonify({'error': 'Unauthorized'}), 401
    full = user_store.get_user_by_id(u['id'])
    if not full:
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'user': {'id': full['id'], 'email': full['email']}}), 200


@app.route('/api/chat/messages', methods=['GET'])
def api_chat_messages_list():
    u = _current_user()
    if not u:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        limit = int(request.args.get('limit', 100))
    except ValueError:
        limit = 100
    messages = user_store.list_chat_messages(u['id'], limit)
    return jsonify({'messages': messages}), 200


@app.route('/api/chat/messages', methods=['POST'])
def api_chat_messages_create():
    u = _current_user()
    if not u:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        body = request.get_json(silent=True) or {}
        role = (body.get('role') or '').strip()
        content = body.get('content', '')
        row = user_store.add_chat_message(u['id'], role, content)
        return jsonify({'ok': True, 'message': row}), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        log_error('api_chat_messages_create', e)
        return jsonify({'error': str(e)}), 500


# ========================================
# REDIS DATA VIEWER API ENDPOINTS
# ========================================

@app.route('/api/redis/keys', methods=['GET'])
def get_redis_keys():
    """Get all Redis keys categorized by type."""
    try:
        if not redis_service.is_connected():
            return jsonify({'error': 'Redis not connected'}), 503
        
        client = redis_service.client
        data_keys = []
        alias_keys = []
        embedding_keys = []
        canonical_keys = []
        
        # Scan all keys
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor, match="*", count=100)
            for key in keys:
                if key.startswith("data:"):
                    data_keys.append(key)
                elif key.startswith("alias:"):
                    alias_keys.append(key)
                elif key.startswith("emb:"):
                    embedding_keys.append(key)
                elif key.startswith("canonical:"):
                    canonical_keys.append(key)
            if cursor == 0:
                break
        
        return jsonify({
            'data_keys': sorted(data_keys),
            'alias_keys': sorted(alias_keys),
            'embedding_keys': sorted(embedding_keys),
            'canonical_keys': sorted(canonical_keys)
        })
    except Exception as e:
        log_error('get_redis_keys', e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/redis/all-data', methods=['GET'])
def get_all_redis_data():
    """Get all cached data from Redis."""
    try:
        if not redis_service.is_connected():
            return jsonify({'error': 'Redis not connected'}), 503
        
        client = redis_service.client
        all_data = []
        
        # Get all data keys
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor, match="data:*", count=100)
            for key in keys:
                try:
                    data = client.get(key)
                    if data:
                        canonical_key = key.replace("data:", "")
                        parsed_data = json.loads(data)
                        all_data.append({
                            'canonical_key': canonical_key,
                            'data': parsed_data
                        })
                except:
                    continue
            if cursor == 0:
                break
        
        return jsonify(all_data)
    except Exception as e:
        log_error('get_all_redis_data', e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/redis/data/<canonical_key>', methods=['GET'])
def get_redis_data_by_key(canonical_key):
    """Get cached data for a specific canonical key."""
    try:
        if not redis_service.is_connected():
            return jsonify({'error': 'Redis not connected'}), 503
        
        data = redis_service.fetch_from_redis(canonical_key)
        if data:
            return jsonify(data)
        else:
            return jsonify({'error': 'Key not found'}), 404
    except Exception as e:
        log_error('get_redis_data_by_key', e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/redis/aliases', methods=['GET'])
def get_all_aliases():
    """Get all aliases grouped by canonical key."""
    try:
        if not redis_service.is_connected():
            return jsonify({'error': 'Redis not connected'}), 503
        
        client = redis_service.client
        aliases_by_key = {}
        
        # Get all canonical keys with their aliases
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor, match="canonical:*:aliases", count=100)
            for key in keys:
                try:
                    aliases_json = client.get(key)
                    if aliases_json:
                        # Extract canonical key from "canonical:KEY:aliases"
                        canonical_key = key.replace("canonical:", "").replace(":aliases", "")
                        aliases = json.loads(aliases_json)
                        aliases_by_key[canonical_key] = aliases
                except:
                    continue
            if cursor == 0:
                break
        
        return jsonify(aliases_by_key)
    except Exception as e:
        log_error('get_all_aliases', e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/redis/embeddings', methods=['GET'])
def get_embeddings_stats():
    """Get embeddings statistics."""
    try:
        if not redis_service.is_connected():
            return jsonify({'error': 'Redis not connected'}), 503
        
        client = redis_service.client
        total = 0
        sample_dim = None
        
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor, match="emb:*", count=100)
            total += len(keys)
            
            # Get dimension from first embedding
            if sample_dim is None and keys:
                try:
                    data = client.get(keys[0])
                    if data:
                        parsed = json.loads(data)
                        if 'embedding' in parsed:
                            sample_dim = len(parsed['embedding'])
                except:
                    pass
            
            if cursor == 0:
                break
        
        return jsonify({
            'total_embeddings': total,
            'embedding_dimension': sample_dim
        })
    except Exception as e:
        log_error('get_embeddings_stats', e)
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    log_api_request('GET', '/health', 200)
    return jsonify({
        'status': 'healthy',
        'redis_connected': redis_service.is_connected(),
        'openai_configured': bool(OPENAI_API_KEY),
        'similarity_threshold': SIMILARITY_THRESHOLD
    })


@app.route('/stats', methods=['GET'])
def get_stats():
    """Get system statistics."""
    log_api_request('GET', '/stats', 200)
    return jsonify(query_controller.get_stats())


@app.route('/api/faq', methods=['GET'])
def api_faq_suggestions():
    """Suggested starter questions for the assistant chat UI."""
    return jsonify({"suggestions": FAQ_SUGGESTIONS})


@app.route('/query', methods=['POST'])
def handle_query():
    """
    Main query endpoint.
    
    STREAMING WORKFLOW (cache miss):
    1. Embeddings + quick Redis lookup (no LLM blocking)
    2. Stream answer to the user immediately
    3. Background: extract data, aliases, embeddings, Redis cache
    
    Expected request body:
    {
        "query": "student question",
        "redis_json": {...},       // optional; if set, skips workflow
        "planner_context": {...}   // optional; academic plan scheduling (structured program + profile)
    }
    """
    try:
        log_api_request('POST', '/query')
        data = request.get_json()
        if not data or 'query' not in data:
            log_api_request('POST', '/query', 400)
            return jsonify({'error': 'Missing required field: query'}), 400
        query = data['query']
        redis_json = data.get('redis_json', None)
        planner_context = data.get('planner_context')
        # Process query through controller (7-step workflow)
        result = query_controller.process_query(query, redis_json, planner_context)
        try:
            from services.analytics_service import record_query_event
            topic = (result.get('json') or {}).get('topic') or 'unknown'
            record_query_event(query, result.get('source', 'live_web'), topic, 'sync')
        except Exception:
            pass
        # Validate output format
        is_valid, error_msg = OutputValidator.validate_output(result)
        log_validation_result(is_valid, error_msg)
        if not is_valid:
            log_api_request('POST', '/query', 500)
            return jsonify({'error': f'Output validation failed: {error_msg}','result': result}), 500
        log_api_request('POST', '/query', 200)
        return jsonify(result), 200
    except Exception as e:
        log_error('handle_query', e)
        log_api_request('POST', '/query', 500)
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500


@app.route('/query/stream', methods=['POST'])
def handle_query_stream():
    """
    Streaming query endpoint using Server-Sent Events (SSE).
    
    Returns a streaming response where the answer is sent chunk by chunk
    as it's generated, making the response appear faster to users.
    
    Expected request body:
    {
        "query": "student question",
        "redis_json": {...},       // optional
        "planner_context": {...}   // optional; same as /query
    }
    """
    try:
        log_api_request('POST', '/query/stream')
        data = request.get_json(silent=True) or {}
        if not data:
            log_api_request('POST', '/query/stream', 400)
            return jsonify({'error': 'Invalid JSON body'}), 400
        
        query = (data.get("query") or "").strip()
        redis_json = data.get('redis_json', None)
        planner_context = data.get('planner_context', None)
        image_payload, image_err = _parse_image_upload(data)
        if image_err:
            return jsonify({"error": image_err}), 400
        if image_payload and not query:
            query = "Please help me understand this image in the context of university life at JUST."

        if not query and not image_payload:
            return jsonify({"error": "Missing required field: query"}), 400
        
        # Log query received
        from logger import log_query_received
        log_query_received(query, redis_json is not None)
        sys.stdout.flush()  # Force flush to terminal
        
        def _step(index, label, detail=""):
            return f"data: {json.dumps({'type': 'step', 'index': index, 'label': label, 'detail': detail})}\n\n"

        def _run_with_live_steps(work_fn):
            """Run blocking pipeline work on a thread; yield step events as they happen."""
            step_queue = queue.Queue()
            result_box: dict = {}
            error_box: dict = {}

            def on_step(index, label, detail=""):
                step_queue.put(("step", index, label, detail or ""))

            def worker():
                try:
                    result_box["data"] = work_fn(on_step)
                except Exception as exc:
                    error_box["error"] = exc
                finally:
                    step_queue.put(("end", None, None, None))

            threading.Thread(target=worker, daemon=True).start()

            while True:
                kind, idx, label, detail = step_queue.get()
                if kind == "end":
                    break
                yield _step(idx, label, detail)

            if "error" in error_box:
                raise error_box["error"]
            return result_box["data"]

        def generate():
            try:
                from logger import log_answer_generation, log_answer_streaming_start, log_answer_streaming_complete, log_response_ready

                if image_payload:
                    img_b64, img_mime = image_payload
                    yield _step(1, "Analysing image", "Reading your uploaded image")
                    yield f"data: {json.dumps({'type': 'metadata', 'data': {'source': 'vision', 'mode': 'image'}})}\n\n"
                    yield _step(2, "Generating answer", "Writing a detailed response")
                    log_answer_streaming_start()
                    openai_service = query_controller.openai_service
                    total_chars = 0
                    for chunk in openai_service.generate_vision_answer_stream(
                        query, img_b64, img_mime
                    ):
                        if chunk:
                            total_chars += len(chunk)
                            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)}\n\n"
                    log_answer_streaming_complete(total_chars)
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    return

                result_data = yield from _run_with_live_steps(
                    lambda on_step: query_controller.process_query_for_streaming(
                        query, redis_json, planner_context, on_step=on_step
                    )
                )
                sys.stdout.flush()

                source = result_data.get('source', 'live_web')

                try:
                    from services.analytics_service import record_query_event
                    jd = result_data.get('json') or {}
                    topic = jd.get('topic') or 'unknown'
                    record_query_event(query, source, topic, 'stream')
                except Exception:
                    pass

                # Send metadata
                yield f"data: {json.dumps({'type': 'metadata', 'data': result_data})}\n\n"

                # Final pipeline step — answer generation
                yield _step(6, "Generating answer", "Writing a detailed response")

                json_data = result_data.get('json', {})
                clean_json = {k: v for k, v in json_data.items() if k != 'aliases'}

                log_answer_generation(source)
                log_answer_streaming_start()
                sys.stdout.flush()

                openai_service = query_controller.openai_service
                total_chars = 0
                for chunk in openai_service.generate_answer_stream(clean_json, query, source):
                    if chunk:
                        total_chars += len(chunk)
                        yield f"data: {json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)}\n\n"

                log_answer_streaming_complete(total_chars)
                log_response_ready(source, total_chars, len(result_data.get('aliases', [])))
                sys.stdout.flush()

                yield f"data: {json.dumps({'type': 'done'})}\n\n"

            except Exception as e:
                log_error('handle_query_stream', e)
                error_msg = str(e).replace('\n', '\\n')
                yield f"data: {json.dumps({'type': 'error', 'message': error_msg})}\n\n"
        
        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive'
            }
        )
        
    except Exception as e:
        log_error('handle_query_stream', e)
        log_api_request('POST', '/query/stream', 500)
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500


@app.route('/cache/<topic_key>', methods=['GET'])
def get_cache(topic_key):
    """Get cached data for a topic."""
    log_api_request('GET', f'/cache/{topic_key}')
    cached = query_controller.get_cached_data(topic_key)
    if cached:
        return jsonify(cached), 200
    else:
        return jsonify({'message': 'No cached data found'}), 404


@app.route('/cache/<topic_key>', methods=['DELETE'])
def delete_cache(topic_key):
    """Delete cached data for a topic."""
    log_api_request('DELETE', f'/cache/{topic_key}')
    success = redis_service.delete_key(topic_key)
    if success:
        return jsonify({'message': f'Cache deleted for {topic_key}'}), 200
    else:
        return jsonify({'message': 'Cache deletion failed'}), 500


@app.route('/api/redis/clear', methods=['DELETE'])
def clear_all_redis():
    """Clear all Redis data (for testing)."""
    try:
        if not redis_service.is_connected():
            return jsonify({'error': 'Redis not connected'}), 503
        
        client = redis_service.client
        client.flushdb()
        return jsonify({'message': 'All Redis data cleared'}), 200
    except Exception as e:
        log_error('clear_all_redis', e)
        return jsonify({'error': str(e)}), 500


@app.route('/aliases/generate', methods=['POST'])
def generate_aliases():
    """
    Generate aliases for a query and store with embeddings.
    
    Expected request body:
    {
        "query": "student question"
    }
    """
    try:
        log_api_request('POST', '/aliases/generate')
        data = request.get_json()
        
        if not data or 'query' not in data:
            log_api_request('POST', '/aliases/generate', 400)
            return jsonify({
                'error': 'Missing required field: query'
            }), 400
        
        query = data['query']
        result = query_controller.generate_aliases(query)
        
        log_api_request('POST', '/aliases/generate', 200)
        return jsonify(result), 200
        
    except Exception as e:
        log_error('generate_aliases', e)
        log_api_request('POST', '/aliases/generate', 500)
        return jsonify({
            'error': f'Internal server error: {str(e)}'
        }), 500


@app.route('/aliases/<canonical_key>', methods=['GET'])
def get_aliases(canonical_key):
    """Get all aliases for a canonical key."""
    try:
        log_api_request('GET', f'/aliases/{canonical_key}')
        
        if not redis_service.is_connected():
            return jsonify({'error': 'Redis not connected'}), 503
        
        result = query_controller.get_aliases(canonical_key)
        
        if result['aliases']:
            return jsonify(result), 200
        else:
            return jsonify({
                'canonical_key': canonical_key,
                'aliases': [],
                'message': 'No aliases found for this key'
            }), 404
            
    except Exception as e:
        log_error('get_aliases', e)
        return jsonify({
            'error': f'Internal server error: {str(e)}'
        }), 500


if __name__ == '__main__':
    from config import SERVER_HOST, SERVER_PORT
    from logger import log_system_ready
    
    # Initialize logging
    log_system_start()
    log_system_config(redis_service.is_connected(), bool(OPENAI_API_KEY))
    
    logger = get_logger()
    logger.info(f"📊 Similarity Threshold: {SIMILARITY_THRESHOLD}")
    logger.info(f"🔄 WORKFLOW: Query → Embeddings → Redis Cache → PDF/Web Extract → Answer")
    
    log_system_ready(SERVER_HOST, SERVER_PORT)
    
    app.run(host=SERVER_HOST, port=SERVER_PORT, debug=True)
