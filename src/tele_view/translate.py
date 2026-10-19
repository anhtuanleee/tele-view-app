import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

# the endpoint Google's own web widgets use: free and keyless, but unofficial, so any failure just
# means "no translation" and the original text is shown as before
API = "https://translate.googleapis.com/translate_a/single"
MAX_CHARS = 5000

# letters only Vietnamese uses; text with a few of them is already Vietnamese, skip the request
VIETNAMESE_LETTERS = set("ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ")


@dataclass
class Translation:
    text: str
    # detected language of the original, e.g. "en", "zh-CN"
    source: str


async def translate(text: str, target: str) -> Translation | None:
    """Translate into `target`, or None if the text is already in it, untranslatable, or the call fails."""
    text = text.strip()
    if not text:
        return None
    if target == "vi" and sum(c in VIETNAMESE_LETTERS for c in text.lower()) >= 3:
        return None
    params = {"client": "gtx", "sl": "auto", "tl": target, "dt": "t"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # POST: long captions don't fit in a URL
            resp = await client.post(API, params=params, data={"q": text[:MAX_CHARS]})
            resp.raise_for_status()
            data = resp.json()
        translated = "".join(part[0] for part in data[0] if part and part[0]).strip()
        source = data[2]
    except (httpx.HTTPError, ValueError, LookupError, TypeError) as e:
        log.info("translation failed: %s", e)
        return None
    # hashtags, names and emoji come back unchanged
    if source == target or not translated or translated == text:
        return None
    return Translation(translated, source)
