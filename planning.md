# Provenance Guard — Planning

## 1. Detection Signals

**Signal 1 — LLM-based classification (Groq, `llama-3.3-70b-versatile`)**
The model is prompted to read the submitted text and judge, holistically, whether it reads as AI-generated or human-written. It captures *semantic and stylistic coherence* — things like generic phrasing, hedging ("it is important to note that..."), overly balanced argument structure, and the kind of topic-generic fluency that large language models tend to produce. It is a single model call that returns a JSON object `{"ai_likelihood": <0-1 float>, "reasoning": "<short string>"}`. The prompt explicitly asks for a probability, not a binary label, so the raw output is already a 0–1 score (`S_llm`).
*Blind spot:* it cannot see structural/statistical regularities directly — it can be fooled by short text with too little signal to judge, and by AI text that has been paraphrased or edited by a human, since paraphrasing perturbs exactly the surface patterns the model is keying on.

**Signal 2 — Stylometric heuristics (pure Python)**
Three cheap, computable statistics over the raw text, each capturing a different *structural* property that tends to differ between human and AI writing:
- **Sentence-length variance** — AI text tends toward uniform sentence length; human writing is bursty (short sentences mixed with long ones).
- **Type-token ratio (TTR)** — vocabulary diversity (unique words / total words). Very smooth, "safe" AI phrasing often has lower lexical diversity across a passage than human writing that reaches for varied words.
- **Punctuation density/burstiness** — human writing is more irregular in em-dashes, ellipses, exclamation points, etc.; AI output tends toward a narrow, standard punctuation profile.
Each metric is normalized to 0–1 and averaged into a single composite `S_stylo` (0–1, "AI-likelihood").
*Blind spot:* these are statistical proxies, not semantic understanding — they can't tell if the content itself makes sense, and they are unstable on very short text (few sentences means variance and TTR are noisy), and can misfire on legitimately terse/formulaic human writing (short, simple, repetitive text can look "uniform" like AI text does).

