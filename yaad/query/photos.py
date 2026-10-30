import json
import re
from pathlib import Path

from yaad.query.retrieve import STOPWORDS, search


PHOTO_WORDS = frozenset({
    "photo", "photos", "photograph", "photographs",
    "picture", "pictures", "image", "images",
})
# Value/decision signals that route a photo-word question through the
# shared answer pipeline instead of the listing path. Mirrors the
# intent lists in answer.extract_field_value (duplicated: photos.py
# cannot import answer.py without a cycle); keep the two in sync.
_VALUE_TERMS = (
    "deadline", "date", "when", "due",
    "fee", "amount", "deposit", "cost", "price", "payment",
    "roll number", "registration number", "application number",
    "id number", "identifier",
)
_BROWSE_VERBS = ("show", "list", "find", "display", "give")
_INTERROGATIVES = frozenset({
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    "is", "are", "was", "were", "do", "does", "did", "can", "could",
})
# Photo-artifact metadata ("where was this photo taken") stays on the
# listing path: it asks about the file, not the world's state.
_ARTIFACT_TERMS = ("taken", "gps", "coordinates", "exif", "camera")
_POLITE_PREFIX = re.compile(r"^(please\s+|can you\s+|could you\s+|would you\s+)+")


def _question_tokens(question: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", question.casefold())


def photo_intent(question: str) -> bool:
    """Whole-word photo reference ("photos", "photograph"); substrings
    ("photography", "photosynthesis", "picturesque") never trigger."""
    return bool(set(_question_tokens(question)) & PHOTO_WORDS)


def all_photos_intent(question: str) -> bool:
    q = question.casefold()
    return bool(re.search(r"\b(all|every|fetch|show|list)\b", q) and photo_intent(q))


def photo_value_intent(question: str) -> bool:
    """Photo words plus a substantive question ("What is the venue?
    Show me the photos"): route through the shared pipeline with
    photos attached, so conflicts, memory, and abstention still
    apply. Pure browsing, counts, existence checks, and artifact
    metadata stay listing-only. Ambiguous phrasing defaults to the
    shared path: never silently discard a substantive question."""
    q = question.casefold()
    if any(term in q for term in _VALUE_TERMS):
        return True
    stripped = _POLITE_PREFIX.sub("", q).strip()
    tokens = _question_tokens(stripped)
    if tokens and tokens[0] in _BROWSE_VERBS + ("fetch", "all", "every"):
        return False
    if tokens and tokens[0] in PHOTO_WORDS and not (set(tokens) & _INTERROGATIVES):
        return False
    if re.match(r"^(are|is) there\b", stripped):
        return False
    if re.search(r"how many photos?\b", stripped):
        return False
    if any(term in stripped for term in _ARTIFACT_TERMS):
        return False
    return True


_META_ONLY_TOKENS = PHOTO_WORDS | {"poster", "posters"}


def _meta_only(question: str) -> bool:
    """True when the question names only the medium ("what does the
    poster photo show?"): no content terms exist for lexical search
    to match against image descriptions."""
    tokens = set(_question_tokens(question)) - _INTERROGATIVES - STOPWORDS \
        - {"does", "did", "show", "shows", "display", "describe", "shown"}
    return bool(tokens) and tokens <= _META_ONLY_TOKENS


def location_intent(question: str) -> bool:
    return any(word in question.casefold() for word in ("where", "location", "from ", "near ", "in "))


def _photo_record(row):
    description = None
    if row["description"]:
        description = {
            "schema_version": row["schema_version"], "caption": row["caption"],
            "people": json.loads(row["people"] or "[]"), "objects": json.loads(row["objects"] or "[]"),
            "actions": json.loads(row["actions"] or "[]"), "scene": json.loads(row["scene"] or "{}"),
            "visible_text": row["visible_text"], "visual_attributes": json.loads(row["visual_attributes"] or "[]"),
            "uncertainties": json.loads(row["uncertainties"] or "[]"),
        }
    return {"file_id": row["file_id"], "file": row["path"], "folder": row["folder_path"], "filename": Path(row["path"]).name,
            "evidence_id": row["evidence_id"], "quote": row["text"] or (description or {}).get("caption", ""),
            "description": description, "exif": json.loads(row["exif"] or "{}"),
            "preview_url": f"/api/photos/{row['file_id']}/preview"}


def _with_location_confidence(photo):
    photo["location_confidence"] = ("coordinates_recorded" if photo["exif"].get("GPSLatitudeDecimal") and photo["exif"].get("GPSLongitudeDecimal")
                                    else "unconfirmed_visual_match")
    return photo


def list_photos(db, folder_id: int | None = None):
    sql = """SELECT f.id AS file_id,f.path,d.file_id AS description,d.schema_version,d.caption,d.people,d.objects,d.actions,d.scene,
             d.visible_text,d.visual_attributes,d.uncertainties,u.evidence_id,u.text,
             (SELECT json_group_object(key,value) FROM photo_meta pm WHERE pm.file_id=f.id AND pm.source='exif') AS exif,fo.path AS folder_path
             FROM files f JOIN folders fo ON fo.id=f.folder_id LEFT JOIN photo_descriptions d ON d.file_id=f.id
             LEFT JOIN units u ON u.id=(SELECT MIN(id) FROM units WHERE file_id=f.id AND kind='image_understanding')
             WHERE f.kind='image' AND f.status IN ('indexed','partial') AND u.id IS NOT NULL"""
    args = []
    if folder_id is not None:
        sql += " AND f.folder_id=?"
        args.append(folder_id)
    sql += " ORDER BY f.path"
    with db.connect() as con:
        return [_with_location_confidence(_photo_record(row)) for row in con.execute(sql, args)]


def _question_hits(photo: dict, question: str) -> int:
    """Question-term hits in filename, caption, and visible text:
    deterministic relevance for ranking photo results."""
    terms = set(_question_tokens(question)) - STOPWORDS
    if not terms:
        return 0
    description = photo.get("description") or {}
    blob = (Path(photo.get("file", "")).name + " "
            + (description.get("caption") or "") + " "
            + (description.get("visible_text") or "") + " "
            + (photo.get("quote") or "")).casefold()
    # Word-boundary hits only: "red" must not score inside "covered".
    return sum(1 for term in terms
               if re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", blob))


def _rank_photos(photos: list[dict], question: str) -> list[dict]:
    return sorted(photos, key=lambda p: (-_question_hits(p, question),
                                         p.get("file", "")))


def search_photos(db, question: str, folder_id: int | None = None, limit: int = 30):
    rows = search(db, question, folder_id, limit * 2, file_kinds=["image"])
    seen, results = set(), []
    for row in rows:
        if row["file_id"] in seen:
            continue
        seen.add(row["file_id"])
        with db.connect() as con:
            details = con.execute("""SELECT f.id AS file_id,f.path,d.file_id AS description,d.schema_version,d.caption,d.people,d.objects,d.actions,d.scene,
                 d.visible_text,d.visual_attributes,d.uncertainties,? AS evidence_id,? AS text,
                 (SELECT json_group_object(key,value) FROM photo_meta pm WHERE pm.file_id=f.id AND pm.source='exif') AS exif,fo.path AS folder_path
                 FROM files f JOIN folders fo ON fo.id=f.folder_id LEFT JOIN photo_descriptions d ON d.file_id=f.id WHERE f.id=?""",
                                  (row["evidence_id"], row["text"], row["file_id"])).fetchone()
        result = _with_location_confidence(_photo_record(details))
        results.append(result)
        if len(results) >= limit:
            break
    if not results and _meta_only(question):
        # Meta-only wording has no content terms to match: fall back to
        # the folder's indexed images (bounded) with their existing
        # descriptions and evidence references.
        for photo in list_photos(db, folder_id)[:limit]:
            if photo["file_id"] not in seen:
                results.append(photo)
                seen.add(photo["file_id"])
    if not results and location_intent(question):
        # Broad scenery may be visually similar, but cannot confirm a place name.
        rows = search(db, "mountain hills landscape valley", folder_id, limit, file_kinds=["image"])
        for row in rows:
            if row["file_id"] in seen:
                continue
            seen.add(row["file_id"])
            with db.connect() as con:
                details = con.execute("""SELECT f.id AS file_id,f.path,d.file_id AS description,d.schema_version,d.caption,d.people,d.objects,d.actions,d.scene,
                     d.visible_text,d.visual_attributes,d.uncertainties,? AS evidence_id,? AS text,
                     (SELECT json_group_object(key,value) FROM photo_meta pm WHERE pm.file_id=f.id AND pm.source='exif') AS exif,fo.path AS folder_path
                     FROM files f JOIN folders fo ON fo.id=f.folder_id LEFT JOIN photo_descriptions d ON d.file_id=f.id WHERE f.id=?""",
                                      (row["evidence_id"], row["text"], row["file_id"])).fetchone()
            result = _with_location_confidence(_photo_record(details))
            results.append(result)
            if len(results) >= limit:
                break
    if location_intent(question):
        q = question.casefold()
        place_terms = [term.strip(".,!?;:") for term in re.findall(r"[\w-]+", q)
                       if len(term) > 3 and term not in {"find", "show", "photos", "photo", "pictures", "picture", "where", "from", "near", "location", "taken", "captured", "with", "that", "have", "this"}]
        for photo in list_photos(db, folder_id):
            folder_path = photo["folder"].casefold()
            if photo["file_id"] in seen or not any(term in folder_path for term in place_terms):
                continue
            photo["location_confidence"] = "folder_context"
            results.append(photo)
            seen.add(photo["file_id"])
    if any(term in question.casefold() for term in ("gps", "coordinates", "camera", "taken in", "taken on", "date taken")):
        q = question.casefold()
        for photo in list_photos(db, folder_id):
            exif_text = " ".join(f"{key} {value}" for key, value in photo["exif"].items()).casefold()
            has_gps = bool(photo["exif"].get("GPSLatitudeDecimal") and photo["exif"].get("GPSLongitudeDecimal"))
            metadata_match = ("gps" in q or "coordinates" in q) and has_gps
            metadata_match = metadata_match or ("camera" in q and any(word in exif_text for word in ("make", "model")))
            year_terms = re.findall(r"\b(?:19|20)\d{2}\b", q)
            metadata_match = metadata_match or any(year in exif_text for year in year_terms)
            if metadata_match and photo["file_id"] not in seen:
                photo["metadata_match"] = "GPS coordinates" if has_gps and ("gps" in q or "coordinates" in q) else "EXIF metadata"
                results.append(photo)
                seen.add(photo["file_id"])
    # Question-aware ranking: photos whose filename, caption, or visible
    # text hit the question's content terms come first; path order is
    # only the tiebreak. Pure browsing keeps its existing order.
    if any(_question_hits(photo, question) for photo in results):
        results = _rank_photos(results, question)
    return results[:limit]
