"""NLLB-200 language registry + per-language model routing.

Why this exists: the app translates into any of the 202 languages the
local NLLB-200 model was trained on, but two things vary per language
and must be decided in exactly one place:

  1. Which local model serves it. Darija has a specialist model
     (Terjman-Nano, 77M params) that is both faster and more genuinely
     Moroccan than NLLB-600M, so `ary_Arab` routes there; every other
     language routes to the NLLB model. Measured, not assumed — see
     tests/bench_translate.py.
  2. How the overlay must render it: script (font choice) and
     writing direction (RTL for Arabic/Hebrew scripts).

Codes are FLORES-200 style (`spa_Latn`); the set below was extracted
from the NLLB tokenizer's special-token list on disk, so it is exactly
what the model accepts — no aspirational entries.
"""

import os

# code -> (English name, native name). Native falls back to the English
# name where no widely-accepted endonym exists. Script is the code
# suffix; direction derives from the script.
LANGS: dict[str, tuple[str, str]] = {
    "ace_Arab": ("Acehnese (Jawi)", "Aceh (Jawi)"),
    "ace_Latn": ("Acehnese", "Bahsa Acèh"),
    "acm_Arab": ("Mesopotamian Arabic", "عراقي"),
    "acq_Arab": ("Ta'izzi-Adeni Arabic", "عربي يمني"),
    "aeb_Arab": ("Tunisian Arabic", "تونسي"),
    "afr_Latn": ("Afrikaans", "Afrikaans"),
    "ajp_Arab": ("South Levantine Arabic", "شامي جنوبي"),
    "aka_Latn": ("Akan", "Akan"),
    "als_Latn": ("Albanian", "Shqip"),
    "amh_Ethi": ("Amharic", "አማርኛ"),
    "apc_Arab": ("North Levantine Arabic", "شامي"),
    "arb_Arab": ("Arabic (Modern Standard)", "العربية الفصحى"),
    "ars_Arab": ("Najdi Arabic", "نجدي"),
    "ary_Arab": ("Moroccan Arabic (Darija)", "الدارجة"),
    "arz_Arab": ("Egyptian Arabic", "مصري"),
    "asm_Beng": ("Assamese", "অসমীয়া"),
    "ast_Latn": ("Asturian", "Asturianu"),
    "awa_Deva": ("Awadhi", "अवधी"),
    "ayr_Latn": ("Aymara", "Aymar aru"),
    "azb_Arab": ("South Azerbaijani", "تۆرکجه"),
    "azj_Latn": ("Azerbaijani", "Azərbaycanca"),
    "bak_Cyrl": ("Bashkir", "Башҡортса"),
    "bam_Latn": ("Bambara", "Bamanankan"),
    "ban_Latn": ("Balinese", "Basa Bali"),
    "bel_Cyrl": ("Belarusian", "Беларуская"),
    "bem_Latn": ("Bemba", "Ichibemba"),
    "ben_Beng": ("Bengali", "বাংলা"),
    "bho_Deva": ("Bhojpuri", "भोजपुरी"),
    "bjn_Arab": ("Banjar (Jawi)", "Banjar (Jawi)"),
    "bjn_Latn": ("Banjar", "Bahasa Banjar"),
    "bod_Tibt": ("Tibetan", "བོད་སྐད་"),
    "bos_Latn": ("Bosnian", "Bosanski"),
    "bug_Latn": ("Buginese", "Basa Ugi"),
    "bul_Cyrl": ("Bulgarian", "Български"),
    "cat_Latn": ("Catalan", "Català"),
    "ceb_Latn": ("Cebuano", "Cebuano"),
    "ces_Latn": ("Czech", "Čeština"),
    "cjk_Latn": ("Chokwe", "Chokwe"),
    "ckb_Arab": ("Kurdish (Sorani)", "کوردیی ناوەندی"),
    "crh_Latn": ("Crimean Tatar", "Qırımtatarca"),
    "cym_Latn": ("Welsh", "Cymraeg"),
    "dan_Latn": ("Danish", "Dansk"),
    "deu_Latn": ("German", "Deutsch"),
    "dik_Latn": ("Dinka", "Thuɔŋjäŋ"),
    "dyu_Latn": ("Dyula", "Julakan"),
    "dzo_Tibt": ("Dzongkha", "རྫོང་ཁ"),
    "ell_Grek": ("Greek", "Ελληνικά"),
    "eng_Latn": ("English", "English"),
    "epo_Latn": ("Esperanto", "Esperanto"),
    "est_Latn": ("Estonian", "Eesti"),
    "eus_Latn": ("Basque", "Euskara"),
    "ewe_Latn": ("Ewe", "Eʋegbe"),
    "fao_Latn": ("Faroese", "Føroyskt"),
    "fij_Latn": ("Fijian", "Vosa Vakaviti"),
    "fin_Latn": ("Finnish", "Suomi"),
    "fon_Latn": ("Fon", "Fɔngbe"),
    "fra_Latn": ("French", "Français"),
    "fur_Latn": ("Friulian", "Furlan"),
    "fuv_Latn": ("Nigerian Fulfulde", "Fulfulde"),
    "gaz_Latn": ("Oromo", "Afaan Oromoo"),
    "gla_Latn": ("Scottish Gaelic", "Gàidhlig"),
    "gle_Latn": ("Irish", "Gaeilge"),
    "glg_Latn": ("Galician", "Galego"),
    "grn_Latn": ("Guarani", "Avañe'ẽ"),
    "guj_Gujr": ("Gujarati", "ગુજરાતી"),
    "hat_Latn": ("Haitian Creole", "Kreyòl Ayisyen"),
    "hau_Latn": ("Hausa", "Hausa"),
    "heb_Hebr": ("Hebrew", "עברית"),
    "hin_Deva": ("Hindi", "हिन्दी"),
    "hne_Deva": ("Chhattisgarhi", "छत्तीसगढ़ी"),
    "hrv_Latn": ("Croatian", "Hrvatski"),
    "hun_Latn": ("Hungarian", "Magyar"),
    "hye_Armn": ("Armenian", "Հայերեն"),
    "ibo_Latn": ("Igbo", "Asụsụ Igbo"),
    "ilo_Latn": ("Ilocano", "Ilokano"),
    "ind_Latn": ("Indonesian", "Bahasa Indonesia"),
    "isl_Latn": ("Icelandic", "Íslenska"),
    "ita_Latn": ("Italian", "Italiano"),
    "jav_Latn": ("Javanese", "Basa Jawa"),
    "jpn_Jpan": ("Japanese", "日本語"),
    "kab_Latn": ("Kabyle", "Taqbaylit"),
    "kac_Latn": ("Jingpho", "Jinghpaw"),
    "kam_Latn": ("Kamba", "Kikamba"),
    "kan_Knda": ("Kannada", "ಕನ್ನಡ"),
    "kas_Arab": ("Kashmiri (Arabic)", "كٲشُر"),
    "kas_Deva": ("Kashmiri (Devanagari)", "कॉशुर"),
    "kat_Geor": ("Georgian", "ქართული"),
    "kaz_Cyrl": ("Kazakh", "Қазақша"),
    "kbp_Latn": ("Kabiye", "Kabɩyɛ"),
    "kea_Latn": ("Kabuverdianu", "Kriolu"),
    "khk_Cyrl": ("Mongolian", "Монгол"),
    "khm_Khmr": ("Khmer", "ខ្មែរ"),
    "kik_Latn": ("Kikuyu", "Gĩkũyũ"),
    "kin_Latn": ("Kinyarwanda", "Ikinyarwanda"),
    "kir_Cyrl": ("Kyrgyz", "Кыргызча"),
    "kmb_Latn": ("Kimbundu", "Kimbundu"),
    "kmr_Latn": ("Kurdish (Kurmanji)", "Kurmancî"),
    "knc_Arab": ("Kanuri (Ajami)", "Kanuri (Ajami)"),
    "knc_Latn": ("Kanuri", "Kanuri"),
    "kon_Latn": ("Kikongo", "Kikongo"),
    "kor_Hang": ("Korean", "한국어"),
    "lao_Laoo": ("Lao", "ລາວ"),
    "lij_Latn": ("Ligurian", "Lìgure"),
    "lim_Latn": ("Limburgish", "Limburgs"),
    "lin_Latn": ("Lingala", "Lingála"),
    "lit_Latn": ("Lithuanian", "Lietuvių"),
    "lmo_Latn": ("Lombard", "Lombard"),
    "ltg_Latn": ("Latgalian", "Latgalīšu"),
    "ltz_Latn": ("Luxembourgish", "Lëtzebuergesch"),
    "lua_Latn": ("Luba-Kasai", "Tshiluba"),
    "lug_Latn": ("Luganda", "Luganda"),
    "luo_Latn": ("Luo", "Dholuo"),
    "lus_Latn": ("Mizo", "Mizo ṭawng"),
    "lvs_Latn": ("Latvian", "Latviešu"),
    "mag_Deva": ("Magahi", "मगही"),
    "mai_Deva": ("Maithili", "मैथिली"),
    "mal_Mlym": ("Malayalam", "മലയാളം"),
    "mar_Deva": ("Marathi", "मराठी"),
    "min_Latn": ("Minangkabau", "Baso Minangkabau"),
    "mkd_Cyrl": ("Macedonian", "Македонски"),
    "mlt_Latn": ("Maltese", "Malti"),
    "mni_Beng": ("Meitei (Manipuri)", "মৈতৈলোন্"),
    "mos_Latn": ("Mossi", "Mooré"),
    "mri_Latn": ("Maori", "Te Reo Māori"),
    "mya_Mymr": ("Burmese", "မြန်မာစာ"),
    "nld_Latn": ("Dutch", "Nederlands"),
    "nno_Latn": ("Norwegian (Nynorsk)", "Nynorsk"),
    "nob_Latn": ("Norwegian (Bokmål)", "Bokmål"),
    "npi_Deva": ("Nepali", "नेपाली"),
    "nso_Latn": ("Sepedi", "Sepedi"),
    "nus_Latn": ("Nuer", "Thok Naath"),
    "nya_Latn": ("Chichewa", "Chichewa"),
    "oci_Latn": ("Occitan", "Occitan"),
    "ory_Orya": ("Odia", "ଓଡ଼ିଆ"),
    "pag_Latn": ("Pangasinan", "Pangasinan"),
    "pan_Guru": ("Punjabi", "ਪੰਜਾਬੀ"),
    "pap_Latn": ("Papiamento", "Papiamentu"),
    "pbt_Arab": ("Pashto", "پښتو"),
    "pes_Arab": ("Persian (Farsi)", "فارسی"),
    "plt_Latn": ("Malagasy", "Malagasy"),
    "pol_Latn": ("Polish", "Polski"),
    "por_Latn": ("Portuguese", "Português"),
    "prs_Arab": ("Dari", "دری"),
    "quy_Latn": ("Quechua (Ayacucho)", "Runasimi"),
    "ron_Latn": ("Romanian", "Română"),
    "run_Latn": ("Kirundi", "Ikirundi"),
    "rus_Cyrl": ("Russian", "Русский"),
    "sag_Latn": ("Sango", "Sängö"),
    "san_Deva": ("Sanskrit", "संस्कृतम्"),
    "sat_Beng": ("Santali", "Santali"),
    "scn_Latn": ("Sicilian", "Sicilianu"),
    "shn_Mymr": ("Shan", "Shan"),
    "sin_Sinh": ("Sinhala", "සිංහල"),
    "slk_Latn": ("Slovak", "Slovenčina"),
    "slv_Latn": ("Slovenian", "Slovenščina"),
    "smo_Latn": ("Samoan", "Gagana Sāmoa"),
    "sna_Latn": ("Shona", "ChiShona"),
    "snd_Arab": ("Sindhi", "سنڌي"),
    "som_Latn": ("Somali", "Soomaali"),
    "sot_Latn": ("Sesotho", "Sesotho"),
    "spa_Latn": ("Spanish", "Español"),
    "srd_Latn": ("Sardinian", "Sardu"),
    "srp_Cyrl": ("Serbian", "Српски"),
    "ssw_Latn": ("Swati", "SiSwati"),
    "sun_Latn": ("Sundanese", "Basa Sunda"),
    "swe_Latn": ("Swedish", "Svenska"),
    "swh_Latn": ("Swahili", "Kiswahili"),
    "szl_Latn": ("Silesian", "Ślōnski"),
    "tam_Taml": ("Tamil", "தமிழ்"),
    "taq_Latn": ("Tamasheq", "Tamasheq"),
    "taq_Tfng": ("Tamasheq (Tifinagh)", "ⵜⴰⵎⴰⵛⵀⵈ"),
    "tat_Cyrl": ("Tatar", "Татарча"),
    "tel_Telu": ("Telugu", "తెలుగు"),
    "tgk_Cyrl": ("Tajik", "Тоҷикӣ"),
    "tgl_Latn": ("Tagalog (Filipino)", "Tagalog"),
    "tha_Thai": ("Thai", "ไทย"),
    "tir_Ethi": ("Tigrinya", "ትግርኛ"),
    "tpi_Latn": ("Tok Pisin", "Tok Pisin"),
    "tsn_Latn": ("Setswana", "Setswana"),
    "tso_Latn": ("Tsonga", "Xitsonga"),
    "tuk_Latn": ("Turkmen", "Türkmençe"),
    "tum_Latn": ("Tumbuka", "Chitumbuka"),
    "tur_Latn": ("Turkish", "Türkçe"),
    "twi_Latn": ("Twi", "Twi"),
    "tzm_Tfng": ("Tamazight (Central Atlas)", "ⵜⴰⵎⴰⵣⵉⵖⵜ"),
    "uig_Arab": ("Uyghur", "ئۇيغۇرچە"),
    "ukr_Cyrl": ("Ukrainian", "Українська"),
    "umb_Latn": ("Umbundu", "Umbundu"),
    "urd_Arab": ("Urdu", "اردو"),
    "uzn_Latn": ("Uzbek", "Oʻzbekcha"),
    "vec_Latn": ("Venetian", "Vèneto"),
    "vie_Latn": ("Vietnamese", "Tiếng Việt"),
    "war_Latn": ("Waray", "Winaray"),
    "wol_Latn": ("Wolof", "Wolof"),
    "xho_Latn": ("Xhosa", "IsiXhosa"),
    "ydd_Hebr": ("Yiddish", "ייִדיש"),
    "yor_Latn": ("Yoruba", "Yorùbá"),
    "yue_Hant": ("Cantonese", "廣東話"),
    "zho_Hans": ("Chinese (Simplified)", "简体中文"),
    "zho_Hant": ("Chinese (Traditional)", "繁體中文"),
    "zsm_Latn": ("Malay", "Bahasa Melayu"),
    "zul_Latn": ("Zulu", "IsiZulu"),
}

