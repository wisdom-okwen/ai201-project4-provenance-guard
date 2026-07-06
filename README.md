# Provenance Guard

A Flask backend that classifies submitted text as likely AI-generated, likely human-written, or uncertain — returning a confidence score, a plain-language transparency label, and support for creator appeals, rate limiting, and a structured audit log.

Full design rationale (signal choices, scoring formula, thresholds, edge cases, architecture diagram) lives in [`planning.md`](planning.md). This README documents what was built, why, and the evidence that it works.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Mac/Linux
pip install -r requirements.txt
```

Create a `.env` file in the repo root (never commit it):

```
GROQ_API_KEY=your_key_here
```

Run the server:

```bash
python app.py
```

The server initializes `provenance.db` (SQLite) automatically on first run.

## Architecture Overview

A submission's text is run through two independent detection signals — an LLM semantic read and a pure-Python structural read — combined into a single confidence score, mapped to one of three transparency labels, and permanently recorded in an audit log before the response returns. An appeal never re-classifies content; it looks up the existing submission, flips its status to `under_review`, and appends a new audit log row carrying the creator's reasoning, so `GET /log` always shows the full history.

```
Client
  │ POST /submit {content, creator_id}
  ▼
Flask-Limiter ──(limit exceeded)──► 429 ──► Client
  │ ok
  ▼
signals.get_llm_score(text)  +  signals.get_stylometric_score(text)
  │ (Groq semantic judgment)     (sentence-length variance, TTR, punctuation)
  └──────────────┬────────────────────────┘
                 ▼
   scoring.combine() → confidence   →   scoring.classify() → (attribution, label)
                 ▼
   db.insert_submission()  →  db.insert_audit_log(status='classified')
                 ▼
Client ◄── 201 {content_id, attribution, confidence, llm_score, stylometric_score, label, status}
```

```
Client
  │ POST /appeal {content_id, reasoning}
  ▼
db.get_submission() ──(not found)──► 404      ──(already under_review)──► 409
  │ found
  ▼
db.insert_appeal()  →  db.update_submission_status('under_review')  →  db.insert_audit_log(status='under_review', appeal_reasoning=...)
  ▼
