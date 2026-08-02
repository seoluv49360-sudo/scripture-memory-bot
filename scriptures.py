import json
import os
from pathlib import Path


SCRIPTURES_FILE = Path(os.getenv("SCRIPTURES_FILE", "data/scriptures.json"))


def load_scriptures(path: Path = SCRIPTURES_FILE) -> list[dict[str, str]]:
    if not path.is_file():
        raise RuntimeError(
            f"본문 데이터 파일이 없습니다: {path}. "
            "scriptures.example.json을 복사해 운영 데이터를 입력해 주세요."
        )
    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"본문 데이터 파일을 읽을 수 없습니다: {path}") from error
    if not isinstance(raw_data, list) or not raw_data:
        raise RuntimeError("본문 데이터는 하나 이상의 항목이 있는 JSON 배열이어야 합니다.")

    scriptures: list[dict[str, str]] = []
    scripture_ids: set[str] = set()
    for item in raw_data:
        if not isinstance(item, dict):
            raise RuntimeError("본문 데이터의 각 항목은 JSON 객체여야 합니다.")
        scripture = {
            "id": str(item.get("id", "")).strip(),
            "reference": str(item.get("reference", "")).strip(),
            "text": str(item.get("text", "")).strip(),
        }
        if not all(scripture.values()):
            raise RuntimeError("본문 데이터의 id, reference, text 값은 비어 있을 수 없습니다.")
        if scripture["id"] in scripture_ids:
            raise RuntimeError(f"중복된 본문 id가 있습니다: {scripture['id']}")
        scripture_ids.add(scripture["id"])
        scriptures.append(scripture)
    return scriptures


SCRIPTURES = load_scriptures()
SCRIPTURE_BY_ID = {scripture["id"]: scripture for scripture in SCRIPTURES}
