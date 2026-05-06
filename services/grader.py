


# # # services/grader.py
# # # Fallback chain: Gemini 2.5-flash → Gemini 1.5-flash-latest → HuggingFace (free inference API)

# # import os
# # import re
# # import json
# # import time
# # import logging
# # import requests

# # logger = logging.getLogger(__name__)

# # # ── API config ────────────────────────────────────────────────────────────────
# # GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# # # HF_API_KEY     = os.getenv("HF_API_KEY", "")
# # HF_API_KEY = os.getenv("HF_API_KEY", "")
# # HF_URL     = "https://router.huggingface.co/hf-inference/models/mistralai/Mistral-7B-Instruct-v0.3/v1/chat/completions"
# # HF_MODEL   = "mistralai/Mistral-7B-Instruct-v0.3"

# # GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# # # Models in fallback order
# # # NOTE: "gemini-1.5-flash" 404s — correct name is "gemini-1.5-flash-latest"
# # GEMINI_MODELS = [
# #     "gemini-2.5-flash",  # Primary
# #     "gemini-2.0-flash",  # Fallback — separate quota
# # ]


# # # HuggingFace — using a model that is reliably available on free inference API
# # HF_MODEL = "mistralai/Mistral-7B-Instruct-v0.3"
# # HF_URL   = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"

# # # ── Custom error type ─────────────────────────────────────────────────────────

# # class RateLimitError(Exception):
# #     pass


# # # ── Shared helpers ────────────────────────────────────────────────────────────

# # def _build_rubric_text(rubric: dict | None) -> str:
# #     if rubric:
# #         return "\n".join([f"- {k}: {v} marks" for k, v in rubric.items()])
# #     return (
# #         "- Content & Arguments: 40 marks\n"
# #         "- Structure & Organization: 30 marks\n"
# #         "- Grammar & Language: 30 marks"
# #     )


# # def _build_prompt(essay_text: str, rubric_text: str) -> str:
# #     # Truncate very long essays to avoid response truncation
# #     safe_essay = essay_text[:3000]
# #     if len(essay_text) > 3000:
# #         safe_essay += "\n[... essay truncated for grading ...]"

# #     return f"""You are an expert essay grader. Grade the essay below based on this rubric.

# # RUBRIC:
# # {rubric_text}

# # ESSAY:
# # \"\"\"
# # {safe_essay}
# # \"\"\"

# # CRITICAL INSTRUCTIONS:
# # - Return ONLY a single valid JSON object.
# # - No markdown, no code fences, no commentary before or after the JSON.
# # - Keep ALL feedback strings SHORT — under 80 words each. This is required to avoid truncation.
# # - Do NOT include the essay text in your response.
# # - Use EXACTLY this structure, nothing more:

# # {{
# #   "total_score": <integer 0-100>,
# #   "breakdown": {{
# #     "Content & Arguments": {{"score": <int>, "max_score": 40, "feedback": "<max 80 words>"}},
# #     "Structure & Organization": {{"score": <int>, "max_score": 30, "feedback": "<max 80 words>"}},
# #     "Grammar & Language": {{"score": <int>, "max_score": 30, "feedback": "<max 80 words>"}}
# #   }},
# #   "overall_feedback": "<max 60 words>",
# #   "strengths": ["<short phrase>", "<short phrase>"],
# #   "improvements": ["<short phrase>", "<short phrase>"],
# #   "graded_by": "placeholder"
# # }}"""


# # def _extract_json(text: str) -> dict:
# #     """
# #     Robustly extract a JSON object from AI output.
# #     Handles: markdown fences, extra text, and TRUNCATED responses.
# #     """
# #     # Strip markdown fences
# #     text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

# #     # Try direct parse first (clean response)
# #     try:
# #         return json.loads(text)
# #     except json.JSONDecodeError:
# #         pass

# #     # Find the outermost { ... } block
# #     start = text.find("{")
# #     end   = text.rfind("}")
# #     if start != -1 and end != -1 and end > start:
# #         try:
# #             return json.loads(text[start:end + 1])
# #         except json.JSONDecodeError:
# #             pass

# #     # ── TRUNCATION RECOVERY ───────────────────────────────────────────────────
# #     # JSON was cut off mid-string. Salvage the score so grading doesn't fail.
# #     if start != -1:
# #         partial = text[start:]
# #         score_match = re.search(r'"total_score"\s*:\s*(\d+)', partial)
# #         if score_match:
# #             score = int(score_match.group(1))
# #             logger.warning(f"⚠️  JSON truncated — salvaged total_score={score}, using fallback structure")
# #             return {
# #                 "total_score": score,
# #                 "breakdown": {
# #                     "Content & Arguments":     {"score": round(score * 0.4), "max_score": 40, "feedback": "Auto-extracted (response truncated)."},
# #                     "Structure & Organization":{"score": round(score * 0.3), "max_score": 30, "feedback": "Auto-extracted (response truncated)."},
# #                     "Grammar & Language":      {"score": round(score * 0.3), "max_score": 30, "feedback": "Auto-extracted (response truncated)."},
# #                 },
# #                 "overall_feedback": "Grading completed but detailed feedback was truncated. Score extracted successfully.",
# #                 "strengths":    ["Score successfully extracted"],
# #                 "improvements": ["Re-grade for full feedback"],
# #                 "graded_by":    "truncation-recovery",
# #             }

