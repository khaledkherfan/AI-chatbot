# JUST Student Assistant — Business Logic

A plain-language map of **what the system does, what each part is responsible for, and how the parts work together.**
This document is meant for a non-technical reader (client / stakeholder): it explains the *business behind the buttons*, not the code.

> The system is a smart assistant for students of **Jordan University of Science & Technology (JUST)**.
> A student can **ask any university question, attach a picture, plan their study schedule, and track academic calendar deadlines** — all from one chat-style app.

---

## Component diagram — at a glance

The drawing below shows every general component and how each one deals with the others.
Colours show the *type* of interaction (user action, internal work, outside source, answer back).

![Business logic component diagram](./business-logic-diagram.png)

*Source: regenerate any time with `python docs/draw_business_logic.py`.*

---

## Caching & Redis — the core of the system

The single most important idea in the product is its **memory**. The diagram below zooms in on it:
how Redis stores knowledge, how a known question is answered **instantly**, and how a new question
is **learned** and saved so it becomes instant next time.

![Caching and Redis core diagram](./caching-redis-diagram.png)

**What Redis keeps (4 kinds of records):**

| Record | Plain meaning |
|--------|---------------|
| `data:<topic>` | The actual saved answer (fees, dates, policies …) as structured data. |
| `alias:<phrase>` | A known phrasing mapped to its topic — covers both Arabic and English wording. |
| `emb:<phrase>` | A "meaning fingerprint" of a phrase, so similar questions match even if worded differently. |
| `canonical:<topic>:aliases` | The full list of phrasings that point to one topic. |

**Why this is the heart of the system:**

- **Two layers of speed** — an in-RAM layer holds the meaning-vectors so the system doesn't even scan Redis on every question, and Redis itself is an in-memory store (millisecond reads).
- **Instant on repeats** — if a question matches something already known, the saved answer comes straight back with **no web lookup and no AI cost**.
- **It learns automatically** — a brand-new question is answered the slower way once, then quietly stored (answer + phrasings + vectors). The *next* student asking the same thing gets the instant path.
- **Always fresh** — every stored record auto-expires after a set time (TTL), so cached answers can't go stale.

*Source: regenerate any time with `python docs/draw_caching.py`.*

---

## 1. The big picture (what the product is)

```mermaid
flowchart TB
    subgraph WHO["👥 Who uses it"]
        STU["🎓 Student<br/>asks questions, plans study,<br/>sets reminders"]
        ADM["🛠️ Administrator<br/>watches usage &<br/>manages data"]
    end

    subgraph APP["📱 The Assistant App"]
        BRAIN["🧠 The Brain<br/>understands the question<br/>and decides how to answer"]
    end

    subgraph VALUE["✨ What the student gets"]
        ANS["Instant, accurate answers<br/>about fees, registration,<br/>deadlines, study plans"]
    end

    STU --> APP
    ADM --> APP
    APP --> VALUE

    style WHO fill:#e8f0fe,stroke:#4285f4
    style APP fill:#fff4e5,stroke:#f4a142
    style VALUE fill:#e6f4ea,stroke:#34a853
```

**In one sentence:** a student types a question, the system decides the *fastest reliable way* to answer it, and streams the answer back word-by-word like a chat.

---

## 2. The main building blocks (each component, in business terms)

