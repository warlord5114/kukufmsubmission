"""Language handling for English, Hindi and Hinglish.

Hinglish is the case that matters and the one most systems get wrong. People
with everyday legal problems type "landlord ne deposit wapas nahi kiya" in
Roman script far more often than they type Devanagari. A naive script check
calls that English, replies in formal English, and loses the user.

Detection is deliberately cheap and transparent: Devanagari range for Hindi,
and a marker-word list for Romanised Hindi. No model call, so it is instant,
free and inspectable, and it degrades to English, which is always readable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

RE_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Common Hindi function words as people actually romanise them. Function words
# are used rather than nouns because they survive topic changes.
HINGLISH_MARKERS = {
    "nahi", "nahin", "nai", "hai", "hain", "tha", "thi", "the", "kiya", "kiye",
    "karna", "karne", "kar", "raha", "rahi", "rahe", "mera", "meri", "mere",
    "aap", "aapka", "aapke", "aapki", "mujhe", "mujhko", "humko", "hamein",
    "unhone", "usne", "uska", "uski", "unka", "kya", "kyun", "kyon", "kaise",
    "kab", "kahan", "kitna", "kitne", "kitni", "koi", "kuch", "sab", "bhi",
    "lekin", "par", "aur", "ya", "to", "toh", "phir", "abhi", "ab", "baad",
    "pehle", "wapas", "wapis", "dena", "diya", "milna", "mila", "mili",
    "chahiye", "sakta", "sakte", "sakti", "paisa", "paise", "rupay", "rupaye",
    "makaan", "malik", "maalik", "kiraya", "kiraye", "ghar", "naukri",
    "salary", "company", "saal", "mahina", "mahine", "din", "haan", "ji",
    "bola", "boli", "kehte", "kaha", "bataya", "jawab", "shikayat", "adalat",
    "vakeel", "kanoon", "kanooni", "galat", "sahi", "theek", "zyada", "kam",
}

# Words that look Hinglish but are ordinary English, so they must not count.
_FALSE_FRIENDS = {"the", "to", "par", "kar", "ab", "bhi", "so", "or", "and"}
_SCORED_MARKERS = HINGLISH_MARKERS - _FALSE_FRIENDS

LANG_NAMES = {"en": "English", "hi": "Hindi", "hinglish": "Hinglish"}


@dataclass
class Detection:
    lang: str           # en | hi | hinglish
    confidence: float   # 0-1
    reason: str

    @property
    def reply_lang(self) -> str:
        """Which rendering of KB text to use. Hinglish users read Hindi plain text."""
        return "hi" if self.lang in ("hi", "hinglish") else "en"


def detect(text: str) -> Detection:
    if not text or not text.strip():
        return Detection("en", 0.0, "empty input; defaulted to English")

    deva = len(RE_DEVANAGARI.findall(text))
    total_letters = len(re.findall(r"\w", text))
    if total_letters and deva / max(1, total_letters) > 0.20:
        return Detection("hi", min(1.0, deva / max(1, total_letters) * 2),
                         f"{deva} Devanagari characters")

    words = re.findall(r"[a-z']+", text.lower())
    if not words:
        return Detection("en", 0.2, "no alphabetic words; defaulted to English")

    hits = [w for w in words if w in _SCORED_MARKERS]
    ratio = len(hits) / len(words)
    if len(hits) >= 2 and ratio >= 0.12:
        return Detection(
            "hinglish", min(1.0, ratio * 3),
            f"{len(hits)} Romanised Hindi markers ({', '.join(sorted(set(hits))[:5])})",
        )
    return Detection("en", 1.0 - ratio, "no strong Hindi signal")


# --- fixed strings -----------------------------------------------------------
# Kept as data so the whole interface can change language in one place, and so
# nothing user-facing and legally consequential is phrased by the model.

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "disclaimer": (
            "This is general legal information, not legal advice, and this is not a "
            "lawyer. It covers three problems only and can be wrong. Check anything "
            "important with a real lawyer before you act on it — free legal aid is "
            "available on **15100**."
        ),
        "greeting": (
            "Tell me what happened, in your own words. You can write in English, "
            "Hindi or a mix — whatever is easiest. There is no wrong way to start."
        ),
        "which_problem": "Which of these is closest to your problem?",
        "deadline_header": "Your deadline",
        "expired": "On the dates you gave, the deadline looks like it has already passed.",
        "days_left": "You have about {days} days left.",
        "urgent": "That is soon. Treat this as urgent.",
        "forum_header": "Where to go",
        "evidence_header": "What to gather",
        "next_header": "What to do next",
        "see_lawyer": "You should speak to a real lawyer",
        "free_aid": (
            "Free legal aid: call **15100** (NALSA), or walk into the District Legal "
            "Services Authority at your district court. Both are free."
        ),
        "not_covered": (
            "This assistant has only been built and checked for three problems: a "
            "security deposit a landlord will not return, unpaid salary, and a refund "
            "for goods or services. Yours looks like something else, so anything I "
            "said about it would be unverified."
        ),
    },
    # Devanagari, for someone who wrote in Devanagari. Replying to देवनागरी in
    # Roman script is the small discourtesy that tells a user a tool was not
    # built for them, so Hindi and Hinglish get separate tables rather than
    # sharing one romanised set.
    "hi": {
        "disclaimer": (
            "यह सामान्य कानूनी जानकारी है, कानूनी सलाह नहीं, और मैं वकील नहीं हूँ। "
            "यह केवल तीन समस्याओं के लिए बनाया गया है और गलत हो सकता है। कोई भी "
            "ज़रूरी कदम उठाने से पहले असली वकील से पूछें — मुफ़्त कानूनी मदद "
            "**15100** पर मिलती है।"
        ),
        "greeting": (
            "आपके साथ क्या हुआ, अपने शब्दों में बताइए। आप हिंदी, अंग्रेज़ी या दोनों "
            "मिलाकर लिख सकते हैं — जो आसान लगे। शुरू करने का कोई गलत तरीका नहीं है।"
        ),
        "which_problem": "इनमें से आपकी समस्या किससे मिलती है?",
        "deadline_header": "आपकी समय-सीमा",
        "expired": "आपने जो तारीखें बताई हैं, उनके हिसाब से समय-सीमा निकल चुकी लगती है।",
        "days_left": "आपके पास लगभग {days} दिन बचे हैं।",
        "urgent": "यह बहुत जल्दी है। इसे ज़रूरी समझें।",
        "forum_header": "कहाँ जाना है",
        "evidence_header": "क्या सबूत जुटाना है",
        "next_header": "अब क्या करना है",
        "see_lawyer": "आपको असली वकील से बात करनी चाहिए",
        "free_aid": (
            "मुफ़्त कानूनी मदद: **15100** (NALSA) पर कॉल करें, या अपने ज़िला कोर्ट "
            "में District Legal Services Authority जाएँ। दोनों मुफ़्त हैं।"
        ),
        "not_covered": (
            "यह सहायक केवल तीन समस्याओं के लिए बनाया और जाँचा गया है: सिक्योरिटी "
            "डिपॉज़िट वापस न मिलना, सैलरी न मिलना, और सामान या सेवा का रिफंड। आपकी "
            "बात इनसे अलग लगती है, इसलिए इस पर कुछ कहना बिना जाँचा होगा।"
        ),
    },
    # Romanised Hindi, for someone who typed Romanised Hindi.
    "hinglish": {
        "disclaimer": (
            "Yeh aam kanooni jaankari hai, kanooni salah nahi, aur main vakeel nahi "
            "hoon. Sirf teen samasyaon ke liye bana hai aur galat ho sakta hai. Kuch "
            "bhi zaroori kaam karne se pehle asli vakeel se poochhein — muft kanooni "
            "madad **15100** par milti hai."
        ),
        "greeting": (
            "Aapke saath kya hua, apne shabdon mein bataiye. English, Hindi ya dono "
            "mila kar — jaise aasan lage. Shuru karne ka koi galat tareeka nahi hai."
        ),
        "which_problem": "In mein se aapki samasya kis se milti hai?",
        "deadline_header": "Aapki samay-seema",
        "expired": "Aapne jo tareekhein batayi hain, uske hisaab se samay-seema nikal chuki lagti hai.",
        "days_left": "Aapke paas lagbhag {days} din bache hain.",
        "urgent": "Yeh bahut jaldi hai. Ise zaroori samjhein.",
        "forum_header": "Kahan jaana hai",
        "evidence_header": "Kya sabooot jutaana hai",
        "next_header": "Ab kya karna hai",
        "see_lawyer": "Aapko asli vakeel se baat karni chahiye",
        "free_aid": (
            "Muft kanooni madad: **15100** (NALSA) par call karein, ya apne district "
            "court mein District Legal Services Authority jaayein. Dono muft hain."
        ),
        "not_covered": (
            "Yeh assistant sirf teen samasyaon ke liye bana aur jaancha gaya hai: "
            "security deposit wapas na milna, salary na milna, aur saamaan ya service "
            "ka refund. Aapki baat inse alag lagti hai, isliye is par kuch kehna "
            "bina jaanche hoga."
        ),
    },
}


def t(key: str, lang: str = "en", **fmt) -> str:
    table = STRINGS.get(lang) or STRINGS["en"]
    s = table.get(key) or STRINGS["en"].get(key, key)
    return s.format(**fmt) if fmt else s