# #     raise ValueError(f"Could not parse JSON from response:\n{text[:400]}")


# # # ── Gemini grader ─────────────────────────────────────────────────────────────

# # def _grade_with_gemini(essay_text: str, rubric_text: str, model: str) -> dict:
# #     url    = f"{GEMINI_BASE}/{model}:generateContent?key={GEMINI_API_KEY}"
# #     prompt = _build_prompt(essay_text, rubric_text)

# #     resp = requests.post(
# #         url,
# #         headers={"Content-Type": "application/json"},
# #         json={
# #             "contents": [{"parts": [{"text": prompt}]}],
# #             "generationConfig": {
# #                 "temperature":      0.0,
# #                 "maxOutputTokens":  1200,   # Enough for short feedback, prevents truncation
# #                 "topP":             1.0,
# #                 "topK":             1,
# #                 "responseMimeType": "application/json",
# #             },
# #         },
# #         timeout=60,
# #     )

# #     if resp.status_code == 429:
# #         logger.warning(f"⚠️  Gemini rate limit hit on {model} — trying next fallback")
# #         raise RateLimitError(f"Rate limit on {model}")

# #     resp.raise_for_status()

# #     data = resp.json()

# #     # Check for blocked/empty response
# #     candidates = data.get("candidates", [])
# #     if not candidates:
# #         raise ValueError(f"No candidates returned by {model}")

# #     finish_reason = candidates[0].get("finishReason", "")
# #     if finish_reason not in ("STOP", "MAX_TOKENS", ""):
# #         raise ValueError(f"Bad finish reason from {model}: {finish_reason}")

# #     raw    = candidates[0]["content"]["parts"][0]["text"]
# #     result = _extract_json(raw)
# #     result["graded_by"] = model
# #     return result


# # # ── HuggingFace grader ────────────────────────────────────────────────────────

# # # def _grade_with_huggingface(essay_text: str, rubric_text: str) -> dict:
# # #     """
# # #     HuggingFace free inference fallback.
# # #     flan-t5-large is always warm and handles structured prompts well.
# # #     """
# # #     if not HF_API_KEY:
# # #         raise ValueError("HF_API_KEY not set in environment")

# # #     safe_essay = essay_text[:1500]
# # #     prompt = (
# # #                 f"[INST] Grade this essay from 0-100 based on this rubric: {rubric_text}\n\n"
# # #                 f"Essay: {safe_essay}\n\n"
# # #                 f"Reply with ONLY valid JSON, nothing else:\n"
# # #                 f"{{\"total_score\": <int>, \"overall_feedback\": \"<str>\", "
# # #                 f"\"strengths\": [\"<str>\", \"<str>\"], \"improvements\": [\"<str>\", \"<str>\"]}}"
# # #                 f" [/INST]"
# # #             )

# # #     # prompt = (
# # #     #         f"<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"
# # #     #         f"Grade this essay 0-100 based on: {rubric_text}\n\n"
# # #     #         f"Essay: {safe_essay}\n\n"
# # #     #         f"Reply with ONLY a JSON object: {{\"total_score\": <int>, \"overall_feedback\": \"<str>\", "
# # #     #         f"\"strengths\": [\"<str>\", \"<str>\"], \"improvements\": [\"<str>\", \"<str>\"]}}"
# # #     #         f"<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
# # #     #     )

# # #     payload = {
# # #         "inputs": prompt,
# # #         "parameters": {
# # #             "max_new_tokens":   300,
# # #             "temperature":      0.1,
# # #             "return_full_text": False,
# # #         },
# # #     }

# # #     resp = requests.post(
# # #         HF_URL,
# # #         headers={"Authorization": f"Bearer {HF_API_KEY}"},
# # #         json=payload,
# # #         timeout=120,
# # #     )

# # #     # Model loading cold start — wait and retry once
# # #     if resp.status_code == 503:
# # #         body = resp.json()
# # #         wait = min(body.get("estimated_time", 20), 30)
# # #         logger.info(f"⏳ HuggingFace model loading, waiting {wait}s …")
# # #         time.sleep(wait)
# # #         resp = requests.post(
# # #             HF_URL,
# # #             headers={"Authorization": f"Bearer {HF_API_KEY}"},
# # #             json=payload,
# # #             timeout=120,
# # #         )

# # #     resp.raise_for_status()

# # #     data = resp.json()
# # #     if isinstance(data, list) and data:
# # #         raw = data[0].get("generated_text", "")
# # #     else:
# # #         raw = str(data)

# # #     try:
# # #         result = _extract_json(raw)
# # #     except ValueError:
# # #         # flan-t5 may not return proper JSON — salvage a score from the text
# # #         score_match = re.search(r'\b([4-9]\d|100)\b', raw)  # look for 40-100 range
# # #         score = int(score_match.group(1)) if score_match else 50
# # #         score = max(0, min(100, score))
# # #         result = {
# # #             "total_score":      score,
# # #             "overall_feedback": raw[:200] if raw else "Graded by HuggingFace fallback.",
# # #             "strengths":        ["Evaluated by fallback model"],
# # #             "improvements":     ["Re-grade with primary model when quota resets"],
# # #         }

