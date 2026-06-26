import asyncio
import re
from typing import Any, Dict

from bs4 import BeautifulSoup
import requests
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel

from ocr_platform.observability.logging import get_logger

logger = get_logger(__name__)

# Простая модель для структурированного вывода LLM для всех полей
class FieldResult(BaseModel):
    value: Any
    confidence: float
    reasoning: str | None

from ocr_platform.services.agent_tools import search_creditor_inn, search_creditor_name, _search_by_inn


def _clean_llm_string(s: str | None) -> str | None:
    """Удаляет экранирующие бэкслеши и все кавычки в строке, возвращённой LLM."""
    if s is None:
        return None
    s = s.replace('\\"', '"').replace("\\'", "'")
    s = s.replace('"', '').replace("'", '')
    return s

from pydantic_ai.models.openai import OpenAIModel
from ocr_platform.config.settings import get_settings

settings = get_settings()

base_url = settings.ollama_ocr_url.rstrip("/")
if base_url.endswith("/api/chat"):
    base_url = base_url[:-9]
if not base_url.endswith("/v1"):
    base_url = f"{base_url}/v1"

# Initialize model
model = OpenAIModel(
    "gpt-oss:20b",
    base_url=base_url,
    api_key=settings.ollama_ocr_token or "ollama"
)

# PydanticAI Agent without tools
extraction_agent = Agent(
    model,
    deps_type=str,  # Document text
    result_type=FieldResult,
    retries=2,
    system_prompt=(
        "You are an expert legal document analyst. "
        "Extract the requested field accurately based on the provided text and instructions. "
        "IMPORTANT: You MUST respond ONLY with raw, valid JSON matching the requested schema. "
        "Do not wrap the JSON in markdown blocks like ```json ... ```. "
        "Do not include any other text."
    ),
)

# PydanticAI Agent with tools
extraction_agent_with_tools = Agent(
    model,
    deps_type=str,  # Document text
    result_type=FieldResult,
    retries=2,
    system_prompt=(
        "You are an expert legal document analyst. "
        "Extract the requested field accurately based on the provided text and instructions. "
        "IMPORTANT: You MUST respond ONLY with raw, valid JSON matching the requested schema. "
        "Do not wrap the JSON in markdown blocks like ```json ... ```. "
        "Do not include any other text."
    ),
)
extraction_agent_with_tools.tool(search_creditor_inn)
extraction_agent_with_tools.tool(search_creditor_name)

# Вспомогательные модели и агенты для верификации кредитора
class CompanyNameResult(BaseModel):
    company_name: str | None
    reasoning: str | None

class CompanyComparisonResult(BaseModel):
    is_same: bool
    difference_type: str  # "exact", "minor", "critical"
    reasoning: str

company_name_extraction_agent = Agent(
    model,
    result_type=CompanyNameResult,
    system_prompt=(
        "You are an expert business registrar analyst. "
        "Analyze the provided search results to find the official company name or organization name corresponding to the given INN. "
        "Return the name clearly in the company_name field. If no company name is found, return null."
    )
)

company_comparison_agent = Agent(
    model,
    result_type=CompanyComparisonResult,
    system_prompt=(
        "You are an expert entity resolution system. "
        "Compare two organization names: one extracted from the document via OCR, and the other found in the official registry/internet by INN. "
        "Determine if they represent the same legal entity/organization. "
        "Classify the difference as:\n"
        "- 'exact': the names are identical or have only minor formatting differences (e.g. quotes, lowercase/uppercase, spacing).\n"
        "- 'minor': there are small typos/OCR errors (e.g. one or a few characters differ) or minor abbreviation differences (e.g. ООО vs Общество с ограниченной ответственностью), but they clearly refer to the same entity.\n"
        "- 'critical': the names are completely different and refer to different entities."
    )
)

