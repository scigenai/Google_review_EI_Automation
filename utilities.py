# Utilities for Google Reviews

from datetime import datetime, timedelta
# from googletrans import Translator
# translator = Translator()
from deep_translator import GoogleTranslator
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

def translate_to_english(text):
    try:
        return GoogleTranslator(source='auto', target='en').translate(text)
    except Exception as e:
        return f"Translation failed: {e}"

