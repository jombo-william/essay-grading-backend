# services/grader.py

import os
import re
import json
import time
import logging
import requests
import numpy as np
import pandas as pd

from pathlib import Path
from collections import Counter
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# ── API CONFIG ────────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

TRAINING_DATA_PATH = Path(__file__).parent.parent / "training_data.csv"

# ── CUSTOM ERROR ──────────────────────────────────────────────────────────────


class RateLimitError(Exception):
    pass


# ── SHARED HELPERS ────────────────────────────────────────────────────────────


def _build_rubric_text(rubric: dict | None) -> str:
    if rubric:
        return "\n".join([f"- {k}: {v} marks" for k, v in rubric.items()])

    return (
        "- Content & Arguments: 40 marks\n"
        "- Structure & Organization: 30 marks\n"
        "- Grammar & Language: 30 marks"
    )


def _build_prompt(essay_text: str, rubric_text: str) -> str:
    safe_essay = essay_text[:3000]

    if len(essay_text) > 3000:
        safe_essay += "\n[... essay truncated ...]"

    return f"""
You are an expert essay grader.

Grade the essay below based on this rubric.

RUBRIC:
{rubric_text}

ESSAY:
\"\"\"
{safe_essay}
\"\"\"

CRITICAL INSTRUCTIONS:
- Return ONLY valid JSON.
- No markdown.
- No explanations.
- No code fences.
- Keep feedback short.
- Use EXACTLY this structure:

{{
  "total_score": <integer 0-100>,
  "breakdown": {{
    "Content & Arguments": {{
      "score": <int>,
      "max_score": 40,
      "feedback": "<short feedback>"
    }},
    "Structure & Organization": {{
      "score": <int>,
      "max_score": 30,
      "feedback": "<short feedback>"
    }},
    "Grammar & Language": {{
      "score": <int>,
      "max_score": 30,
      "feedback": "<short feedback>"
    }}
  }},
  "overall_feedback": "<short feedback>",
  "strengths": ["<short phrase>"],
  "improvements": ["<short phrase>"],
  "graded_by": "placeholder"
}}
"""


def _extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    text = text.replace("```", "").strip()

    try:
        return json.loads(text)

    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])

        except json.JSONDecodeError:
            pass

    if start != -1:
        score_match = re.search(r'"total_score"\s*:\s*(\d+)', text[start:])

        if score_match:
            score = int(score_match.group(1))

            logger.warning(
                f"⚠️ JSON truncated — salvaged total_score={score}"
            )

            return {
                "total_score": score,
                "breakdown": {
                    "Content & Arguments": {
                        "score": round(score * 0.4),
                        "max_score": 40,
                        "feedback": "Response truncated."
                    },
                    "Structure & Organization": {
                        "score": round(score * 0.3),
                        "max_score": 30,
                        "feedback": "Response truncated."
                    },
                    "Grammar & Language": {
                        "score": round(score * 0.3),
                        "max_score": 30,
                        "feedback": "Response truncated."
                    },
                },
                "overall_feedback": "Score extracted from truncated response.",
                "strengths": ["Score extracted"],
                "improvements": ["Re-grade for full feedback"],
                "graded_by": "truncation-recovery",
            }

    raise ValueError(f"Could not parse JSON:\n{text[:400]}")


# ── GEMINI GRADER ─────────────────────────────────────────────────────────────


