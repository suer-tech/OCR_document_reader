import importlib.util
import json
from pathlib import Path

from test_rtk3_evidence import fixture, build


def preparer():
    path = Path(__file__).resolve().parents[2] / "scripts" / "prepare_rtk3_training.py"
    spec = importlib.util.spec_from_file_location("prepare_rtk3_training", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preparation_preserves_originals_separates_external_and_masks_uncertain(tmp_path):
    payload, text = fixture()
    text = text.replace("Дата публикации в КАД: 05.10.2026", "")
    payload["kad_publication_date"] = {"value": None, "quote": ""}
    fields = build(payload, text)
    source, output = tmp_path / "source", tmp_path / "prepared"
    folder = source / "p-test"
    folder.mkdir(parents=True)
    human = {"fields": {k: {"value": v["value"]} for k, v in fields.items()}}
    human["fields"]["kad_publication_date"]["value"] = "05.10.2026"
    human["fields"]["creditor"]["value"] = "Непроверенная правка"
    (folder / "human.json").write_text(json.dumps(human, ensure_ascii=False), encoding="utf-8")
    (folder / "ocr.json").write_text(json.dumps({"fields": fields, "raw_text": text}, ensure_ascii=False), encoding="utf-8")
    (folder / "source.pdf").write_bytes(b"synthetic original bytes")
    before = {f.name: f.read_bytes() for f in folder.iterdir()}
    preparer().prepare(source, output)
    assert before == {f.name: f.read_bytes() for f in folder.iterdir()}
    assert all((output / folder.name / name).read_bytes() == content for name, content in before.items())
    labels = json.loads((output / folder.name / "document_labels.json").read_text(encoding="utf-8"))["fields"]
    external = json.loads((output / folder.name / "external_data.json").read_text(encoding="utf-8"))["fields"]
    pending = json.loads((output / folder.name / "review_required.json").read_text(encoding="utf-8"))["fields"]
    assert labels["kad_publication_date"]["value"] is None
    assert external["kad_publication_date"]["value"] == "05.10.2026"
    assert "creditor" not in labels  # Masked, NOT a null target.
    assert pending["creditor"]["value"] == "Непроверенная правка"
    assert labels["claims"]["value"][0]["total_amount"] == "235242.76"
