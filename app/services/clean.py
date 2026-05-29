"""
clean.py — AMR Warehouse Data Cleaning
=======================================
Stateless transformation functions applied to the raw list-of-dicts produced
by ``conn.fetch_warehouse_data()``.

All cleaning is done **in Python** after a single DB fetch so the warehouse
connection is held as briefly as possible and the same data can be reused by
multiple downstream steps (transform, analysis) without extra round-trips.

Individual cleaners (all pure functions, each takes a row dict and returns a
modified copy):

    clean_gender(row)
    build_specimen_date(row)
    flag_specimen_date(row, specimen_date)
    resolve_facility_name(row)
    resolve_location(row)
    resolve_specimen(row)
    resolve_organism(row)
"""

import logging
import re
from copy import deepcopy
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Facility code → human-readable name
# ---------------------------------------------------------------------------
FACILITY_MAP: dict[str, str] = {
    "15828": "Bungoma County Referral Hospital",
    "15834": "Busia County Referral Hospital",
    "11289": "Coast General Teaching and Referral Hospital",
    "12004": "Embu Level 5 Hospital",
    "13939": "Jaramogi Oginga Odinga Teaching & Referral Hospital",
    "15915": "Kakamega County General Hospital",
    "13023": "Kenyatta National Hospital",
    "10539": "Kiambu County Referal Hospital",
    "13703": "Kisii Teaching and Referral Hospital",
    "14947": "Kitale County Referral Hospital",
    "12438": "Machakos County Referral Hospital",
    "12457": "Makueni County Referral Hospital",
    "19133": "Mater Misericordiae Hospital",
    "18323": "Mbagathi Hospital",
    "15204": "Moi Teaching Refferal Hospital",
    "11934": "MP Shah Hospital",
    "10777": "Murang'a County Refferal Hospital",
    "15288": "Nakuru County Referral & Teaching Hospital",
    "10903": "Nyeri County Referral Hospital",
    "19847": "The Nairobi Hospital",
    "11094": "Thika Level 5 Hospital",
    "13080": "Mbagathi District Hospital",
    "11555": "Malindi District Hospital",
    "24979": "Kenyatta University Teaching Refferal and Research Hospital",
    "15077": "Longisa County Referral  Hospital",
    "19543": "The Mater Hospital",
    "13098": "MP Shah Hospital",
    "25582" : "The Nairobi Hospital(Capital Centre)",
    "99999": "LIMS TEST FACILITY",
    "13240": "Umoja Health Centre",
    "12345": "Kiseuni Dispensary",
    "25412": "Ammusait General Hospital",
    "13359": "Hagadera Hospital", 
}