def _grade_with_gemini(
    essay_text: str,
    rubric_text: str,
    model: str
) -> dict:

    url = f"{GEMINI_BASE}/{model}:generateContent?key={GEMINI_API_KEY}"

    prompt = _build_prompt(essay_text, rubric_text)

    resp = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        json={
            "contents": [
                {
                    "parts": [
                        {
                            "text": prompt
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 1200,
                "topP": 1.0,
                "topK": 1,
                "responseMimeType": "application/json",
            },
        },
        timeout=60,
    )

    if resp.status_code == 429:
        logger.warning(
            f"⚠️ Gemini rate limit hit on {model}"
        )

        raise RateLimitError(f"Rate limit on {model}")

    resp.raise_for_status()

    data = resp.json()

    candidates = data.get("candidates", [])

    if not candidates:
        raise ValueError(f"No candidates returned by {model}")

    raw = candidates[0]["content"]["parts"][0]["text"]

    result = _extract_json(raw)

    result["graded_by"] = model

    return result


# ── SIMILARITY MODEL ──────────────────────────────────────────────────────────

_similarity_model = None


def _load_similarity_model():
    global _similarity_model

    if _similarity_model is not None:
        return _similarity_model

    if not TRAINING_DATA_PATH.exists():
        raise FileNotFoundError(
            f"training_data.csv not found at {TRAINING_DATA_PATH}"
        )

    logger.info(
        f"📚 Loading training data from {TRAINING_DATA_PATH}"
    )

    df = pd.read_csv(TRAINING_DATA_PATH)

    df = df[df["score"] != "score"].copy()

    df["score"] = pd.to_numeric(
        df["score"],
        errors="coerce"
    )

    df["max_score"] = pd.to_numeric(
        df["max_score"],
        errors="coerce"
    )

    df = df.dropna(subset=["score", "essay_text"])

    df["essay_text"] = (
        df["essay_text"]
        .astype(str)
        .str.strip()
    )

    df = df[df["essay_text"].str.len() > 20]

    df["score_100"] = (
        df["score"] / df["max_score"] * 100
    ).clip(0, 100)

    vectorizer = TfidfVectorizer(
        max_features=8000,
        ngram_range=(1, 2),
        sublinear_tf=True,
        strip_accents="unicode",
    )

    vectors = vectorizer.fit_transform(df["essay_text"])

    _similarity_model = {
        "vectorizer": vectorizer,
        "vectors": vectors,
        "scores": df["score_100"].values,
        "labels": df["label"].values,
    }

    logger.info(
        f"✅ Similarity model ready — {len(df)} essays"
    )

    return _similarity_model


def _grade_with_similarity(
    essay_text: str,
    rubric_text: str
) -> dict:

    model = _load_similarity_model()

    vec = model["vectorizer"].transform([essay_text])

    sims = cosine_similarity(
        vec,
        model["vectors"]
    )[0]

    K = 10

    top_idx = np.argsort(sims)[-K:][::-1]

    top_sims = sims[top_idx]
    top_scores = model["scores"][top_idx]
    top_labels = model["labels"][top_idx]

    weight_sum = top_sims.sum()

    if weight_sum < 0.01:
        total_score = float(np.mean(model["scores"]))
        confidence = "low"

    else:
        total_score = float(
            np.average(top_scores, weights=top_sims)
        )

        confidence = (
            "high"
            if top_sims[0] > 0.3
            else "medium"
        )

    total_score = round(
        max(0, min(100, total_score))
    )

    label = Counter(top_labels).most_common(1)[0][0]

    feedback_map = {
        "excellent": "Excellent essay with strong arguments.",
        "good": "Good essay with solid organisation.",
        "satisfactory": "Satisfactory coverage of the topic.",
        "weak": "Weak essay needing more detail.",
        "poor": "Poor engagement with the topic.",
        "off_topic": "Essay appears off-topic.",
    }

    strengths_map = {
        "excellent": "Strong analysis and structure.",
        "good": "Relevant content included.",
        "satisfactory": "Basic understanding shown.",
        "weak": "Some relevant ideas included.",
        "poor": "Attempt was made.",
        "off_topic": "Essay submitted successfully.",
    }

    improvements_map = {
        "excellent": "Add more recent references.",
        "good": "Deepen analysis further.",
        "satisfactory": "Expand arguments with evidence.",
        "weak": "Improve structure and detail.",
        "poor": "Review topic carefully.",
        "off_topic": "Follow assignment instructions.",
    }

    feedback = feedback_map.get(
        label,
        "Graded by similarity model."
    )

    c_score = round(total_score * 0.4)
    s_score = round(total_score * 0.3)
    g_score = round(total_score * 0.3)

    return {
        "total_score": total_score,
        "breakdown": {
            "Content & Arguments": {
                "score": c_score,
                "max_score": 40,
                "feedback": feedback,
            },
            "Structure & Organization": {
                "score": s_score,
                "max_score": 30,
                "feedback": feedback,
            },
            "Grammar & Language": {
                "score": g_score,
                "max_score": 30,
                "feedback": feedback,
            },
        },
        "overall_feedback": feedback,
        "strengths": [
            strengths_map.get(label, "Essay submitted.")
        ],
        "improvements": [
            improvements_map.get(
                label,
                "Review rubric."
            )
        ],
        "graded_by": (
            f"similarity-model "
            f"(label={label}, confidence={confidence})"
        ),
    }


# ── OLLAMA GRADER ─────────────────────────────────────────────────────────────


def _grade_with_ollama(
    essay_text: str,
    rubric_text: str
) -> dict:

    try:
        requests.get(
            f"{OLLAMA_URL}/api/tags",
            timeout=5
        ).raise_for_status()

    except Exception as e:
        raise RuntimeError(
            f"Ollama not running at {OLLAMA_URL}: {e}"
        )

    safe_essay = essay_text[:1200]

    prompt = (
        f"Grade this essay.\n\n"
        f"Rubric:\n{rubric_text}\n\n"
        f"Essay:\n{safe_essay}\n\n"
        f"Reply ONLY with JSON."
    )

    logger.info(
        f"⏳ Sending to Ollama ({OLLAMA_MODEL})..."
    )

    resp = requests.post(
        f"{OLLAMA_URL}/api/generate",
        headers={"Content-Type": "application/json"},
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_predict": 200,
                "top_k": 1,
            },
        },
        timeout=180,
    )

    resp.raise_for_status()

    raw = resp.json().get("response", "")

    if not raw:
        raise ValueError(
            "Ollama returned empty response"
        )

    try:
        parsed = _extract_json(raw)

    except ValueError:
        score_match = re.search(
            r'\b(\d{1,3})\b',
            raw
        )

        score = (
            int(score_match.group(1))
            if score_match
            else 55
        )

        parsed = {"total_score": score}

    total = max(
        0,
        min(100, int(parsed.get("total_score", 55)))
    )

    c_score = int(
        parsed.get(
            "content_score",
            round(total * 0.4)
        )
    )

    s_score = int(
        parsed.get(
            "structure_score",
            round(total * 0.3)
        )
    )

    g_score = int(
        parsed.get(
            "grammar_score",
            round(total * 0.3)
        )
    )

    feedback = parsed.get(
        "feedback",
        "Graded by local Ollama model."
    )

    return {
        "total_score": total,
        "breakdown": {
            "Content & Arguments": {
                "score": c_score,
                "max_score": 40,
                "feedback": feedback,
            },
            "Structure & Organization": {
                "score": s_score,
                "max_score": 30,
                "feedback": feedback,
            },
            "Grammar & Language": {
                "score": g_score,
                "max_score": 30,
                "feedback": feedback,
            },
        },
        "overall_feedback": feedback,
        "strengths": [
            parsed.get(
                "strength",
                "Essay submitted."
            )
        ],
        "improvements": [
            parsed.get(
                "improve",
                "Review rubric."
            )
        ],
        "graded_by": f"ollama/{OLLAMA_MODEL}",
    }


