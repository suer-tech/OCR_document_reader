import asyncio
import re
from typing import Any, Dict

from bs4 import BeautifulSoup
import requests
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel

from ocr_platform.observability.logging import get_logger

logger = get_logger(__name__)

# Pydantic schemas for structured outputs
from pydantic import Field

class CreditorResult(BaseModel):
    creditor_name: str | None = Field(description="Наименование кредитора, найденное в тексте, или null")
    creditor_name_web: str | None = Field(description="Наименование из интернета, если использовался поиск, иначе null")
    creditor_final: str | None = Field(description="ИТОГОВОЕ финальное наименование кредитора после анализа")
    confidence: float
    reasoning: str

class CreditorInnResult(BaseModel):
    INN: str | None = Field(description="10 или 12 цифр ИНН кредитора")
    confidence: float
    reasoning: str

class ClaimsAmountResult(BaseModel):
    commitments_count: int | None = Field(description="Количество отдельных обязательств/договоров, или null")
    amounts: list[float] | None = Field(description="Список сумм для каждого обязательства, или null")
    confidence: float
    reasoning: str

class GroundsResult(BaseModel):
    grounds: str | None = Field(description="Точное значение из списка допустимых оснований, либо null")
    confidence: float
    reasoning: str

class TaxCreditorHeaderResult(BaseModel):
    creditor_header: str | None = Field(description="Наименование налоговой из шапки документа или null")
    confidence: float
    reasoning: str

class GenericFieldResult(BaseModel):
    value: Any = Field(description="Извлеченное значение поля, либо null")
    confidence: float
    reasoning: str | None

from ocr_platform.services.agent_tools import search_creditor_inn, search_creditor_name, _search_by_inn


