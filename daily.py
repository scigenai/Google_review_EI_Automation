##### Google Review - Daily Negative Comments (Apify version) #####

import pandas as pd
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
import time
import os, sys
import random
import re
import urllib.request
from apify_client import ApifyClient
import openai
from openai import OpenAI
import matplotlib
matplotlib.use("Agg")
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid
import base64
import warnings
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore")
import spacy
import json
from PIL import Image, ImageDraw, ImageFont
import math
from typing import Dict, Any, List
from dotenv import load_dotenv
load_dotenv()
# Load the English tokenizer, tagger, parser, NER, and word vectors
nlp = spacy.load("en_core_web_sm")

from utilities import (relative_date_to_datetime, label_rating, translate_to_english,
                       prefetch_translations_gemini, translate_to_english_gemini)
from config import list_of_links

# ----------------------------------
# LOAD SUPPORT DATASETS
# ----------------------------------
branch_region_mapping = pd.read_excel('branch_region_mapping.xlsx')

# ----------------------------------
# GLOBAL VARIABLES
# ----------------------------------
date_suffix = datetime.now().strftime("%d%b%Y")  # Get current date in ddmmmyyyy format e.g., '18Nov2025'

# ----------------------------------
# CONFIGURATION
# ----------------------------------

key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=key)

# Email details
sender_email = os.getenv("SENDER_EMAIL")
receiver_email = os.getenv("RECEIVER_EMAIL").split(",")
app_password = os.getenv("APP_PASSWORD")  # Use your generated app password for sending gmail

# Apify settings -- token comes from environment / GitHub Actions secret
APIFY_TOKEN = os.getenv("APIFY_TOKEN")
if not APIFY_TOKEN:
    raise RuntimeError("APIFY_TOKEN is not set (add it as a GitHub secret / .env variable).")
ACTOR_ID = "web_wanderer/google-reviews-scraper"
TZ = ZoneInfo("Asia/Dubai")
MAX_ATTEMPTS = 3          # retries only when a run fails / errors
RETRY_WAIT_SECONDS = 30
REVIEWS_LIMIT_PER_BRANCH = 200

# Which branches to run: keys in config.list_of_links starting with this prefix.
# Defaults to EI branches; set BRANCH_PREFIX env var to override.
BRANCH_PREFIX = os.getenv("BRANCH_PREFIX", "EI")

# How many days back to report on (1 = yesterday, UAE time)
DAYS_BACK = 1

# ----------------------------------
# HELPER FUNCTIONS
# ----------------------------------

# Function to create HTML
def convert_gpt_text_to_html(text):
    # Convert **bold** to <strong>...</strong>
    text_bold = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", text)

    # Convert newlines to <br> for spacing
    html_content = text_bold.replace("\n\n", "<br><br>").replace("\n", "<br>")

    # Wrap in a styled HTML body for consistency
    html_body = f"""
    <html>
    <head>
      <style>
        body {{
          font-family: Arial, sans-serif;
          font-size: 14px;
          color: #000000;
          line-height: 1.6;
        }}
        strong {{
          font-weight: bold;
        }}
      </style>
    </head>
    <body>
    {html_content}
    </body>
    </html>
    """
    return html_body