# ── PUBLIC INTERFACE ──────────────────────────────────────────────────────────


def grade_essay(
    essay_text: str,
    rubric: dict = None
) -> dict:

    rubric_text = _build_rubric_text(rubric)

    errors = []

    # ── Gemini ─────────────────────────────────────────

    for model in GEMINI_MODELS:

        try:
            result = _grade_with_gemini(
                essay_text,
                rubric_text,
                model
            )

            logger.info(
                f"✅ Graded with {model}"
            )

            return result

        except RateLimitError as e:
            errors.append(str(e))

        except requests.HTTPError as e:
            errors.append(
                f"{model} HTTP error: {e}"
            )

        except Exception as e:
            errors.append(
                f"{model} error: {e}"
            )

    # ── Similarity Model ───────────────────────────────

    try:
        result = _grade_with_similarity(
            essay_text,
            rubric_text
        )

        logger.info(
            "✅ Graded with similarity model"
        )

        return result

    except Exception as e:
        errors.append(
            f"Similarity model error: {e}"
        )

    # ── Ollama ─────────────────────────────────────────

    try:
        result = _grade_with_ollama(
            essay_text,
            rubric_text
        )

        logger.info(
            "✅ Graded with Ollama"
        )

        return result

    except Exception as e:
        errors.append(
            f"Ollama error: {e}"
        )

    raise RuntimeError(
        "All grading providers failed:\n"
        + "\n".join(errors)
    )