# # #     result["breakdown"] = result.get("breakdown", {
# # #         "Content & Arguments":     {"score": round(result["total_score"] * 0.4), "max_score": 40, "feedback": "Estimated by fallback model."},
# # #         "Structure & Organization":{"score": round(result["total_score"] * 0.3), "max_score": 30, "feedback": "Estimated by fallback model."},
# # #         "Grammar & Language":      {"score": round(result["total_score"] * 0.3), "max_score": 30, "feedback": "Estimated by fallback model."},
# # #     })
# # #     result["graded_by"] = f"huggingface/{HF_MODEL}"
# # #     return result



# # def _grade_with_huggingface(essay_text: str, rubric_text: str) -> dict:
# #     if not HF_API_KEY:
# #         raise ValueError("HF_API_KEY not set in environment")

# #     safe_essay = essay_text[:1500]

# #     resp = requests.post(
# #         HF_URL,
# #         headers={
# #             "Authorization": f"Bearer {HF_API_KEY}",
# #             "Content-Type":  "application/json",
# #         },
# #         json={
# #             "model": HF_MODEL,
# #             "messages": [
# #                 {
# #                     "role": "user",
# #                     "content": (
# #                         f"Grade this essay 0-100 based on this rubric: {rubric_text}\n\n"
# #                         f"Essay: {safe_essay}\n\n"
# #                         f"Reply with ONLY valid JSON, no extra text:\n"
# #                         f"{{\"total_score\": <int>, \"overall_feedback\": \"<str>\", "
# #                         f"\"strengths\": [\"<str>\", \"<str>\"], \"improvements\": [\"<str>\", \"<str>\"]}}"
# #                     )
# #                 }
# #             ],
# #             "max_tokens": 400,
# #             "temperature": 0.1,
# #         },
# #         timeout=60,
# #     )

# #     if resp.status_code == 503:
# #         wait = 20
# #         logger.info(f"⏳ HuggingFace model loading, waiting {wait}s …")
# #         time.sleep(wait)
# #         resp = requests.post(
# #             HF_URL,
# #             headers={
# #                 "Authorization": f"Bearer {HF_API_KEY}",
# #                 "Content-Type":  "application/json",
# #             },
# #             json={
# #                 "model": HF_MODEL,
# #                 "messages": [{"role": "user", "content": f"Grade essay: {safe_essay[:500]}. Reply JSON: {{\"total_score\": 50}}"}],
# #                 "max_tokens": 100,
# #             },
# #             timeout=60,
# #         )

# #     resp.raise_for_status()

# #     data = resp.json()
# #     raw  = data["choices"][0]["message"]["content"]

# #     try:
# #         result = _extract_json(raw)
# #     except ValueError:
# #         score_match = re.search(r'\b(\d{1,3})\b', raw)
# #         score = int(score_match.group(1)) if score_match else 50
# #         score = max(0, min(100, score))
# #         result = {
# #             "total_score":      score,
# #             "overall_feedback": raw[:200] if raw else "Graded by HuggingFace fallback.",
# #             "strengths":        ["Evaluated by fallback model"],
# #             "improvements":     ["Re-grade with primary model when quota resets"],
# #         }

# #     result["breakdown"] = result.get("breakdown", {
# #         "Content & Arguments":     {"score": round(result["total_score"] * 0.4), "max_score": 40, "feedback": "Estimated by fallback model."},
# #         "Structure & Organization":{"score": round(result["total_score"] * 0.3), "max_score": 30, "feedback": "Estimated by fallback model."},
# #         "Grammar & Language":      {"score": round(result["total_score"] * 0.3), "max_score": 30, "feedback": "Estimated by fallback model."},
# #     })
# #     result["graded_by"] = f"huggingface/{HF_MODEL}"
# #     return result



# # # ── Public interface ──────────────────────────────────────────────────────────

# # def grade_essay(essay_text: str, rubric: dict = None) -> dict:
# #     """
# #     Grade an essay with automatic fallback:
# #       1. Gemini 2.5-flash-preview-05-20  (primary)
# #       2. Gemini 1.5-flash-latest          (fallback — separate quota)
# #       3. HuggingFace flan-t5-large        (last resort — no quota issues)

# #     Always returns a valid grading dict.
# #     Raises only if ALL three options fail.
# #     """
# #     rubric_text = _build_rubric_text(rubric)
# #     errors      = []

# #     # ── Try each Gemini model in order ────────────────────────────────────
# #     for model in GEMINI_MODELS:
# #         try:
# #             result = _grade_with_gemini(essay_text, rubric_text, model)
# #             logger.info(f"✅ Graded with {model} — score: {result.get('total_score')}")
# #             return result
# #         except RateLimitError as e:
# #             errors.append(str(e))
# #             continue
# #         except requests.HTTPError as e:
# #             errors.append(f"{model} HTTP error: {e}")
# #             logger.warning(f"⚠️  {model} failed: {e}")
# #             continue
# #         except Exception as e:
# #             errors.append(f"{model} error: {e}")
# #             logger.warning(f"⚠️  {model} unexpected error: {e}")
# #             continue

# #     # ── Try HuggingFace ───────────────────────────────────────────────────
# #     try:
# #         result = _grade_with_huggingface(essay_text, rubric_text)
# #         logger.info(f"✅ Graded with HuggingFace — score: {result.get('total_score')}")
# #         return result
# #     except Exception as e:
# #         errors.append(f"HuggingFace error: {e}")
# #         logger.error(f"❌ HuggingFace also failed: {e}")

# #     # ── All three failed ──────────────────────────────────────────────────
# #     raise RuntimeError(
# #         "All grading providers failed. Details:\n" + "\n".join(errors)
# #     )