def _clean_llm_string(s: str | None) -> str | None:
    """Удаляет экранирующие бэкслеши и лишние кавычки только по краям строки."""
    if s is None:
        return None
    # Сначала удаляем любые литеральные бэкслеши
    s = s.replace('\\"', '"').replace("\\'", "'").replace('\\', '').strip()
    
    # Удаляем кавычки, только если они обертывают всю строку (например, "ООО Ромашка")
    if len(s) >= 2 and ((s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'"))):
        s = s[1:-1].strip()
        
    # Если внутри остались прямые двойные кавычки, заменяем их на ёлочки «»,
    # чтобы при сериализации в JSON не появлялись экранирующие слэши (\")
    if '"' in s:
        parts = s.split('"')
        new_s = ""
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                new_s += part
            else:
                new_s += part + ("«" if i % 2 == 0 else "»")
        s = new_s
        
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

# Agents definitions
SYSTEM_PROMPT = (
    "You are an expert legal document analyst. "
    "Extract the requested field accurately based on the provided text and instructions. "
    "IMPORTANT: You MUST respond ONLY with raw, valid JSON matching the requested schema. "
    "Do not wrap the JSON in markdown blocks like ```json ... ```. "
    "Do not include any other text."
)

agent_generic = Agent(
    model, deps_type=str, result_type=GenericFieldResult, retries=3, system_prompt=SYSTEM_PROMPT
)

agent_generic_with_tools = Agent(
    model, deps_type=str, result_type=GenericFieldResult, retries=3, system_prompt=SYSTEM_PROMPT
)
agent_generic_with_tools.tool(search_creditor_inn)
agent_generic_with_tools.tool(search_creditor_name)

agent_creditor = Agent(
    model, deps_type=str, result_type=CreditorResult, retries=3, system_prompt=SYSTEM_PROMPT
)
agent_creditor.tool(search_creditor_inn)
agent_creditor.tool(search_creditor_name)

agent_creditor_inn = Agent(
    model, deps_type=str, result_type=CreditorInnResult, retries=3, system_prompt=SYSTEM_PROMPT
)
agent_creditor_inn.tool(search_creditor_inn)
agent_creditor_inn.tool(search_creditor_name)

agent_claims_amount = Agent(
    model, deps_type=str, result_type=ClaimsAmountResult, retries=3, system_prompt=SYSTEM_PROMPT
)

agent_grounds = Agent(
    model, deps_type=str, result_type=GroundsResult, retries=3, system_prompt=SYSTEM_PROMPT
)

agent_tax_creditor = Agent(
    model, deps_type=str, result_type=TaxCreditorHeaderResult, retries=3, system_prompt=SYSTEM_PROMPT
)

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

                if field_name == "creditor":
                    if is_tax_document:
                        prompt_instruction = """МЕТОДОЛОГИЯ АНАЛИЗА (SGR / Step-by-Step Reasoning):
      Сначала выполни пошаговые рассуждения в поле "reasoning" строго следуя шагам:
      1. Шаг 1: Найди наименование и номер межрайонной налоговой инспекции в шапке документа.
      2. Шаг 2. Верни значение в поле creditor_header, оцени confidence."""
                        base_prompt = f"Instruction: {prompt_instruction}\n\nDocument Text:\n{text[:10000]}"
                        
                        agent = agent_tax_creditor
                        max_attempts = 3
                        val = None
                        confidence = 0.0
                        reasoning = ""
                        for attempt in range(1, max_attempts + 1):
                            try:
                                result = await agent.run(base_prompt, deps=text)
                                val = result.data.creditor_header
                                confidence = result.data.confidence
                                reasoning = result.data.reasoning
                                break
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
                        
                    agent = agent_creditor
                    CREDITOR_RETRIES = 3
                    val = None
                    confidence = 0.0
                    reasoning = ""

                    for creditor_attempt in range(1, CREDITOR_RETRIES + 1):
                        current_prompt = base_prompt
                        if creditor_attempt > 1:
                            if not val and known_inn:
                                logger.info(f"Creditor extraction retry {creditor_attempt}/{CREDITOR_RETRIES} due to null value with known INN {known_inn}")
                                current_prompt = (
                                    f"{base_prompt}\n\n"
                                    f"CRITICAL WARNING: You previously returned null for the creditor name. "
                                    f"Однако, ИНН кредитора ИЗВЕСТЕН: '{known_inn}'.\n"
                                    f"Ты ОБЯЗАН прямо сейчас использовать инструмент `search_creditor_name`, передав туда этот ИНН '{known_inn}'. "
                                    f"Дождись ответа от инструмента и запиши полученное официальное наименование в поле 'creditor_final'. "
                                    f"Ни в коем случае не возвращай null!"
                                )
                            else:
                                logger.info(f"Creditor extraction retry {creditor_attempt}/{CREDITOR_RETRIES} due to recognition error")

                        max_attempts = 3
                        for attempt in range(1, max_attempts + 1):
                            try:
                                result = await agent.run(current_prompt, deps=text)
                                break
                            except Exception as e:
                                logger.warning(f"LLM call attempt {attempt} failed for field creditor: {e}")
                                if attempt == max_attempts:
                                    raise e
                                if attempt == 2:
                                    logger.info("Pausing for 15 seconds after the second failed attempt...")
                                    await asyncio.sleep(15)

                        tool_called = False
                        try:
                            for msg in result.all_messages():
                                if hasattr(msg, 'parts'):
                                    for part in msg.parts:
                                        if hasattr(part, 'tool_name') and getattr(part, 'tool_name') == 'search_creditor_name':
                                            tool_called = True
                        except Exception as e:
                            logger.error(f"Error checking tool calls: {e}")
                        
                        if not is_tax_document and known_inn and not tool_called:
                            cleaned_inn = "".join(c for c in str(known_inn) if c.isdigit())
                            if len(cleaned_inn) in (10, 12):
                                logger.info(f"LLM hallucinated and didn't call search_creditor_name. Forcing manual call for INN: {cleaned_inn}")
                                tool_result_text = _search_by_inn(cleaned_inn) or "Company name not found"
                                
                                forced_prompt = (
                                    f"{current_prompt}\n\n"
                                    f"ВНИМАНИЕ! Ты проигнорировал требование вызвать инструмент поиска имени по ИНН. Я вызвал его принудительно.\n"
                                    f"Результат поиска по ИНН {cleaned_inn}:\n{tool_result_text}\n"
                                    f"Учитывая эти данные поиска, извлеки корректное официальное наименование кредитора и верни JSON."
                                )
                                try:
                                    result = await agent.run(forced_prompt, deps=text)
                                except Exception as e:
                                    logger.warning(f"Forced LLM call failed for field creditor: {e}")

                        data = result.data
                        val = _clean_llm_string(data.creditor_final)
                        confidence = data.confidence
                        
                        sgr_reasoning_parts = []
                        if data.creditor_name: sgr_reasoning_parts.append(f"creditor_name: {data.creditor_name}")
                        if data.creditor_name_web: sgr_reasoning_parts.append(f"creditor_name_web: {data.creditor_name_web}")
                        reasoning = f"{data.reasoning} | SGR: {'; '.join(sgr_reasoning_parts)}"

                        # Сверка названия кредитора по ИНН через интернет
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

                        if val != "Ошибка распознавания":
                            if not val and known_inn:
                                if creditor_attempt < CREDITOR_RETRIES:
                                    continue
                            break

                elif field_name == "claims_amount":
                    agent = agent_claims_amount
                    max_attempts = 3
                    for attempt in range(1, max_attempts + 1):
                        try:
                            result = await agent.run(base_prompt, deps=text)
                            break
                        except Exception as e:
                            logger.warning(f"LLM call attempt {attempt} failed for field claims_amount: {e}")
                            if attempt == max_attempts:
                                raise e
                            if attempt == 2:
                                await asyncio.sleep(15)

                    data = result.data
                    confidence = data.confidence
                    reasoning = data.reasoning
                    
                    if data.commitments_count is not None and isinstance(data.amounts, list) and len(data.amounts) > 0:
                        total = sum(data.amounts)
                        val = f"{total:.2f}" if total > 0 else None
                    else:
                        val = None

                elif field_name == "grounds":
                    # Парсим допустимые значения из промпта
                    parsed_grounds = re.findall(r'-\s*"([^"]+)"', prompt_instruction)
                    VALID_GROUNDS = parsed_grounds if parsed_grounds else [
                        "договор на предоставление коммунальных услуг",
                        "кредитный договор",
                        "соглашение о кредитовании",
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
                    agent = agent_grounds
                    max_attempts = 3
                    val = None
                    confidence = 0.0
                    reasoning = ""

                    current_prompt = base_prompt

                    for attempt in range(1, max_attempts + 1):
                        try:
                            result = await agent.run(current_prompt, deps=text)
                        except Exception as e:
                            logger.warning(f"LLM call attempt {attempt} failed for field grounds: {e}")
                            if attempt == max_attempts:
                                raise e
                            await asyncio.sleep(5)
                            continue

                        data = result.data
                        confidence = data.confidence
                        reasoning = data.reasoning
                        
                        clean_val_str = "null"
                        if data.grounds:
                            clean_val_str = str(data.grounds).strip().lower().strip("'.\",")

                        if clean_val_str in ("null", "none", ""):
                            val = None
                            break

                        # Проверяем регулярками (принудительно для РТК, но можно и для всех)
                        if profile_id == "rtk":
                            matched_ground = None
                            for vg in VALID_GROUNDS:
                                if re.search(r'\b' + re.escape(vg.lower()) + r'(?:$|\b)', clean_val_str, re.IGNORECASE | re.UNICODE):
                                    matched_ground = vg
                                    break

                            if matched_ground:
                                val = matched_ground
                                break
                            else:
                                logger.warning(f"Grounds validation failed on attempt {attempt}: '{clean_val_str}' not in VALID_GROUNDS.")
                                if attempt < max_attempts:
                                    current_prompt = (
                                        f"{base_prompt}\n\n"
                                        f"CRITICAL WARNING: В предыдущей попытке ты вернул значение '{clean_val_str}', которое НЕ ЯВЛЯЕТСЯ точным совпадением.\n"
                                        f"Твой ответ должен СТРОГО соответствовать одному из этих значений: {', '.join(VALID_GROUNDS)}.\n"
                                        f"Если в тексте нет ничего похожего, верни null. Не придумывай свои варианты!"
                                    )
                                else:
                                    # Принудительная проверка - если так и не совпало, возвращаем null
                                    val = None
                        else:
                            # Для других профилей просто берем значение
                            val = clean_val_str
                            break

                elif field_name == "creditor_inn":
                    agent = agent_creditor_inn if extraction_method == "llm_with_tools" else agent_generic
                    max_attempts = 3
                    for attempt in range(1, max_attempts + 1):
                        try:
                            result = await agent.run(base_prompt, deps=text)
                            break
                        except Exception as e:
                            logger.warning(f"LLM extraction attempt {attempt} failed for field {field_name}: {e}")
                            if attempt == max_attempts:
                                raise e
                            if attempt == 2:
                                await asyncio.sleep(15)

                    data = result.data
                    confidence = data.confidence
                    reasoning = data.reasoning
                    val = None
                    
                    if hasattr(data, "INN"):
                        if data.INN:
                            inn_match = re.search(r'\d{10,12}', str(data.INN))
                            val = inn_match.group(0) if inn_match else data.INN
                    else:
                        if data.value:
                            inn_match = re.search(r'\d{10,12}', str(data.value))
                            val = inn_match.group(0) if inn_match else data.value

                else:
                    agent = agent_generic_with_tools if extraction_method == "llm_with_tools" else agent_generic
                    max_attempts = 3
                    for attempt in range(1, max_attempts + 1):
                        try:
                            result = await agent.run(base_prompt, deps=text)
                            break
                        except Exception as e:
                            logger.warning(f"LLM extraction attempt {attempt} failed for field {field_name}: {e}")
                            if attempt == max_attempts:
                                raise e
                            if attempt == 2:
                                await asyncio.sleep(15)

                    data = result.data
                    val = data.value
                    confidence = data.confidence
                    reasoning = data.reasoning

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