Client ◄── 201 {content_id, status: "under_review", message}
```

Endpoints: `POST /submit`, `GET /log`, `POST /appeal`. Full request/response contracts are in `planning.md`.

## Detection Signals

**Signal 1 — LLM-based classification** (`signals.get_llm_score`, Groq `llama-3.3-70b-versatile`). The model is prompted to judge, holistically, whether a passage reads as AI-generated or human-written, and returns a JSON object with a `0–1 ai_likelihood` float. This captures *semantic and stylistic coherence* — generic phrasing, hedging language, overly balanced argument structure, topic-generic fluency.
*What it misses:* it can't see structural/statistical regularities directly, and it is fooled by AI text that's been paraphrased or human-edited, since paraphrasing perturbs exactly the surface cues it keys on.

**Signal 2 — Stylometric heuristics** (`signals.get_stylometric_score`, pure Python). Three metrics, each normalized to 0–1 and averaged:
- Sentence-length variance (coefficient of variation) — AI text tends toward uniform sentence length.
- Type-token ratio (vocabulary diversity) — treated as neutral below 60 words, since TTR is only a meaningful proxy for lexical diversity past a minimum sample size (short passages naturally have few repeated words regardless of authorship).
- Punctuation density/burstiness (em-dashes, ellipses, semicolons, exclamation points) — human writing is more irregular here.
*What it misses:* it has no semantic understanding at all — it can't tell if the content makes sense — and it misfires on legitimately terse/formulaic human writing, which looks statistically "uniform" the same way AI text does.

**Why these two:** one is semantic (a model's holistic read), one is purely structural (counting/statistics on the raw string). They fail in different ways using different information, so agreement between them is much stronger evidence than either alone.

## Confidence Scoring

```python
raw = 0.7 * llm_score + 0.3 * stylo_score
disagreement = abs(llm_score - stylo_score)
penalty = 0.15 if disagreement > 0.4 else 0.0
confidence = max(0.0, raw - penalty)
```

`llm_score` is weighted higher because it's the stronger, less noisy signal; the disagreement penalty exists specifically to guard against false positives — if the two independent signals point in very different directions, that disagreement is evidence the read is unreliable, so the score is pulled *down*, never up, biasing ambiguous cases toward "uncertain" or "human" rather than a confident-sounding wrong "AI" call. This directly targets the hint that a false positive (flagging a human as AI) is worse than a false negative on a creative-writing platform.

Thresholds: `confidence >= 0.75` → `likely_ai`, `0.40–0.75` → `uncertain`, `< 0.40` → `likely_human`. The AI bar sits well above the human floor on purpose — see `planning.md` §2 for the full reasoning.

**How I tested it was meaningful:** I ran the four inputs from the milestone spec (clearly AI, clearly human, formal-human borderline, lightly-edited-AI borderline) through the pipeline and checked the scores spread across the full range rather than clustering near 0.5, and that all three label buckets were actually reachable. This is also how I caught a real bug: on the "clearly AI" sample (a short 3-sentence paragraph), type-token ratio scored it as strongly *human*-like, because a 43-word passage naturally has almost no repeated words regardless of who wrote it — dragging the combined score down to "uncertain" instead of "likely AI". I fixed this by treating TTR as neutral below 60 words (documented in `signals.py`), which is consistent with the stylometric literature (TTR needs a long enough sample to mean anything).

**Two example submissions, actual scores from a live run:**

| Content (truncated) | `llm_score` | `stylo_score` | `confidence` | Attribution |
|---|---|---|---|---|
| "In today's fast-paced world, it is important to note that technology continues to play an increasingly vital role..." | 0.90 | 0.589 | **0.8068** | `likely_ai` (high confidence) |
| "I have been thinking a lot about remote work lately. There are genuine tradeoffs, flexibility and no commute on one side..." | 0.70 | 0.585 | **0.6654** | `uncertain` (lower confidence) |

Both passages read as somewhat AI-like to the LLM signal, but the second — a lightly-edited, more personal piece — lands 14 points lower and crosses into a materially different label ("uncertain" vs. "likely AI generation"), showing the score isn't a binary flip at a single cutoff.

## Transparency Label

All three variants interpolate the actual score (`pct = round(confidence * 100)`) and explain *why* the label landed where it did — none is a static sentence.

| Variant | Exact text |
|---|---|
| **High-confidence AI** (`confidence ≥ 0.75`) | `"This content shows strong signs of AI generation — our analysis estimates a {pct}% likelihood of AI authorship, based on consistent agreement between language-pattern and writing-style analysis."` |
| **High-confidence human** (`confidence < 0.40`) | `"This content shows strong signs of human authorship — our analysis estimates only a {pct}% likelihood of AI involvement, based on consistent agreement between language-pattern and writing-style analysis."` |
| **Uncertain** (`0.40 ≤ confidence < 0.75`) | `"We could not confidently determine the origin of this content — our analysis estimates a {pct}% likelihood of AI involvement, but the language-pattern and writing-style signals did not agree strongly enough to reach a confident conclusion."` |

## Rate Limiting

| Route | Limit | Reasoning |
|---|---|---|
| `POST /submit` | 10/minute, 100/day (per IP) | A real writer submitting or iterating on their own work does so a handful of times per sitting — 10/min comfortably covers that while blocking a script from hammering the classification pipeline (which costs a Groq API call per request). 100/day caps sustained abuse across a full day while remaining generous for demo/grading traffic. |
| `GET /log` | 30/minute (per IP) | Read-only, no external API cost, low abuse risk — higher ceiling than the write routes. |
| `POST /appeal` | 5/minute, 20/day (per IP) | An appeal should be a rare, deliberate action by a genuinely aggrieved creator, not a bulk operation. A tight cap makes appeal-spam (flooding the audit log or probing the review queue) both obvious and capped. |

**Evidence** — 12 rapid requests to `/submit` (limit is 10/minute), fresh server start:

```
201
201
201
201
201
201
201
201
201
201
429
429
```

The first 10 succeed; requests 11–12 are correctly rejected with `429`.

## Audit Log

`GET /log` returns structured JSON entries (timestamp, content_id, creator_id, attribution, confidence, both individual signal scores, status, and appeal_reasoning when present). It is append-only: a submission writes one entry, and an appeal writes a second entry against the same `content_id` rather than overwriting the first — so the full decision history is always visible.

Sample output (5 entries from a live run — 4 submissions across all three label buckets, plus 1 appeal):

```json
{
  "count": 5,
  "entries": [
    {
      "id": 5, "content_id": 3, "creator_id": "creator-jamal",
      "attribution": "likely_ai", "confidence": 0.7674,
      "llm_score": 0.8, "stylometric_score": 0.6912,
      "status": "under_review",
      "appeal_reasoning": "I wrote this myself for an economics class assignment. I am a non-native English speaker and tend to write more formally, which may explain the classification.",
      "timestamp": "2026-07-06T04:44:39.065710+00:00"
    },
    {
      "id": 4, "content_id": 4, "creator_id": "creator-priya",
      "attribution": "uncertain", "confidence": 0.6654,
      "llm_score": 0.7, "stylometric_score": 0.5845,
      "status": "classified", "appeal_reasoning": null,
      "timestamp": "2026-07-06T04:44:33.828505+00:00"
    },
    {
      "id": 3, "content_id": 3, "creator_id": "creator-jamal",
      "attribution": "likely_ai", "confidence": 0.7674,
      "llm_score": 0.8, "stylometric_score": 0.6912,
      "status": "classified", "appeal_reasoning": null,
      "timestamp": "2026-07-06T04:44:33.428588+00:00"
    },
    {
      "id": 2, "content_id": 2, "creator_id": "creator-devbot-test",
      "attribution": "likely_ai", "confidence": 0.8068,
      "llm_score": 0.9, "stylometric_score": 0.5892,
      "status": "classified", "appeal_reasoning": null,
      "timestamp": "2026-07-06T04:44:33.032010+00:00"
    },
    {
      "id": 1, "content_id": 1, "creator_id": "creator-maya",
      "attribution": "likely_human", "confidence": 0.29,
      "llm_score": 0.2, "stylometric_score": 0.5,
      "status": "classified", "appeal_reasoning": null,
      "timestamp": "2026-07-06T04:44:32.627375+00:00"
    }
  ]
}
```

Note entries `id=3` and `id=5` share `content_id=3` — the original classification and the subsequent appeal, both preserved.

## Appeals Workflow

`POST /appeal` with `{content_id, reasoning}`:
1. Looks up the submission (`404` if missing, `409` if already `under_review`).
2. Inserts a row into `appeals` capturing the creator's reasoning.
3. Updates the submission's `status` to `under_review`.
4. Writes a new `audit_log` row alongside the original — same scores/attribution, `status='under_review'`, `appeal_reasoning` populated.
5. Returns `{content_id, status: "under_review", message}`.

No automated re-classification happens — the appeal is meant to route the content to a human reviewer, who can query `GET /log?status=under_review` to see every pending appeal alongside the original decision and the creator's stated reasoning.

**Real example from testing:** a passage of formal economics writing (genuinely human, but written in a dense academic register) was classified `likely_ai` at 0.7674 confidence — a live demonstration of the exact false-positive risk this project is designed to minimize but can't eliminate. Filing an appeal against it correctly moved its status to `under_review` and a second attempt to appeal the same content correctly returned `409`.

## Known Limitations

1. **Short, formulaic human writing** (terse notes, simple form answers). The stylometric signal needs enough sentences for variance and TTR to be meaningful; short, plain human writing can look as "flattened" as AI text on those metrics, pulling the combined score upward for legitimately human content.
2. **Formal/academic human writing** — demonstrated directly above. Dense, structured, hedge-heavy prose (common in academic or non-native-English writing) triggers *both* signals in the same direction: the LLM reads it as "AI-like" phrasing, and the stylometric signal reads its formal uniformity as AI-like structure too. Because the disagreement penalty only helps when signals *disagree*, it does nothing when both signals are fooled by the same surface feature — this is the single biggest false-positive risk in the current design, and exactly why the appeals workflow exists.

## Spec Reflection

**How the spec helped:** Writing out the exact label text and thresholds in `planning.md` *before* writing scoring code forced me to decide what a 0.6 should mean to a reader before I had any code that could accidentally define it for me by default behavior. When the AI-generated scoring implementation's thresholds didn't visibly diverge from the spec, that was because the spec was specific enough to implement against directly — closing the "reasonable-looking but wrong" gap the assignment specifically warned about.

**Where implementation diverged:** The plan assumed the stylometric composite would cleanly separate AI vs. human on the milestone's example texts. In practice, the type-token-ratio sub-metric was actively wrong on short passages (see Confidence Scoring section above) — the spec didn't anticipate that TTR needs a minimum sample size to be meaningful. I added a length-gated neutral fallback that wasn't in the original design, and documented the underlying limitation instead of trying to fully "fix" it, since the spec's own hint says perfect detection is not the goal.

## AI Usage

1. **Architecture and scoring design.** I directed an AI planning agent (via Claude Code's Plan subagent) to turn my chosen approach — Groq LLM signal + stylometric heuristics, weighted combination, asymmetric thresholds — into concrete numbers: the exact 0.7/0.3 weights, the 0.4 disagreement threshold and 0.15 penalty, the 0.75/0.40 label thresholds, the SQLite schema, and the API error contract. I reviewed every number for whether it matched the "false positives are worse" design goal (e.g., confirming the AI threshold sits meaningfully above 0.5, not at it) before writing planning.md from its output.
2. **Signal and endpoint implementation, then correcting a real bug.** I had the implementation generate `signals.py`, `scoring.py`, and `app.py` directly from the finalized spec. Testing the generated `get_stylometric_score` against the milestone's four benchmark texts (Milestone 4's required test) surfaced that a short, clearly-AI-generated paragraph was scoring as "uncertain" instead of "likely_ai" — the type-token-ratio sub-metric was miscalibrated for short text. I diagnosed the root cause (TTR needs enough words to be a meaningful diversity measure) and overrode the generated code with a length-gated neutral fallback rather than accepting the initial output as-is.

## Testing Summary

- 4 hand-picked inputs (clearly AI, clearly human, formal-human borderline, edited-AI borderline) run through the full pipeline; confirmed all three label buckets are reachable and scores vary meaningfully (0.29 → 0.665 → 0.767 → 0.807 across the four).
- Filed a real appeal against a genuine false-positive case; confirmed `409` on a duplicate appeal attempt.
- Fired 12 rapid `/submit` requests against a fresh server; confirmed the first 10 return `201` and the rest return `429`.
- Confirmed `GET /log` returns structured, append-only entries reflecting both original classifications and appeals.