# # services/grader.py
# # Fallback chain: Gemini 2.5-flash → Gemini 2.0-flash → Ollama (local, free)

# import os
# import re
# import json
# import time
# import logging
# import requests

# logger = logging.getLogger(__name__)

# # ── API config ────────────────────────────────────────────────────────────────
# GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# GEMINI_BASE    = "https://generativelanguage.googleapis.com/v1beta/models"

# GEMINI_MODELS = [
#     "gemini-2.5-flash",   # Primary
#     "gemini-2.0-flash",   # Fallback — separate quota
# ]

# # Ollama local config
# OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")
# OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://localhost:11434")


# # ── Custom error type ─────────────────────────────────────────────────────────

# class RateLimitError(Exception):
#     pass


# # ── Shared helpers ────────────────────────────────────────────────────────────

# def _build_rubric_text(rubric: dict | None) -> str:
#     if rubric:
#         return "\n".join([f"- {k}: {v} marks" for k, v in rubric.items()])
#     return (
#         "- Content & Arguments: 40 marks\n"
#         "- Structure & Organization: 30 marks\n"
#         "- Grammar & Language: 30 marks"
#     )


# def _build_prompt(essay_text: str, rubric_text: str) -> str:
#     safe_essay = essay_text[:1500]
#     if len(essay_text) > 1500:
#         safe_essay += "\n[... essay truncated for grading ...]"

#     return f"""You are an expert essay grader. Grade the essay below based on this rubric.

# RUBRIC:
# {rubric_text}

# ESSAY:
# \"\"\"
# {safe_essay}
# \"\"\"

# CRITICAL INSTRUCTIONS:
# - Return ONLY a single valid JSON object.
# - No markdown, no code fences, no commentary before or after the JSON.
# - Keep ALL feedback strings SHORT — under 80 words each.
# - Do NOT include the essay text in your response.
# - Use EXACTLY this structure:

# {{
#   "total_score": <integer 0-100>,
#   "breakdown": {{
#     "Content & Arguments": {{"score": <int>, "max_score": 40, "feedback": "<max 80 words>"}},
#     "Structure & Organization": {{"score": <int>, "max_score": 30, "feedback": "<max 80 words>"}},
#     "Grammar & Language": {{"score": <int>, "max_score": 30, "feedback": "<max 80 words>"}}
#   }},
#   "overall_feedback": "<max 60 words>",
#   "strengths": ["<short phrase>", "<short phrase>"],
#   "improvements": ["<short phrase>", "<short phrase>"],
#   "graded_by": "placeholder"
# }}"""


# def _extract_json(text: str) -> dict:
#     """
#     Robustly extract a JSON object from AI output.
#     Handles: markdown fences, extra text, and TRUNCATED responses.
#     """
#     text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

#     try:
#         return json.loads(text)
#     except json.JSONDecodeError:
#         pass

#     start = text.find("{")
#     end   = text.rfind("}")
#     if start != -1 and end != -1 and end > start:
#         try:
#             return json.loads(text[start:end + 1])
#         except json.JSONDecodeError:
#             pass

#     # Truncation recovery — salvage the score at minimum
#     if start != -1:
#         partial = text[start:]
#         score_match = re.search(r'"total_score"\s*:\s*(\d+)', partial)
#         if score_match:
#             score = int(score_match.group(1))
#             logger.warning(f"⚠️  JSON truncated — salvaged total_score={score}")
#             return {
#                 "total_score": score,
#                 "breakdown": {
#                     "Content & Arguments":     {"score": round(score * 0.4), "max_score": 40, "feedback": "Auto-extracted (response truncated)."},
#                     "Structure & Organization":{"score": round(score * 0.3), "max_score": 30, "feedback": "Auto-extracted (response truncated)."},
#                     "Grammar & Language":      {"score": round(score * 0.3), "max_score": 30, "feedback": "Auto-extracted (response truncated)."},
#                 },
#                 "overall_feedback": "Grading completed but detailed feedback was truncated. Score extracted successfully.",
#                 "strengths":    ["Score successfully extracted"],
#                 "improvements": ["Re-grade for full feedback"],
#                 "graded_by":    "truncation-recovery",
#             }

#     raise ValueError(f"Could not parse JSON from response:\n{text[:400]}")


# # ── Gemini grader ─────────────────────────────────────────────────────────────

# def _grade_with_gemini(essay_text: str, rubric_text: str, model: str) -> dict:
#     url    = f"{GEMINI_BASE}/{model}:generateContent?key={GEMINI_API_KEY}"
#     prompt = _build_prompt(essay_text, rubric_text)

#     resp = requests.post(
#         url,
#         headers={"Content-Type": "application/json"},
#         json={
#             "contents": [{"parts": [{"text": prompt}]}],
#             "generationConfig": {
#                 "temperature":      0.0,
#                 "maxOutputTokens":  1200,
#                 "topP":             1.0,
#                 "topK":             1,
#                 "responseMimeType": "application/json",
#             },
#         },
#         timeout=60,
#     )

#     if resp.status_code == 429:
#         logger.warning(f"⚠️  Gemini rate limit hit on {model} — trying next fallback")
#         raise RateLimitError(f"Rate limit on {model}")

#     resp.raise_for_status()
#     data = resp.json()

#     candidates = data.get("candidates", [])
#     if not candidates:
#         raise ValueError(f"No candidates returned by {model}")

