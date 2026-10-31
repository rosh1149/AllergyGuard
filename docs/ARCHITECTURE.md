# Architecture

AllergyGuard is one FastAPI service, a PostgreSQL database and a React single-page app. Both customer
pathways (packaged food and restaurant food) feed one deterministic allergen engine; external services
are optional sources that can fail without breaking a check. The diagrams below are Mermaid (they render
on GitHub).

## 1. Components

```mermaid
flowchart LR
  subgraph Browser["React + TypeScript app"]
    C[Customer pages]
    D[Restaurant dashboard]
    A[Admin pages]
  end
  subgraph API["FastAPI service"]
    AUTH[Auth, roles, tenant checks]
    PP[Packaged pipeline]
    RP[Restaurant pipeline]
    SRC[Source adapters<br/>retry + circuit breaker]
    ENG[Allergen engine<br/>normalize → map → fuse → decide]
    KB[Knowledge layer<br/>concepts, relations, rules]
    EXP[Explanation<br/>template or LLM with output checks]
    OBS[Metrics, traces, audit]
  end
  DB[(PostgreSQL<br/>Alembic migrations)]
  OFF[Open Food Facts]
  WEB[Manufacturer pages / web search]
  LLM[Claude API, optional]
  TES[Tesseract OCR + zxing barcodes]

  C & D & A --> AUTH
  AUTH --> PP & RP
  PP --> TES
  PP & RP --> SRC
  SRC --> OFF & WEB
  PP & RP --> ENG
  ENG --> KB
  ENG --> EXP --> LLM
  PP & RP & ENG & KB & OBS --> DB
```

| Component | Code | Responsibility |
|---|---|---|
| Auth and roles | `app/api/deps.py`, `app/core/security.py` | JWT sign-in; customer / restaurant (owner, manager, staff) / admin; restaurant membership on every dashboard route |
| Packaged pipeline | `app/packaged/` | photo checks, barcode, OCR, product identification, external sources |
| Restaurant pipeline | `app/restaurant/` | dish matching, branch and recipe-version selection, unregistered-restaurant fallback |
| Source adapters | `app/sources/` | one interface for every evidence source; health counters, retries, circuit breaker |
| Allergen engine | `app/engine/` | normalization, concept mapping, evidence fusion, decision rules, provenance, trace |
| Knowledge layer | `app/knowledge/`, `app/seed/` | ingredient concepts and relations, validation rules, versioned four-eyes changes |
| Evaluation | `app/evaluation/` | development benchmark and release gate, held-out runner, baselines, ablations |
| Observability | `app/core/` | request ids, structured events without personal data, metrics, limits |

## 2. One check, end to end

```mermaid
sequenceDiagram
  participant U as Customer
  participant API as API
  participant P as Pipeline
  participant S as Sources
  participant E as Engine
  participant DB as Database
  U->>API: photos / barcode / dish (+ token)
  API->>API: authenticate, load saved allergy profile
  API->>P: run packaged or restaurant pipeline
  P->>P: image checks, barcode, OCR (photo-job slot)
  P->>S: product database, manufacturer page, restaurant records
  S-->>P: evidence sources (or failed / skipped status)
  P->>E: sources + identity confidence + profile
  E->>E: normalize → map to concepts → fuse → decide per allergen
  E-->>P: outcomes, reasons, provenance, conflicts, trace
  P->>DB: store assessment (no photos)
  API-->>U: result with "Why", sources, limitations
```

## 3. Inside the engine

```mermaid
flowchart TB
  S[Evidence sources] --> N1[Clean text, split compound ingredients]
  N1 --> N2[OCR correction · spelling · synonyms · fuzzy match<br/>guesses may only add allergens]
  N2 --> M[Map to concepts; follow relations to allergens<br/>definite or possible]
  M --> PR[Per source: trusted? eligible for absence?<br/>verified, complete, fresh, identified, readable, all resolved]
  PR --> F[Fuse sources: compare lists, record conflicts]
  F --> D{Per allergen,<br/>first rule that applies}
  D -->|R1| P1[PRESENT]
  D -->|R2| P2[POSSIBLE CROSS-CONTACT]
  D -->|R3, R4, R6| P3[UNKNOWN]
  D -->|R5| P4[NOT LISTED IN VERIFIED RECORDS]
```

## 4. Knowledge changes

```mermaid
stateDiagram-v2
  [*] --> Proposed: admin proposes concept / alias / relation / FoodOn id
  Proposed --> Rejected: second admin rejects
  Proposed --> Checking: second admin approves
  Checking --> Rejected: graph rules, regression cases or safety benchmark fail
  Checking --> Published: all checks pass → new knowledge version
  Published --> [*]
  Rejected --> [*]
```

## 5. Restaurant feedback

```mermaid
flowchart LR
  CU[Customer suggestion] --> Q[Restaurant feedback queue]
  Q -->|ask a question| CL[Clarification from customer] --> Q
  Q -->|reject| X[Nothing changes]
  Q -->|confirm| V[New recipe version, verified by staff]
  V --> R[Future checks use the new version]
```

A suggestion is shown to customers only as an unverified candidate until staff confirm it.

## 6. Deployment

```mermaid
flowchart LR
  B[Browser] -->|HTTPS| RP[Reverse proxy / static files]
  RP -->|/api| API[FastAPI · uvicorn<br/>migrations at startup]
  API --> PG[(PostgreSQL 16)]
  API -.optional.-> OFF[Open Food Facts]
  API -.optional.-> CL[Claude API]
  API -.optional.-> BR[Brave search]
```

`docker-compose.yml` runs the database, API and frontend. CI runs the tests and the safety gate on every
push (`.github/workflows/ci.yml`).