# ---------------------------------------------------------------------------
# Facility name → county
# ---------------------------------------------------------------------------
FACILITY_COUNTY_MAP: dict[str, str] = {
    "Memorial Hospital":                                "Nairobi",
    "Kombewa District Hospital":                        "Kisumu",
    "Busia District Hospital":                          "Busia",
    "Kenyatta National Hospital":                       "Nairobi",
    "Nyeri CRH":                                        "Nyeri",
    "Nyeri County Referral Hospital":                   "Nyeri",
    "JOOTRH":                                           "Kisumu",
    "Jaramogi Oginga Odinga Teaching & Referral Hospital": "Kisumu",
    "Malindi SCH":                                      "Kilifi",
    "Malindi District Hospital":                        "Kilifi",
    "Kitale CRH":                                       "Trans Nzoia",
    "Kitale County Referral Hospital":                  "Trans Nzoia",
    "Nakuru CRH":                                       "Nakuru",
    "Nakuru County Referral & Teaching Hospital":       "Nakuru",
    "Bungoma CRH":                                      "Bungoma",
    "Bungoma County Referral Hospital":                 "Bungoma",
    "KEMRI":                                            "Nairobi",
    "MP Shah Hospital":                                 "Nairobi",
    "Mbagathi Hospital":                                "Nairobi",
    "Mbagathi District Hospital":                       "Nairobi",
    "Mater Hospital":                                   "Nairobi",
    "Mater Misericordiae Hospital":                     "Nairobi",
    "Kisumu District Hospital":                         "Kisumu",
    "Moi Air Base Hospital":                            "Nairobi",
    "Kakamega Provincial General Hospital":             "Kakamega",
    "Kakamega County General Hospital":                 "Kakamega",
    "Kijabe (AIC) Hospital":                            "Kiambu",
    "Thika Level 5 Hospital":                           "Kiambu",
    "CGTRH":                                            "Mombasa",
    "Coast General Teaching and Referral Hospital":     "Mombasa",
    "Machakos Level 5 Hospital":                        "Machakos",
    "Machakos County Referral Hospital":                "Machakos",
    "Murang'a CRH":                                     "Muranga",
    "Murang'a County Refferal Hospital":                "Muranga",
    "Kisii Hospital":                                   "Kisii",
    "Kisii Teaching and Referral Hospital":             "Kisii",
    "Nyanza Provincial General Hospital":               "Nyanza",
    "Makueni CRH":                                      "Makueni",
    "Makueni County Referral Hospital":                 "Makueni",
    "KUTRRH":                                           "Nairobi",
    "Kenyatta University Teaching Refferal and Research Hospital" : "Nairobi",
    "MTRH":                                             "Uasin Gishu",
    "Moi Teaching Refferal Hospital":                   "Uasin Gishu",
    "The Nairobi Hospital":                             "Nairobi",
    # "Embu Level 5 Hospital":                            "Embu",
    "Kiambu County Referal Hospital":                   "Kiambu",
    "Busia County Referral Hospital":                   "Busia",
    "The Mater Hospital" :                               "Nairobi",
    
}

# ---------------------------------------------------------------------------
# Specimen lookup: raw value (lowercased, stripped) → canonical name
# ---------------------------------------------------------------------------