#     finish_reason = candidates[0].get("finishReason", "")
#     if finish_reason not in ("STOP", "MAX_TOKENS", ""):
#         raise ValueError(f"Bad finish reason from {model}: {finish_reason}")

#     raw    = candidates[0]["content"]["parts"][0]["text"]
#     result = _extract_json(raw)
#     result["graded_by"] = model
#     return result


# # ── Ollama local grader ───────────────────────────────────────────────────────

# # def _grade_with_ollama(essay_text: str, rubric_text: str) -> dict:
# #     """
# #     Use local Ollama (llama3.2:1b or whichever model is set in OLLAMA_MODEL).
# #     Ollama must be running: `ollama serve`
# #     Model must be pulled:   `ollama pull llama3.2:1b`
# #     """
# #     prompt = _build_prompt(essay_text, rubric_text)

# #     # First check Ollama is reachable
# #     try:
# #         health = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
# #         health.raise_for_status()
# #     except Exception as e:
# #         raise RuntimeError(
# #             f"Ollama is not running at {OLLAMA_URL}. "
# #             f"Start it with: ollama serve\n({e})"
# #         )

# #     logger.info(f"⏳ Sending to Ollama ({OLLAMA_MODEL}) — this may take 30-60s for 1b model...")

# #     resp = requests.post(
# #         f"{OLLAMA_URL}/api/generate",
# #         headers={"Content-Type": "application/json"},
# #         json={
# #             "model":  OLLAMA_MODEL,
# #             "prompt": prompt,
# #             "stream": False,
# #             "options": {
# #                 "temperature": 0.1,
# #                 "num_predict": 400,   # Keep output short so JSON doesn't get cut
# #             },
# #         },
# #         timeout=120,  # Local models can be slow on CPU
# #     )
# #     resp.raise_for_status()

# #     data = resp.json()
# #     raw  = data.get("response", "")

# #     if not raw:
# #         raise ValueError("Ollama returned an empty response")

# #     try:
# #         result = _extract_json(raw)
# #     except ValueError:
# #         # llama3.2:1b sometimes wraps JSON in text — try harder
# #         logger.warning("⚠️  Ollama response wasn't clean JSON, attempting recovery...")
# #         score_match = re.search(r'\b(\d{2,3})\b', raw)
# #         score = int(score_match.group(1)) if score_match else 55
# #         score = max(0, min(100, score))
# #         result = {
# #             "total_score":      score,
# #             "overall_feedback": raw[:300] if raw else "Graded by local Ollama model.",
# #             "strengths":        ["Evaluated locally"],
# #             "improvements":     ["Re-grade with Gemini when quota resets"],
# #         }

# #     # Ensure breakdown always exists
# #     result["breakdown"] = result.get("breakdown", {
# #         "Content & Arguments":     {"score": round(result["total_score"] * 0.4), "max_score": 40, "feedback": "Estimated by local model."},
# #         "Structure & Organization":{"score": round(result["total_score"] * 0.3), "max_score": 30, "feedback": "Estimated by local model."},
# #         "Grammar & Language":      {"score": round(result["total_score"] * 0.3), "max_score": 30, "feedback": "Estimated by local model."},
# #     })
# #     result["graded_by"] = f"ollama/{OLLAMA_MODEL}"
# #     return result




# def _grade_with_ollama(essay_text: str, rubric_text: str) -> dict:
#     """
#     Use local Ollama (llama3.2:1b).
#     Uses a SIMPLIFIED prompt — small models fail on complex JSON templates.
#     """
#     # First check Ollama is reachable
#     try:
#         health = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
#         health.raise_for_status()
#     except Exception as e:
#         raise RuntimeError(
#             f"Ollama is not running at {OLLAMA_URL}. "
#             f"Start it with: ollama serve\n({e})"
#         )

#     # Truncate essay hard — 1b model struggles with long input
#     safe_essay = essay_text[:1200]

#     # SIMPLE prompt — small models fail on complex multi-field JSON
#     # Give it ONE job: return a score and short feedback
#     prompt = f"""Grade this essay. Rubric: {rubric_text}

# Essay: {safe_essay}

# Reply with ONLY this JSON, nothing else:
# {{"total_score": <number 0-100>, "content_score": <number 0-40>, "structure_score": <number 0-30>, "grammar_score": <number 0-30>, "feedback": "<one sentence feedback>", "strength": "<one strength>", "improve": "<one improvement>"}}"""

#     logger.info(f"⏳ Sending to Ollama ({OLLAMA_MODEL})...")

#     resp = requests.post(
#         f"{OLLAMA_URL}/api/generate",
#         headers={"Content-Type": "application/json"},
#         json={
#             "model":  OLLAMA_MODEL,
#             "prompt": prompt,
#             "stream": False,
#             "options": {
#                 "temperature": 0.0,   # deterministic = more consistent scores
#                 "num_predict": 200,   # short output = less chance of truncation
#                 "top_k": 1,           # greedy = most predictable JSON output
#             },
#         },
#         timeout=180,  # 3 mins — 1b on CPU can be slow but will finish
#     )
#     resp.raise_for_status()

#     raw = resp.json().get("response", "")
#     logger.info(f"📝 Ollama raw response: {raw[:200]}")

#     if not raw:
#         raise ValueError("Ollama returned empty response")

