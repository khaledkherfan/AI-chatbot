# JUST Student Assistant — System Architecture

Architecture reference for the **JUST University Student Assistant**: a Flask backend with Redis caching, semantic matching, LLM-powered answers, and static web clients.

> **For project discussions:** start with [§2 Executive structure](#2-executive-structure-discussion-diagram) — a high-level component map (Chat, Cache, LLM Engine, etc.), not a deep dive.

---

## 1. System context

```
                    ┌─────────────────────┐
                    │  JUST official      │
                    │  websites           │
                    └──────────▲──────────┘
                               │ fetch / scrape
  ┌──────────────┐    HTTPS     │
  │ Student /    │──────────────┼──────────────────────────────┐
  │ Admin        │              │                              │
  └──────────────┘              ▼                              │
                    ┌───────────────────────┐                  │
                    │  JUST Assistant       │                  │
                    │  Flask + HTML UI      │                  │
                    └─┬─────┬─────┬────┬────┘                  │
                      │     │     │    │                       │
           chat/vision│     │     │    │users + history        │
                      │     │     │    │                       │
         ┌────────────┘     │     │    └──────────┐            │
         ▼                  │     │               ▼            │
  ┌─────────────┐           │     │        ┌─────────────┐     │
  │ LLM API     │           │     │        │  SQLite     │     │
  │ OpenAI/Groq │           │     │        │  (users,    │     │
  └─────────────┘           │     │        │   chat)     │     │
                            │     │        └─────────────┘     │
                            │     │                            │
                            │     │ vectors                    │
                            │     ▼                            │
                            │  ┌─────────────┐                 │
                            │  │ Embeddings  │                 │
                            │  │ API         │                 │
                            │  └─────────────┘                 │
                            │                                  │
                            │ cache                            │
                            ▼                                  │
                     ┌─────────────┐                           │
                     │ Redis /     │◄──────────────────────────┘
                     │ Memurai     │
                     └─────────────┘
```

| Actor / system | Role |
|----------------|------|
| **Student** | Chat, study planner, calendar reminders (browser) |
| **Admin** | Dashboard, Redis inspection, analytics |
| **Flask server** | REST + SSE APIs, serves HTML |
| **Redis** | Cached topic JSON, alias embeddings, calendar blob, per-user reminders |
| **SQLite** | Accounts, saved chat messages |
| **LLM provider** | Answers, extraction, planner, vision (optional model) |

---

## 2. Executive structure (discussion diagram)

**Purpose:** one slide-style view for supervisors or demos — *how the system is organized*, not every class or endpoint.

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                           CLIENT LAYER                                       ║
╠══════════════════════════════════════════════════════════════════════════════╣
║   ┌─────────────────────────────┐    ┌─────────────────────────────┐       ║
║   │  Web UI                     │    │  Admin UI                   │       ║
║   │  Chat · Planner · Calendar  │    │  Metrics · Redis tools      │       ║
║   │  Auth                       │    │                             │       ║
║   └──────────────┬──────────────┘    └──────────────┬──────────────┘       ║
╚══════════════════╪═══════════════════════════════════╪═══════════════════════╝
                   │                                   │
                   └─────────────────┬─────────────────┘
                                     ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║                           API GATEWAY                                        ║
╠══════════════════════════════════════════════════════════════════════════════╣
║              ┌─────────────────────────────────────────┐                     ║
║              │  Flask application                      │                     ║
║              │  HTTP routes + SSE stream               │                     ║
║              └───┬─────────┬─────────┬─────────┬───────┘                     ║
╚══════════════════╪═════════╪═════════╪═════════╪═════════════════════════════╝
                   │         │         │         │
         ┌─────────┘         │         │         └──────────────┐
         ▼                   │         ▼                        ▼
╔════════════════════╗       │  ┌──────────────┐      ┌─────────────────┐
║ CORE INTELLIGENCE  ║       │  │ User store   │      │ Calendar        ║
╠════════════════════╣       │  │ SQLite + JWT │      │ service         ║
║                    ║       │  └──────────────┘      └────────┬────────┘
║ ┌────────────────┐ ║       │                                 │
║ │ Query          │ ║       │                                 │
║ │ orchestrator   │ ║       │                                 │
║ └───┬───┬───┬────┘ ║       │                                 │
║     │   │   │      ║       │                                 │
║     │   │   │      ║       ▼                                 ▼
║     │   │   │      ║  ┌─────────────────────────────────────────────┐
║     │   │   └──────┼─►│ MEMORY & SPEED                              │
║     │   │          ║  ├─────────────────────────────────────────────┤
║     │   │          ║  │  ┌──────────────┐  ┌──────────────────┐  │
║     │   │          ║  │  │ Redis cache  │  │ Semantic index   │  │
║     │   │          ║  │  │ topic data   │  │ embeddings+alias │  │
║     │   │          ║  │  └──────┬───────┘  └────────┬─────────┘  │
║     │   │          ║  └─────────┼───────────────────┼────────────┘
║     │   │          ║            │                   │
║     ▼   ▼          ▼            ▼                   ▼
║ ┌─────┐ ┌────────┐ ┌────────┐              ┌──────────┐
║ │ LLM │ │Content │ │ Vision │              │  Redis   │
║ │engine│ │extractor│ │ engine │              │  server  │
║ └──┬──┘ └───┬────┘ └───┬────┘              └──────────┘
╚════╪════════╪══════════╪══════════════════════════════════════════════╝
     │        │          │
     │        │          │
     ▼        ▼          ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║  EXTERNAL                                                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║   ┌─────────────────────────┐              ┌─────────────────────────┐           ║
║   │ JUST official sources │              │ Cloud LLM API           │           ║
║   │ sites · viewplan · cal  │              │ Groq / OpenAI           │           ║
║   └─────────────────────────┘              └─────────────────────────┘           ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

**Talking points (30 seconds):**

1. Users hit a **single Flask API** that serves pages and JSON/SSE.
2. Most questions go through the **query orchestrator**: match semantically → **Redis** if possible → else **extract** from official sources → **LLM** streams the answer.
3. **Images** bypass the cache pipeline and go to a **vision** model.
4. **Login**, chat history, and **calendar reminders** use **SQLite + Redis** respectively.

---

## 3. Component diagram (logical)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ PRESENTATION                                                                │
├─────────────────────────────────────────────────────────────────────────────┤
│  assistant.html   planner.html   calendar-reminders.html   login/register │
│  admin-ui/                          static Redis viewer pages               │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ HTTP LAYER  (server.py)                                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│  /query · /query/stream    /api/auth/*    /api/chat/messages                │
│  /api/calendar/*           /api/admin/* · /api/redis/*    /health · /faq  │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          ▼                         ▼                         ▼
┌──────────────────┐    ┌──────────────────────┐    ┌─────────────────────┐
│ QueryController  │    │ AuthService          │    │ CalendarReminder    │
│ admin_api        │    │ UserStore            │    │ Service             │
│ analytics        │    │                      │    │                     │
└────────┬─────────┘    └──────────┬───────────┘    └──────────┬──────────┘
         │                         │                           │
         ▼                         ▼                           ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ DOMAIN SERVICES                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  OpenAIService   RedisService   EmbeddingsService   AliasService            │
│  ExtractorService  ◄── resources.json                                       │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
             ┌──────────┐   ┌──────────┐   ┌──────────────┐
             │  Redis   │   │ SQLite   │   │ logs/ JSONL  │
             │          │   │ instance/│   │              │
             └──────────┘   └──────────┘   └──────────────┘
```

---

## 4. Class diagram (main types)

Simplified view of core classes and dependencies (singletons where used in code).

```
                    ┌─────────────────────┐
                    │     FlaskApp        │
                    │  routes, SSE, HTML  │
                    └──────────┬──────────┘
           ┌──────────────────┼──────────────────┬─────────────────┐
           │                  │                  │                 │
           ▼                  ▼                  ▼                 ▼
┌──────────────────┐  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐
│ QueryController  │  │ AuthService │  │  UserStore   │  │ CalendarReminder  │
│                  │  │             │  │              │  │ Service           │
│ - RedisService   │  └──────┬──────┘  └──────▲───────┘  └─────────┬─────────┘
│ - OpenAIService  │         │               │                      │
│ - AliasService   │         └───────────────┘                      │
│ - ExtractorSvc   │                                                │
│ - EmbeddingsSvc  │                                                │
└────────┬─────────┘                                                │
         │ uses                                                      │
         ├──────────────────────────────────────────────────────────┤
         │          │              │              │                  │
         ▼          ▼              ▼              ▼                  ▼
   ┌──────────┐ ┌────────┐ ┌────────────┐ ┌────────────┐   ┌──────────────┐
   │ OpenAI   │ │ Redis  │ │ Embeddings │ │   Alias    │   │  OpenAI      │
   │ Service  │ │ Service│ │  Service   │ │  Service   │   │  Service     │
   └──────────┘ └───┬────┘ └─────┬──────┘ └────────────┘   └──────────────┘
                     │            │
                     └─────uses───┘

         QueryController ─ ─ ─► OutputValidator  (validates response JSON)

                    ┌─────────────────────┐
                    │  AnalyticsService   │  ◄── admin_api / query logs
                    └─────────────────────┘
```

| Class | Main responsibility |
|-------|---------------------|
| `QueryController` | End-to-end question workflow |
| `OpenAIService` | LLM calls: answers, extraction, vision, keys |
| `RedisService` | Cache read/write |
| `EmbeddingsService` | Similarity search over aliases |
| `ExtractorService` | Web/PDF/resource extraction |
| `AuthService` | JWT register/login |
| `UserStore` | SQLite users + chat messages |

---

## 5. Sequence diagrams

### 5.1 Standard chat question (cache miss → live answer)

```
  User          assistant.html      Flask           QueryController    Embeddings    Redis      Extractor      LLM         Cloud API
   │                 │                 │                  │               │           │            │            │              │
   │  type + send    │                 │                  │               │           │            │            │              │
   │────────────────►│                 │                  │               │           │            │            │              │
   │                 │  POST /query/stream {query}         │               │           │            │            │              │
   │                 │────────────────►│                  │               │           │            │            │              │
   │                 │                 │ process_query_for_streaming     │           │            │            │              │
   │                 │                 │─────────────────►│               │           │            │            │              │
   │                 │                 │                  │ match aliases │           │            │            │              │
   │                 │                 │                  │──────────────►│           │            │            │              │
   │                 │                 │                  │               │ load keys │            │            │              │
   │                 │                 │                  │               │──────────►│            │            │              │
   │                 │                 │                  │◄──────────────│           │            │            │              │
   │                 │                 │                  │               │           │            │            │              │
   │                 │                 │                  │         ┌─────┴─────┐     │            │            │              │
   │                 │                 │                  │         │ CACHE HIT │     │            │            │              │
   │                 │                 │                  │         └─────┬─────┘     │            │            │              │
   │                 │                 │                  │    fetch JSON │           │            │            │              │
   │                 │                 │                  │───────────────┼──────────►│            │            │              │
   │                 │                 │                  │◄──────────────┼───────────│            │            │              │
   │                 │                 │                  │         ┌─────┴─────┐     │            │            │              │
   │                 │                 │                  │         │ CACHE MISS│     │            │            │              │
   │                 │                 │                  │         └─────┬─────┘     │            │            │              │
   │                 │                 │                  │               │  extract  │            │            │              │
   │                 │                 │                  │───────────────┼───────────┼───────────►│            │              │
   │                 │                 │                  │               │           │            │ structure  │              │
   │                 │                 │                  │               │           │            │───────────►│─────────────►│
   │                 │                 │                  │               │           │            │◄───────────│◄─────────────│
   │                 │                 │◄─────────────────│  metadata     │           │            │            │              │
   │                 │◄────────────────│  SSE metadata    │               │           │            │            │              │
   │                 │                 │                  │               │           │            │            │              │
   │                 │                 │     stream answer (loop)          │           │            │            │              │
   │                 │                 │──────────────────────────────────┼───────────┼────────────┼───────────►│─────────────►│
   │                 │◄────────────────│  SSE chunks      │               │           │            │◄───────────│◄─────────────│
   │◄────────────────│  show answer    │                  │               │           │            │            │              │
   │                 │                 │                  │               │           │            │            │              │
```

### 5.2 Image question (vision path)

```
  User          assistant.html              Flask                    OpenAIService              Vision model
   │                 │                        │                            │                          │
   │ attach image    │                        │                            │                          │
   │────────────────►│                        │                            │                          │
   │                 │  POST /query/stream    │                            │                          │
   │                 │  {query, image_base64} │                            │                          │
   │                 │───────────────────────►│                            │                          │
   │                 │                        │ validate size / mime       │                          │
   │                 │◄───────────────────────│  SSE metadata (mode=image) │                          │
   │                 │                        │                            │                          │
   │                 │                        │  generate_vision_stream    │                          │
   │                 │                        │───────────────────────────►│                          │
   │                 │                        │                            │  multimodal stream       │
   │                 │                        │                            │─────────────────────────►│
   │                 │◄───────────────────────│◄───────────────────────────│◄─────────────────────────│
   │◄────────────────│  SSE text chunks       │                            │                          │
   │                 │                        │                            │                          │
```

### 5.3 Auth + persisted chat

```
  User              login / assistant              Flask                 AuthService              SQLite
   │                      │                         │                        │                      │
   │  register / login    │                         │                        │                      │
   │─────────────────────►│                         │                        │                      │
   │                      │  POST /api/auth/*       │                        │                      │
   │                      │────────────────────────►│                        │                      │
   │                      │                         │  register / verify     │                      │
   │                      │                         │───────────────────────►│                      │
   │                      │                         │                        │  read / write user   │
   │                      │                         │                        │─────────────────────►│
   │                      │                         │◄───────────────────────│◄─────────────────────│
   │                      │◄────────────────────────│  JWT                   │                      │
   │                      │  store token            │                        │                      │
   │                      │                         │                        │                      │
   │  send chat message   │                         │                        │                      │
   │─────────────────────►│  POST /api/chat/messages + Bearer               │                      │
   │                      │────────────────────────►│ validate JWT          │                      │
   │                      │                         │───────────────────────►│ save message         │
   │                      │                         │                        │─────────────────────►│
   │                      │  POST /query/stream     │                        │                      │
   │                      │────────────────────────►│  (answer streams)      │                      │
   │                      │                         │                        │                      │
```

### 5.4 Study planner (structured context)

```
  User              planner.html                 Flask                QueryController           OpenAIService
   │                      │                         │                        │                      │
   │  configure + ask     │                         │                        │                      │
   │─────────────────────►│                         │                        │                      │
   │                      │  POST /query/stream     │                        │                      │
   │                      │  {query, planner_ctx}   │                        │                      │
   │                      │────────────────────────►│                        │                      │
   │                      │                         │  skip RAG — build JSON │                      │
   │                      │                         │───────────────────────►│                      │
   │                      │                         │◄───────────────────────│  source=planner      │
   │                      │                         │  stream plan answer    │                      │
   │                      │                         │───────────────────────────────────────────────►│
   │                      │◄────────────────────────│◄───────────────────────────────────────────────│
   │                      │  optional PLAN_JSON merge into grid              │                      │
   │                      │                         │                        │                      │
```

---

## 6. State machine — query processing

States for a **text** question through `QueryController` (not vision, not forced `redis_json`).

```
                              ┌─────────────┐
                              │   START     │
                              └──────┬──────┘
                                     │ POST /query or /stream
                                     ▼
                              ┌─────────────┐
                              │  RECEIVED   │
                              └──────┬──────┘
                                     │ evaluate input
                                     ▼
                              ┌─────────────┐
                         ┌───│ CHECK BYPASS│───┐
                         │   └─────────────┘   │
           redis_json    │                     │ planner_context
                         ▼                     ▼
                  ┌─────────────┐       ┌─────────────┐
                  │ USE PROVIDED│       │  PLANNER    │
                  │    JSON     │       │    MODE     │
                  └──────┬──────┘       └──────┬──────┘
                         │                     │
                         └──────────┬──────────┘
                                    │
                         normal path│
                                    ▼
                              ┌─────────────┐
                              │  SEMANTIC   │
                              │   MATCH     │
                              └──────┬──────┘
                         ┌──────────┴──────────┐
                         ▼                     ▼
                  ┌─────────────┐       ┌─────────────┐
                  │  CACHE HIT  │       │ CACHE MISS  │
                  └──────┬──────┘       └──────┬──────┘
                         │                     │
                         │                     ▼
                         │              ┌─────────────┐
                         │              │ LIVE EXTRACT│
                         │              └──────┬──────┘
                         │                     ▼
                         │              ┌─────────────┐
                         │              │ BUILD JSON  │
                         │              └──────┬──────┘
                         │                     ▼
                         │              ┌─────────────┐
                         │              │ BACKGROUND  │
                         │              │ cache write │
                         │              └──────┬──────┘
                         └──────────┬──────────┘
                                    ▼
                              ┌─────────────┐
                              │ ANSWER READY│
                              │ (LLM input) │
                              └──────┬──────┘
                         ┌──────────┴──────────┐
                         ▼                     ▼
                  ┌─────────────┐       ┌─────────────┐
                  │  STREAMING  │       │    SYNC     │
                  │  SSE done   │       │  JSON resp  │
                  └──────┬──────┘       └──────┬──────┘
                         └──────────┬──────────┘
                                    ▼
                              ┌─────────────┐
                              │     END     │
                              └─────────────┘
```

### 6.1 Stream request (API layer)

```
                         ┌─────────────┐
                         │   START     │
                         └──────┬──────┘
                                │ parse JSON body
                                ▼
                         ┌─────────────┐
                         │ PARSE BODY  │
                         └──────┬──────┘
                    ┌───────────┴───────────┐
         image_base64│                       │ text only
                    ▼                       ▼
             ┌─────────────┐         ┌─────────────┐
             │ VISION PATH │         │  TEXT PATH  │
             └──────┬──────┘         └──────┬──────┘
                    │                       │
                    ▼                       ▼
             ┌─────────────┐         ┌─────────────┐
             │VISION STREAM│         │ ORCHESTRATE │
             └──────┬──────┘         │ QueryCtrl   │
                    │                └──────┬──────┘
                    │                       ▼
                    │                ┌─────────────┐
                    │                │EMIT METADATA│
                    │                └──────┬──────┘
                    │                       ▼
                    │                ┌─────────────┐
                    │                │TOKEN STREAM │
                    │                └──────┬──────┘
                    └──────────┬───────────┘
                               ▼
                         ┌─────────────┐
                         │  done/error │
                         └─────────────┘
```

---

## 7. Deployment view (Docker / Render)

```
   ┌──────────────┐
   │   Browser    │
   └──────┬───────┘
          │ HTTPS
          ▼
   ┌──────────────────────────────────────┐
   │  Render.com — Web service            │
   │  Docker → Gunicorn → server:app      │
   └───┬─────────┬─────────┬─────────────┘
       │         │         │
       ▼         ▼         ▼
 ┌──────────┐ ┌────────┐ ┌─────────────────┐
 │ Render   │ │  .env  │ │ LLM provider    │
 │ Redis    │ │ secrets│ │ (Groq/OpenAI)   │
 └──────────┘ └────────┘ └─────────────────┘
       │
       ▼
 ┌──────────────────┐
 │ Ephemeral disk   │
 │ SQLite instance/ │
 └──────────────────┘
```

---

## 8. Key data stores (Redis key patterns)

| Pattern | Purpose |
|---------|---------|
| `data:{canonical_key}` | Cached topic JSON (fees, registration, …) |
| `alias:*` / embedding keys | Alias text + vectors for similarity |
| `calendar:academic_events_v1` | Parsed academic calendar |
| `calendar:reminders:{user_id}` | Per-user reminder list |

---

## 9. Related files

| Area | Location |
|------|----------|
| HTTP entry | `server.py` |
| Query workflow | `controllers/query_controller.py` |
| LLM | `services/openai_service.py` |
| Config | `config.py`, `.env` |
| Official URLs | `resources.json` |
| Admin | `admin_api.py`, `admin-ui/` |
| Container | `Dockerfile` |

---

## 10. Diagram index

| # | Diagram | Use when |
|---|---------|----------|
| 1 | System context | External dependencies |
| **2** | **Executive structure** | **Project pitch / discussion** |
| 3 | Component (logical) | Engineering onboarding |
| 4 | Class diagram | Code-level design review |
| 5.1–5.4 | Sequences | Explain flows step-by-step |
| 6 | State machines | Workflow & branching |
| 7 | Deployment | DevOps / Render setup |

---

*Generated from the codebase structure of the JUST Student Assistant repository.*