# Full-text matching lookup (lowercased input → canonical name)
SPECIMEN_TEXT_MAP: dict[str, str] = {
    # Pus
    "p": "Pus", "ps": "Pus", "pus": "Pus",
    # CSF
    "c": "Cerebrospinal Fluid", "cs": "Cerebrospinal Fluid", "sf": "Cerebrospinal Fluid",
    "csf": "Cerebrospinal Fluid", "celebrospinal fluid": "Cerebrospinal Fluid",
    "cerebrospinal fluid": "Cerebrospinal Fluid", "cerebral spinal fluid": "Cerebrospinal Fluid",
    "cfs": "Cerebrospinal Fluid", "cst": "Cerebrospinal Fluid",
    # Urine
    "ur": "Urine", "ue": "Urine", "urne": "Urine", "urnie": "Urine", "urine": "Urine",
    "urinary tract": "Urine", "u": "Urine", "un": "Urine",
    # Blood
    "bl": "Blood", "bllod aspirate": "Blood", "blood culture": "Blood",
    "blood_b": "Blood", "blood": "Blood", "b": "Blood", "blod": "Blood", "bloos": "Blood",
    # Pus Aspirate
    "pus_aspira": "Pus Aspirate", "pus aspirate": "Pus Aspirate",
    "pu aspirate": "Pus Aspirate", "abcsess": "Pus Aspirate", "abscess": "Pus Aspirate",
    "pus/tissue": "Pus Aspirate", "pus abscess": "Pus Aspirate",
    # Pus/Wound Swab
    "pus awab": "Pus/Wound Swab", "pus_swab": "Pus/Wound Swab", "pus swab": "Pus/Wound Swab",
    " pus awab": "Pus/Wound Swab", "pu swab": "Pus/Wound Swab",
    "wound/skin swab": "Pus/Wound Swab", "wound swab": "Pus/Wound Swab",
    "pus  swab": "Pus/Wound Swab", "pus;wound/skin swab": "Pus/Wound Swab",
    "puis swab": "Pus/Wound Swab", "wound": "Pus/Wound Swab",
    "wound_swab": "Pus/Wound Swab", "wd": "Pus/Wound Swab", "p/swab": "Pus/Wound Swab",
    "pus/wound swab": "Pus/Wound Swab", "pus tissue": "Pus/Wound Swab",
    "puw swab": "Pus/Wound Swab", "wound tissue": "Pus/Wound Swab",
    # Ascitic Fluid
    "ascitic": "Ascitic Fluid", "ascitic fluid": "Ascitic Fluid",
    "perito_fl": "Ascitic Fluid", "peritonial fluid": "Ascitic Fluid",
    "peritoneal fluid": "Ascitic Fluid", "ascitic_f": "Ascitic Fluid",
    "abdominal fluid": "Ascitic Fluid", "ascitis fluid": "Ascitic Fluid",
    "ab": "Ascitic Fluid", "abdominal cyst fluid": "Ascitic Fluid",
    # Stool
    "st": "Stool", "s": "Stool", "stool": "Stool",
    # Sputum
    "sp": "Sputum", "sputum": "Sputum", "sputim": "Sputum",
    # Rectal Swab
    "rectal swab": "Rectal Swab", "rectal": "Rectal Swab",
    # Swab
    "swab": "Swab", "sb": "Swab",
    # CVC Tip
    "cvc tip": "Cvc Tip", "cvc": "Cvc Tip", "central line": "Cvc Tip", "cvc line": "Cvc Tip",
    # Ear Swab
    "ear swab": "Ear Swab", "ear discharge": "Ear Swab", "swab_ear": "Ear Swab",
    "swab_eye": "Ear Swab", "ear_swab": "Ear Swab", "ear": "Ear Swab", "eye_swab": "Ear Swab",
    # HVS
    "hvs swab": "HVS", "hvs": "HVS", "high vaginal swab": "HVS", "vaginal swab": "HVS",
    "vaginal tissue": "HVS", "higvaginal swab": "HVS", "virginal swab": "HVS",
    "high vagina/": "HVS", "high vaginal": "HVS", "high vaginal sw": "HVS",
    "gn": "HVS", "h.v.s": "HVS", "va": "HVS",
    # Cervical Swab
    "cervix": "Cervical Swab", "cervical_s": "Cervical Swab",
    # Joint Fluid
    "joint fluid": "Joint Fluid", "synovial fluid": "Joint Fluid",
    "synovial": "Joint Fluid", "knee aspirate": "Joint Fluid",
    # Pleural Fluid
    "pleural fluid": "Pleural Fluid", "pleural tap": "Pleural Fluid",
    "pleural": "Pleural Fluid", "pleural aspirate": "Pleural Fluid",
    "pleural effusion": "Pleural Fluid", "pleural effussion": "Pleural Fluid",
    # Throat Swab
    "throat swab": "Throat Swab", "throat": "Throat Swab", "throat_swa": "Throat Swab",
    # Tissue
    "tissue": "Tissue", "bone": "Tissue", "knee_joint": "Tissue",
    "ti": "Tissue", "soft tissue": "Tissue", "bone tissue": "Tissue", "bone/tissue": "Tissue",
    # Urethral Swab
    "swab_ureth": "Urethral Swab", "urethral swab": "Urethral Swab",
    "urethral": "Urethral Swab", "urethra": "Urethral Swab",
    # Tracheal Aspirate
    "tracheal_aspirate": "Tracheal Aspirate", "trachael_a": "Tracheal Aspirate",
    "tracheal aspirate": "Tracheal Aspirate", "trachealaspirate": "Tracheal Aspirate",
    "t/asp": "Tracheal Aspirate", "t/asp.": "Tracheal Aspirate", "trachel": "Tracheal Aspirate",
    "t/aspirate": "Tracheal Aspirate", "tachael aspirate": "Tracheal Aspirate",
    "tracheal asp": "Tracheal Aspirate", "t/as": "Tracheal Aspirate",
    "tracheal": "Tracheal Aspirate", "t/a": "Tracheal Aspirate",
    "trachael aspirate": "Tracheal Aspirate", "t.asp": "Tracheal Aspirate",
    # BAL
    "ba": "BAL", "br": "BAL", "bronchial_aspirate": "BAL", "bal": "BAL",
    "bronchial  lavage": "BAL", "broncho-alveolar lavage": "BAL", "bronch_asp": "BAL",
    "bronchial": "BAL", "brochial wash": "BAL", "bronchoalveolar lavage": "BAL",
}

