"""Explaining a family's own land records to them, in their language — for free.

Families pay document writers and middlemen to read records that are theirs. This builds the
same explanation from the case's analyses: whose name is on the record, what happened since,
who the land would pass to, where the records disagree, what the land is carefully worth, and
the first thing to do. Sentences are fixed templates (English, Tamil, Hindi), filled only with
values the analyses produced — so nothing is invented. With Gemini configured, other languages
are translated from the English text, and the translation is labelled as machine-made.
"""

from __future__ import annotations

from typing import Any

from .valuation import inr

T: dict[str, dict[str, str]] = {
    "en": {
        "holder": "This land, survey number {survey} in {village}, is recorded in the name of {holder}.",
        "deceased": "{holder} has died. The record has not been changed since, so no one can sell, divide or pledge the land cleanly.",
        "heirs": "Under {law}, the land would now pass to {n} people (draft shares below). Only the Tahsildar or a court can confirm them.",
        "away": "{n} of them live away from the village: {names}. They can review their share and reply from their phone.",
        "agree": "The records agree with each other on everything checked.",
        "disagree": "The records disagree in {k} place(s): {list}.",
        "value": "A careful estimate of the land's value is {low} to {high} (most likely about {mid}; confidence: {conf}).",
        "lend": "For a loan, plan on {lend}; a lender may lend up to about {ceiling}.",
        "next": "The first thing to do: {step} at the {office}.",
        "ready": "The papers a lender asks for are in order.",
        "free": "A farm loan of up to {limit} needs no land as security, so it can be applied for now.",
        "share": "{name}: {share}",
        "disclaimer": "This is an explanation prepared from your records. It is not a legal decision.",
    },
    "ta": {
        "holder": "இந்த நிலம் (சர்வே எண் {survey}, {village}) {holder} அவர்களின் பெயரில் பதிவாகியுள்ளது.",
        "deceased": "{holder} அவர்கள் இறந்துவிட்டார். அதன் பிறகு பதிவு மாற்றப்படவில்லை; எனவே நிலத்தை விற்கவோ, பிரிக்கவோ, அடமானம் வைக்கவோ முடியாது.",
        "heirs": "{law} படி, இந்த நிலம் இப்போது {n} நபர்களுக்குச் சேரும் (வரைவுப் பங்குகள் கீழே). வட்டாட்சியர் அல்லது நீதிமன்றம் மட்டுமே அவற்றை உறுதி செய்ய முடியும்.",
        "away": "அவர்களில் {n} பேர் கிராமத்திற்கு வெளியே வசிக்கின்றனர்: {names}. அவர்கள் தங்கள் பங்கைத் தொலைபேசியிலேயே பார்த்துப் பதில் அளிக்கலாம்.",
        "agree": "சரிபார்த்த அனைத்திலும் பதிவுகள் ஒன்றுக்கொன்று ஒத்துப்போகின்றன.",
        "disagree": "பதிவுகள் {k} இடங்களில் ஒத்துப்போகவில்லை: {list}.",
        "value": "நிலத்தின் மதிப்பு கவனமான மதிப்பீட்டில் {low} முதல் {high} வரை (பெரும்பாலும் சுமார் {mid}; நம்பகத்தன்மை: {conf}).",
        "lend": "கடனுக்கு {lend} என்று கணக்கிடுங்கள்; வங்கி சுமார் {ceiling} வரை கடன் தரலாம்.",
        "next": "முதலில் செய்ய வேண்டியது: {office} — {step}.",
        "ready": "கடன் வழங்குபவர் கேட்கும் ஆவணங்கள் சரியாக உள்ளன.",
        "free": "{limit} வரையிலான விவசாயக் கடனுக்கு நிலப் பிணை தேவையில்லை; எனவே இப்போதே விண்ணப்பிக்கலாம்.",
        "share": "{name}: {share}",
        "disclaimer": "இது உங்கள் பதிவுகளிலிருந்து தயாரிக்கப்பட்ட விளக்கம் மட்டுமே; சட்டப்படியான முடிவு அல்ல.",
    },
    "hi": {
        "holder": "यह ज़मीन (सर्वे नंबर {survey}, {village}) {holder} के नाम पर दर्ज है।",
        "deceased": "{holder} का निधन हो चुका है। उसके बाद रिकॉर्ड नहीं बदला गया, इसलिए ज़मीन को ठीक से बेचा, बाँटा या गिरवी नहीं रखा जा सकता।",
        "heirs": "{law} के अनुसार यह ज़मीन अब {n} लोगों को मिलेगी (प्रस्तावित हिस्से नीचे हैं)। केवल तहसीलदार या अदालत ही इनकी पुष्टि कर सकते हैं।",
        "away": "इनमें से {n} लोग गाँव से बाहर रहते हैं: {names}। वे अपने फ़ोन पर अपना हिस्सा देखकर जवाब दे सकते हैं।",
        "agree": "जाँची गई हर बात पर रिकॉर्ड आपस में मेल खाते हैं।",
        "disagree": "रिकॉर्ड {k} जगहों पर मेल नहीं खाते: {list}।",
        "value": "ज़मीन की सावधानी से आँकी गई कीमत {low} से {high} के बीच है (सबसे संभावित लगभग {mid}; भरोसा: {conf})।",
        "lend": "कर्ज़ के लिए {lend} मानकर चलें; बैंक लगभग {ceiling} तक कर्ज़ दे सकता है।",
        "next": "सबसे पहले यह करें: {office} में — {step}।",
        "ready": "कर्ज़ देने वाले जो काग़ज़ माँगते हैं, वे पूरे हैं।",
        "free": "{limit} तक के कृषि कर्ज़ के लिए ज़मीन गिरवी रखने की ज़रूरत नहीं है, इसलिए अभी आवेदन किया जा सकता है।",
        "share": "{name}: {share}",
        "disclaimer": "यह आपके रिकॉर्ड से तैयार की गई जानकारी है, कानूनी फ़ैसला नहीं।",
    },
}