#     try:
#         parsed = _extract_json(raw)
#     except ValueError:
#         # Last resort — scan for any number 0-100 in the output
#         score_match = re.search(r'\b([4-9]\d|100|[1-3]\d)\b', raw)
#         score = int(score_match.group(1)) if score_match else 55
#         parsed = {"total_score": score}

#     # Normalize whatever the model returned into the standard shape
#     total = int(parsed.get("total_score", 55))
#     total = max(0, min(100, total))

#     # Use per-criterion scores if the model gave them, otherwise estimate
#     c_score = int(parsed.get("content_score",   round(total * 0.4)))
#     s_score = int(parsed.get("structure_score", round(total * 0.3)))
#     g_score = int(parsed.get("grammar_score",   round(total * 0.3)))

#     feedback  = parsed.get("feedback",  "Graded by local model.")
#     strength  = parsed.get("strength",  "Essay submitted successfully.")
#     improve   = parsed.get("improve",   "Review rubric criteria.")

#     return {
#         "total_score": total,
#         "breakdown": {
#             "Content & Arguments":     {"score": c_score, "max_score": 40, "feedback": feedback},
#             "Structure & Organization":{"score": s_score, "max_score": 30, "feedback": feedback},
#             "Grammar & Language":      {"score": g_score, "max_score": 30, "feedback": feedback},
#         },
#         "overall_feedback": feedback,
#         "strengths":    [strength],
#         "improvements": [improve],
#         "graded_by":    f"ollama/{OLLAMA_MODEL}",
#     }




# # ── Public interface ──────────────────────────────────────────────────────────

# def grade_essay(essay_text: str, rubric: dict = None) -> dict:
#     """
#     Grade an essay with automatic fallback:
#       1. Gemini 2.5-flash   (primary)
#       2. Gemini 2.0-flash   (fallback — separate quota)
#       3. Ollama local       (last resort — completely free, runs on your machine)

#     Always returns a valid grading dict.
#     Raises only if ALL three options fail.
#     """
#     rubric_text = _build_rubric_text(rubric)
#     errors      = []

#     # ── Try each Gemini model in order ────────────────────────────────────
#     for model in GEMINI_MODELS:
#         try:
#             result = _grade_with_gemini(essay_text, rubric_text, model)
#             logger.info(f"✅ Graded with {model} — score: {result.get('total_score')}")
#             return result
#         except RateLimitError as e:
#             errors.append(str(e))
#             continue
#         except requests.HTTPError as e:
#             errors.append(f"{model} HTTP error: {e}")
#             logger.warning(f"⚠️  {model} failed: {e}")
#             continue
#         except Exception as e:
#             errors.append(f"{model} error: {e}")
#             logger.warning(f"⚠️  {model} unexpected error: {e}")
#             continue

#     # ── Try Ollama (local, free) ───────────────────────────────────────────
#     try:
#         result = _grade_with_ollama(essay_text, rubric_text)
#         logger.info(f"✅ Graded with Ollama ({OLLAMA_MODEL}) — score: {result.get('total_score')}")
#         return result
#     except Exception as e:
#         errors.append(f"Ollama error: {e}")
#         logger.error(f"❌ Ollama also failed: {e}")

#     # ── All failed ────────────────────────────────────────────────────────
#     raise RuntimeError(
#         "All grading providers failed. Details:\n" + "\n".join(errors)
#     )






# services/grader.py
# Fallback chain:
#   1. Gemini 2.5-flash          (best quality, cloud)
#   2. Gemini 2.0-flash          (cloud fallback)
#   3. Similarity grader         (uses training_data.csv — fast, free, accurate)
#   4. Ollama local              (last resort)

import os
import re
import json
import logging
import requests
import time  
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# ── API config ────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_BASE    = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODELS  = ["gemini-2.5-flash", "gemini-2.0-flash"]

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")
OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://localhost:11434")

# Path to training data — sits in the backend root folder
TRAINING_DATA_PATH = Path(__file__).parent.parent / "training_data.csv"


# ── Custom errors ─────────────────────────────────────────────────────────────
class RateLimitError(Exception):
    pass


# ── Similarity grader ─────────────────────────────────────────────────────────

_similarity_model = None  # cached — only loaded once per server start

def _load_similarity_model():
    global _similarity_model
    if _similarity_model is not None:
        return _similarity_model

    if not TRAINING_DATA_PATH.exists():
        raise FileNotFoundError(
            f"training_data.csv not found at {TRAINING_DATA_PATH}"
        )

    logger.info(f"📚 Loading training data from {TRAINING_DATA_PATH}...")
    df = pd.read_csv(TRAINING_DATA_PATH)

    # Remove stray header rows that got mixed into the data
    df = df[df["score"] != "score"].copy()
    df["score"]     = pd.to_numeric(df["score"],     errors="coerce")
    df["max_score"] = pd.to_numeric(df["max_score"], errors="coerce")
    df = df.dropna(subset=["score", "essay_text"])
    df["essay_text"] = df["essay_text"].astype(str).str.strip()
    df = df[df["essay_text"].str.len() > 20]

    # Normalise score to 0-100
    df["score_100"] = (df["score"] / df["max_score"] * 100).clip(0, 100)

    vectorizer = TfidfVectorizer(
        max_features=8000,
        ngram_range=(1, 2),
        sublinear_tf=True,
        strip_accents="unicode",
    )
    vectors = vectorizer.fit_transform(df["essay_text"])

    _similarity_model = {
        "vectorizer": vectorizer,
        "vectors":    vectors,
        "scores":     df["score_100"].values,
        "labels":     df["label"].values,
    }
    logger.info(f"✅ Similarity model ready — {len(df)} training examples")
    return _similarity_model


