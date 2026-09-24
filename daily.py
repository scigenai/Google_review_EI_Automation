##### Google Review - Daily Negative Comments #####

import pandas as pd
from datetime import datetime
import time
import os, sys
import random
import re
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
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

# setting working directory to load utilities & config
#os.chdir("C:/Users/SCI_Analytics/Documents/Akshit's Workspace/Google Reviews")
#sys.path.append(os.getcwd())
from utilities import relative_date_to_datetime, label_rating, translate_to_english
from config import list_of_links

# setting working directory to write files
#os.chdir("C:/Users/SCI_Analytics/Documents/Akshit's Workspace/Google Reviews/daily negative comments")
#sys.path.append(os.getcwd())

# ----------------------------------
# LOAD SUPPORT DATASETS
# ----------------------------------
branch_region_mapping = pd.read_excel('branch_region_mapping.xlsx')

# ----------------------------------
# GLOBAL VARIABLES
# ----------------------------------
date_suffix = datetime.now().strftime("%d%b%Y")  # Get current date in ddmmmyyyy format e.g., '18Nov2025'

# How many times to retry a branch end-to-end (reload + re-click) before
# giving up on it. This targets ONE specific, confirmed problem: an
# intermittent Google sign-in / consent interstitial that occasionally
# appears right around the "Sort by Newest" step and blocks it (this was
# the original, real complaint -- roughly 3 of 5 runs hit it). A reload
# clears it almost every time.
MAX_BRANCH_ATTEMPTS = 3

# ----------------------------------
# CONFIGURATION
# ----------------------------------

# In[22]:
#try:
 #   key = st.secrets["OPENAI_API_KEY_linkedin"]
#except Exception:
 #   key = os.getenv("OPENAI_API_KEY_linkedin")

#client = OpenAI(api_key=key )
#client = openai.OpenAI(
 #   api_key=os.getenv("OPENAI_API_KEY")
#)
key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=key )