async def extract_company_name_from_search(inn: str, search_text: str) -> str | None:
    prompt = f"Official INN: {inn}\n\nSearch Results:\n{search_text}"
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            result = await company_name_extraction_agent.run(prompt)
            return result.data.company_name
        except Exception as e:
            logger.warning(f"LLM company name extraction attempt {attempt} failed: {e}")
            if attempt == max_attempts:
                return None
            if attempt == 2:
                logger.info("Pausing for 15 seconds after the second failed attempt...")
                await asyncio.sleep(15)
    return None

async def compare_company_names(llm_name: str, web_name: str) -> CompanyComparisonResult:
    prompt = f"Name Extracted from OCR: {llm_name}\nName Found in Registry by INN: {web_name}"
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            result = await company_comparison_agent.run(prompt)
            return result.data
        except Exception as e:
            logger.warning(f"LLM company comparison attempt {attempt} failed: {e}")
            if attempt == max_attempts:
                return CompanyComparisonResult(
                    is_same=False,
                    difference_type="critical",
                    reasoning=f"Comparison failed with exception: {e}"
                )
            if attempt == 2:
                logger.info("Pausing for 15 seconds after the second failed attempt...")
                await asyncio.sleep(15)
    return CompanyComparisonResult(
        is_same=False,
        difference_type="critical",
        reasoning="Failed to compare due to unknown errors."
    )

