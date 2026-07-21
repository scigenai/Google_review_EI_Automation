import spacy
nlp = spacy.load("en_core_web_sm")

import undetected_chromedriver as uc

driver = uc.Chrome(version_main=147)

print("Everything working")

driver.quit()