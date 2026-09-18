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

# Function to translate Arabic comments to English
# def translate_to_english(text):
#     try:
#         # Translate the text to English
#         translation = translator.translate(text, src='auto', dest='en')
#         return translation.text
#     except Exception as e:
#         print(f"Translation failed for: {text}\nError: {e}")
#         return text  # Return original text if translation fails

# ---------------------------------------------------------------------------
# Persistent translation cache (survives across days/runs, not just one run)
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
    """
    Translate text to English via deep_translator's free GoogleTranslator --
    the same unauthenticated web endpoint translate.google.com's own site
    uses internally. No API key, no published quota, no SLA: it isn't meant
    for bulk/programmatic use, which is why it kept failing here. Google's
    abuse detection tracks request volume+pattern per source IP; cross an
    undocumented threshold and it blocks that IP -- not for a second, but for
    an extended, variable cooldown (often 30 minutes to several hours). The
    "5 requests/second" figure deep_translator reports in its error message
    is just its own rough documentation of observed behavior, not a real
    contract Google publishes. Repeated test runs of this script during
    debugging, each firing an unthrottled burst of calls, is exactly the
    pattern that triggers (and can re-trigger/extend) that block.

    This hardens the free-endpoint approach without switching providers:
      - a translation cache persisted to disk (translation_cache.json) so
        repeated boilerplate text is never re-requested, even across days,
      - a much more conservative delay between calls (3s + jitter, not the
        documented-but-unreliable 5/sec) to avoid tripping a fresh block,
      - once a TooManyRequests is seen, this run stops calling the endpoint
        entirely and falls back to original text for everything remaining,
        rather than retry-storming an endpoint that's already blocking you
        (which risks extending the block further),
      - never overwrites review text with an error string -- always falls
        back to the original text on any failure.

    Important: this reduces how often you trigger a block, it does not
    remove the underlying risk -- you're still riding an unofficial,
    undocumented, IP-throttled endpoint. If it keeps blocking you at your
    actual daily volume, the only way to eliminate that risk entirely is
    Google's *official* Cloud Translation API (a real, authenticated,
    paid product -- 500,000 characters/month free, then paid -- via the
    `google-cloud-translate` package), since that's a genuinely different,
    quota-based service rather than a scraped free page.
    """
    global _endpoint_blocked

    if not text or not str(text).strip():
        return text

    text = str(text)
    if text in _translation_cache:
        return _translation_cache[text]

    if _endpoint_blocked:
        # Already hit a block earlier in this run -- don't keep hammering it.
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
                      "keeping original text for all remaining reviews rather than "
                      "keep hammering a blocked endpoint.")
                _endpoint_blocked = True
                _translation_cache[text] = text
                return text
        except Exception as e:
            print(f"  [WARN] Translation failed, keeping original text: {e}")
            _translation_cache[text] = text
            return text

    _translation_cache[text] = text
    return text