# Short 2-char code lookup (fallback after text map fails)
SPECIMEN_CODE_MAP: dict[str, str] = {
    "no": "Nose", "th": "Throat", "sp": "Sputum", "br": "Bronchial", "ea": "Ear",
    "ey": "Eyes", "mo": "Mouth", "sk": "Skin", "jt": "Joint",
    "uc": "Urine, catheter", "ur": "Urine", "bl": "Blood", "sf": "Cerebrospinal Fluid",
    "lu": "Lung", "ab": "Abdominal Fluid", "ga": "Gastric Fluid", "dr": "Drainage",
    "fl": "Fluid", "bi": "Bile", "wd": "Wound", "sw": "Wound, Surgical",
    "as": "Abscess", "ps": "Pus", "am": "Amniotic Fluid", "tr": "Tracheal",
    "si": "Sinus", "sb": "Swab", "at": "Aspirate", "fn": "Aspirate, Fine-Needle",
    "og": "Organ", "cx": "Cervix", "um": "Umbilical Cord", "va": "Vagina",
    "bx": "Biopsy", "ti": "Tissue", "bn": "Brain", "he": "Heart",
    "hv": "Heart Valve", "li": "Liver", "st": "Stool", "re": "Rectal",
    "mu": "Muscle", "ha": "Hand", "ar": "Arm", "ax": "Axilla",
    "co": "Conjunctiva", "em": "Ear, Middle", "eo": "Ear, Outer", "fi": "Fistula",
    "gn": "Genital", "gm": "Genital, Male", "gf": "Genital, Female", "pl": "Placenta",
    "bt": "Breast", "mi": "Breast Milk", "sm": "Semen", "ue": "Urethra",
    "cv": "Urine, Clean-Voided", "se": "Secretion", "bo": "Bone", "bm": "Bone Marrow",
    "ba": "BAL", "pf": "Pleural Fluid", "rl": "Respiratory, Lower",
    "ru": "Respiratory, Upper", "ta": "Tracheal Aspirate", "ux": "Ulcer, External",
    "ui": "Ulcer, Internal", "sc": "Screening", "mr": "Screen for MRSA",
    "vr": "Screen for VRE", "rs": "Research", "la": "Laboratory",
    "ex": "Quality Control, External", "rp": "Respiratory", "ul": "Ulcer",
    "ub": "Urine, Bladder", "uk": "Urine, Kidney", "ua": "Urine, Suprapubic Aspirate",
    "ki": "Kidney", "ut": "Uterus", "du": "Duodenum", "me": "Mediastinum",
    "bu": "Bursa", "cy": "Cyst", "ln": "Lymph Node", "gl": "Gland", "ot": "Other",
    "ca": "Catheter", "di": "Dialysis Fluid", "en": "Environmental", "pr": "Prosthesis",
    "cc": "Catheter, Central", "wa": "Water", "fo": "Food", "un": "Unknown",
    "qc": "Quality Control", "hr": "Hair", "nl": "Nail", "np": "Nasopharynx",
    "ft": "Foot", "au": "Autopsy", "fb": "Foreign Body", "mc": "Meconium",
    "iu": "Intra-Uterine Device", "fv": "Urine, First-Voided", "us": "Umbilicus",
    "is": "Isolate", "ad": "Abscess, Abdominal", "bs": "Burns",
    "cp": "Catheter, Permanent", "cr": "Cornea", "ee": "Empyema",
    "ep": "Empyema, Pleural", "es": "Empyema, Subdural", "hp": "Heart Valve, Prosthetic",
    "mb": "Mini-BAL", "pm": "Pacemaker", "pn": "Pancreas", "pd": "Pericardial Fluid",
    "cs": "Catheter Site", "pb": "Prosthesis, Bone", "sh": "Prosthesis, Shunt",
    "pv": "Prosthesis, Vascular", "pc": "Protected Brush Catheter", "tn": "Tonsils",
    "ud": "Ulcer, Decubitis", "uz": "Urine, Non-Catheterized",
    "uo": "Urine, Nephrostomy", "uu": "Urine, Unknown Source",
    "pt": "Abscess, Peritonsillar", "de": "Abscess, Dental",
    "ac": "Abscess, Perirectal", "ak": "Abscess, Skin", "ap": "Appendix",
    "ch": "Catheter, Peripheral", "cm": "Culture Medium",
    "cb": "Cyst, Bartholin's", "dn": "Drain", "ec": "Endocardium",
    "ed": "Epididymis", "eg": "Epiglottis", "eh": "Esophagus",
    "fa": "Fallopian Tube", "fu": "Furuncle", "gb": "Gallbladder",
    "hi": "Hip", "hf": "Hip Fluid", "it": "Insect", "jf": "Joint Fluid",
    "kn": "Knee", "kf": "Knee Fluid", "lx": "Larynx", "lp": "Lip",
    "lo": "Lochia", "ov": "Ovary", "pe": "Penis", "pg": "Penile Discharge",
    "pi": "Perineum", "pa": "Plasma", "po": "Prostate", "pu": "Prostatic Fluid",
    "rc": "Rectouterine Pouch", "tp": "Scotch Tape Test", "sr": "Serum",
    "su": "Shunt Fluid", "sl": "Spleen", "sa": "Sputum for AFB",
    "in": "Sputum, Induced", "sg": "String Test", "te": "Testes", "to": "Tooth",
    "wo": "Worm", "wt": "Wound, Sternal", "an": "Abdomen", "bv": "Blood Vessel",
    "hd": "Head", "sv": "Salivary Gland", "lg": "Leg", "nk": "Neck",
    "nv": "Nerve", "lv": "Pelvis", "cd": "Screen for C. Difficile",
    "gr": "Groin", "ig": "Inguinal", "cu": "Catheter, Umbilical",
    "fd": "Foot, Diabetic",
}