TERMS: dict[str, dict[str, str]] = {
    "ta": {
        "names": "பெயர்கள்", "extents": "பரப்பளவு", "boundaries": "எல்லைகள்", "survey_numbers": "சர்வே எண்", "land_class": "நில வகை",
        "deceased_holder": "இறந்தவர் பெயரில் பதிவு", "encumbrance": "வில்லங்கம்",
        "death_certificate": "இறப்புச் சான்றிதழ் பெறுதல்", "legal_heir_certificate": "வாரிசுச் சான்றிதழ் பெறுதல்",
        "co_heir_consent": "இணை வாரிசுகளின் ஒப்புதல் பெறுதல்", "mutation": "பட்டா மாற்றம் செய்தல்",
        "encumbrance_certificate": "வில்லங்கச் சான்றிதழ் பெறுதல்", "sketch_copy": "புல வரைபட (FMB) நகல் பெறுதல்",
        "survey_measurement": "நிலத்தை மீண்டும் அளவீடு செய்தல்", "cultivation_record": "அடங்கல் நகல் பெறுதல்",
        "tax_receipt": "நில வரி செலுத்தி ரசீது பெறுதல்", "valuation_report": "மதிப்பீட்டு அறிக்கை பெறுதல்",
        "legal_scrutiny": "சட்ட ஆய்வு அறிக்கை பெறுதல்", "title_documents": "மூல ஆவண நகல்கள் பெறுதல்",
        "hindu": "இந்து வாரிசுரிமைச் சட்டம், 1956", "christian": "இந்திய வாரிசுரிமைச் சட்டம், 1925",
        "high": "அதிகம்", "medium": "நடுத்தரம்", "low": "குறைவு",
    },
    "hi": {
        "names": "नाम", "extents": "रकबा", "boundaries": "सीमाएँ", "survey_numbers": "सर्वे नंबर", "land_class": "ज़मीन का प्रकार",
        "deceased_holder": "मृत व्यक्ति के नाम पर रिकॉर्ड", "encumbrance": "भार (गिरवी/अनुबंध)",
        "death_certificate": "मृत्यु प्रमाणपत्र लेना", "legal_heir_certificate": "उत्तराधिकार प्रमाणपत्र लेना",
        "co_heir_consent": "सह-वारिसों की सहमति लेना", "mutation": "नामांतरण (दाखिल-खारिज) कराना",
        "encumbrance_certificate": "भार-मुक्त प्रमाणपत्र लेना", "sketch_copy": "नक्शे की प्रति लेना",
        "survey_measurement": "ज़मीन की दोबारा नाप कराना", "cultivation_record": "खसरा/फसल रिकॉर्ड की प्रति लेना",
        "tax_receipt": "लगान भरकर रसीद लेना", "valuation_report": "मूल्यांकन रिपोर्ट लेना",
        "legal_scrutiny": "कानूनी जाँच रिपोर्ट लेना", "title_documents": "पुराने दस्तावेज़ों की प्रमाणित प्रति लेना",
        "hindu": "हिंदू उत्तराधिकार अधिनियम, 1956", "christian": "भारतीय उत्तराधिकार अधिनियम, 1925",
        "high": "अधिक", "medium": "मध्यम", "low": "कम",
    },
}
EN_STEP = {
    "death_certificate": "get the death certificate", "legal_heir_certificate": "apply for the legal heir certificate",
    "co_heir_consent": "collect every co-heir's consent", "mutation": "apply for mutation of the record into the heirs' names",
    "encumbrance_certificate": "get an encumbrance certificate", "sketch_copy": "get a copy of the survey sketch",
    "survey_measurement": "ask for the plot to be measured again", "cultivation_record": "get a copy of the cultivation record",
    "tax_receipt": "pay the land revenue and keep the receipt", "valuation_report": "get a valuer's report",
    "legal_scrutiny": "get the lender's legal scrutiny report", "title_documents": "get certified copies of the parent documents",
}
LANGUAGES = ("en", "ta", "hi")