# Email details
sender_email = os.getenv("SENDER_EMAIL")
receiver_email = os.getenv("RECEIVER_EMAIL").split(",")
app_password = os.getenv("APP_PASSWORD")# Use your generated app password for sending gmail

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

    # Filter reviews for the selected month
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
    if prompt.startswith("No reviews found"):
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
    Generate a 'Daily Negative Reviews' visual image similar to the first mock
    (big title + intro + clean review cards).

    Args:
        data: dictionary with keys:
            - date_str: str
            - intro: str
            - reviews: List[{
                  "category": str,
                  "branch_name": str,
                  "region_name": str,
                  "rating": float or None,
                  "review_text": str,
                  "responded": bool
              }]
        width: canvas width in pixels

    Returns:
        PIL.Image object
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

    # ---- star rating helpers (optional per review) ----

    def draw_star_polygon(cx, cy, outer_radius, inner_radius, num_points=5):
        """Generate points for a star shape polygon"""
        points = []
        angle = -math.pi/2
        step = math.pi / num_points
        for i in range(2*num_points):
            r = outer_radius if i % 2 == 0 else inner_radius
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            points.append((x,y))
            angle += step
        return points

    def draw_star_row(img, draw, x, y, rating, max_stars=5, size=38, gap=10,
                      fill=(255,215,0), empty=(230,230,230)):
        """
        Draws a star rating row with fractional support.
        - img: base image
        - draw: ImageDraw object
        - x,y: top-left position
        - rating: float (e.g., 3.6)
        - max_stars: total number of stars
        - size: star size
        - gap: spacing between stars
        - fill: gold color for filled stars
        - empty: gray color for empty stars
        """
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
                # small star image
                star_img = Image.new("RGBA", (size, size), (255,255,255,0))
                star_draw = ImageDraw.Draw(star_img)
                local_poly = [(px - (cx - size/2), py - (cy - size/2)) for (px,py) in poly]
                star_draw.polygon(local_poly, fill=fill)

                # mask for fractional fill
                mask = Image.new("L", (size, size), 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.rectangle([0,0,int(size*frac),size], fill=255)

                img.paste(star_img, (int(cx-size/2), int(cy-size/2)), mask)

    # =========================
    # PRE-COMPUTE HEIGHT
    # =========================
    # Temp image to measure text
    tmp_img = Image.new("RGB", (width, 2000), BG)
    tmp_draw = ImageDraw.Draw(tmp_img)

    content_width = width - 2 * MARGIN_X

    # Intro text
    intro = data['intro']
    intro_lines = wrap_text(tmp_draw, intro, font_intro, content_width)

    title_height = font_title.size + 10
    date_height = font_date.size + 20
    intro_height = len(intro_lines) * (font_intro.size + 6) + 20
    header_block_height = MARGIN_TOP + title_height + date_height + intro_height + 40

    # Reviews
    reviews = data.get("reviews", [])
    per_card_heights = []
    for rv in reviews:
        # category + meta + text + paddings
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

    title = "EI DAILY REPORT: NEGATIVE GOOGLE REVIEWS"
    draw.text((x0, y), title, font=font_title, fill=TITLE_COLOR)
    y += title_height + 10

    date_str = data.get("date_str", "")
    if date_str:
        draw.text((x0, y), date_str, font=font_date, fill=MUTED)
        y += date_height

    # Intro paragraph
    for line in intro_lines:
        draw.text((x0, y), line, font=font_intro, fill=BODY_TEXT)
        y += font_intro.size + 6
    y += 20  # space before cards

    # thin divider
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

        # inner padding
        inner_x = card_left + 40
        inner_y = card_top + 30
        inner_w = (card_right - card_left) - 80

        # Category (as a 'tag' style heading)
        cat_text = rv.get("category", "Feedback")
        draw.text((inner_x, inner_y), cat_text.upper(),
                  font=font_cat, fill=TITLE_COLOR)
        inner_y += font_cat.size + 12

        # Branch · Region + optional responded badge
        meta = f"{rv.get('branch_name', '')} · {rv.get('region_name', '')}"
        draw.text((inner_x, inner_y), meta,
                  font=font_meta, fill=MUTED)

        # Responded badge on the right, if you want to show it
        responded = rv.get("responded", False)
        badge_text = "Responded" if responded else "Pending"
        badge_color = (214, 239, 214) if responded else (255, 235, 235)
        badge_text_color = (24, 118, 62) if responded else (176, 42, 55)
        # measure badge
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

        # Stars (optional; skip if rating None)
        rating = rv.get("rating")
        if rating is not None:
            # draw_star_row(draw, inner_x, inner_y, float(rating))

            draw_star_row(img, draw, inner_x, inner_y, float(rating))

            inner_y += 40

        # Review text
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

        # divider between cards
        y = card_bottom + CARD_VERTICAL_GAP

    # =========================
    # FOOTER
    # =========================

    # At this point, y is at the first free space after the last card
    footer_divider_y = y

    # Divider line just below the last card
    draw.line(
        (MARGIN_X, footer_divider_y, width - MARGIN_X, footer_divider_y),
        fill=(200, 200, 200),
        width=2
        )

    footer_text_y = footer_divider_y + 30

    # Left footer text
    left_footer_text = "Strategy & Customer Intelligence"
    draw.text(
        (MARGIN_X, footer_text_y),
        left_footer_text,
        font=font_body,
        fill=TITLE_COLOR
        )

    # Right footer text (multiline)
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

# ----------------------------------
# MAIN RUN CODE
# ----------------------------------

# Step 1 : Google Review from last day - All EI Banks

# ── Paths ─────────────────────────────────────────────────────────────────────
#os.chdir("C:/Users/SCI_Analytics/Documents/Akshit's Workspace/Google Reviews/daily negative comments")
#sys.path.append(os.getcwd())

# ── Driver ────────────────────────────────────────────────────────────────────

def create_driver():
    """
    Launch Chrome via undetected_chromedriver.

    Deliberately back to a plain, temp Chrome profile (no --user-data-dir).
    A persistent profile was tried and made things WORSE -- a run showed it
    breaking Google Maps' page routing entirely (every branch failed to
    even reach its own place page, landing on the generic Maps view
    instead), which never happened with a fresh temp profile. That's not
    what the original problem was, so it's reverted.

    The only real, confirmed fix kept here is version resolution:
    read the ACTUAL installed Chrome version from the CHROME_MAJOR_VERSION
    env var (the CI workflow sets this from `chrome --version`) instead of
    a hardcoded version_main. A run's log showed uc's own auto-detection
    guessing chromedriver v153 for an actually-installed Chrome v152 --
    reading the real version straight from the binary avoids that mismatch
    without probing through a dozen version numbers.
    """
    def _options():
        options = uc.ChromeOptions()
        #options.add_argument("--headless")
        options.add_argument("--lang=en")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-gpu")
        options.add_experimental_option('prefs', {'intl.accept_languages': 'en,en_US'})
        return options

    env_version = os.getenv("CHROME_MAJOR_VERSION", "").strip()
    if env_version.isdigit():
        try:
            driver = uc.Chrome(options=_options(), version_main=int(env_version))
            print(f"[SETUP] Chrome launched successfully (version_main={env_version}, from CHROME_MAJOR_VERSION).\n")
            return driver
        except Exception as e:
            print(f"[SETUP] Launch with CHROME_MAJOR_VERSION={env_version} failed: {e}")
            print("[SETUP] Falling back to auto-detection...")

    try:
        driver = uc.Chrome(options=_options())  # version_main omitted -> auto-detect
        print("[SETUP] Chrome launched successfully (auto-detected version).\n")
        return driver
    except Exception as e:
        print(f"[SETUP] Auto-detected Chrome launch failed: {e}")

    # Last resort: try current known-recent versions, newest first.
    for candidate_version in range(160, 149, -1):
        try:
            driver = uc.Chrome(options=_options(), version_main=candidate_version)
            print(f"[SETUP] Chrome launched successfully (version_main={candidate_version}).\n")
            return driver
        except Exception as e:
            print(f"  [SETUP] version_main={candidate_version} failed: {e}")
            continue

    raise RuntimeError(
        "Could not launch Chrome via undetected_chromedriver. Check the "
        "'Install Chrome' / 'Log installed Chrome version' step output in "
        "the workflow run."
    )


def dismiss_signin_or_consent_overlay(driver):
    """
    This is the fix for the ORIGINAL, confirmed complaint: Google Maps
    sometimes throws a cookie-consent banner over the page right around the
    Sort step, which is why "Newest sort option not found" only happened on
    some runs and not others.

    IMPORTANT: this ONLY clicks exact "Accept all" / "Reject all" consent
    buttons -- it deliberately does NOT click anything matched by a broad
    aria-label like "Close", "Dismiss", or "Not now". Those broad matches
    were tried in an earlier version and turned out to be the actual cause
    of a full regression: Google Maps' own place page ALWAYS has a
    legitimate button with aria-label="Close" on it (for the side panel,
    an image viewer, etc. -- visible in every branch's button dump, not
    just failing ones), and matching on that generic label auto-clicked it
    right after page load, closing the branch's own info panel and
    dropping the page back to the plain Maps view -- which is exactly the
    "Reviews button not found" / generic-Maps-view symptom that showed up
    on every single branch once this function started running. Do not
    re-add broad "Close"/"Dismiss"/"Not now" matching here.

    If it's an actual sign-in modal (which can't be safely auto-clicked
    without the same risk), this only detects and logs it -- the caller's
    retry-with-reload handles recovery instead.
    """
    # Bilingual (Arabic + English) combined into single XPath per structural
    # pattern -- same reasoning as find_reviews_button: avoids paying a full
    # WebDriverWait timeout for whichever language Google didn't serve.
    consent_strategies = [
        (By.XPATH, '//button[.//span[contains(text(),"Accept all") or contains(text(),"Reject all")]]'),
        (By.XPATH, '//button[contains(@aria-label,"Accept all") or contains(@aria-label,"Reject all")]'),
        (By.XPATH, '//button[contains(text(),"قبول الكل") or contains(text(),"رفض الكل")]'),
    ]
    for by, sel in consent_strategies:
        try:
            btn = WebDriverWait(driver, 1).until(EC.element_to_be_clickable((by, sel)))
            btn.click()
            time.sleep(1)
            return True
        except Exception:
            continue

    # Presence checks (not waits) -- instant, no timeout cost when absent.
    try:
        driver.find_element(By.XPATH, '//iframe[contains(@src,"accounts.google.com")]')
        print("  [INFO] Sign-in overlay detected.")
        return True
    except Exception:
        pass

    try:
        driver.find_element(By.XPATH, '//div[contains(@aria-label,"Sign in")]')
        print("  [INFO] Sign-in prompt detected.")
        return True
    except Exception:
        pass

    return False


def find_reviews_button(driver):
    """
    Find the Reviews tab using stable aria-label / role selectors.

    Google Maps' UI language is decided server-side by IP geolocation, not
    by the --lang Chrome flag: a UAE IP (local runs) gets served Arabic,
    a US IP (GitHub-hosted CI runners) gets served English. Both are
    legitimate depending on where the script runs, so we still need to
    handle both -- but checking them as SEPARATE sequential WebDriverWait
    attempts means every run wastes a full timeout on whichever language
    isn't being served that time (confirmed as a major, unnecessary chunk
    of the ~3.5min/branch runtime seen on CI). Combining both languages
    into ONE XPath per structural pattern (via XPath's own "or") matches
    immediately regardless of which language Google actually serves,
    without favoring either environment.
    """
    strategies = [
        (By.XPATH, '//button[contains(@aria-label,"المراجعات") or contains(@aria-label,"Reviews")]'),
        (By.XPATH, '//div[@role="tab"][contains(.,"المراجعات") or contains(.,"Reviews")]'),
        (By.XPATH, '//button[.//div[contains(text(),"المراجعات") or contains(text(),"Reviews")]]'),
    ]
    short_wait = WebDriverWait(driver, 3)
    for by, selector in strategies:
        try:
            btn = short_wait.until(EC.element_to_be_clickable((by, selector)))
            return btn
        except TimeoutException:
            continue
    return None

def click_sort_newest(driver):
    """
    Click the Sort button, then select Newest.

    Same bilingual-combining approach as find_reviews_button -- see that
    function's docstring for why sequential per-language selectors were
    costing real time on every branch regardless of environment.
    """
    short_wait = WebDriverWait(driver, 5)

    sort_strategies = [
        (By.XPATH, '//button[contains(@aria-label,"ترتيب") or contains(@aria-label,"Sort")]'),
        (By.XPATH, '//button[@data-value="sort"]'),
        (By.XPATH, '//div[@role="main"]//button[.//span[contains(text(),"ترتيب") or contains(text(),"Sort")]]'),
    ]
    clicked_sort = False
    for by, selector in sort_strategies:
        try:
            btn = short_wait.until(EC.element_to_be_clickable((by, selector)))
            btn.click()
            clicked_sort = True
            break
        except Exception:
            continue

    if not clicked_sort:
        print("[WARNING] Sort button not found.")
        return False

    time.sleep(1)

    newest_strategies = [
        (By.XPATH, '//div[@role="menuitemradio"][contains(.,"الأحدث") or contains(.,"Newest")]'),
        (By.XPATH, '//li[@role="menuitemradio"][contains(.,"الأحدث") or contains(.,"Newest")]'),
        (By.XPATH, '(//div[@role="menu"]//div[@role="menuitemradio"])[2]'),  # Newest is always 2nd
        (By.XPATH, '(//div[@data-index="1"])[1]'),
    ]
    for by, selector in newest_strategies:
        try:
            opt = short_wait.until(EC.element_to_be_clickable((by, selector)))
            opt.click()
            return True
        except Exception:
            continue

    print("[WARNING] Newest sort option not found.")
    return False


def find_reviews_container(driver):
    """Find the scrollable reviews container using stable selectors."""
    short_wait = WebDriverWait(driver, 8)
    strategies = [
        (By.XPATH, '//div[@role="main"]//div[contains(@aria-label, "Reviews") or contains(@aria-label, "المراجعات")]'),
        (By.XPATH, '//div[@tabindex="-1"][.//div[@data-review-id]]'),
        (By.XPATH, '//div[contains(@class,"m6QErb") and contains(@class,"DxyBCb")]'),
        (By.XPATH, '//div[@role="main"]//div[@tabindex="-1"]'),
    ]
    for by, selector in strategies:
        try:
            container = short_wait.until(EC.presence_of_element_located((by, selector)))
            is_scrollable = driver.execute_script(
                "return arguments[0].scrollHeight > arguments[0].clientHeight;",
                container
            )
            if is_scrollable:
                return container
        except Exception:
            continue
    return None


# ── Daily cutoff logic ────────────────────────────────────────────────────────

# Google Maps uses relative timestamps. Within 24 hours it shows:
#   "just now", "X minutes ago", "X hours ago"   ← KEEP
# Anything else (day ago, week ago, …)            ← STOP
KEEP_PATTERNS_EN = re.compile(
    r'just now|^\d+\s+minute|^\d+\s+hour', re.IGNORECASE
)
KEEP_PATTERNS_AR = re.compile(
    r'الآن|دقيقة|دقائق|ساعة|ساعات'   # now / minute(s) / hour(s)
)

def is_older_than_one_day(date_text: str) -> bool:
    """
    Return True (→ stop scraping) if the review is older than ~24 hours.
    Google Maps shows "just now / X minutes ago / X hours ago" for same-day
    reviews; anything else (a day ago, 2 days ago, a week ago …) is older.
    """
    text = date_text.strip()
    if KEEP_PATTERNS_EN.search(text):
        return False
    if KEEP_PATTERNS_AR.search(text):
        return False
    # Everything else (day, week, month, year / يوم, أيام, أسبوع …) → too old
    return True


# ── Review extraction  ─────────────────────────

def extract_review_cards(driver, seen_ids: set):
    """
    Extract review cards not yet seen, tracked by data-review-id.
    Returns (new_cards, hit_cutoff).
    """
    cards = []
    hit_cutoff = False

    review_elements = driver.find_elements(By.XPATH, '//div[@data-review-id]')

    for el in review_elements:
        try:
            review_id = el.get_attribute('data-review-id')
            if review_id in seen_ids:
                continue

            # ── Date ──────────────────────────────────────────────────────────
            date_text = None
            for cls in ['rsqaWe', 'xRkPPb', 'dehysf']:
                try:
                    date_text = el.find_element(
                        By.XPATH, f'.//span[contains(@class,"{cls}")]'
                    ).text.strip()
                    if date_text:
                        break
                except Exception:
                    continue

            if not date_text:
                continue

            if is_older_than_one_day(date_text):
                hit_cutoff = True
                break

            date_obj = relative_date_to_datetime(date_text)

            # ── Name ──────────────────────────────────────────────────────────
            name = ""
            name_strategies = [
                './/div[@class="d4r55"]',
                './/div[contains(@class,"d4r55")]',
                './/span[@class="lRVwie"]',
                './/div[@class="WNxzHc qLhwHc"]//div[@class="d4r55"]',
                './/a[contains(@href,"contrib")]',
            ]
            for xpath in name_strategies:
                try:
                    el_name = el.find_element(By.XPATH, xpath)
                    name = el_name.text.strip() or el_name.get_attribute("aria-label") or ""
                    if name:
                        break
                except Exception:
                    continue

            # ── Rating ────────────────────────────────────────────────────────
            rating = ""
            try:
                rating_el = el.find_element(By.XPATH, './/span[@role="img"]')
                rating = rating_el.get_attribute("aria-label") or ""
            except Exception:
                pass

            # ── Comment ───────────────────────────────────────────────────────
            comment = ""
            try:
                try:
                    more_btn = el.find_element(
                        By.XPATH,
                        './/button[contains(@aria-label,"See more") or contains(@aria-label,"مزيد")]'
                    )
                    driver.execute_script("arguments[0].click();", more_btn)
                    time.sleep(0.5)
                except Exception:
                    pass
                comment = el.find_element(
                    By.XPATH, './/span[@class="wiI7pd"]'
                ).text.strip()
            except Exception:
                try:
                    comment = el.find_element(
                        By.XPATH, './/div[contains(@class,"MyEned")]//span'
                    ).text.strip()
                except Exception:
                    pass

            # ── Owner Response ────────────────────────────────────────────────
            response = ""
            try:
                resp_div = el.find_element(By.XPATH, './/div[contains(@class,"CDe7pd")]')
                full_text = resp_div.text.strip()
                if full_text:
                    lines = [l.strip() for l in full_text.split('\n') if l.strip()]
                    skip_phrases = [
                        'رد من المالك', 'تمت الترجمة بواسطة', 'عرض النص الأصلي',
                        'Reply from owner', 'Translated by Google', 'See original',
                    ]
                    clean_lines = []
                    for line in lines:
                        if line == lines[0] and any(
                            kw in line for kw in ['قبل', 'ago', 'just now']
                        ):
                            continue
                        if any(phrase in line for phrase in skip_phrases):
                            continue
                        if line.strip() == '・':
                            continue
                        clean_lines.append(line)
                    response = '\n'.join(clean_lines).strip()
            except Exception:
                pass

            cards.append({
                'review_id': review_id,
                'name':      name,
                'rating':    rating,
                'comment':   comment,
                'date':      date_obj,
                'response':  response
            })
            seen_ids.add(review_id)

        except Exception:
            continue

    return cards, hit_cutoff


def scroll_and_extract(driver, reviews_container):
    """
    Scroll through reviews, deduplicating by data-review-id.
    Stops when daily cutoff hit or no new reviews load.
    """
    all_reviews = []
    seen_ids = set()
    previous_scroll_position = -1
    no_new_reviews_count = 0

    while True:
        cards, hit_cutoff = extract_review_cards(driver, seen_ids)
        all_reviews.extend(cards)

        if hit_cutoff:
            print(f"  [INFO] Reached 24-hour cutoff after {len(all_reviews)} reviews.")
            break

        if len(cards) == 0:
            no_new_reviews_count += 1
            if no_new_reviews_count >= 3:
                print(f"  [INFO] No new reviews after 3 scrolls. Total: {len(all_reviews)}")
                break
        else:
            no_new_reviews_count = 0

        driver.execute_script(
            'arguments[0].scrollTop = arguments[0].scrollHeight',
            reviews_container
        )
        time.sleep(2.5)

        current_scroll_position = driver.execute_script(
            'return arguments[0].scrollTop;', reviews_container
        )
        if current_scroll_position == previous_scroll_position:
            print(f"  [INFO] Reached bottom. Total: {len(all_reviews)}")
            break

        previous_scroll_position = current_scroll_position

    return all_reviews


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


# ── Branch processing with retry ───────────────────────────────────────────────

def process_branch(driver, wait, key, value, max_attempts=MAX_BRANCH_ATTEMPTS):
    """
    Same navigation as the original code (driver.get straight on the short
    link -- letting Chrome follow the redirect itself, exactly as before,
    since that always landed on the correct branch page). The only
    addition is: if the sign-in/consent overlay shows up and blocks the
    Sort step, dismiss it and reload/retry instead of skipping the branch
    outright.

    Returns (raw_reviews, blocked):
      - blocked=True  -> every attempt failed before ever reaching the
        scroll/extract step (Reviews button, Sort, or reviews container
        never became available). This is a genuine failure -- the caller
        may want to restart the whole browser and retry.
      - blocked=False -> we successfully reached and read the reviews
        container. raw_reviews may still be an empty list, but that's a
        legitimate "no reviews in the last 24 hours", not a failure.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            driver.get(value)

            try:
                wait.until(EC.presence_of_element_located((By.XPATH, '//div[@role="main"]')))
            except TimeoutException:
                time.sleep(2)
            time.sleep(1)

            # Clear any consent/sign-in overlay before touching the UI
            overlay_seen = dismiss_signin_or_consent_overlay(driver)
            if overlay_seen:
                time.sleep(1)

            # ── Find & click Reviews tab ──────────────────────────────────────
            reviews_button = find_reviews_button(driver)
            if reviews_button is None:
                print(f"  [DEBUG] Reviews button not found for: {key} (attempt {attempt}/{max_attempts})")
                if attempt < max_attempts:
                    time.sleep(3)
                    continue
                buttons = driver.find_elements(By.TAG_NAME, 'button')
                for btn in buttons:
                    label = btn.get_attribute('aria-label') or ''
                    text  = btn.text[:60] if btn.text else ''
                    if label or text:
                        print(f"    text='{text}' | aria-label='{label}'")
                return [], True

            reviews_button.click()
            time.sleep(0.3)

            # Overlay can also appear right after opening the Reviews tab
            overlay_seen = dismiss_signin_or_consent_overlay(driver)
            if overlay_seen:
                time.sleep(1)

            # ── Sort by Newest ────────────────────────────────────────────────
            sorted_ok = click_sort_newest(driver)
            if not sorted_ok:
                print(f"  [WARNING] Could not sort by newest for: {key} (attempt {attempt}/{max_attempts})")
                if attempt < max_attempts:
                    print(f"  [INFO] Reloading and retrying {key}...")
                    time.sleep(3)
                    continue
                return [], True
            time.sleep(2)

            # ── Find scrollable container ─────────────────────────────────────
            reviews_container = find_reviews_container(driver)
            if reviews_container is None:
                print(f"  [WARNING] Reviews container not found for: {key} (attempt {attempt}/{max_attempts})")
                if attempt < max_attempts:
                    time.sleep(3)
                    continue
                return [], True

            # ── Scroll & extract ──────────────────────────────────────────────
            raw_reviews = scroll_and_extract(driver, reviews_container)
            return raw_reviews, False

        except Exception as e:
            print(f"  [ERROR] {key} (attempt {attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                time.sleep(3)
                continue
            return [], True

    return [], True


# ── Main ──────────────────────────────────────────────────────────────────────

# Filter to EI branches only
ei_only = {k: v for k, v in list_of_links.items() if k.startswith("EI")}

driver = create_driver()
wait   = WebDriverWait(driver, 10)

all_reviews_rows  = []
no_negative_reviews = 0

# If the block shows up right on the FIRST branch of a run, that points at
# something wrong with this particular browser session/launch itself (rather
# than a per-branch fluke), since every later branch reuses the same Chrome
# instance. So the "quit and relaunch Chrome" recovery is only applied to the
# first branch: if that one comes through clean (after a restart if needed),
# the rest of the run keeps using that same browser exactly as before, with
# no restart logic on later branches. If it were genuinely IP/pattern-based
# blocking, no amount of restarting would fix it anyway -- this is purely to
# rule out a bad initial session.
MAX_FIRST_BRANCH_RESTARTS = 5

for i, (key, value) in enumerate(ei_only.items()):

    sleep_time = random.uniform(5, 15)
    print(f"Sleeping for {sleep_time:.2f} seconds...")
    time.sleep(sleep_time)

    print(f"\n[BRANCH] {key}")

    raw_reviews, blocked = process_branch(driver, wait, key, value)

    if i == 0:
        restarts_used = 0
        while blocked and restarts_used < MAX_FIRST_BRANCH_RESTARTS:
            restarts_used += 1
            print(f"  [INFO] First branch ({key}) still blocked after "
                  f"{MAX_BRANCH_ATTEMPTS} attempts -- restarting browser "
                  f"({restarts_used}/{MAX_FIRST_BRANCH_RESTARTS}) and retrying...")
            try:
                driver.quit()
            except Exception:
                pass
            driver = create_driver()
            wait   = WebDriverWait(driver, 10)
            time.sleep(3)
            raw_reviews, blocked = process_branch(driver, wait, key, value)

    if blocked:
        print(f"  [WARNING] {key} still blocked -- skipping.")
        continue

    if not raw_reviews:
        print(f"  [INFO] No reviews in last 24 hours for: {key}")
        continue

    reviews_df = pd.DataFrame(raw_reviews)
    reviews_df['branch_flag'] = key
    reviews_df = preprocess_reviews(reviews_df)

    all_reviews_rows.append(reviews_df)
    print(f"  [REVIEWS] Extracted {len(reviews_df)} reviews for: {key}")

# ── Quit driver ───────────────────────────────────────────────────────────────
driver.quit()

def send_simple_email(subject, body):

    msg = EmailMessage()

    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = ", ".join(receiver_email)

    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:

        smtp.login(sender_email, app_password)

        smtp.sendmail(
            sender_email,
            receiver_email,
            msg.as_string()
        )

    print("[EMAIL] Notification email sent.")

# ── Filter negatives & save ───────────────────────────────────────────────────
if not all_reviews_rows:
    no_negative_reviews = 1
    message = "No reviews extracted across all EI branches."
    print(f"\n{message}")
    send_simple_email(
        subject=f"Google Reviews Automation Update - {date_suffix}",
        body=message
    )


    ##print("\nNo reviews extracted across all EI branches.")
else:
    all_reviews = pd.concat(all_reviews_rows, ignore_index=True)
    final_df_reviews = all_reviews[all_reviews['label_rating'] < 4]

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

        # Translate Arabic → English
        try:
            print("Translating reviews...")
            final_df_reviews['User_comment_review'] = (
                final_df_reviews['User_comment_review'].apply(translate_to_english)
            )
            print("  Comment translation complete.")
            final_df_reviews['Response_to_review'] = (
                final_df_reviews['Response_to_review'].apply(translate_to_english)
            )
            print("  Response translation complete.")
        except Exception as e:
            print(f"  [ERROR] Translation failed: {e}")

        final_df_reviews = final_df_reviews.drop_duplicates()
        output_path = f"negative_reviews_enbd_bank_branches_{date_suffix}.xlsx"
        final_df_reviews.to_excel(output_path, index=False)
        print(f"[DONE] Negative reviews saved: {output_path}")

# Step 2 : Preparing GPT output

if no_negative_reviews == 0:
    try:
        response_text = generate_newsletter()
        raw = response_text.strip()
        # If the model wrapped output in ```json ... ``` fences, strip them
        if raw.startswith("```"):
            # Remove leading ```json or ``` and trailing ```
            raw = re.sub(r"^```[a-zA-Z]*\n", "", raw)   # remove ``` or ```json + newline
            raw = re.sub(r"\n```$", "", raw.strip())    # remove closing ```
        response_json = json.loads(raw)
        #yesterday = datetime.now() - timedelta(days=1)
        date_str = datetime.now().strftime("%d %B %Y")
        response_json['date_str'] = date_str
        print(response_json)
    except Exception as e:
        print(f"Unexpected error occurred during newsletter generation: {e}")

# Step 3 : Preparing Daily Negative Visual email

if no_negative_reviews == 0:
    img = generate_negative_reviews_daily_image(response_json)
    img.save(f"daily_negative_google_reviews_{date_suffix}.png")

# Step 4 : Sending email
if no_negative_reviews == 0:
    # === Generate CID for inline image ===
    image_cid = make_msgid(domain="enbd.ai")[1:-1]

    # Opening line of the email
    opening_line = (
        "Hello team,<br>"
        "Here is a summary of the negative Google reviews received in last 24 hours from all EI branches."
    )

    # Convert PNG to base64 string
    with open(f"daily_negative_google_reviews_{date_suffix}.png", "rb") as img_file:
        encoded_img = base64.b64encode(img_file.read()).decode("utf-8")

    # Embed directly into HTML as data URI
    html_newsletter = f"""
    <html>
      <body style="font-family: Arial, sans-serif; font-size: 14px; color: #333; margin:0; padding:0;">
          <!-- Intro Text -->
          <div style="padding: 20px; width: 80%; margin: 0 auto; text-align: left;">
              {opening_line}
          </div>

          <!-- Image (80% width, centered) -->
          <div style="text-align: center; margin: 0; padding: 0;">
              <img src="data:image/png;base64,{encoded_img}"
                    alt="Newsletter Visual"
                    style="width:80%; max-width:800px; height:auto; display:block; margin:0 auto;" />
          </div>
      </body>
    </html>
    """
    # Create the email
    msg = EmailMessage()
    msg["Subject"] = f"EI Branch Google Reviews - Negative Posts - {date_suffix}"
    msg["From"] = sender_email
    msg["To"] = ", ".join(receiver_email)
    msg.set_content(response_text)  # Fallback plain text
    msg.add_alternative(html_newsletter, subtype="html")  # HTML body

    # Send the email
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender_email, app_password)
        #smtp.send_message(msg)
        smtp.sendmail(
            sender_email,
            receiver_email,
            msg.as_string())


    print("Email sent successfully with embedded image!")

################################ End of Code #################################
