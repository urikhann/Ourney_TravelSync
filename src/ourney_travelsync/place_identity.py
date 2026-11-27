"""Conservative visit identity; source POI IDs and catalogue rows stay intact."""
from hashlib import sha256
import re
import unicodedata


REGIONS = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구",
    "인천광역시": "인천", "광주광역시": "광주", "대전광역시": "대전",
    "울산광역시": "울산", "세종특별자치시": "세종", "제주특별자치도": "제주",
    "강원특별자치도": "강원", "강원도": "강원", "전북특별자치도": "전북",
    "전라북도": "전북", "전라남도": "전남", "경상북도": "경북", "경상남도": "경남",
    "충청북도": "충북", "충청남도": "충남", "경기도": "경기",
}


def visit_identity(name: str, address: str) -> str | None:
    """Require matching names AND numbered addresses; never merge by name alone.

    Normalize regional abbreviations and a trailing neighborhood annotation.
    Preserve branch names, address numbers, hyphens and unit/floor details.
    Coordinates are deliberately not identity evidence: source pins can differ.
    """
    name = unicodedata.normalize("NFKC", name or "").casefold()
    address = unicodedata.normalize("NFKC", address or "").casefold().strip()
    address = re.sub(r"\s*\(\s*[가-힣0-9]+(?:동|리|읍|면)\s*\)\s*$", "", address)
    for full, short in REGIONS.items():
        if address.startswith(full):
            address = short + address[len(full):]
            break
    name = "".join(c for c in name if c.isalnum())
    address = "".join(c for c in address if c.isalnum() or c == "-")
    if not name or not address or not any(c.isdigit() for c in address):
        return None
    return "name-address-v1:" + sha256(f"{name}\0{address}".encode()).hexdigest()


def candidate_identity(candidate) -> str:
    return candidate.visit_identity or candidate.poi_id