def _term(lang: str, key: str, fallback: str) -> str:
    return TERMS.get(lang, {}).get(key, fallback)


def build(lang: str, *, parcel: Any, conc: dict[str, Any], succ: dict[str, Any], val: dict[str, Any], ready: dict[str, Any]) -> dict[str, Any]:
    lang = lang if lang in T else "en"
    t = T[lang]
    lines: list[str] = []
    holder = ", ".join(conc.get("holders") or []) or (parcel.recorded_holder if parcel else "") or "—"
    lines.append(t["holder"].format(survey=conc.get("survey_key") or "—", village=(parcel.village if parcel else "") or "—", holder=holder))
    if conc.get("deceased_holders"):
        lines.append(t["deceased"].format(holder=", ".join(conc["deceased_holders"])))
    shares = [s for s in succ.get("shares", []) if s["alive"]]
    if succ.get("status") in ("computed", "partial") and shares:
        law = _term(lang, succ.get("law", ""), succ.get("law_name", ""))
        lines.append(t["heirs"].format(law=law, n=len(shares)))
        away = succ.get("away_from_village") or []
        if away:
            lines.append(t["away"].format(n=len(away), names=", ".join(away)))
    counts = {k: v for k, v in (conc.get("counts") or {}).items() if v}
    if counts:
        lines.append(t["disagree"].format(k=sum(counts.values()), list=", ".join(f"{_term(lang, k, k.replace('_', ' '))} ({v})" for k, v in counts.items())))
    elif conc.get("records_compared"):
        lines.append(t["agree"])
    if val.get("status") == "estimated":
        lines.append(t["value"].format(low=inr(val["p10_inr"]), high=inr(val["p90_inr"]), mid=inr(val["p50_inr"]), conf=_term(lang, val["confidence"], val["confidence"])))
        lines.append(t["lend"].format(lend=inr(val["lending_value_inr"]), ceiling=inr(val["loan_ceiling_inr"])))
    plan = ready.get("plan") or []
    if ready.get("collateral_free"):
        lines.append(t["free"].format(limit=inr(ready.get("collateral_free_limit_inr"))))
    if plan:
        first = sorted(plan, key=lambda p: (p["start_day"], not p["critical"]))[0]
        lines.append(t["next"].format(step=_term(lang, first["service"], EN_STEP.get(first["service"], first["name"])), office=first["office"]))
    elif ready.get("verdict") == "ready":
        lines.append(t["ready"])
    share_lines = [t["share"].format(name=s["name"], share=s["share"]) for s in shares]
    return {"language": lang, "lines": lines, "shares": share_lines, "disclaimer": t["disclaimer"], "machine_translated": False}
