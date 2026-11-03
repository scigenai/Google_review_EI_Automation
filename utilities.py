# Utilities for Google Reviews

from datetime import datetime, timedelta
# from googletrans import Translator
# translator = Translator()
import json
import random
import time
from pathlib import Path
from deep_translator import GoogleTranslator
from deep_translator.exceptions import TooManyRequests
from nltk.util import ngrams

import spacy
# Load the English tokenizer, tagger, parser, NER, and word vectors
nlp = spacy.load("en_core_web_sm")

import warnings
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore")

# ---- Added for Gemini translation (new imports only) ----
import os
import re
import pandas as pd
from google import genai
from google.genai import types
# ---------------------------------------------------------

# Find the Date of the review
def relative_date_to_datetime(relative_date_string):
    today = datetime.now()

    # English cases
    if 'minute' in relative_date_string or 'minutes' in relative_date_string:
        minutes = int(relative_date_string.split()[0] if not relative_date_string.startswith('a ') else 1)
        return today - timedelta(minutes=minutes)

    if 'hour' in relative_date_string or 'hours' in relative_date_string:
        hours = int(relative_date_string.split()[0] if not relative_date_string.startswith('an ') else 1)
        return today - timedelta(hours=hours)

    if 'day' in relative_date_string or 'days' in relative_date_string:
        days = int(relative_date_string.split()[0] if not relative_date_string.startswith('a ') else 1)
        return today - timedelta(days=days)

    if 'week' in relative_date_string or 'weeks' in relative_date_string:
        weeks = int(relative_date_string.split()[0] if not relative_date_string.startswith('a ') else 1)
        return today - timedelta(weeks=weeks)

    if 'month' in relative_date_string or 'months' in relative_date_string:
        months = int(relative_date_string.split()[0] if not relative_date_string.startswith('a ') else 1)
        return today - timedelta(days=30 * months)

    if 'year' in relative_date_string or 'years' in relative_date_string:
        years = int(relative_date_string.split()[0] if not relative_date_string.startswith('a ') else 1)
        return today - timedelta(days=365 * years)

    # Arabic cases
    if 'قبل' in relative_date_string:
        parts = relative_date_string.split()

        # Dual form cases
        if 'دقيقتين' in relative_date_string:
            return today - timedelta(minutes=2)

        if 'ساعتين' in relative_date_string:
            return today - timedelta(hours=2)

        if 'يومين' in relative_date_string:
            return today - timedelta(days=2)

        if 'أسبوعين' in relative_date_string:
            return today - timedelta(weeks=2)

        if 'شهرين' in relative_date_string:
            return today - timedelta(days=60)

        if 'سنتين' in relative_date_string or 'عامين' in relative_date_string:
            return today - timedelta(days=365 * 2)

        # Singular/plural cases
        if 'دقيقة' in relative_date_string or 'دقائق' in relative_date_string:
            minutes = int(parts[1]) if parts[1].isdigit() else 1
            return today - timedelta(minutes=minutes)

        if 'ساعة' in relative_date_string or 'ساعات' in relative_date_string:
            hours = int(parts[1]) if parts[1].isdigit() else 1
            return today - timedelta(hours=hours)

        if 'يوم' in relative_date_string or 'أيام' in relative_date_string:
            days = int(parts[1]) if parts[1].isdigit() else 1
            return today - timedelta(days=days)

        if 'أسبوع' in relative_date_string or 'أسابيع' in relative_date_string:
            weeks = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            return today - timedelta(weeks=weeks)

        if 'شهر' in relative_date_string or 'أشهر' in relative_date_string:  # month(s)
            months = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            return today - timedelta(days=30 * months)

        if 'سنة' in relative_date_string or 'سنوات' in relative_date_string or 'عام' in relative_date_string or 'أعوام' in relative_date_string:  # year(s)
            years = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            return today - timedelta(days=365 * years)

    return today  # If no match is found, return the current datetime

# Label the sentiments by rates by the users
def label_rating(rating):
    if rating in [4, 5]:
        return 'Positive'
    elif rating == 3:
        return 'Neutral'
    else:
        return 'Negative'

# Grouping by month and calculating the average score
def monthly_avg_score(df):
    return df.groupby([df["User_review_date"].dt.year, df["User_review_date"].dt.month])["label_rating"].mean()

# Group by month and count the number of reviews
def monthly_review_count(df):
    return df.groupby([df["User_review_date"].dt.year, df["User_review_date"].dt.month]).size()

# Generate n-grams
def generate_ngrams(words_list, n):
    return [' '.join(ng) for ng in ngrams(words_list, n)]

def avg_score_6mon(df):
    return df.groupby(df["branch_flag"])["avg_rating_per_month"].mean()

# ---------------------------------------------------------------------------
# OLD translator (free Google endpoint) - kept for backward compatibility.
# daily.py now uses the Gemini translator below instead.
# ---------------------------------------------------------------------------
_CACHE_PATH = Path("translation_cache.json")

def _load_cache():
    if _CACHE_PATH.exists():
        try:
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"  [WARN] Could not read translation cache, starting fresh: {e}")
    return {}

def _save_cache(cache):
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [WARN] Could not save translation cache: {e}")

_translation_cache = _load_cache()
_endpoint_blocked = False  # once True, stop calling the endpoint for the rest of this run