async def run_agent_extraction(
    text: str,
    fields_config: Dict[str, Any],
    profile_id: str | None = None
) -> Dict[str, dict]:
    results = {}

    is_tax_document = False
    if profile_id == "rtk":
        header_text = text[:2000]
        tax_pattern = r"(МИНФИН РОССИИ ФЕДЕРАЛЬНАЯ НАЛОГОВАЯ СЛУЖБА|МЕЖРАЙОННАЯ ИНСПЕКЦИЯ|ФЕДЕРАЛЬНОЙ НАЛОГОВОЙ СЛУЖБЫ)"
        if re.search(tax_pattern, header_text, re.IGNORECASE):
            is_tax_document = True

    ordered_fields = list(fields_config.keys())
    if is_tax_document:
        if "creditor" in ordered_fields and "creditor_inn" in ordered_fields:
            ordered_fields.remove("creditor")
            ordered_fields.insert(0, "creditor")
    else:
        # Упорядочиваем поля, чтобы creditor_inn обрабатывался раньше creditor
        if "creditor_inn" in ordered_fields and "creditor" in ordered_fields:
            ordered_fields.remove("creditor_inn")
            ordered_fields.insert(0, "creditor_inn")

    for field_name in ordered_fields:
        field_def = fields_config[field_name]
        extraction_method = field_def.get("extraction_method", "llm")
        logger.info(f"Extracting field {field_name} using method {extraction_method}")

        if profile_id == "rtk" and not is_tax_document and field_name == "creditor_inn":
            # Ищем первое упоминание ИНН и 10 или 12 цифр после него
            inn_match = re.search(r'(?i:инн)[^\d]*(\d{10}|\d{12})\b', text)
            if inn_match:
                found_inn = inn_match.group(1)
                logger.info(f"Creditor INN found via regex: {found_inn}")
                results[field_name] = {
                    "value": found_inn,
                    "confidence": 1.0,
                    "reasoning": "Found INN via regex (first occurrence after 'ИНН')",
                    "source": "regex_fallback"
                }
                continue

        if extraction_method == "regex":
            pattern = field_def.get("regex_pattern")
            if pattern:
                match = re.search(pattern, text, re.IGNORECASE)
                val = match.group(1) if match else None
                results[field_name] = {
                    "value": val,
                    "confidence": 1.0 if val else 0.0,
                    "reasoning": "Regex match",
                    "source": "regex"
                }
            else:
                results[field_name] = {"value": None, "confidence": 0.0, "reasoning": "No pattern"}

        elif extraction_method in ["llm", "llm_with_tools", "llm_claims_amount"]:
            prompt_instruction = field_def.get("prompt_instruction", "")
            base_prompt = f"Instruction: {prompt_instruction}\n\nDocument Text:\n{text[:10000]}"

            try:
                if is_tax_document and field_name == "creditor_inn":
                    creditor_name = results.get("creditor", {}).get("value")
                    if creditor_name:
                        logger.info(f"Tax document detected. Injecting creditor_name '{creditor_name}' into creditor_inn prompt")
                        base_prompt = f"ВНИМАНИЕ! Для данного документа мы уже определили точное наименование кредитора (налогового органа): '{creditor_name}'.\nТы ОБЯЗАН использовать именно это наименование '{creditor_name}' для вызова инструмента search_creditor_inn, чтобы найти его ИНН в интернете.\n\n{base_prompt}"
                        # Allow to fall through to normal LLM extraction

                # Специальная логика для creditor (JSON валидация, ретраи с усилением промпта и проверка по ИНН)
                if field_name == "creditor":
                    if is_tax_document:
                        prompt_instruction = """МЕТОДОЛОГИЯ АНАЛИЗА (SGR / Step-by-Step Reasoning):
      Сначала выполни пошаговые рассуждения в поле "reasoning" строго следуя шагам:
      1. Шаг 1: Найди наименование и номер межрайонной налоговой инспекции в шапке документа. Оно может начинаться со слова "Межрайонная" и заканчиваться наименованием области или республики или края. Например: "МЕЖРАЙОННАЯ ИНСПЕКЦИЯ ФЕДЕРАЛЬНОЙ НАЛОГОВОЙ СЛУЖБЫ № 5 ПО ОМСКОЙ ОБЛАСТИ" или "МЕЖРАЙОННАЯ ИНСПЕКЦИЯ ФЕДЕРАЛЬНОЙ НАЛОГОВОЙ СЛУЖБЫ № 9 ПО РЕСПУБЛИКЕ БАШКОРТОСТАН"
              
      2. Шаг 2. ДАЙ ОТВЕТ В ФОРМАТЕ:
      Верни JSON-объект со следующими полями:
      {
        "creditor_header": "наименование и номер межрайонной налоговой испекции из шапки документа или null"
      }      
      Если наименование межрайонной инспекции не удалось определить — верни null в "creditor_header".
      
      В поле "confidence" оцени уверенность (0.9 если уверен, 0.5 если сомневаешься)."""
                        base_prompt = f"Instruction: {prompt_instruction}\n\nDocument Text:\n{text[:10000]}"
                        
                        max_attempts = 3
                        val = None
                        confidence = 0.0
                        reasoning = ""
                        for attempt in range(1, max_attempts + 1):
                            try:
                                result = await extraction_agent.run(base_prompt, deps=text)
                                raw_val = result.data.value
                                confidence = result.data.confidence
                                reasoning = result.data.reasoning
                                
                                if isinstance(raw_val, dict) and "creditor_header" in raw_val:
                                    val = raw_val["creditor_header"]
                                    break
                                elif isinstance(raw_val, str):
                                    stripped = raw_val.strip()
                                    if stripped.startswith("{"):
                                        try:
                                            import json
                                            parsed = json.loads(stripped)
                                            val = parsed.get("creditor_header")
                                            break
                                        except Exception:
                                            pass
                            except Exception as e:
                                logger.warning(f"LLM extraction attempt {attempt} failed for tax creditor: {e}")
                                if attempt == max_attempts:
                                    raise e
                                if attempt == 2:
                                    await asyncio.sleep(15)
                        
                        results[field_name] = {
                            "value": _clean_llm_string(val),
                            "confidence": confidence,
                            "reasoning": reasoning,
                            "source": "llm_tax"
                        }
                        continue

                    # Передаем найденный ИНН в промпт для поля кредитора
                    known_inn = results.get("creditor_inn", {}).get("value")
                    if known_inn:
                        base_prompt = f"ВНИМАНИЕ! Ранее по этому документу был найден ИНН кредитора: {known_inn}\nИспользуй этот ИНН для вызова инструмента search_creditor_name.\n\n{base_prompt}"
                        
                    CREDITOR_KEY = "creditor_final"
                    CREDITOR_RETRIES = 3
                    val = None
                    confidence = 0.0
                    reasoning = ""

                    for creditor_attempt in range(1, CREDITOR_RETRIES + 1):
                        is_null_retry = False
                        if creditor_attempt > 1:
                            if not val and known_inn:
                                is_null_retry = True
                                logger.info(f"Creditor extraction retry {creditor_attempt}/{CREDITOR_RETRIES} due to null value with known INN {known_inn}")
                            else:
                                logger.info(f"Creditor extraction retry {creditor_attempt}/{CREDITOR_RETRIES} due to recognition error")
                            val = None
                            confidence = 0.0
                            reasoning = ""

                        max_format_attempts = 3
                        for format_attempt in range(1, max_format_attempts + 1):
                            if format_attempt > 1:
                                current_prompt = (
                                    f"{base_prompt}\n\n"
                                    f"CRITICAL WARNING: Your previous response did not follow the required JSON structure. "
                                    f"You MUST return a JSON object containing the key 'creditor_final'."
                                )
                            elif is_null_retry:
                                current_prompt = (
                                    f"{base_prompt}\n\n"
                                    f"CRITICAL WARNING: You previously returned null for the creditor name. "
                                    f"Однако, ИНН кредитора ИЗВЕСТЕН: '{known_inn}'.\n"
                                    f"Ты ОБЯЗАН прямо сейчас использовать инструмент `search_creditor_name`, передав туда этот ИНН '{known_inn}'. "
                                    f"Дождись ответа от инструмента и запиши полученное официальное наименование в поле 'creditor_final'. "
                                    f"Ни в коем случае не возвращай null!"
                                )
                            else:
                                current_prompt = base_prompt

                            # Вызов LLM с ретраями при любых ошибках
                            max_attempts = 3
                            for attempt in range(1, max_attempts + 1):
                                try:
                                    agent = extraction_agent_with_tools if extraction_method == "llm_with_tools" else extraction_agent
                                    result = await agent.run(current_prompt, deps=text)
                                    break
                                except Exception as e:
                                    logger.warning(f"LLM call attempt {attempt} failed for field creditor: {e}")
                                    if attempt == max_attempts:
                                        raise e
                                    if attempt == 2:
                                        logger.info("Pausing for 15 seconds after the second failed attempt...")
                                        await asyncio.sleep(15)

                            raw_val = result.data.value
                            confidence = result.data.confidence
                            reasoning = result.data.reasoning

                            # Валидация формата
                            is_valid_format = False
                            creditor_name_extracted = None
                            sgr_data = {}

                            if isinstance(raw_val, dict):
                                sgr_data = raw_val
                                if CREDITOR_KEY in raw_val:
                                    creditor_name_extracted = raw_val[CREDITOR_KEY]
                                    is_valid_format = True
                            elif isinstance(raw_val, str):
                                stripped = raw_val.strip()
                                if stripped.startswith("{"):
                                    try:
                                        import ast
                                        parsed = ast.literal_eval(stripped)
                                        if isinstance(parsed, dict):
                                            sgr_data = parsed
                                            if CREDITOR_KEY in parsed:
                                                creditor_name_extracted = parsed[CREDITOR_KEY]
                                                is_valid_format = True
                                    except Exception:
                                        try:
                                            import json
                                            parsed = json.loads(stripped)
                                            if isinstance(parsed, dict):
                                                sgr_data = parsed
                                                if CREDITOR_KEY in parsed:
                                                    creditor_name_extracted = parsed[CREDITOR_KEY]
                                                    is_valid_format = True
                                        except Exception:
                                            pass

                            if is_valid_format:
                                # Обогащаем reasoning SGR-данными
                                sgr_reasoning_parts = []
                                for k in ("creditor_name", "creditor_name_web", "creditor_header", "creditor_inn", "creditor_body", "creditor_web"):
                                    if k in sgr_data and sgr_data[k]:
                                        sgr_reasoning_parts.append(f"{k}: {sgr_data[k]}")
                                if sgr_reasoning_parts:
                                    reasoning = f"{reasoning} | SGR: {'; '.join(sgr_reasoning_parts)}"
                                
                                if "confidence" in sgr_data and sgr_data["confidence"] is not None:
                                    try:
                                        confidence = float(sgr_data["confidence"])
                                    except ValueError:
                                        pass

                                val = _clean_llm_string(creditor_name_extracted)
                                break
                            elif isinstance(raw_val, str) and len(raw_val.strip()) > 0 and not raw_val.strip().startswith("{"):
                                # Fallback: treat string as the value напрямую, если модель упорно отказывается выдать JSON
                                val = _clean_llm_string(raw_val)
                                confidence = 0.5
                                reasoning = f"{reasoning} | Fallback: accepted raw string as creditor name, reduced confidence."
                                break
                            else:
                                logger.warning(f"Invalid format for creditor field on format attempt {format_attempt}: {raw_val}")
                                if format_attempt == max_format_attempts:
                                    raise ValueError(f"Failed to obtain valid JSON format with '{CREDITOR_KEY}' after {max_format_attempts} attempts. Last value: {raw_val}")

                        # Сверка названия кредитора по ИНН через интернет (только если есть val)
                        if val:
                            inn = results.get("creditor_inn", {}).get("value")
                            if inn:
                                cleaned_inn = "".join(c for c in str(inn) if c.isdigit())
                                if len(cleaned_inn) in (10, 12):
                                    web_search_text = _search_by_inn(cleaned_inn)
                                    if web_search_text:
                                        web_company_name = _clean_llm_string(await extract_company_name_from_search(cleaned_inn, web_search_text))
                                        if web_company_name:
                                            comp_res = await compare_company_names(val, web_company_name)
                                            if comp_res.is_same:
                                                if comp_res.difference_type == "exact":
                                                    pass
                                                elif comp_res.difference_type == "minor":
                                                    logger.info(f"Minor difference: doc='{val}', web='{web_company_name}'. Correcting to web name, setting confidence to 50%.")
                                                    val = _clean_llm_string(web_company_name)
                                                    confidence = 0.5
                                                    reasoning = f"{reasoning} | Corrected from web registry by INN {cleaned_inn} (original: {val}, internet: {web_company_name}, confidence reduced to 50% due to minor mismatch)."
                                                else:
                                                    logger.warning(f"Critical difference: doc='{val}', web='{web_company_name}'. Returning recognition error.")
                                                    val = "Ошибка распознавания"
                                                    confidence = 0.0
                                                    reasoning = f"{reasoning} | CRITICAL MISMATCH with registry for INN {cleaned_inn} (internet: {web_company_name})."
                                            else:
                                                logger.warning(f"Names do not match: doc='{val}', web='{web_company_name}'. Returning recognition error.")
                                                val = "Ошибка распознавания"
                                                confidence = 0.0
                                                reasoning = f"{reasoning} | CRITICAL MISMATCH with registry for INN {cleaned_inn} (internet: {web_company_name})."

                        # Если получили нормальное значение — выходим из цикла ретраев
                        if val != "Ошибка распознавания":
                            if not val and known_inn:
                                if creditor_attempt < CREDITOR_RETRIES:
                                    continue # Идем на ретрай с усиленным промптом
                            break

                elif field_name == "claims_amount":
                    CLAIMS_RETRIES = 3
                    val = None
                    confidence = 0.0
                    reasoning = ""

                    for claims_attempt in range(1, CLAIMS_RETRIES + 1):
                        if claims_attempt > 1:
                            logger.info(f"Claims_amount retry {claims_attempt}/{CLAIMS_RETRIES} due to null value")
                            val = None
                            confidence = 0.0
                            reasoning = ""

                        max_format_attempts = 3
                        for format_attempt in range(1, max_format_attempts + 1):
                            if format_attempt > 1:
                                current_prompt = (
                                    f"{base_prompt}\n\n"
                                    f"CRITICAL WARNING: Your previous response did not follow the required JSON structure. "
                                    f"You MUST return a JSON object with keys 'commitments_count' (integer) and 'amounts' (list of plain numbers). "
                                    f"Format: {{\"commitments_count\": <int>, \"amounts\": [<float>, ...]}}"
                                    f"Do NOT include textual descriptions, units, or non-numeric characters in the amounts."
                                )
                            else:
                                current_prompt = base_prompt

                            max_attempts = 3
                            for attempt in range(1, max_attempts + 1):
                                try:
                                    result = await extraction_agent.run(current_prompt, deps=text)
                                    break
                                except Exception as e:
                                    logger.warning(f"LLM call attempt {attempt} failed for field claims_amount: {e}")
                                    if attempt == max_attempts:
                                        raise e
                                    if attempt == 2:
                                        logger.info("Pausing for 15 seconds after the second failed attempt...")
                                        await asyncio.sleep(15)

                            raw_value = result.data.value
                            confidence = result.data.confidence
                            reasoning = result.data.reasoning

                            parsed = None
                            if isinstance(raw_value, dict):
                                parsed = raw_value
                            elif isinstance(raw_value, str):
                                stripped = raw_value.strip()
                                if stripped.startswith("{"):
                                    try:
                                        import ast
                                        parsed = ast.literal_eval(stripped)
                                    except Exception:
                                        try:
                                            import json
                                            parsed = json.loads(stripped)
                                        except Exception:
                                            pass

                            if isinstance(parsed, dict):
                                commitments_count = parsed.get("commitments_count")
                                amounts = parsed.get("amounts")

                                if commitments_count is not None and isinstance(amounts, list) and len(amounts) > 0:
                                    try:
                                        numeric_amounts = []
                                        for a in amounts:
                                            # Remove spaces and non-numeric chars except . and -
                                            cleaned = str(a).replace(" ", "").replace(",", ".")
                                            numeric_amounts.append(float(cleaned))
                                        total = sum(numeric_amounts)
                                        val = f"{total:.2f}" if total > 0 else None
                                        break
                                    except (ValueError, TypeError) as e:
                                        logger.warning(f"Claims_amount parse error (attempt {format_attempt}): {e}. Raw amounts: {amounts}")
                                        if format_attempt == max_format_attempts:
                                            val = None
                                            confidence = 0.0
                                            reasoning = f"Failed to parse numeric amounts after {max_format_attempts} attempts. Last value: {raw_value}"
                                else:
                                    logger.warning(f"Invalid claims_amount format (attempt {format_attempt}): missing commitments_count or amounts. Got: {parsed}")
                                    if format_attempt == max_format_attempts:
                                        val = None
                                        confidence = 0.0
                                        reasoning = f"Failed to obtain valid format after {max_format_attempts} attempts. Last value: {raw_value}"
                            else:
                                logger.warning(f"Invalid claims_amount format (attempt {format_attempt}): not a dict. Got: {raw_value}")
                                if format_attempt == max_format_attempts:
                                    val = None
                                    confidence = 0.0
                                    reasoning = f"Failed to obtain valid JSON format after {max_format_attempts} attempts. Last value: {raw_value}"

                        if val is not None:
                            break

                elif field_name == "grounds":
                    VALID_GROUNDS = [
                        "договор на предоставление коммунальных услуг",
                        "кредитный договор",
                        "договор потребительского микрозайма",
                        "договор потребительского займа",
                        "договор банковского счета",
                        "договор энергоснабжения",
                        "договор займа",
                        "задолженность по уплате налога",
                        "налоговая задолженность",
                        "исполнительный лист",
                        "исполнительный документ",
                        "судебный приказ",
                        "судебный акт",
                        "административное правонарушение"
                    ]
                    GROUNDS_RETRIES = 3
                    val = None
                    confidence = 0.0
                    reasoning = ""

                    for attempt in range(1, GROUNDS_RETRIES + 1):
                        current_prompt = base_prompt
                        if attempt > 1:
                            logger.info(f"Grounds retry {attempt}/{GROUNDS_RETRIES} due to invalid value: {val}")
                            valid_list_str = "\\n- ".join(VALID_GROUNDS)
                            current_prompt = (
                                f"{base_prompt}\n\n"
                                f"CRITICAL WARNING: Your previous response '{val}' is INVALID. "
                                f"You MUST return an EXACT match from the following list ONLY:\n- {valid_list_str}\n\n"
                                f"Do NOT invent new categories. Pick the closest exact match from the list above."
                            )

                        try:
                            agent = extraction_agent_with_tools if extraction_method == "llm_with_tools" else extraction_agent
                            result = await agent.run(current_prompt, deps=text)
                        except Exception as e:
                            logger.warning(f"LLM call attempt {attempt} failed for field grounds: {e}")
                            if attempt == GROUNDS_RETRIES:
                                raise e
                            await asyncio.sleep(5)
                            continue

                        raw_val = result.data.value
                        confidence = result.data.confidence
                        reasoning = result.data.reasoning

                        # Validate
                        if isinstance(raw_val, str):
                            clean_val = raw_val.strip().lower()
                            # Sometimes LLM puts quotes or periods at the end
                            clean_val = clean_val.strip("'.\",")
                            if clean_val in VALID_GROUNDS:
                                val = clean_val
                                break
                            else:
                                val = clean_val
                        else:
                            val = str(raw_val)

                else:
                    # Стандартная логика с ретраями для других полей
                    max_attempts = 3
                    for attempt in range(1, max_attempts + 1):
                        try:
                            agent = extraction_agent_with_tools if extraction_method == "llm_with_tools" else extraction_agent
                            result = await agent.run(base_prompt, deps=text)
                            break
                        except Exception as e:
                            logger.warning(f"LLM extraction attempt {attempt} failed for field {field_name}: {e}")
                            if attempt == max_attempts:
                                raise e
                            if attempt == 2:
                                logger.info("Pausing for 15 seconds after the second failed attempt...")
                                await asyncio.sleep(15)

                    val = result.data.value
                    confidence = result.data.confidence
                    reasoning = result.data.reasoning

                    # Постобработка для ИНН
                    if field_name == "creditor_inn":
                        inn_value = None
                        if isinstance(val, dict):
                            inn_value = val.get("INN") or val.get("inn") or val.get("value")
                        elif isinstance(val, str):
                            stripped = val.strip()
                            if stripped.startswith("{"):
                                try:
                                    import ast
                                    parsed = ast.literal_eval(stripped)
                                    if isinstance(parsed, dict):
                                        inn_value = parsed.get("INN") or parsed.get("inn") or parsed.get("value")
                                except (ValueError, SyntaxError):
                                    inn_match = re.search(r'\d{10,12}', stripped)
                                    inn_value = inn_match.group(0) if inn_match else stripped
                            else:
                                inn_match = re.search(r'\d{10,12}', stripped)
                                inn_value = inn_match.group(0) if inn_match else stripped
                        val = inn_value

                    elif isinstance(val, dict):
                        val = str(val)

                results[field_name] = {
                    "value": val,
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "source": extraction_method
                }
            except Exception as e:
                import pydantic_ai
                raw_text = "Unknown"
                if isinstance(e, pydantic_ai.exceptions.UnexpectedModelBehavior):
                    if hasattr(e, '__cause__') and e.__cause__:
                        raw_text = str(e.__cause__)
                logger.error(f"Failed to extract {field_name} with LLM: {e}. Raw cause: {raw_text}")
                results[field_name] = {
                    "value": None,
                    "confidence": 0.0,
                    "reasoning": str(e),
                    "source": extraction_method
                }

    return results