DEFAULT_TARGET = "ary_Arab"

# Scripts written right-to-left. Of NLLB-200's scripts only these two
# occur (no Thaana/N'Ko in the 202-language set on disk).
_RTL_SCRIPTS = {"Arab", "Hebr"}

# Languages with a local specialist model that beats NLLB on both speed
# and quality (measured in tests/bench_translate.py). Everything else
# uses the generalist NLLB model.
SPECIALIST_MODELS = {"ary_Arab": "terjman-nano-int8"}
GENERAL_MODEL = "nllb-600m-int8"


def script(code: str) -> str:
    """'spa_Latn' -> 'Latn'."""
    return code.rsplit("_", 1)[-1]


def is_rtl(code: str) -> bool:
    return script(code) in _RTL_SCRIPTS


def english_name(code: str) -> str:
    return LANGS[code][0] if code in LANGS else code


def label(code: str) -> str:
    """Human label for pickers: 'Spanish — Español'."""
    en, native = LANGS[code]
    return en if native == en else f"{en} — {native}"


def all_labels() -> list[str]:
    """Sorted picker labels for every supported target (English first)."""
    return [label(c) for c in sorted(LANGS, key=lambda c: LANGS[c][0])]


def resolve(query: str) -> str | None:
    """Turn user input (code, English or native name, or a label from
    all_labels) into a language code. None if nothing matches."""
    q = (query or "").strip()
    if not q:
        return None
    if q in LANGS:
        return q
    ql = q.lower()
    # exact label / name match first, then unique prefix, then substring
    for code, (en, native) in LANGS.items():
        if ql in (en.lower(), native.lower(), label(code).lower()):
            return code
    starts = [c for c, (en, native) in LANGS.items()
              if en.lower().startswith(ql) or native.lower().startswith(ql)]
    if len(starts) == 1:
        return starts[0]
    if starts:  # ambiguous prefix: prefer the shortest English name
        return min(starts, key=lambda c: len(LANGS[c][0]))
    contains = [c for c, (en, native) in LANGS.items()
                if ql in en.lower() or ql in native.lower()]
    if contains:
        return min(contains, key=lambda c: len(LANGS[c][0]))
    return None


def pick_model(code: str, models_root: str) -> str:
    """Absolute model dir for a target language. Specialist model if we
    have one on disk for that language, else the general NLLB model."""
    name = SPECIALIST_MODELS.get(code)
    if name:
        path = os.path.join(models_root, name)
        if os.path.isdir(path):
            return path
    return os.path.join(models_root, GENERAL_MODEL)