# Dates before this are flagged as suspect
SPECIMEN_DATE_MIN = date(2019, 1, 1)



# Lowercase raw value → canonical organism name
ORGANISM_TEXT_MAP: dict[str, str] = {
    # No growth / negative results
    "x": "No growth", "xx": "No growth", "xxx": "No growth",
    "xxxx": "No growth", "xxxxx": "No growth", "ng": "No growth",
    "no growth": "No growth", "nil growth": "No growth",
    # No significant growth
    "xsg": "No significant growth found",
    "no significant growth": "No significant growth found",
    "no significant growth found": "No significant growth found",
    # No organism isolated
    "no organism isolated": "No organism isolated",
    "noi": "No organism isolated",
    # Contaminated
    "con": "Contaminated specimen", "contaminated": "Contaminated specimen",
    "contaminated specimen": "Contaminated specimen",
    # Unknown / Other
    "unk": "Unknown", "unknown": "Unknown",
    "oth": "Other", "other": "Other",
    # Shortcodes
    "kl": "Klebsiella",
    # Candida species
    "c.tropicalis": "Candida tropicalis",
    "c.glabrata": "Candida glabrata",
    "c.ciferrii": "Candida ciferrii",
    "c.lusitaniae": "Candida lusitaniae",
    "c.albicans": "Candida albicans",
    "c.krusei": "Candida krusei",
    "c.parapsilosis": "Candida parapsilosis",
    # Staphylococcus
    "staph.aureus": "Staphylococcus aureus",
    "s.aureus": "Staphylococcus aureus",
    "staph aureus": "Staphylococcus aureus",
    "staphylococcus aureus": "Staphylococcus aureus",
    "mrsa": "Staphylococcus aureus (MRSA)",
    "mssa": "Staphylococcus aureus (MSSA)",
    "coagulase negative staph": "Coagulase negative Staphylococcus",
    "cons": "Coagulase negative Staphylococcus",
    "coagulase neg staph": "Coagulase negative Staphylococcus",
    # Streptococcus
    "strep pneumoniae": "Streptococcus pneumoniae",
    "s.pneumoniae": "Streptococcus pneumoniae",
    "streptococcus pneumoniae": "Streptococcus pneumoniae",
    "strep pyogenes": "Streptococcus pyogenes",
    # E. coli
    "e.coli": "Escherichia coli",
    "e. coli": "Escherichia coli",
    "escherichia coli": "Escherichia coli",
    "ec": "Escherichia coli",
    # Klebsiella
    "klebsiella": "Klebsiella pneumoniae",
    "klebsiella pneumoniae": "Klebsiella pneumoniae",
    "k.pneumoniae": "Klebsiella pneumoniae",
    # Pseudomonas
    "pseudomonas": "Pseudomonas aeruginosa",
    "pseudomonas aeruginosa": "Pseudomonas aeruginosa",
    "p.aeruginosa": "Pseudomonas aeruginosa",
    # Acinetobacter
    "acinetobacter": "Acinetobacter baumannii",
    "acinetobacter baumannii": "Acinetobacter baumannii",
    # Enterococcus
    "enterococcus": "Enterococcus faecalis",
    "enterococcus faecalis": "Enterococcus faecalis",
    "enterococcus faecium": "Enterococcus faecium",
    # Salmonella
    "salmonella": "Salmonella species",
    "salmonella typhi": "Salmonella typhi",
    # Proteus
    "proteus": "Proteus mirabilis",
    "proteus mirabilis": "Proteus mirabilis",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_blank(value: Any) -> bool:
    """Return True if value is None, NaN, empty string, or whitespace-only."""
    if value is None:
        return True
    if isinstance(value, float):
        import math
        return math.isnan(value)
    return str(value).strip() == ""


def _to_date(value: Any) -> date | None:
    """
    Coerce a value to a ``date`` object.

    Accepts ``datetime``, ``date``, pandas ``Timestamp``, or ISO-format strings.
    Returns ``None`` if conversion fails or value is pandas NaT.

    Args:
        value: Raw date-like value from DB row.

    Returns:
        date | None
    """
    if value is None:
        return None
    # Reject pandas NaT (truthy check raises, pd.isna is safest)
    try:
        import pandas as pd
        if pd.isna(value):
            return None
    except (ImportError, TypeError, ValueError):
        pass
    try:
        # pandas Timestamp / datetime
        if hasattr(value, "date"):
            d = value.date() if callable(value.date) else value.date
            # Ensure we got a real date, not another NaT-like
            _ = d.year
            return d
        if isinstance(value, date):
            return value
        # string fallback
        s = str(value).strip()
        for fmt in ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                    "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    except Exception:
        pass
    return None


def _is_realistic_date(d: date | None) -> bool:
    """
    Return True if the date is plausible for specimen collection/testing.

    A date is unrealistic if it is before year 2018 or after today.

    Args:
        d: date to check.

    Returns:
        bool
    """
    if d is None:
        return False
    try:
        today = date.today()
        # Guard against pandas NaT or other non-standard date objects
        return date(2018, 1, 1) <= d <= today
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Gender
# ---------------------------------------------------------------------------

def clean_gender(row: dict) -> dict:
    """
    Normalise the GENDER field.

    Mapping:
        "m" | "male"   → "Male"
        "f" | "female" → "Female"
        anything else  → "Unknown"

    Args:
        row (dict): Raw warehouse row.

    Returns:
        dict: Row with ``gender`` key set.
    """
    raw = str(row.get("GENDER", "") or "").strip().lower()
    if raw in ("m", "male"):
        gender = "Male"
    elif raw in ("f", "female"):
        gender = "Female"
    else:
        gender = "Unknown"
    row["gender"] = gender
    return row

def build_specimen_date(row: dict) -> dict:
    """
    Derive ``specimen_date`` (YYYY-MM-DD string) from date fields in priority order.

    Priority:
        1. COLLECTION_DATE  — if present and realistic
        2. DATE_TESTED      — if present and realistic
        3. TIME_POSTED      — if present and realistic
        4. Empty string     — no usable date found

    "Realistic" means between 2018-01-01 and today.  Dates like 1900-01-01 or
    2050-01-01 are rejected and the next candidate is tried.

    Strips the time component so the output is always ``"DD/MM/YYYY"`` or ``""``.

    Flags:
        ``specimen_date_flag``  —  True if the resolved date is before
        2019-01-01 or after today; False otherwise.  Empty string dates are
        left without a flag (flag = False).

    Args:
        row (dict): Raw warehouse row (may contain COLLECTION_DATE, DATE_TESTED,
                    TIME_POSTED as datetime / Timestamp / string / None).

    Returns:
        dict: Row with ``specimen_date`` and ``specimen_date_flag`` added.
    """
    for field in ("COLLECTION_DATE", "DATE_TESTED", "TIME_POSTED"):
        raw = row.get(field)
        if _is_blank(raw):
            continue
        d = _to_date(raw)
        if _is_realistic_date(d):
            row["specimen_date"] = d.strftime("%d/%m/%Y")
            # Flag if outside accepted epidemiological window
            today = date.today()
            row["specimen_date_flag"] = d < SPECIMEN_DATE_MIN or d > today
            return row

    # No usable date found
    row["specimen_date"] = ""
    row["specimen_date_flag"] = False
    return row


# ---------------------------------------------------------------------------
# Facility Name
# ---------------------------------------------------------------------------

def resolve_facility_name(row: dict) -> dict:
    """
    Map FACILITY_NAME code to human-readable hospital name.

    Rules:
        - If code exists in FACILITY_MAP → replace with name.
        - If code is not in the map      → keep the raw code as-is.
        - If blank                        → leave blank.

    Args:
        row (dict): Raw warehouse row.

    Returns:
        dict: Row with ``facility_name`` key set.
    """
    raw = row.get("FACILITY_NAME")
    if _is_blank(raw):
        row["facility_name"] = ""
        return row
    code = str(raw).strip()
    row["facility_name"] = FACILITY_MAP.get(code, code)
    return row


# ---------------------------------------------------------------------------
# Location / County
# ---------------------------------------------------------------------------

def resolve_location(row: dict) -> dict:
    """
    Derive ``county`` from the resolved ``facility_name``.

    Must be called **after** ``resolve_facility_name``.

    Args:
        row (dict): Row that already has ``facility_name`` set.

    Returns:
        dict: Row with ``county`` key set (empty string if not mappable).
    """
    name = row.get("facility_name", "")
    row["county"] = FACILITY_COUNTY_MAP.get(name, "")
    return row


# ---------------------------------------------------------------------------
# Specimen
# ---------------------------------------------------------------------------

def resolve_specimen(row: dict) -> dict:
    """
    Derive a clean ``specimen`` value from multiple source columns.

    Source priority:
        1. SPECIMEN_SOURCE_CDW  (CDW-cleaned value — most reliable)
        2. SPECIMEN_SOURCE
        3. C_ANALYSIS_NAME

    Matching strategy:
        1. Lowercase + strip the raw value.
        2. Try full-text match in SPECIMEN_TEXT_MAP.
        3. Try 2-char code match in SPECIMEN_CODE_MAP.
        4. Fall back to "Others".

    If all sources are blank → ``specimen`` is set to ``""`` (no value flagged).

    Args:
        row (dict): Raw warehouse row.

    Returns:
        dict: Row with ``specimen`` key set.
    """
    for field in ("SPECIMEN_SOURCE_CDW", " SPECIMEN_SOURCE_CDW",
                  "SPECIMEN_SOURCE", "C_ANALYSIS_NAME"):
        raw = row.get(field)
        if _is_blank(raw):
            continue
        norm = str(raw).strip().lower()
        # Full-text lookup
        if norm in SPECIMEN_TEXT_MAP:
            row["specimen"] = SPECIMEN_TEXT_MAP[norm]
            return row
        # 2-char code lookup
        if len(norm) == 2 and norm in SPECIMEN_CODE_MAP:
            row["specimen"] = SPECIMEN_CODE_MAP[norm]
            return row
        # Partial match attempt (strip underscores/hyphens)
        plain = re.sub(r"[_\-]", " ", norm).strip()
        if plain in SPECIMEN_TEXT_MAP:
            row["specimen"] = SPECIMEN_TEXT_MAP[plain]
            return row
        # Non-empty but unrecognised → Others
        row["specimen"] = "Others"
        return row

    row["specimen"] = ""
    return row


# ---------------------------------------------------------------------------
# Organism / Pathogen
# ---------------------------------------------------------------------------

# def resolve_organism(row: dict) -> dict:
#     """
#     Derive ``organism`` from ORGANISM_ISOLATED_CDW (preferred) or ORGANISM_ISOLATED.

#     Rules:
#         - ORGANISM_ISOLATED_CDW is used first — it contains cleaned values.
#         - ORGANISM_ISOLATED is used only as fallback but is **not** cleaned —
#           it is taken as-is to avoid masking dirty data.
#         - Blank in both → ``organism`` is ``""``; ``organism_flag`` is True.

#     Args:
#         row (dict): Raw warehouse row.

#     Returns:
#         dict: Row with ``organism`` and ``organism_flag`` keys set.
#     """
#     cdw = row.get("ORGANISM_ISOLATED_CDW")
#     raw = row.get("ORGANISM_ISOLATED")

#     if not _is_blank(cdw):
#         row["organism"] = str(cdw).strip()
#         row["organism_flag"] = False
#         return row

#     if not _is_blank(raw):
#         row["organism"] = str(raw).strip()
#         row["organism_flag"] = False
#         return row

#     row["organism"] = ""
#     row["organism_flag"] = True
#     return row


def resolve_organism(row: dict) -> dict:
    cdw = row.get("ORGANISM_ISOLATED_CDW")
    raw = row.get("ORGANISM_ISOLATED")

    for value in (cdw, raw):
        if not _is_blank(value):
            norm = str(value).strip().lower()
            row["organism"] = ORGANISM_TEXT_MAP.get(norm, str(value).strip().title())
            row["organism_flag"] = False
            return row

    row["organism"] = ""
    row["organism_flag"] = True
    return row

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def clean_records(raw_rows: list[dict]) -> list[dict]:
    """
    Apply all cleaning steps to every row fetched from the warehouse.

    Cleaning pipeline (in order):
        1. clean_gender
        2. build_specimen_date  (also sets specimen_date_flag)
        3. resolve_facility_name
        4. resolve_location
        5. resolve_specimen
        6. resolve_organism     (also sets organism_flag)

    Each step mutates a shallow copy of the row to avoid side effects on the
    caller's data.

    Args:
        raw_rows (list[dict]): Output of ``conn.fetch_warehouse_data()``.

    Returns:
        list[dict]: Cleaned rows.  Failed rows are skipped and logged.

    Example:
        from app.services.conn import fetch_warehouse_data
        from app.services.clean import clean_records

        cleaned = clean_records(fetch_warehouse_data())
    """
    cleaned: list[dict] = []
    errors = 0

    for idx, row in enumerate(raw_rows):
        try:
            r = dict(row)  
            r = clean_gender(r)
            r = build_specimen_date(r)
            r = resolve_facility_name(r)
            r = resolve_location(r)
            r = resolve_specimen(r)
            r = resolve_organism(r)
            cleaned.append(r)
        except Exception as exc:
            errors += 1
            logger.warning("Skipping row %d due to cleaning error: %s", idx, exc)

    if errors:
        logger.warning("clean_records: %d rows skipped due to errors.", errors)
    logger.info("clean_records: %d / %d rows cleaned successfully.", len(cleaned), len(raw_rows))
    return cleaned