def _grade_with_similarity(essay_text: str, rubric_text: str) -> dict:
    model = _load_similarity_model()
    vec   = model["vectorizer"].transform([essay_text])
    sims  = cosine_similarity(vec, model["vectors"])[0]

    K       = 10
    top_idx = np.argsort(sims)[-K:][::-1]
    top_sims   = sims[top_idx]
    top_scores = model["scores"][top_idx]
    top_labels = model["labels"][top_idx]

    weight_sum = top_sims.sum()
    if weight_sum < 0.01:
        total_score = float(np.mean(model["scores"]))
        confidence  = "low"
    else:
        total_score = float(np.average(top_scores, weights=top_sims))
        confidence  = "high" if top_sims[0] > 0.3 else "medium"

    total_score = round(max(0, min(100, total_score)))

    from collections import Counter
    label = Counter(top_labels).most_common(1)[0][0]

    feedback_map = {
        "excellent":    "Excellent — thorough, well-structured, well-referenced.",
        "good":         "Good essay with solid content and organisation.",
        "satisfactory": "Satisfactory. Covers the topic but lacks depth or references.",
        "weak":         "Weak. Needs more detail, structure, and supporting evidence.",
        "poor":         "Poor. Very limited engagement with the topic.",
        "off_topic":    "Off-topic. Please review the assignment instructions.",
    }
    strengths_map = {
        "excellent":    "Comprehensive coverage with strong evidence and clear structure.",
        "good":         "Good use of relevant content and logical organisation.",
        "satisfactory": "Adequate understanding of the topic demonstrated.",
        "weak":         "Some relevant points were included.",
        "poor":         "An attempt was made to address the topic.",
        "off_topic":    "Essay was submitted on time.",
    }
    improvements_map = {
        "excellent":    "Consider adding more diverse and recent sources.",
        "good":         "Deepen analysis and add more specific examples.",
        "satisfactory": "Add references and expand key arguments with evidence.",
        "weak":         "Significantly expand content and improve essay structure.",
        "poor":         "Review the topic thoroughly and rewrite with proper structure.",
        "off_topic":    "Re-read the assignment brief and write on the correct topic.",
    }

    feedback   = feedback_map.get(label, "Graded by similarity model.")
    c_score    = round(total_score * 0.40)
    s_score    = round(total_score * 0.30)
    g_score    = round(total_score * 0.30)

    logger.info(
        f"✅ Similarity — score: {total_score}, label: {label}, "
        f"confidence: {confidence}, top_sim: {top_sims[0]:.3f}"
    )

    return {
        "total_score": total_score,
        "breakdown": {
            "Content & Arguments":     {"score": c_score, "max_score": 40, "feedback": feedback},
            "Structure & Organization":{"score": s_score, "max_score": 30, "feedback": feedback},
            "Grammar & Language":      {"score": g_score, "max_score": 30, "feedback": feedback},
        },
        "overall_feedback": feedback,
        "strengths":        [strengths_map.get(label, "Essay submitted.")],
        "improvements":     [improvements_map.get(label, "Review rubric and improve.")],
        "graded_by":        f"similarity-model (label={label}, confidence={confidence})",
    }


# ── Shared helpers ────────────────────────────────────────────────────────────

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

    return f"""You are an expert essay grader. Grade the essay below based on this rubric.

RUBRIC:
{rubric_text}

ESSAY:
\"\"\"
{safe_essay}
\"\"\"

CRITICAL INSTRUCTIONS:
- Return ONLY a single valid JSON object.
- No markdown, no code fences, no commentary.
- Keep ALL feedback strings under 80 words.
- Do NOT include the essay text in your response.
- Use EXACTLY this structure:

{{
  "total_score": <integer 0-100>,
  "breakdown": {{
    "Content & Arguments": {{"score": <int>, "max_score": 40, "feedback": "<max 80 words>"}},
    "Structure & Organization": {{"score": <int>, "max_score": 30, "feedback": "<max 80 words>"}},
    "Grammar & Language": {{"score": <int>, "max_score": 30, "feedback": "<max 80 words>"}}
  }},
  "overall_feedback": "<max 60 words>",
  "strengths": ["<short phrase>", "<short phrase>"],
  "improvements": ["<short phrase>", "<short phrase>"],
  "graded_by": "placeholder"
}}"""


def _extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    if start != -1:
        score_match = re.search(r'"total_score"\s*:\s*(\d+)', text[start:])
        if score_match:
            score = int(score_match.group(1))
            logger.warning(f"⚠️  JSON truncated — salvaged total_score={score}")
            return {
                "total_score": score,
                "breakdown": {
                    "Content & Arguments":     {"score": round(score * 0.4), "max_score": 40, "feedback": "Response truncated."},
                    "Structure & Organization":{"score": round(score * 0.3), "max_score": 30, "feedback": "Response truncated."},
                    "Grammar & Language":      {"score": round(score * 0.3), "max_score": 30, "feedback": "Response truncated."},
                },
                "overall_feedback": "Score extracted from truncated response.",
                "strengths":    ["Score extracted"],
                "improvements": ["Re-grade for full feedback"],
                "graded_by":    "truncation-recovery",
            }

    raise ValueError(f"Could not parse JSON:\n{text[:400]}")