# HELPER: Build the GPT Prompt
def build_prompt_for_daily_email(review_data):

    if review_data.empty:
        return "No negative reviews found for the day."

    # Negative reviews text
    negative_reviews_text = ""
    for _, row in review_data.iterrows():
        negative_reviews_text += f"- {row['branch']} from {row['region']} \n received star rating: {row['label_rating']} \n user comment: {row['User_comment_review']} \n bank responded: {row['Response_to_review']}\n\n"

    # Final prompt
    prompt = f"""
You are a senior Customer Experience Manager at Emirates NBD.

Your task is to read the provided negative Google Reviews for Emirates NBD branches
and generate a concise JSON summary for a daily management dashboard.

The JSON must follow this exact structure:

{{
  "intro": "<one-sentence summary of yesterday's negative reviews>",
  "reviews": [
    {{
      "category": "<one main Theme of Concern or reason>",
      "branch_name": "<branch name>",
      "region_name": "<region name>",
      "rating": <numeric rating, e.g. 1.0>,
      "review_text": "<exact customer review text>",
      "responded": true or false
    }}
  ]
}}

----------------------------------------------------------------------
1. Overall rules
----------------------------------------------------------------------
• Use ONLY the data provided. Do not invent reviews, branches, regions, ratings, or responses.
• Never alter the wording of the customer’s review or the bank’s reply.
• If a piece of information is missing in the input, leave it empty or infer it only if it is trivially obvious from the text (e.g., branch and region fields if explicitly labelled).
• The output MUST be valid JSON. No comments, no trailing commas, no extra text before or after the JSON.

----------------------------------------------------------------------
2. Themes of Concern ("category")
----------------------------------------------------------------------
For each negative review, assign ONE primary Theme of Concern or reason in the "category" field.

A. You are NOT restricted to a fixed list. Create a short, clear phrase that best represents the main issue in the customer’s feedback, for example:
• "Long wait time"
• "Queue management"
• "Service delays / processing time"
• "Staff attitude / behaviour"
• "ATM / CDM issues"
• "Cheque handling / clearing"
• "Account / card servicing issues"
• "Digital banking / mobile app issues"
• "Product information / communication"
• "Branch environment / facilities"
• Or any other concise, relevant phrasing that accurately reflects the complaint.

B. Special rule for ratings without comments:
• If there is a low rating but NO actual comment text from the user (i.e., no clear reason is given in the input),
  then set:
  "category": "Not specified by the user"

C. If multiple themes apply, choose the most important one based on the review text.

D. When summarising top reasons in the intro sentence, do NOT treat "Not specified by the user" as a theme of concern.

----------------------------------------------------------------------
3. Intro sentence ("intro" field)
----------------------------------------------------------------------
The "intro" field should be ONE concise sentence summarising yesterday’s negative reviews.

It MUST include:
a) The total number of negative reviews received.
b) How many of those reviews received a bank response.
c) The top 1 to 3 Themes of Concern (by frequency), based on the "category" values, excluding "Not specified by the user".

Examples of style (do NOT copy these literally, just follow the pattern):

• "Yesterday, we received 5 negative Google reviews, of which 3 have been responded to, mainly highlighting long wait time, queue management, and service delays."
• "Yesterday, 2 negative Google reviews were recorded and both were responded to, with themes centred on staff attitude and ATM issues."

Rules:
• Be factual and neutral – no opinions, no blame.
• Do not mention themes that are not actually present in the data.
• If there is only 1 dominant theme, mention just that one.
• If all reviews only have "Not specified by the user" as category, state that reasons were not specified by customers.

----------------------------------------------------------------------
4. Reviews array ("reviews" field)
----------------------------------------------------------------------
The "reviews" array must contain one object per negative review.

For each review object:
• "category": one primary Theme of Concern or reason (a short phrase you define, or "Not specified by the user" if there is no comment).
• "branch_name": branch name from the input (exact text, if available).
• "region_name": region name from the input (exact text, if available).
• "rating": numeric rating as a float (e.g., 1.0, 2.0, 3.0).
  - If rating is given as text (e.g. "1 star"), convert to the correct numeric value.
• "review_text": the exact customer review text (do not paraphrase or edit). If the user left no comment, use an empty string "".
• "responded":
  - true if there is a bank/branch response to this review in the input data.
  - false if there is clearly no response present.

If any field is genuinely not available:
• For "branch_name" or "region_name": use an empty string "" rather than inventing values.
• For "rating": if you truly cannot find a rating, you may omit that review entirely instead of guessing.

----------------------------------------------------------------------
6. Input data
----------------------------------------------------------------------
The following is the full set of negative reviews and related metadata.
Use ONLY this information to build the JSON output.

{negative_reviews_text}

----------------------------------------------------------------------
7. Output format
----------------------------------------------------------------------
Now, produce ONLY the final JSON object, with:
• Keys: "intro", "reviews"
• No comments or explanations
• No markdown
• No surrounding text before or after the JSON

    """
    return prompt

# Function to get GPT-4o response
def generate_newsletter():
    prompt = build_prompt_for_daily_email(final_df_reviews)
    if prompt.startswith("No negative reviews found"):
        return prompt  # No data to summarize

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a senior Customer Experience Manager."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=4000,
        temperature=0.7
    )
    response_out = response.choices[0].message.content
    return response_out