def translate_to_english(text, max_retries=2, base_delay=3.0):
    """Old free-endpoint translator. Falls back to original text on any failure."""
    global _endpoint_blocked

    if not text or not str(text).strip():
        return text

    text = str(text)
    if text in _translation_cache:
        return _translation_cache[text]

    if _endpoint_blocked:
        return text

    for attempt in range(max_retries):
        try:
            result = GoogleTranslator(source='auto', target='en').translate(text)
            result = result if result else text
            _translation_cache[text] = result
            _save_cache(_translation_cache)
            time.sleep(base_delay + random.uniform(0, 1.0))
            return result
        except TooManyRequests:
            if attempt < max_retries - 1:
                wait = base_delay * (2 ** (attempt + 1))
                print(f"  [WARN] Translation rate-limited, retrying in {wait:.1f}s...")
                time.sleep(wait)
            else:
                print("  [WARN] Translation endpoint appears blocked for this run; "
                      "keeping original text for all remaining reviews.")
                _endpoint_blocked = True
                _translation_cache[text] = text
                return text
        except Exception as e:
            print(f"  [WARN] Translation failed, keeping original text: {e}")
            _translation_cache[text] = text
            return text

    _translation_cache[text] = text
    return text


# ===========================================================================
# Gemini translation (batch, Arabic -> English)
# ===========================================================================

# ---------- Gemini translation config ----------
# Read from environment / GitHub Actions secret (never hardcode the key)
_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Separate cache file from the old translator's translation_cache.json
_GEMINI_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemini_translation_cache.json")
_GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.7-flash", "gemini-3.8-flash", "gemini-2.5-flash"]
_BATCH_SIZE, _WAIT = 40, 7

_ARABIC = re.compile(r"[\u0600-\u06FF]")
_client = None
_model_idx = 0

_PROMPT = """You translate Google reviews of bank branches, and the bank's replies, from Arabic (often Gulf dialect) into natural English.
Rules:
- Personal names are names: transliterate them (e.g. هيلة -> Haila, عماد الحربي -> Emad Al-Harbi). Never translate a name as a word.
- Translate the meaning, not word by word. Render religious and courtesy phrases naturally (e.g. يعطيه العافية -> "bless him", ماشاء الله -> "mashallah").
- Keep emojis and any English words as they are.
- Return ONLY a JSON list: [{"id": <id>, "en": "<translation>"}], one item per input, same ids.

Input:
"""


def _load_gemini_cache():
    if os.path.exists(_GEMINI_CACHE_FILE):
        try:
            with open(_GEMINI_CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"  [WARN] Could not read Gemini cache, starting fresh: {e}")
    return {}


_cache = _load_gemini_cache()


def _get_client():
    global _client
    if _client is None:
        if not _GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set (add it as a GitHub secret / .env variable).")
        _client = genai.Client(api_key=_GEMINI_API_KEY)
    return _client


def _save_gemini_cache():
    try:
        with open(_GEMINI_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False)
    except Exception as e:
        print(f"  [WARN] Could not save Gemini cache: {e}")


def _call_model(text):
    global _model_idx
    rate_hits, errors = 0, 0
    while _model_idx < len(_GEMINI_MODELS):
        try:
            return _get_client().models.generate_content(
                model=_GEMINI_MODELS[_model_idx], contents=text,
                config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
            ).text
        except Exception as e:
            msg = str(e)
            if any(k in msg for k in ("404", "NOT_FOUND", "limit: 0", "PerDay")) or rate_hits >= 5:
                print(f"  {_GEMINI_MODELS[_model_idx]} unavailable or out of free quota, switching model")
                _model_idx, rate_hits = _model_idx + 1, 0
            elif "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                rate_hits += 1
                print("  rate limit, waiting 60 s")
                time.sleep(60)
            else:
                errors += 1
                if errors > 5:
                    raise
                print(f"  temporary error, retrying: {msg[:80]}")
                time.sleep(20)
    raise RuntimeError("Free quota used up for today. Progress saved; run again tomorrow.")


def _translate_batch(batch):
    items = [{"id": i, "text": t} for i, t in enumerate(batch)]
    for _ in range(3):
        try:
            result = {int(o["id"]): o["en"]
                      for o in json.loads(_call_model(_PROMPT + json.dumps(items, ensure_ascii=False)))}
            if all(i in result for i in range(len(batch))):
                return {batch[i]: result[i] for i in range(len(batch))}
        except (ValueError, KeyError, TypeError):
            pass
    if len(batch) > 1:
        mid = len(batch) // 2
        return {**_translate_batch(batch[:mid]), **_translate_batch(batch[mid:])}
    return {}


def prefetch_translations_gemini(*series):
    """Batch-translate all unique Arabic texts in the given Series into the cache."""
    todo = set()
    for s in series:
        texts = s.dropna().astype(str)
        has_arabic = texts.apply(lambda t: bool(_ARABIC.search(t)))
        todo.update(t for t in texts[has_arabic].unique() if t not in _cache)
    todo = sorted(todo)
    print(f"  {len(todo)} texts to translate")
    for i in range(0, len(todo), _BATCH_SIZE):
        _cache.update(_translate_batch(todo[i:i + _BATCH_SIZE]))
        _save_gemini_cache()
        print(f"  {min(i + _BATCH_SIZE, len(todo))}/{len(todo)} done")
        time.sleep(_WAIT)


def translate_to_english_gemini(text):
    """Use with .apply(). Returns the English translation; non-Arabic/empty values pass through."""
    if pd.isna(text):
        return text
    text = str(text)
    if not _ARABIC.search(text):
        return text
    if text not in _cache:                      # fallback if prefetch wasn't run
        _cache.update(_translate_batch([text]))
        _save_gemini_cache()
    return _cache.get(text, text)

# ---------- Gemini translation ends ----------