```mermaid
flowchart LR
    subgraph FRONT["🖥️ What the student sees"]
        CHAT["💬 Chat & Vision<br/>ask in text or<br/>upload an image"]
        PLAN["📅 Study Planner<br/>build a semester plan"]
        CAL["🔔 Calendar & Reminders<br/>track academic dates"]
        LOGIN["🔐 Login / Register"]
    end

    subgraph CORE["🧠 The decision-maker"]
        ORCH["Query Orchestrator<br/><i>chooses how to answer<br/>every question</i>"]
    end

    subgraph HELPERS["⚙️ The specialist workers"]
        MATCH["🎯 Smart Matcher<br/>recognises questions it<br/>has answered before"]
        MEM["⚡ Fast Memory (Cache)<br/>stores known answers<br/>for instant reuse"]
        FETCH["🌐 Information Fetcher<br/>reads official JUST<br/>web pages & PDFs"]
        AI["🤖 AI Writer<br/>turns facts into a clear,<br/>friendly answer"]
    end

    subgraph RECORDS["🗄️ The records"]
        USERS["👤 Accounts & Chat History"]
        STATS["📊 Usage Analytics"]
    end

    CHAT --> ORCH
    PLAN --> ORCH
    CAL --> CORE
    LOGIN --> USERS

    ORCH --> MATCH
    ORCH --> MEM
    ORCH --> FETCH
    ORCH --> AI
    ORCH --> STATS

    style FRONT fill:#e8f0fe,stroke:#4285f4
    style CORE fill:#fff4e5,stroke:#f4a142
    style HELPERS fill:#f3e8fd,stroke:#a142f4
    style RECORDS fill:#e6f4ea,stroke:#34a853
```

| Component | What it is responsible for (business value) |
|-----------|-----------------------------------------------|
| **Chat & Vision** | The main door. Student asks in plain Arabic/English, or uploads a photo (e.g. a fee table) to ask about it. |
| **Study Planner** | Helps a student build a personalised semester study plan based on their program and profile. |
| **Calendar & Reminders** | Shows official academic calendar events and lets students set personal reminders. |
| **Login / Register** | Identifies the student so their chat history and reminders are saved and private. |
| **Query Orchestrator** | The "manager." For every question it decides: *Do we already know this? Should we look it up? How do we answer fastest?* |
| **Smart Matcher** | Recognises that "كم الرسوم" and "what are the fees" mean the same thing, so old answers can be reused. |
| **Fast Memory (Cache)** | Keeps answers we've already prepared, so repeated questions return **instantly** and cost nothing extra. |
| **Information Fetcher** | When something is new, it reads the **official JUST sources** (web pages / PDFs) to get real, current facts. |
| **AI Writer** | Takes the facts and writes a clear, complete, human-sounding answer — streamed live to the student. |
| **Accounts & Chat History** | Safely stores who the user is and what they discussed. |
| **Usage Analytics** | Records what students ask, so administrators understand demand and quality. |

---

## 3. How a question gets answered (the core business flow)

This is the heart of the product — the logic that makes it **fast *and* accurate**.

```mermaid
flowchart TD
    START(["🎓 Student asks a question"]) --> IMG{"Is it<br/>an image?"}

    IMG -->|Yes| VISION["🤖 AI looks at the image<br/>and explains it"]
    VISION --> STREAM

    IMG -->|No| PLANCHK{"Is it a<br/>study-plan<br/>request?"}
    PLANCHK -->|Yes| PLANANS["📅 Build a personalised<br/>study plan"]
    PLANANS --> STREAM

    PLANCHK -->|No| KNOWN{"🎯 Have we<br/>answered something<br/>like this before?"}

    KNOWN -->|"Yes — in Fast Memory"| HIT["⚡ Reuse the saved answer<br/><b>(instant, no lookup)</b>"]
    HIT --> STREAM

    KNOWN -->|"No — it's new"| FETCH["🌐 Read official JUST<br/>web pages / PDFs<br/>to get the real facts"]
    FETCH --> STREAM["📝 AI writes the answer and<br/>streams it word-by-word"]

    STREAM --> SHOW(["💬 Student sees the answer"])

    FETCH -.->|"in the background, later"| LEARN["🧠 Save this answer to<br/>Fast Memory + learn its<br/>wording, so next time<br/>it's instant"]

    style START fill:#e8f0fe,stroke:#4285f4
    style SHOW fill:#e6f4ea,stroke:#34a853
    style HIT fill:#e6f4ea,stroke:#34a853
    style LEARN fill:#fff4e5,stroke:#f4a142
    style STREAM fill:#f3e8fd,stroke:#a142f4
```