# HELPER: Preparing Visual Newsletter
def generate_negative_reviews_daily_image(data: Dict[str, Any],
                                          width: int = 1800) -> Image.Image:
    """
    Generate a 'Daily Negative Reviews' visual image
    (big title + intro + clean review cards).
    """

    # =========================
    # BASIC CONFIG
    # =========================
    MARGIN_X = 120
    MARGIN_TOP = 80
    CARD_VERTICAL_GAP = 40
    CARD_HEIGHT_MIN = 230

    BG = (248, 249, 251)           # light grey background
    CARD_BG = (255, 255, 255)      # white
    CARD_BORDER = (225, 228, 234)
    TITLE_COLOR = (20, 40, 80)     # dark blue
    BODY_TEXT = (40, 40, 40)
    MUTED = (120, 120, 120)

    # =========================
    # FONTS
    # =========================
    def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            return ImageFont.load_default()

    font_title = load_font("fonts/PlusJakartaSans-Bold.ttf", 72)
    font_date = load_font("fonts/PlusJakartaSans-SemiBold.ttf", 40)
    font_intro = load_font("fonts/PlusJakartaSans-Regular.ttf", 34)
    font_cat = load_font("fonts/PlusJakartaSans-Bold.ttf", 42)
    font_meta = load_font("fonts/PlusJakartaSans-SemiBold.ttf", 32)
    font_body = load_font("fonts/PlusJakartaSans-Regular.ttf", 32)
    font_badge = load_font("fonts/PlusJakartaSans-SemiBold.ttf", 26)

    # =========================
    # HELPERS
    # =========================
    def wrap_text(draw: ImageDraw.ImageDraw,
                  text: str,
                  font: ImageFont.FreeTypeFont,
                  max_width: int) -> List[str]:
        words = text.split()
        lines: List[str] = []
        line = ""
        for w in words:
            test = (line + " " + w).strip()
            wbox = draw.textbbox((0, 0), test, font=font)
            if wbox[2] - wbox[0] <= max_width:
                line = test
            else:
                if line:
                    lines.append(line)
                line = w
        if line:
            lines.append(line)
        return lines

    def draw_card(draw: ImageDraw.ImageDraw,
                  xy,
                  radius: int = 24,
                  fill=CARD_BG,
                  outline=CARD_BORDER,
                  outline_width: int = 2):
        draw.rounded_rectangle(xy, radius=radius,
                               fill=fill, outline=outline,
                               width=outline_width)

    # ---- star rating helpers ----

    def draw_star_polygon(cx, cy, outer_radius, inner_radius, num_points=5):
        """Generate points for a star shape polygon"""
        points = []
        angle = -math.pi/2
        step = math.pi / num_points
        for i in range(2*num_points):
            r = outer_radius if i % 2 == 0 else inner_radius
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            points.append((x, y))
            angle += step
        return points

    def draw_star_row(img, draw, x, y, rating, max_stars=5, size=38, gap=10,
                      fill=(255, 215, 0), empty=(230, 230, 230)):
        """Draws a star rating row with fractional support."""
        for i in range(max_stars):
            cx = x + i * (size + gap) + size / 2
            cy = y + size / 2
            outer = size / 2
            inner = outer * 0.5
            poly = draw_star_polygon(cx, cy, outer, inner)

            # Draw base empty star
            draw.polygon(poly, fill=empty)

            # Fill fraction
            remaining = rating - i
            if remaining > 0:
                frac = max(0.0, min(1.0, remaining))
                star_img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
                star_draw = ImageDraw.Draw(star_img)
                local_poly = [(px - (cx - size/2), py - (cy - size/2)) for (px, py) in poly]
                star_draw.polygon(local_poly, fill=fill)

                mask = Image.new("L", (size, size), 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.rectangle([0, 0, int(size*frac), size], fill=255)

                img.paste(star_img, (int(cx-size/2), int(cy-size/2)), mask)

    # =========================
    # PRE-COMPUTE HEIGHT
    # =========================
    tmp_img = Image.new("RGB", (width, 2000), BG)
    tmp_draw = ImageDraw.Draw(tmp_img)

    content_width = width - 2 * MARGIN_X

    intro = data['intro']
    intro_lines = wrap_text(tmp_draw, intro, font_intro, content_width)

    title_height = font_title.size + 10
    date_height = font_date.size + 20
    intro_height = len(intro_lines) * (font_intro.size + 6) + 20
    header_block_height = MARGIN_TOP + title_height + date_height + intro_height + 40

    reviews = data.get("reviews", [])
    per_card_heights = []
    for rv in reviews:
        lines_text = wrap_text(tmp_draw,
                               rv.get("review_text", ""),
                               font_body,
                               content_width - 80)
        text_block_height = len(lines_text) * (font_body.size + 6)
        content_height = (30
                          + font_cat.size + 12
                          + font_meta.size + 20
                          + 40
                          + 10
                          + text_block_height
                          + 60
                          )
        card_height = max(CARD_HEIGHT_MIN, content_height)
        per_card_heights.append(card_height)

    total_cards_height = sum(per_card_heights) + max(0, len(reviews) - 1) * CARD_VERTICAL_GAP

    height = int(header_block_height + total_cards_height + 140)
    height = max(height, 1200)

    # =========================
    # CREATE IMAGE & DRAW
    # =========================
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    # ----- HEADER -----
    x0 = MARGIN_X
    y = MARGIN_TOP

    title = f"{BRANCH_PREFIX} DAILY REPORT: NEGATIVE GOOGLE REVIEWS"
    draw.text((x0, y), title, font=font_title, fill=TITLE_COLOR)
    y += title_height + 10

    date_str = data.get("date_str", "")
    if date_str:
        draw.text((x0, y), date_str, font=font_date, fill=MUTED)
        y += date_height

    for line in intro_lines:
        draw.text((x0, y), line, font=font_intro, fill=BODY_TEXT)
        y += font_intro.size + 6
    y += 20

    draw.line((MARGIN_X, y, width - MARGIN_X, y),
              fill=(220, 220, 220), width=2)
    y += 40

    # =========================
    # REVIEW CARDS
    # =========================
    for rv, card_h in zip(reviews, per_card_heights):
        card_top = y
        card_bottom = y + card_h
        card_left = MARGIN_X
        card_right = width - MARGIN_X

        draw_card(draw, (card_left, card_top, card_right, card_bottom))

        inner_x = card_left + 40
        inner_y = card_top + 30
        inner_w = (card_right - card_left) - 80

        cat_text = rv.get("category", "Feedback")
        draw.text((inner_x, inner_y), cat_text.upper(),
                  font=font_cat, fill=TITLE_COLOR)
        inner_y += font_cat.size + 12

        meta = f"{rv.get('branch_name', '')} · {rv.get('region_name', '')}"
        draw.text((inner_x, inner_y), meta,
                  font=font_meta, fill=MUTED)

        responded = rv.get("responded", False)
        badge_text = "Responded" if responded else "Pending"
        badge_color = (214, 239, 214) if responded else (255, 235, 235)
        badge_text_color = (24, 118, 62) if responded else (176, 42, 55)
        bt_w, bt_h = draw.textbbox((0, 0), badge_text, font=font_badge)[2:]
        pad_x, pad_y = 22, 10
        badge_w = bt_w + 2 * pad_x
        badge_h = bt_h + 2 * pad_y
        badge_x1 = card_right - 40 - badge_w
        badge_y1 = card_top + 30
        badge_x2 = badge_x1 + badge_w
        badge_y2 = badge_y1 + badge_h
        draw.rounded_rectangle((badge_x1, badge_y1, badge_x2, badge_y2),
                               radius=badge_h // 2,
                               fill=badge_color,
                               outline=(230, 230, 230))
        draw.text((badge_x1 + pad_x,
                   badge_y1 + pad_y - 4),
                  badge_text, font=font_badge,
                  fill=badge_text_color)

        inner_y += font_meta.size + 20

        rating = rv.get("rating")
        if rating is not None:
            draw_star_row(img, draw, inner_x, inner_y, float(rating))
            inner_y += 40

        text_lines = wrap_text(draw,
                               rv.get("review_text", ""),
                               font_body,
                               inner_w)
        inner_y += 10
        for ln in text_lines:
            draw.text((inner_x, inner_y),
                      ln,
                      font=font_body,
                      fill=BODY_TEXT)
            inner_y += font_body.size + 6

        y = card_bottom + CARD_VERTICAL_GAP

    # =========================
    # FOOTER
    # =========================
    footer_divider_y = y

    draw.line(
        (MARGIN_X, footer_divider_y, width - MARGIN_X, footer_divider_y),
        fill=(200, 200, 200),
        width=2
        )

    footer_text_y = footer_divider_y + 30

    left_footer_text = "Strategy & Customer Intelligence"
    draw.text(
        (MARGIN_X, footer_text_y),
        left_footer_text,
        font=font_body,
        fill=TITLE_COLOR
        )

    right_footer_text = "Powered by\nGen AI"
    bbox = draw.multiline_textbbox((0, 0), right_footer_text, font=font_badge, spacing=4)
    text_w = bbox[2] - bbox[0]

    draw.multiline_text(
        (width - MARGIN_X - text_w, footer_text_y),
        right_footer_text,
        font=font_badge,
        fill=(120, 120, 120),
        spacing=4
        )

    return img


def send_simple_email(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = ", ".join(receiver_email)
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender_email, app_password)
        smtp.sendmail(sender_email, receiver_email, msg.as_string())

    print("[EMAIL] Notification email sent.")

# ----------------------------------
# MAIN RUN CODE
# ----------------------------------

# Step 1 : Google Reviews from yesterday (UAE time)  [APIFY]

apify_client = ApifyClient(APIFY_TOKEN)

yesterday = (datetime.now(TZ) - timedelta(days=DAYS_BACK)).date()
window_start = datetime.combine(yesterday, dtime.min, tzinfo=TZ)
window_end   = datetime.combine(yesterday, dtime(23, 59, 59), tzinfo=TZ)
print(f"Target day (UAE): {yesterday}")


def resolve_short_url(url):
    """Resolve maps.app.goo.gl short links to the full Google Maps URL.
    Falls back to the original link if resolving fails."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.geturl()
    except Exception as e:
        print(f"  [WARNING] Could not resolve short link, using original. ({e})")
        return url


def fetch_branch_reviews(branch_key, url):
    """
    Run the Apify actor for ONE branch and return the raw items.
    Retries only when the run fails or errors. An empty result is treated
    as 'no reviews'.
    """
    full_url = resolve_short_url(url)
    print(f"  [URL] {full_url}")

    run_input = {
        "place_urls": [full_url],
        # ask from 1 day before target so timezone edges can't drop anything; exact filtering is done below
        "anyDate": (yesterday - timedelta(days=1)).isoformat(),
        "order": "newest",
        "source": "google",
        "limit": REVIEWS_LIMIT_PER_BRANCH,
        "lang": "en",
        "include_personal": True,
    }

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            run = apify_client.actor(ACTOR_ID).call(run_input=run_input)
            status = run.get("status") if run else None
            if status == "SUCCEEDED":
                items = list(apify_client.dataset(run["defaultDatasetId"]).iterate_items())
                print(f"  [APIFY] Attempt {attempt}: status={status}, raw reviews={len(items)}")
                return items
            last_error = f"status={status}"
            print(f"  [APIFY] Attempt {attempt}: status={status}")
        except Exception as e:
            last_error = str(e)
            print(f"  [APIFY] Attempt {attempt} error: {e}")
        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_WAIT_SECONDS)

    raise RuntimeError(f"Apify run failed after {MAX_ATTEMPTS} attempts ({last_error})")


def apify_items_to_cards(items):
    """
    Convert Apify items into the card format:
    review_id, name, rating (text like '2 stars'), comment, date, response.
    Keeps only reviews posted on the target day (UAE time).
    """
    cards = []
    seen_ids = set()

    for r in items:
        review_id = r.get("review_id")
        if review_id in seen_ids:
            continue

        # Date (UTC -> UAE, filter to target day)
        d = pd.to_datetime(r.get("reviewed_at_date"), utc=True, errors="coerce")
        if pd.isna(d):
            continue
        if not (window_start <= d <= window_end):
            continue
        date_obj = d.tz_convert("Asia/Dubai").tz_localize(None)

        # Rating -> '1 star' / '2 stars' ...
        rating_val = r.get("rating")
        if rating_val is None or pd.isna(rating_val):
            continue
        rating_int = int(rating_val)
        rating = "1 star" if rating_int == 1 else f"{rating_int} stars"

        # Comment (original text; translation happens later)
        comment = r.get("content") or ""

        # Owner response
        response = r.get("owner_response") or ""
        if isinstance(response, dict):
            response = response.get("text") or response.get("content") or ""
        response = str(response).strip()

        cards.append({
            'review_id': review_id,
            'name':      r.get("reviewer_name") or "",
            'rating':    rating,
            'comment':   comment,
            'date':      date_obj,
            'response':  response
        })
        seen_ids.add(review_id)

    return cards


# ── Preprocessing ─────────────────────────────────────────────────────────────

def preprocess_reviews(reviews_df):
    reviews_df = reviews_df.drop(columns=['review_id'], errors='ignore')
    reviews_df = reviews_df.rename(columns={
        'date':     'User_review_date',
        'rating':   'User_review_rating',
        'comment':  'User_comment_review',
        'response': 'Response_to_review'
    })
    rating_mapping = {
        'نجمة واحدة': '1 star',
        'نجمتان (2)': '2 stars',
        '3 نجوم':     '3 stars',
        '4 نجوم':     '4 stars',
        '5 نجوم':     '5 stars'
    }
    reviews_df['User_review_rating'] = reviews_df['User_review_rating'].replace(rating_mapping)
    reviews_df['label_rating'] = reviews_df['User_review_rating'].str.split().str[0].astype(int)
    reviews_df['label_flag']   = reviews_df['label_rating'].apply(label_rating)
    reviews_df['User_comment_review'] = reviews_df['User_comment_review'].fillna('')
    reviews_df['Response_to_review']  = reviews_df['Response_to_review'].fillna('')
    return reviews_df


# ── Main ──────────────────────────────────────────────────────────────────────

selected_branches = {k: v for k, v in list_of_links.items() if k.startswith(BRANCH_PREFIX)}
print(f"Branches to process ({BRANCH_PREFIX}): {len(selected_branches)}")

all_reviews_rows  = []
failed_branches   = []
no_negative_reviews = 0

for key, value in selected_branches.items():

    print(f"\n[BRANCH] {key}")

    try:
        items = fetch_branch_reviews(key, value)
        raw_reviews = apify_items_to_cards(items)

        if not raw_reviews:
            print(f"  [INFO] No reviews on target day for: {key}")
            continue

        reviews_df = pd.DataFrame(raw_reviews)
        reviews_df['branch_flag'] = key
        reviews_df = preprocess_reviews(reviews_df)

        all_reviews_rows.append(reviews_df)
        print(f"  [REVIEWS] Extracted {len(reviews_df)} reviews for: {key}")

    except Exception as e:
        print(f"  [ERROR] {key}: {e}")
        failed_branches.append(key)
        continue

if failed_branches:
    print(f"\n[WARNING] {len(failed_branches)} branch(es) failed: {failed_branches}")

# ── Filter negatives & save ───────────────────────────────────────────────────
if not all_reviews_rows:
    no_negative_reviews = 1
    if selected_branches and len(failed_branches) == len(selected_branches):
        message = (f"Apify scraping failed for all {BRANCH_PREFIX} branches "
                   f"({len(failed_branches)}). Please check the workflow logs.")
    else:
        message = f"No reviews extracted across all {BRANCH_PREFIX} branches."
    print(f"\n{message}")
    send_simple_email(
        subject=f"Google Reviews Automation Update - {date_suffix}",
        body=message
    )
else:
    all_reviews = pd.concat(all_reviews_rows, ignore_index=True)
    final_df_reviews = all_reviews[all_reviews['label_rating'] < 4].copy()

    if final_df_reviews.empty:
        no_negative_reviews = 1
        message = "No negative reviews found in last 24 hours."
        print(f"\n{message}")
        send_simple_email(
            subject=f"Google Reviews Automation Update - {date_suffix}",
            body=message
        )

    else:
        # Add bank / emirate / branch columns
        final_df_reviews[['bank', 'emirate', 'branch']] = (
            final_df_reviews['branch_flag'].str.split(' - ', expand=True)
        )
        final_df_reviews = pd.merge(
            final_df_reviews, branch_region_mapping,
            on="branch_flag", how="left"
        )

        # Save backup before translation
        backup_path = f"negative_reviews_enbd_bank_branches_{date_suffix}_without_translation.xlsx"
        final_df_reviews.to_excel(backup_path, index=False)
        print(f"\n[DONE] Backup saved: {backup_path}")

        # Translate Arabic → English (Gemini)
        try:
            print("Translating reviews...")
            prefetch_translations_gemini(final_df_reviews['User_comment_review'],
                                         final_df_reviews['Response_to_review'])
            final_df_reviews['User_comment_review'] = (
                final_df_reviews['User_comment_review'].apply(translate_to_english_gemini)
            )
            print("  Comment translation complete.")
            final_df_reviews['Response_to_review'] = (
                final_df_reviews['Response_to_review'].apply(translate_to_english_gemini)
            )
            print("  Response translation complete.")
        except Exception as e:
            print(f"  [ERROR] Translation failed, continuing with original text: {e}")

        final_df_reviews = final_df_reviews.drop_duplicates()
        output_path = f"negative_reviews_enbd_bank_branches_{date_suffix}.xlsx"
        final_df_reviews.to_excel(output_path, index=False)
        print(f"[DONE] Negative reviews saved: {output_path}")

# Step 2 : Preparing GPT output

response_text = None
response_json = None

if no_negative_reviews == 0:
    try:
        response_text = generate_newsletter()
        raw = response_text.strip()
        # If the model wrapped output in ```json ... ``` fences, strip them
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n", "", raw)
            raw = re.sub(r"\n```$", "", raw.strip())
        response_json = json.loads(raw)
        date_str = datetime.now().strftime("%d %B %Y")
        response_json['date_str'] = date_str
        print(response_json)
    except Exception as e:
        print(f"Unexpected error occurred during newsletter generation: {e}")
        send_simple_email(
            subject=f"Google Reviews Automation Update - {date_suffix}",
            body=(f"Negative reviews were found, but the GPT summary step failed: {e}\n"
                  f"Please check the workflow logs.")
        )

# Step 3 : Preparing Daily Negative Visual email

if no_negative_reviews == 0 and response_json is not None:
    img = generate_negative_reviews_daily_image(response_json)
    img.save(f"daily_negative_google_reviews_{date_suffix}.png")

# Step 4 : Sending email
if no_negative_reviews == 0 and response_json is not None:
    image_cid = make_msgid(domain="enbd.ai")[1:-1]

    opening_line = (
        "Hello team,<br>"
        f"Here is a summary of the negative Google reviews received yesterday from all {BRANCH_PREFIX} branches."
    )

    with open(f"daily_negative_google_reviews_{date_suffix}.png", "rb") as img_file:
        encoded_img = base64.b64encode(img_file.read()).decode("utf-8")

    html_newsletter = f"""
    <html>
      <body style="font-family: Arial, sans-serif; font-size: 14px; color: #333; margin:0; padding:0;">
          <div style="padding: 20px; width: 80%; margin: 0 auto; text-align: left;">
              {opening_line}
          </div>
          <div style="text-align: center; margin: 0; padding: 0;">
              <img src="data:image/png;base64,{encoded_img}"
                    alt="Newsletter Visual"
                    style="width:80%; max-width:800px; height:auto; display:block; margin:0 auto;" />
          </div>
      </body>
    </html>
    """
    msg = EmailMessage()
    msg["Subject"] = f"{BRANCH_PREFIX} Branch Google Reviews - Negative Posts - {date_suffix}"
    msg["From"] = sender_email
    msg["To"] = ", ".join(receiver_email)
    msg.set_content(response_text)  # Fallback plain text
    msg.add_alternative(html_newsletter, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender_email, app_password)
        smtp.sendmail(sender_email, receiver_email, msg.as_string())

    print("Email sent successfully with embedded image!")

################################ End of Code #################################
