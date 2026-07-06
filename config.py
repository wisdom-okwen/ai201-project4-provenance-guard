import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = "llama-3.3-70b-versatile"

DB_PATH = os.environ.get("PROVENANCE_DB_PATH", "provenance.db")

MIN_CONTENT_LENGTH = 40

# Signal combination weights
LLM_WEIGHT = 0.7
STYLO_WEIGHT = 0.3
DISAGREEMENT_THRESHOLD = 0.4
DISAGREEMENT_PENALTY = 0.15

# Label thresholds
AI_THRESHOLD = 0.75
HUMAN_THRESHOLD = 0.40

# Rate limits
SUBMIT_RATE_LIMITS = "10 per minute;100 per day"
APPEAL_RATE_LIMITS = "5 per minute;20 per day"
LOG_RATE_LIMITS = "30 per minute"