# ── Gemini grader ─────────────────────────────────────────────────────────────

def _grade_with_gemini(essay_text: str, rubric_text: str, model: str) -> dict:
    url    = f"{GEMINI_BASE}/{model}:generateContent?key={GEMINI_API_KEY}"
    prompt = _build_prompt(essay_text, rubric_text)

    resp = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature":      0.0,
                "maxOutputTokens":  1200,
                "topP":             1.0,
                "topK":             1,
                "responseMimeType": "application/json",
            },
        },
        timeout=60,
    )

    if resp.status_code == 429:
        logger.warning(f"⚠️  Gemini rate limit hit on {model} — trying next fallback")
        raise RateLimitError(f"Rate limit on {model}")

    resp.raise_for_status()
    data       = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise ValueError(f"No candidates returned by {model}")

    finish_reason = candidates[0].get("finishReason", "")
    if finish_reason not in ("STOP", "MAX_TOKENS", ""):
        raise ValueError(f"Bad finish reason from {model}: {finish_reason}")

    raw    = candidates[0]["content"]["parts"][0]["text"]
    result = _extract_json(raw)
    result["graded_by"] = model
    return result


# ── Ollama local grader (last resort) ─────────────────────────────────────────

def _grade_with_ollama(essay_text: str, rubric_text: str) -> dict:
    try:
        requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Ollama not running at {OLLAMA_URL}: {e}")

    safe_essay = essay_text[:1200]
    prompt = (
        f"Grade this essay. Rubric: {rubric_text}\n\n"
        f"Essay: {safe_essay}\n\n"
        f"Reply ONLY with this JSON:\n"
        f'{{ "total_score": <0-100>, "content_score": <0-40>, '
        f'"structure_score": <0-30>, "grammar_score": <0-30>, '
        f'"feedback": "<one sentence>", "strength": "<one phrase>", "improve": "<one phrase>" }}'
    )

    logger.info(f"⏳ Sending to Ollama ({OLLAMA_MODEL})...")
    resp = requests.post(
        f"{OLLAMA_URL}/api/generate",
        headers={"Content-Type": "application/json"},
        json={
            "model":  OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 200, "top_k": 1},
        },
        timeout=180,
    )
    resp.raise_for_status()

    raw = resp.json().get("response", "")
    if not raw:
        raise ValueError("Ollama returned empty response")

    try:
        parsed = _extract_json(raw)
    except ValueError:
        score_match = re.search(r'\b(\d{1,3})\b', raw)
        score = max(0, min(100, int(score_match.group(1)))) if score_match else 55
        parsed = {"total_score": score}

    total    = max(0, min(100, int(parsed.get("total_score", 55))))
    c_score  = int(parsed.get("content_score",   round(total * 0.4)))
    s_score  = int(parsed.get("structure_score", round(total * 0.3)))
    g_score  = int(parsed.get("grammar_score",   round(total * 0.3)))
    feedback = parsed.get("feedback", "Graded by local Ollama model.")

    return {
        "total_score": total,
        "breakdown": {
            "Content & Arguments":     {"score": c_score, "max_score": 40, "feedback": feedback},
            "Structure & Organization":{"score": s_score, "max_score": 30, "feedback": feedback},
            "Grammar & Language":      {"score": g_score, "max_score": 30, "feedback": feedback},
        },
        "overall_feedback": feedback,
        "strengths":        [parsed.get("strength",  "Essay submitted.")],
        "improvements":     [parsed.get("improve",   "Review rubric.")],
        "graded_by":        f"ollama/{OLLAMA_MODEL}",
    }


# ── Public interface ──────────────────────────────────────────────────────────

def grade_essay(essay_text: str, rubric: dict = None) -> dict:
    """
    Grade with automatic fallback:
      1. Gemini 2.5-flash    — best quality
      2. Gemini 2.0-flash    — cloud fallback
      3. Similarity model    — your 10,000+ training essays (instant, free, accurate)
      4. Ollama local        — last resort
    """
    rubric_text = _build_rubric_text(rubric)
    errors      = []

    for model in GEMINI_MODELS:
        try:
            result = _grade_with_gemini(essay_text, rubric_text, model)
            logger.info(f"✅ Graded with {model} — score: {result.get('total_score')}")
            return result
        except RateLimitError as e:
            errors.append(str(e))
        except requests.HTTPError as e:
            errors.append(f"{model} HTTP error: {e}")
            logger.warning(f"⚠️  {model} failed: {e}")
        except Exception as e:
            errors.append(f"{model} error: {e}")
            logger.warning(f"⚠️  {model} unexpected error: {e}")

    try:
        result = _grade_with_similarity(essay_text, rubric_text)
        logger.info(f"✅ Graded with similarity model — score: {result.get('total_score')}")
        return result
    except Exception as e:
        errors.append(f"Similarity grader error: {e}")
        logger.error(f"❌ Similarity grader failed: {e}")

    try:
        result = _grade_with_ollama(essay_text, rubric_text)
        logger.info(f"✅ Graded with Ollama — score: {result.get('total_score')}")
        return result
    except Exception as e:
        errors.append(f"Ollama error: {e}")
        logger.error(f"❌ Ollama also failed: {e}")

    raise RuntimeError("All grading providers failed:\n" + "\n".join(errors))