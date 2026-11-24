import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services import rtk3_regex_jev as service


@pytest.mark.parametrize("printed,registry,model,expected,route", [
    ("1111111111", "1111111111", None, "1111111111", "dadata_party_registry"),
    ("1111111111", "2222222222", None, "2222222222", "dadata_party_registry"),
    ("1111111111", None, None, "1111111111", "document_creditor_inn"),
    (None, "2222222222", None, "2222222222", "dadata_party_registry"),
    (None, None, "3333333333", "3333333333", "gemini_argon"),
    (None, None, "4444444444", None, "gemini_argon"),
], ids=["match", "registry-wins", "registry-unavailable", "registry-only", "model-only", "unquoted-model"])
@pytest.mark.asyncio
async def test_creditor_inn_resolution(monkeypatch, printed, registry, model, expected, route):
    company = "ООО «Пример»"
    # Reverse order is readable by the model but does not match the name-before-INN source rule.
    record = f"{company} (ИНН {printed})" if printed else f"ИНН 3333333333 принадлежит кредитору {company}"
    text = f"ОПРЕДЕЛИЛ: Включить требование {company} в третью очередь. {record}."
    lookup = Mock(return_value=registry)
    monkeypatch.setattr(service, "registered_company_inn", lookup)
    calls = []
    def gateway(**request):
        kind = request["request_type"]
        if kind == "decisions":
            return None
        props = request["response_schema"]["json_schema"]["schema"]["properties"]
        if kind == "rtk3_creditor_inn":
            calls.append(kind)
            assert set(props) == {"creditor_inn"}
            assert json.loads(request["user_content"])["creditor_subject"] == company
            return SimpleNamespace(parsed={"creditor_inn": {"value": model, "quote": record}})
        assert "creditor_inn" not in props
        return SimpleNamespace(parsed={"creditor": {"value": company, "quote": f"требование {company}"}})
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    fields = await service.extract_fields(text, load_profile("rtk3"))
    assert fields["creditor_inn"]["value"] == expected
    assert fields["creditor_inn"]["extraction_route"] == route
    lookup.assert_called_once_with(company, None)
    assert calls == (["rtk3_creditor_inn"] if model else [])


@pytest.mark.parametrize("name,other,inn,ogrn,kind", [
    ("ООО МФК «Быстроденьги»", "МФК Быстроденьги (ООО)", "7325081622", "1000000000000", "LEGAL"),
    ("ИП Иванов Иван Иванович", "Иванов Иван Иванович (ИП)", "123456789012", "123456789012345", "INDIVIDUAL"),
])
def test_registry_form_in_name(monkeypatch, name, other, inn, ogrn, kind):
    data = {"inn": inn, "ogrn": ogrn, "type": kind, "branch_type": "MAIN", "state": {"status": "ACTIVE"}}
    monkeypatch.setattr(service, "dadata_party_suggestions", lambda _: [{"value": other, "data": data}])
    assert service.registered_company_inn(name, None) == inn