**Why these two together:** one is semantic (a model's holistic read), one is purely structural (counting/statistics on the raw string). They fail in different ways and use different information, so agreement between them is much stronger evidence than either alone — and *disagreement* between them is itself a useful signal that the classification is unreliable.

**Combining into confidence:**
```
raw = 0.7 * S_llm + 0.3 * S_stylo
disagreement = abs(S_llm - S_stylo)
penalty = 0.15 if disagreement > 0.4 else 0.0
confidence = max(0.0, raw - penalty)
```
`S_llm` is weighted higher (0.7) because it's the stronger, less noisy signal — it's produced by a model trained on huge volumes of both human and AI text, whereas the stylometric composite is a cheap proxy that swings on short/atypical text. The disagreement penalty exists specifically to protect against false positives: if the two independent signals point in very different directions, that disagreement is itself evidence the read is unreliable, so the score is pulled down (never up) — biasing ambiguous cases toward "uncertain" or "human" rather than risking a confident-sounding wrong "AI" call.

## 2. Uncertainty Representation

`confidence` is a single 0–1 float meant to represent **"how likely this content is to be AI-generated," combined with how much the two independent signals agree.** A 0.6 does not mean "60% correct" in a statistical-confidence sense — it means "the combined evidence leans AI, but not strongly enough, and/or the two signals didn't fully agree, so we should say so plainly rather than force a verdict."

Thresholds partition `[0, 1]` into three bands, asymmetric on purpose (see hint about false positives being worse than false negatives on a writing platform):

| Confidence range | Attribution | Rationale |
|---|---|---|
| `confidence >= 0.75` | `likely_ai` | High bar — combined with the disagreement penalty, this effectively requires both signals to independently and strongly agree the text is AI-generated. |
| `0.40 <= confidence < 0.75` | `uncertain` | A deliberately wide 35-point middle band. Ambiguous cases are routed here instead of being forced across a coin-flip line at 0.5 — this is exactly where the appeals workflow is meant to catch misclassifications. |
| `confidence < 0.40` | `likely_human` | The floor sits below the midpoint, giving the benefit of the doubt — a human is cleared unless there's meaningfully more than 50/50 evidence against them. |

## 3. Transparency Label Design

All three variants interpolate the actual score (`pct = round(confidence * 100)`) and explicitly explain *why* (agreement vs. disagreement between signals) — none of them is a static sentence.

| Variant | Exact text |
|---|---|
| **High-confidence AI** (`confidence >= 0.75`) | `"This content shows strong signs of AI generation — our analysis estimates a {pct}% likelihood of AI authorship, based on consistent agreement between language-pattern and writing-style analysis."` |
| **High-confidence human** (`confidence < 0.40`) | `"This content shows strong signs of human authorship — our analysis estimates only a {pct}% likelihood of AI involvement, based on consistent agreement between language-pattern and writing-style analysis."` |
| **Uncertain** (`0.40 <= confidence < 0.75`) | `"We could not confidently determine the origin of this content — our analysis estimates a {pct}% likelihood of AI involvement, but the language-pattern and writing-style signals did not agree strongly enough to reach a confident conclusion."` |

## 4. Appeals Workflow

Any creator can appeal a classification on their own content by calling `POST /appeal` with `{content_id, reasoning}`. `reasoning` is required free text explaining why they believe the classification is wrong (e.g., "I wrote this myself, I'm a non-native English speaker"). On receipt, the system:
1. Looks up the submission by `content_id` (404 if not found, 409 if already `under_review`).
2. Inserts a row into an `appeals` table capturing the reasoning and timestamp.
3. Updates the submission's `status` to `under_review`.
4. Writes a new `audit_log` entry alongside the original classification's entry — same `content_id`, same original scores/attribution, but `status='under_review'` and `appeal_reasoning` populated — so the log shows the full history (original decision, then the appeal) without overwriting anything.
5. Returns a confirmation: `{content_id, status: "under_review", message: "Your appeal has been recorded and this submission is now under review."}`.

A human reviewer opening the appeal queue (`GET /log?status=under_review`) would see: the original text's `content_id`, `creator_id`, the original `attribution`/`confidence`/individual signal scores, and the `appeal_reasoning` — everything needed to make a manual call, with no automated re-classification.

## 5. Anticipated Edge Cases

1. **Short, formulaic human writing** (terse emails, bullet-style notes, simple form answers). Stylometric heuristics are unstable on very few sentences — sentence-length *variance* needs several sentences to mean anything, and naturally low vocabulary diversity in short, plain writing produces a low TTR that looks like the "flattened" signature AI text also produces. `S_stylo` can false-spike toward AI-likelihood on legitimately terse human writing.
2. **Heavily paraphrased or human-edited AI output.** When an AI draft is rewritten/polished by a human, the LLM signal (tuned to canonical AI surface patterns like uniform cadence and hedging) degrades because paraphrasing perturbs exactly those cues, while the human editing pass reintroduces natural sentence-length variance and irregular punctuation, degrading the stylometric signal toward "human" too. Both signals move the same wrong direction at once, landing substantively AI-originated content in "uncertain" or even "likely human."

## Architecture

**Submission flow:**
```
Client
  │ POST /submit {content, creator_id}
  ▼
Flask-Limiter ──(limit exceeded)──► 429 {error} ──► Client
  │ ok
  ▼
app.py: /submit handler
  │ text
  ├─────────────────────┬─────────────────────┐
  ▼                     ▼
signals.get_llm_score   signals.get_stylometric_score
  │ Groq API call         │ pure python: sentence-length
  │ llm_score (0-1)       │ variance, TTR, punctuation density
  │                       │ stylo_score (0-1)
  └──────────┬────────────┘
             ▼
   scoring.combine(llm_score, stylo_score) → confidence (0-1)
             ▼
   scoring.classify(confidence) → (attribution, label)
             ▼
   db.insert_submission(...) → content_id
             ▼
   db.insert_audit_log(content_id, ..., status='classified')
             ▼
Client ◄── 201 {content_id, attribution, confidence, llm_score,
              stylometric_score, label, status, timestamp}
```

**Appeal flow:**
```
Client
  │ POST /appeal {content_id, reasoning}
  ▼
Flask-Limiter ──(limit exceeded)──► 429 {error} ──► Client
  │ ok
  ▼
app.py: /appeal handler
  │ content_id
  ▼
db.get_submission(content_id) ──(not found)──► 404 ──► Client
                              ──(already under_review)──► 409 ──► Client
  │ submission row
  ▼
db.insert_appeal(content_id, reasoning)
db.update_submission_status(content_id, 'under_review')
db.insert_audit_log(content_id, ..., status='under_review', appeal_reasoning=reasoning)
  ▼
Client ◄── 201 {content_id, status: "under_review", message}
```

**Narrative:** A submission's text is run through two independent signal functions (an LLM semantic read and a pure-Python structural read), combined into a single confidence score that is deliberately biased against false "AI" positives, mapped to one of three labels, and permanently recorded in an append-only audit log before the response is returned. An appeal never changes the original classification — it looks up the existing submission, flips its status to `under_review`, and appends a new audit log row carrying the creator's reasoning, so the full history (original decision + appeal) is always visible via `GET /log`.

## API Surface

- `POST /submit` — body `{content, creator_id}` → `201 {content_id, creator_id, attribution, confidence, llm_score, stylometric_score, label, status, timestamp}`. Errors: `400` missing/short content, `429` rate limited, `503` Groq unavailable.
- `GET /log` — optional query `creator_id`, `status`, `limit`, `offset` → `200 {count, entries: [...]}`.
- `POST /appeal` — body `{content_id, reasoning}` → `201 {content_id, status, message}`. Errors: `400` missing fields, `404` not found, `409` already under review, `429` rate limited.

## Rate Limiting

| Route | Limit | Reasoning |
|---|---|---|
| `POST /submit` | 10/minute, 100/day (per IP) | A real writer submitting/iterating on their own work does so a handful of times per sitting — 10/min comfortably covers that while blocking a script from hammering the (rate-limited, paid-tier) Groq API. 100/day caps sustained abuse across a full day while remaining generous for demo/grading traffic. |
| `GET /log` | 30/minute (per IP) | Read-only, no external API cost, low abuse risk — higher ceiling than the write routes. |
| `POST /appeal` | 5/minute, 20/day (per IP) | An appeal should be a rare, deliberate action, not a bulk operation. A tight cap makes appeal-spam (flooding the audit log or probing the review workflow) both obvious and capped. |

## SQLite Schema

```sql
CREATE TABLE submissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id  TEXT NOT NULL,
    content     TEXT NOT NULL,
    llm_score   REAL NOT NULL,
    stylo_score REAL NOT NULL,
    confidence  REAL NOT NULL,
    attribution TEXT NOT NULL CHECK (attribution IN ('likely_ai','likely_human','uncertain')),
    label       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'classified' CHECK (status IN ('classified','under_review')),
    created_at  TEXT NOT NULL
);

CREATE TABLE appeals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id  INTEGER NOT NULL REFERENCES submissions(id),
    reasoning   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE audit_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT NOT NULL,
    content_id        INTEGER NOT NULL REFERENCES submissions(id),
    creator_id        TEXT NOT NULL,
    attribution       TEXT NOT NULL,
    confidence        REAL NOT NULL,
    llm_score         REAL NOT NULL,
    stylometric_score REAL NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('classified','under_review')),
    appeal_reasoning  TEXT
);
```

`audit_log` is append-only: one row on `/submit`, one more row per `/appeal` — so `GET /log` always shows full history per `content_id`.

## File Layout

```
app.py            # Flask app + Limiter init, route handlers
signals.py        # get_llm_score(text), get_stylometric_score(text)
scoring.py        # combine(llm_score, stylo_score), classify(confidence) -> (attribution, label)
db.py             # init_db, insert_submission, insert_appeal, insert_audit_log, get_audit_log, get_submission, update_submission_status
config.py         # loads .env, weight/threshold/rate-limit constants
schema.sql        # CREATE TABLE statements
requirements.txt
```

## AI Tool Plan

**M3 (submission endpoint + signal 1):** Provide the AI tool the "Detection Signals" section (signal 1 only) and the submission-flow diagram. Ask it to generate the Flask app skeleton with a `POST /submit` stub and the `get_llm_score(text)` function using the Groq SDK. Verify: call `get_llm_score` directly on 2-3 hand-picked texts and check the returned score is a 0-1 float that roughly matches intuition, before wiring it into the route.

**M4 (signal 2 + confidence scoring):** Provide "Detection Signals" (full) + "Uncertainty Representation" + the diagram. Ask for `get_stylometric_score(text)` and `scoring.combine`/`classify`. Verify: check the generated thresholds/weights literally match §1/§2 above (0.7/0.3 weights, 0.4 disagreement penalty, 0.75/0.40 thresholds) — correct any drift — then run the 4 test inputs from the milestone doc and confirm scores spread meaningfully across the range.

**M5 (production layer):** Provide "Transparency Label Design" + "Appeals Workflow" + the diagram. Ask for the label-generation function and the `POST /appeal` route. Verify: generate all three label variants programmatically and diff against the exact strings in §3; test the appeal curl command and confirm `GET /log` shows `status="under_review"` and `appeal_reasoning` populated.