**Why this design matters to the client:**

1. **Speed** — Known questions are answered from Fast Memory instantly; the student never waits.
2. **Accuracy** — New questions are answered from **official JUST sources**, not guesses.
3. **It gets smarter over time** — Every new question is quietly saved and "learned" in the background, so the *next* student asking the same thing gets an instant answer.
4. **One experience, many needs** — text, images, study plans, and reminders all flow through the same friendly chat.

---

## 4. How the parts talk to each other (interaction overview)

```mermaid
flowchart LR
    STUDENT["🎓 Student"] -->|"asks / uploads"| APP["📱 Assistant App<br/>(web pages)"]
    APP -->|"sends the question"| ORCH["🧠 Query Orchestrator"]

    ORCH <-->|"recognise / reuse"| MATCH["🎯 Smart Matcher"]
    ORCH <-->|"read / save answers"| MEM[("⚡ Fast Memory")]
    ORCH -->|"when new"| FETCH["🌐 Information Fetcher"]
    FETCH -->|"reads"| JUST["🏛️ Official JUST<br/>websites & PDFs"]
    ORCH -->|"write the answer"| AI["🤖 AI Writer"]
    AI <-->|"language model"| CLOUD["☁️ AI Provider<br/>(OpenAI / Groq)"]

    APP -->|"login & history"| ACC[("👤 Accounts &<br/>Chat History")]
    APP -->|"reminders"| CAL["🔔 Calendar Service"]
    CAL <--> MEM
    ORCH -->|"records usage"| STATS[("📊 Analytics")]

    ADMIN["🛠️ Administrator"] -->|"monitors"| DASH["📋 Admin Dashboard"]
    DASH --> STATS
    DASH --> MEM

    style STUDENT fill:#e8f0fe,stroke:#4285f4
    style ADMIN fill:#e8f0fe,stroke:#4285f4
    style ORCH fill:#fff4e5,stroke:#f4a142
    style JUST fill:#fde8e8,stroke:#ea4335
    style CLOUD fill:#fde8e8,stroke:#ea4335
```

| Interaction | What's happening in business terms |
|-------------|-------------------------------------|
| Student → App → Orchestrator | The question enters the system and reaches the decision-maker. |
| Orchestrator ↔ Smart Matcher / Fast Memory | "Do we already know this?" — reuse if possible. |
| Orchestrator → Fetcher → Official JUST sources | If new, go get the **real, current facts** from the university. |
| Orchestrator → AI Writer ↔ AI Provider | Turn facts into a clear answer, streamed back live. |
| App → Accounts / Calendar | Keep the student's identity, history, and reminders safe and personal. |
| Orchestrator → Analytics; Admin → Dashboard | Administrators see what's being asked and can inspect the saved answers. |

---

## 5. The four things a student can actually do

```mermaid
flowchart TB
    subgraph S["🎓 Student capabilities"]
        direction LR
        A["💬 <b>Ask anything</b><br/>fees, registration,<br/>deadlines, policies"]
        B["🖼️ <b>Ask about an image</b><br/>upload a photo and<br/>get it explained"]
        C["📅 <b>Plan studies</b><br/>get a personalised<br/>semester plan"]
        D["🔔 <b>Track dates</b><br/>see the calendar and<br/>set reminders"]
    end
    style S fill:#e6f4ea,stroke:#34a853
```

Each of these is powered by the same engine in section 3 — the student just experiences it as four simple features.

---

### Summary for the client

> The JUST Student Assistant works like a **knowledgeable university help-desk that never sleeps and keeps getting smarter.**
> It answers known questions **instantly** from memory, looks up **new ones from official sources**, explains **images**, builds **study plans**, and tracks **deadlines** — all in one chat. Behind the scenes it quietly learns from every question, so the service becomes faster and more complete the more it's used.

*Companion to the technical [`ARCHITECTURE.md`](./ARCHITECTURE.md), which covers the engineering detail.*
