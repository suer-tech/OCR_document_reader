import asyncio
import re
import tempfile
import os
import json
from pathlib import Path
from typing import Any, Dict, Literal

from bs4 import BeautifulSoup
import requests
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel

from ocr_platform.observability.logging import get_logger

logger = get_logger(__name__)

# Pydantic schemas for structured outputs
from pydantic import Field


class CreditorResult(BaseModel):
    creditor_name: str | None = Field(
        description="Наименование кредитора, найденное в тексте, или null"
    )
    creditor_name_web: str | None = Field(
        description="Наименование из интернета, если использовался поиск, иначе null"
    )
    creditor_final: str | None = Field(
        description="ИТОГОВОЕ финальное наименование кредитора после анализа"
    )
    confidence: float
    reasoning: str


class CreditorInnResult(BaseModel):
    INN: str | None = Field(description="10 или 12 цифр ИНН кредитора")
    confidence: float
    reasoning: str


class ClaimsAmountResult(BaseModel):
    commitments_count: int | None = Field(
        description="Количество отдельных обязательств/договоров, или null"
    )
    amounts: list[float] | None = Field(
        description="Список сумм для каждого обязательства, или null"
    )
    confidence: float
    reasoning: str


class GroundsResult(BaseModel):
    grounds: str | None = Field(
        description="Точное значение из списка допустимых оснований, либо null"
    )
    confidence: float
    reasoning: str


class TaxCreditorHeaderResult(BaseModel):
    creditor_header: str | None = Field(
        description="Наименование налоговой из шапки документа или null"
    )
    confidence: float
    reasoning: str


class RtkCombinedResult(BaseModel):
    creditor_inn: str | None = Field(
        description="ИНН кредитора (10 или 12 цифр), либо null"
    )
    creditor_inn_confidence: float
    creditor_inn_reasoning: str

    commitments_count: int | None = Field(
        description="Количество отдельных обязательств/договоров, или null"
    )
    amounts: list[float] | None = Field(
        description="Список сумм для каждого обязательства, или null"
    )
    claims_amount_confidence: float
    claims_amount_reasoning: str

    grounds: str | None = Field(
        description="Точное значение из списка допустимых оснований, либо null"
    )
    grounds_confidence: float
    grounds_reasoning: str


class GenericFieldResult(BaseModel):
    value: Any = Field(description="Извлеченное значение поля, либо null")
    confidence: float
    reasoning: str | None


# Alias for backwards compatibility with tests
FieldResult = GenericFieldResult

from ocr_platform.services.agent_tools import (
    search_creditor_inn,
    search_creditor_name,
    _search_by_inn,
)


def _clean_llm_string(s: str | None) -> str | None:
    """Удаляет экранирующие бэкслеши и лишние кавычки только по краям строки."""
    if s is None:
        return None
    # Сначала удаляем любые литеральные бэкслеши
    s = s.replace('\\"', '"').replace("\\'", "'").replace("\\", "").strip()

    # Удаляем кавычки, только если они обертывают всю строку (например, "ООО Ромашка")
    if len(s) >= 2 and (
        (s.startswith('"') and s.endswith('"'))
        or (s.startswith("'") and s.endswith("'"))
    ):
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


from pydantic_ai.models.openai import OpenAIModel, OpenAIAgentModel
from pydantic_ai.models import check_allow_model_requests
from openai import NOT_GIVEN
from ocr_platform.config.settings import get_settings


class CustomOpenAIAgentModel(OpenAIAgentModel):
    async def _completions_create(self, messages, stream, model_settings):
        from pydantic_ai.messages import (
            ModelResponse,
            ModelRequest,
            ToolCallPart,
            ToolReturnPart,
        )

        # Check if there are already any tool calls/returns in the history
        has_tool_calls = False
        for msg in messages:
            if isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if isinstance(part, ToolCallPart):
                        has_tool_calls = True
                        break
            elif isinstance(msg, ModelRequest):
                for part in msg.parts:
                    if isinstance(part, ToolReturnPart):
                        has_tool_calls = True
                        break
            if has_tool_calls:
                break

        # Check if we have search_creditor_name or search_creditor_inn tools
        search_tool_name = None
        if self.tools:
            for t in self.tools:
                name = t.get("function", {}).get("name")
                if name in ("search_creditor_name", "search_creditor_inn"):
                    search_tool_name = name
                    break

        if not self.tools:
            tool_choice = None
        elif search_tool_name and not has_tool_calls:
            # Force the model to call the search tool on the first turn
            tool_choice = {"type": "function", "function": {"name": search_tool_name}}
        elif not self.allow_text_result:
            tool_choice = "required"
        else:
            tool_choice = "auto"

        from itertools import chain

        openai_messages = list(chain(*(self._map_message(m) for m in messages)))
        model_settings = model_settings or {}

        # Only use json_object format if we are NOT using tools (to avoid API contradiction)
        response_format = {"type": "json_object"} if (not self.tools) else NOT_GIVEN

        return await self.client.chat.completions.create(
            model=self.model_name,
            messages=openai_messages,
            n=1,
            tools=self.tools or NOT_GIVEN,
            tool_choice=tool_choice or NOT_GIVEN,
            stream=stream,
            stream_options={"include_usage": True} if stream else NOT_GIVEN,
            max_tokens=model_settings.get("max_tokens", NOT_GIVEN),
            temperature=model_settings.get("temperature", NOT_GIVEN),
            top_p=model_settings.get("top_p", NOT_GIVEN),
            timeout=model_settings.get("timeout", NOT_GIVEN),
            response_format=response_format,
        )


class CustomOpenAIModel(OpenAIModel):
    async def agent_model(self, *, function_tools, allow_text_result, result_tools):
        check_allow_model_requests()
        tools = [self._map_tool_definition(r) for r in function_tools]
        if result_tools:
            tools += [self._map_tool_definition(r) for r in result_tools]
        return CustomOpenAIAgentModel(
            self.client,
            self.model_name,
            allow_text_result,
            tools,
        )


from pydantic_ai.models import Model, AgentModel
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ModelRequest, SystemPromptPart, UserPromptPart, ToolReturnPart, RetryPromptPart
from pydantic_ai.usage import Usage
from pydantic_ai.tools import ToolDefinition

class OpenCodeCLIAgentModel(AgentModel):
    def __init__(self, model_name: str, result_tools: list[ToolDefinition] | None = None):
        self.model_name = model_name
        self.result_tools = result_tools or []

    async def request(
        self, messages: list[ModelMessage], model_settings: Any | None
    ) -> tuple[ModelResponse, Usage]:
        prompt_lines = []
        for msg in messages:
            if isinstance(msg, ModelRequest):
                for part in msg.parts:
                    if isinstance(part, SystemPromptPart):
                        prompt_lines.append(f"SYSTEM: {part.content}")
                    elif isinstance(part, UserPromptPart):
                        prompt_lines.append(f"USER: {part.content}")
                    elif isinstance(part, ToolReturnPart):
                        prompt_lines.append(f"TOOL RETURN ({part.tool_name}): {part.content}")
                    elif isinstance(part, RetryPromptPart):
                        prompt_lines.append(f"RETRY PROMPT: {part.content}")
                    elif isinstance(part, TextPart):
                        prompt_lines.append(f"TEXT: {part.content}")
        
        prompt_text = "\n\n".join(prompt_lines)
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(prompt_text)
            temp_file_path = f.name
            
        try:
            prompt_instruction = "IMPORTANT: Return ONLY raw JSON. No markdown blocks, no other text."
            
            schema_title = None
            schema_content = ""
            if self.result_tools and hasattr(self.result_tools[0], 'parameters_json_schema'):
                schema_title = self.result_tools[0].parameters_json_schema.get('title')
            
            if schema_title:
                schema_path = Path(__file__).parent.parent / 'config' / 'pipelines' / 'schemas' / f'{schema_title}.json'
                if schema_path.exists():
                    with open(schema_path, 'r', encoding='utf-8') as f:
                        schema_content = f.read()
                    prompt_instruction += f"\n\nCRITICAL: Your final output MUST be a valid JSON object matching this JSON Schema. DO NOT output any thinking process. DO NOT use <think> tags. Return ONLY the JSON object. No other text is allowed.\nSCHEMA:\n{schema_content}"

            # Вписываем инструкцию в файл, чтобы избежать проблем с кавычками в командной строке
            with open(temp_file_path, 'a', encoding='utf-8') as f:
                f.write("\n\n" + prompt_instruction)

            command = f'chcp 65001 >NUL && opencode run --model {self.model_name} "Please process the instructions in the attached file." -f "{temp_file_path}" --format json'
            
            process = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            
            final_text = ""
            
            async def read_output():
                nonlocal final_text
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    
                    line_str = line.decode('utf-8', errors='replace').strip()
                    if not line_str:
                        continue
                        
                    try:
                        data = json.loads(line_str)
                        if data.get("type") == "textDelta":
                            final_text += data.get("textDelta", "")
                        elif data.get("type") == "text" and "part" in data and data["part"].get("type") == "text":
                            final_text += data["part"]["text"]
                    except json.JSONDecodeError:
                        # Capture non-JSON output which might be an error message
                        logger.error(f"OpenCodeCLI raw output: {line_str}")
                        if "Free usage exceeded" in line_str or "Insufficient Balance" in line_str:
                            raise RuntimeError(f"OpenCode API limit reached: {line_str}")
                await process.wait()

            try:
                await asyncio.wait_for(read_output(), timeout=180.0)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except Exception:
                    pass
                raise TimeoutError("OpenCode CLI execution timed out after 180 seconds")
            
            # Извлекаем JSON из текста (на случай если модель добавила <think> или другой текст)
            import re
            json_match = re.search(r'(\{.*\})', final_text, re.DOTALL)
            if json_match:
                final_text = json_match.group(1)
            
            # Clean up the output string if it contains markdown formatting
            final_text = final_text.strip()
            if final_text.startswith("```json"):
                final_text = final_text[7:]
            if final_text.endswith("```"):
                final_text = final_text[:-3]
            final_text = final_text.strip()
            
            logger.info(f"OpenCodeCLIModel final extracted text: {final_text}")
            
            # If Pydantic AI expects a structured result tool, return a ToolCallPart
            if self.result_tools:
                try:
                    args_dict = json.loads(final_text)
                    tool_name = self.result_tools[0].name
                    from pydantic_ai.messages import ToolCallPart
                    return ModelResponse(parts=[ToolCallPart.from_raw_args(tool_name=tool_name, args=args_dict)]), Usage()
                except Exception as e:
                    logger.warning(f"Failed to parse JSON for ToolCallPart: {e}. Falling back to TextPart.")
            
            return ModelResponse(parts=[TextPart(content=final_text)]), Usage()
            
        finally:
            if os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except Exception:
                    pass

class OpenCodeCLIModel(Model):
    def __init__(self, model_name: str = "opencode/deepseek-v4-flash-free"):
        self.model_name = model_name

    async def agent_model(
        self,
        *,
        function_tools: list[Any],
        allow_text_result: bool,
        result_tools: list[Any],
    ) -> AgentModel:
        return OpenCodeCLIAgentModel(
            model_name=self.model_name,
            result_tools=result_tools,
        )

    def name(self) -> str:
        return self.model_name

import contextvars

_active_model: contextvars.ContextVar['Model'] = contextvars.ContextVar(
    '_active_model',
    default=OpenCodeCLIModel("opencode/deepseek-v4-flash-free"),
)


def resolve_llm_model(profile_config: dict | None = None) -> 'Model':
    """
    Build a pydantic-ai Model based on profile_config['models']['llm_extraction'].
    Supported providers:
      - 'opencode' → OpenCodeCLIModel (local CLI agent)
      - 'ollama'   → OpenAIModel pointing to remote Ollama /v1 endpoint
    Falls back to OpenCode if no profile_config is provided.
    """
    if profile_config is None:
        return OpenCodeCLIModel("opencode/deepseek-v4-flash-free")

    models_cfg = profile_config.get("models", {})
    llm_cfg = models_cfg.get("llm_extraction", {})
    provider = llm_cfg.get("provider", "opencode").lower()
    model_name = llm_cfg.get("model", "")

    if provider == "ollama":
        from pydantic_ai.models.openai import OpenAIModel as PydanticOpenAIModel
        import httpx
        from openai import AsyncOpenAI

        settings = get_settings()
        base_url = settings.ollama_ocr_url.rstrip("/")
        if base_url.endswith("/api/chat"):
            base_url = base_url[:-9]
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
            
        timeout_seconds = float(llm_cfg.get("timeout_seconds", 600.0))
        logger.info(f"Resolved LLM provider: ollama, model: {model_name}, base_url: {base_url}, timeout: {timeout_seconds}s")
        
        # Create a custom HTTPX client to bypass the default 5/10 min timeouts
        http_client = httpx.AsyncClient(timeout=timeout_seconds)
        async_openai_client = AsyncOpenAI(
            base_url=base_url,
            api_key=settings.ollama_ocr_token or "ollama",
            http_client=http_client
        )
        
        return PydanticOpenAIModel(
            model_name or "qwen3.6:27b",
            openai_client=async_openai_client,
        )
    elif provider == "router_ai":
        from pydantic_ai.models.openai import OpenAIModel as PydanticOpenAIModel
        import httpx
        from openai import AsyncOpenAI

        settings = get_settings()
        base_url = os.environ.get("OCR_ROUTER_AI_BASE_URL") or settings.router_ai_base_url or "https://routerai.ru/api/v1"
        api_key = os.environ.get("OCR_ROUTER_AI_API_KEY") or settings.router_ai_api_key

        if not api_key:
            raise ValueError("OCR_ROUTER_AI_API_KEY is not set in environment or settings")

        timeout_seconds = float(llm_cfg.get("timeout_seconds", 180.0))
        model_id = model_name or "deepseek/deepseek-v4-flash"
        logger.info(f"Resolved LLM provider: router_ai, model: {model_id}, base_url: {base_url}, timeout: {timeout_seconds}s")

        http_client = httpx.AsyncClient(timeout=timeout_seconds)
        async_openai_client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            http_client=http_client
        )
        return PydanticOpenAIModel(
            model_id,
            openai_client=async_openai_client,
        )
    else:
        # Default: opencode CLI
        opencode_model = model_name or "opencode/deepseek-v4-flash-free"
        logger.info(f"Resolved LLM provider: opencode, model: {opencode_model}")
        return OpenCodeCLIModel(opencode_model)


class DynamicModel(Model):
    """Proxy Model that delegates to whichever real Model is set in _active_model ContextVar."""

    async def agent_model(
        self,
        *,
        function_tools: list[Any],
        allow_text_result: bool,
        result_tools: list[Any],
    ) -> AgentModel:
        real_model = _active_model.get()
        return await real_model.agent_model(
            function_tools=function_tools,
            allow_text_result=allow_text_result,
            result_tools=result_tools,
        )

    def name(self) -> str:
        real_model = _active_model.get()
        return real_model.name()


model = DynamicModel()

# Agents definitions
SYSTEM_PROMPT = (
    "You are an expert legal document analyst. "
    "Extract the requested field accurately based on the provided text and instructions. "
    "IMPORTANT: You MUST respond ONLY with raw, valid JSON matching the requested schema. "
    "Do not wrap the JSON in markdown blocks like ```json ... ```. "
    "Do not include any other text."
)

from pydantic_ai.settings import ModelSettings

default_settings = ModelSettings(temperature=0.5, timeout=180.0)

agent_generic = Agent(
    model,
    deps_type=str,
    result_type=GenericFieldResult,
    retries=3,
    system_prompt=SYSTEM_PROMPT,
    model_settings=default_settings,
)

agent_generic_with_tools = Agent(
    model,
    deps_type=str,
    result_type=GenericFieldResult,
    retries=3,
    system_prompt=SYSTEM_PROMPT,
    model_settings=default_settings,
)
agent_generic_with_tools.tool(search_creditor_inn)
agent_generic_with_tools.tool(search_creditor_name)

agent_creditor = Agent(
    model,
    deps_type=str,
    result_type=CreditorResult,
    retries=3,
    system_prompt=SYSTEM_PROMPT,
    model_settings=default_settings,
)
agent_creditor.tool(search_creditor_inn)
agent_creditor.tool(search_creditor_name)

agent_creditor_inn = Agent(
    model,
    deps_type=str,
    result_type=CreditorInnResult,
    retries=3,
    system_prompt=SYSTEM_PROMPT,
    model_settings=default_settings,
)
agent_creditor_inn.tool(search_creditor_inn)
agent_creditor_inn.tool(search_creditor_name)

agent_claims_amount = Agent(
    model,
    deps_type=str,
    result_type=ClaimsAmountResult,
    retries=3,
    system_prompt=SYSTEM_PROMPT,
    model_settings=default_settings,
)

agent_grounds = Agent(
    model,
    deps_type=str,
    result_type=GroundsResult,
    retries=3,
    system_prompt=SYSTEM_PROMPT,
    model_settings=default_settings,
)

agent_tax_creditor = Agent(
    model,
    deps_type=str,
    result_type=TaxCreditorHeaderResult,
    retries=3,
    system_prompt=SYSTEM_PROMPT,
    model_settings=default_settings,
)

agent_rtk_combined = Agent(
    model,
    deps_type=str,
    result_type=RtkCombinedResult,
    retries=3,
    system_prompt=SYSTEM_PROMPT,
    model_settings=default_settings,
)

# Compatibility aliases for legacy tests/code
extraction_agent = agent_generic
extraction_agent_with_tools = agent_generic_with_tools


# Вспомогательные модели и агенты для верификации кредитора
class CompanyNameResult(BaseModel):
    company_name: str | None = Field(
        description="Official company or organization name found in internet search results, or null"
    )
    reasoning: str | None = Field(
        description="Brief explanation of which search result evidence supports company_name"
    )


class CompanyComparisonResult(BaseModel):
    is_same: bool
    difference_type: Literal["exact", "minor", "critical"]
    reasoning: str


company_name_extraction_agent = Agent(
    model,
    result_type=CompanyNameResult,
    system_prompt=(
        "You are an expert business registrar analyst. "
        "Analyze only the provided search results to find the official company name or organization name corresponding to the given INN. "
        "Use standard Russian legal-form abbreviations such as ООО, ПАО, АО, and ПКО, but do not abbreviate the entity's own name. "
        "Return the name clearly in the company_name field. If no company name is found, return null."
    ),
    model_settings=default_settings,
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
    ),
    model_settings=default_settings,
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


async def compare_company_names(
    llm_name: str, web_name: str
) -> CompanyComparisonResult:
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
                    reasoning=f"Comparison failed with exception: {e}",
                )
            if attempt == 2:
                logger.info("Pausing for 15 seconds after the second failed attempt...")
                await asyncio.sleep(15)
    return CompanyComparisonResult(
        is_same=False,
        difference_type="critical",
        reasoning="Failed to compare due to unknown errors.",
    )


async def run_agent_extraction(
    text: str, fields_config: Dict[str, Any], profile_id: str | None = None, profile_config: dict | None = None
) -> Dict[str, dict]:
    # Resolve and activate the LLM model based on profile config
    resolved_model = resolve_llm_model(profile_config)
    token = _active_model.set(resolved_model)
    try:
        return await _run_agent_extraction_impl(text, fields_config, profile_id)
    finally:
        _active_model.reset(token)


async def _run_agent_extraction_impl(
    text: str, fields_config: Dict[str, Any], profile_id: str | None = None
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

    if profile_id == "rtk" and not is_tax_document:
        combined_fields = ["creditor_inn", "claims_amount", "grounds"]
        if all(f in fields_config for f in combined_fields):
            logger.info("Executing combined RTK extraction for creditor_inn, claims_amount, grounds.")
            combined_prompt_parts = []
            for f in combined_fields:
                f_instruction = fields_config[f].get("prompt_instruction", "")
                combined_prompt_parts.append(
                    f"--- FIELD: {f} ---\n{f_instruction}"
                )
            combined_instructions = "\n\n".join(combined_prompt_parts)
            combined_prompt = (
                f"Instruction: You are extracting multiple fields at once. Here are the specific instructions for each field:\n\n"
                f"{combined_instructions}\n\n"
                f"Document Text:\n{text[:10000]}"
            )
            
            max_attempts = 3
            combined_data = None
            for attempt in range(1, max_attempts + 1):
                try:
                    result = await agent_rtk_combined.run(combined_prompt, deps=text)
                    combined_data = result.data
                    break
                except Exception as e:
                    logger.warning(f"Combined RTK extraction attempt {attempt} failed: {e}")
                    if attempt == max_attempts:
                        logger.error("Combined RTK extraction completely failed. Falling back to individual extraction.")
                    elif attempt == 2:
                        await asyncio.sleep(15)
            
            if combined_data:
                # 1. Store creditor_inn
                inn_val = None
                if combined_data.creditor_inn:
                    inn_match = re.search(r"\d{10,12}", str(combined_data.creditor_inn))
                    inn_val = inn_match.group(0) if inn_match else combined_data.creditor_inn
                results["creditor_inn"] = {
                    "value": inn_val,
                    "confidence": combined_data.creditor_inn_confidence,
                    "reasoning": combined_data.creditor_inn_reasoning,
                    "source": "rtk_combined",
                }
                
                # 2. Store claims_amount
                amt_val = None
                if (
                    combined_data.commitments_count is not None
                    and isinstance(combined_data.amounts, list)
                    and len(combined_data.amounts) > 0
                ):
                    total = sum(combined_data.amounts)
                    amt_val = f"{total:.2f}" if total > 0 else None
                results["claims_amount"] = {
                    "value": amt_val,
                    "confidence": combined_data.claims_amount_confidence,
                    "reasoning": combined_data.claims_amount_reasoning,
                    "source": "rtk_combined",
                }
                
                # 3. Validate and store grounds
                grounds_val = None
                grounds_conf = combined_data.grounds_confidence
                grounds_reason = combined_data.grounds_reasoning
                
                grounds_def = fields_config["grounds"]
                grounds_instruction = grounds_def.get("prompt_instruction", "")
                parsed_grounds = re.findall(r'-\s*"([^"]+)"', grounds_instruction)
                VALID_GROUNDS = (
                    parsed_grounds
                    if parsed_grounds
                    else [
                        "договор на предоставление коммунальных услуг",
                        "кредитный договор",
                        "соглашение о кредитовании",
                        "договор потребительского микрозайма",
                        "договор потребительского займа",
                        "договор банковского счета",
                        "договор энергоснабжения",
                        "договор займа",
                        "налоговая задолженность",
                        "исполнительный лист",
                        "исполнительный документ",
                        "судебный приказ",
                        "судебный акт",
                        "административное правонарушение",
                    ]
                )
                
                clean_val_str = "null"
                if combined_data.grounds:
                    clean_val_str = str(combined_data.grounds).strip().lower().strip("'.\",")
                
                if clean_val_str not in ("null", "none", ""):
                    matched_ground = None
                    for vg in VALID_GROUNDS:
                        if re.search(
                            r"\b" + re.escape(vg.lower()) + r"(?:$|\b)",
                            clean_val_str,
                            re.IGNORECASE | re.UNICODE,
                        ):
                            matched_ground = vg
                            break
                    
                    if matched_ground:
                        grounds_val = matched_ground
                    else:
                        logger.warning(
                            f"Grounds validation failed for combined result '{clean_val_str}'. Launching retry loop with agent_grounds."
                        )
                        current_prompt = (
                            f"Instruction: {grounds_instruction}\n\nDocument Text:\n{text[:10000]}\n\n"
                            f"CRITICAL WARNING: В предыдущей попытке было получено значение '{clean_val_str}', которое НЕ ЯВЛЯЕТСЯ точным совпадением.\n"
                            f"Твой ответ должен СТРОГО соответствовать одному из этих значений: {', '.join(VALID_GROUNDS)}.\n"
                            f"Если в тексте нет ничего похожего, верни null. Не придумывай свои варианты!"
                        )
                        for attempt in range(1, 3):
                            try:
                                grounds_result = await agent_grounds.run(current_prompt, deps=text)
                                g_data = grounds_result.data
                                g_clean = "null"
                                if g_data.grounds:
                                    g_clean = str(g_data.grounds).strip().lower().strip("'.\",")
                                if g_clean in ("null", "none", ""):
                                    grounds_val = None
                                    grounds_conf = g_data.confidence
                                    grounds_reason = g_data.reasoning
                                    break
                                g_match = None
                                for vg in VALID_GROUNDS:
                                    if re.search(
                                        r"\b" + re.escape(vg.lower()) + r"(?:$|\b)",
                                        g_clean,
                                        re.IGNORECASE | re.UNICODE,
                                    ):
                                        g_match = vg
                                        break
                                if g_match:
                                    grounds_val = g_match
                                    grounds_conf = g_data.confidence
                                    grounds_reason = g_data.reasoning
                                    break
                                else:
                                    current_prompt = (
                                        f"Instruction: {grounds_instruction}\n\nDocument Text:\n{text[:10000]}\n\n"
                                        f"CRITICAL WARNING: В предыдущей попытке ты вернул значение '{g_clean}', которое НЕ ЯВЛЯЕТСЯ точным совпадением.\n"
                                        f"Твой ответ должен СТРОГО соответствовать одному из этих значений: {', '.join(VALID_GROUNDS)}.\n"
                                        f"Если в тексте нет ничего похожего, верни null. Не придумывай свои варианты!"
                                    )
                            except Exception as e:
                                logger.warning(f"Grounds retry attempt {attempt} failed: {e}")
                
                results["grounds"] = {
                    "value": grounds_val,
                    "confidence": grounds_conf,
                    "reasoning": grounds_reason,
                    "source": "rtk_combined",
                }

    for field_name in ordered_fields:
        if field_name in results:
            logger.info(f"Field {field_name} already populated (likely from combined extraction), skipping.")
            continue
        field_def = fields_config[field_name]
        extraction_method = field_def.get("extraction_method", "llm")
        logger.info(f"Extracting field {field_name} using method {extraction_method}")


        if profile_id == "court_decision_ru" and field_name in [
            "case_number",
            "decision_date",
            "procedure_end_date",
            "procedure_end_date_is_calculated",
            "early_report_deadline",
        ]:
            from ocr_platform.services.court_decision_legacy_rules import (
                extract_case_number,
                extract_decision_date,
                extract_procedure_end_date_with_meta,
                extract_early_report_deadline,
            )

            val = None
            if field_name == "case_number":
                val = extract_case_number(text)
                results[field_name] = {
                    "value": val,
                    "confidence": 1.0 if val else 0.0,
                    "reasoning": "Extracted via legacy regex",
                    "source": "regex_legacy",
                }
            elif field_name == "decision_date":
                val = extract_decision_date(text)
                results[field_name] = {
                    "value": val,
                    "confidence": 1.0 if val else 0.0,
                    "reasoning": "Extracted via legacy regex",
                    "source": "regex_legacy",
                }
            elif field_name == "procedure_end_date":
                val, is_calc = extract_procedure_end_date_with_meta(text)
                results[field_name] = {
                    "value": val,
                    "confidence": 1.0 if val else 0.0,
                    "reasoning": "Extracted via legacy regex",
                    "source": "regex_legacy",
                }
                results["procedure_end_date_is_calculated"] = {
                    "value": str(is_calc) if is_calc is not None else None,
                    "confidence": 1.0 if is_calc is not None else 0.0,
                    "reasoning": "Calculated alongside procedure_end_date via legacy regex",
                    "source": "regex_legacy",
                }
            elif field_name == "procedure_end_date_is_calculated":
                if field_name not in results:
                    results[field_name] = {
                        "value": None,
                        "confidence": 0.0,
                        "reasoning": "Not found",
                        "source": "regex_legacy",
                    }
            elif field_name == "early_report_deadline":
                ped = results.get("procedure_end_date", {}).get("value")
                val, source_reason = extract_early_report_deadline(text, ped)
                results[field_name] = {
                    "value": val,
                    "confidence": 1.0 if val else 0.0,
                    "reasoning": "Extracted via legacy regex and procedure_end_date",
                    "source": "regex_legacy",
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
                    "source": "regex",
                }
            else:
                results[field_name] = {
                    "value": None,
                    "confidence": 0.0,
                    "reasoning": "No pattern",
                }

        elif extraction_method in ["llm", "llm_with_tools", "llm_claims_amount"]:
            prompt_instruction = field_def.get("prompt_instruction", "")
            base_prompt = (
                f"Instruction: {prompt_instruction}\n\nDocument Text:\n{text[:10000]}"
            )

            try:
                if is_tax_document and field_name == "creditor_inn":
                    creditor_name = results.get("creditor", {}).get("value")
                    if creditor_name:
                        logger.info(
                            f"Tax document detected. Injecting creditor_name '{creditor_name}' into creditor_inn prompt"
                        )
                        base_prompt = f"ВНИМАНИЕ! Для данного документа мы уже определили точное наименование кредитора (налогового органа): '{creditor_name}'.\nТы ОБЯЗАН использовать именно это наименование '{creditor_name}' для вызова инструмента search_creditor_inn, чтобы найти его ИНН в интернете.\n\n{base_prompt}"
                        # Allow to fall through to normal LLM extraction

                if field_name == "creditor":
                    if profile_id == "rtk" and not is_tax_document:
                        known_inn = results.get("creditor_inn", {}).get("value")
                        val = None
                        confidence = 0.0
                        reasoning = ""
                        if known_inn:
                            cleaned_inn = "".join(c for c in str(known_inn) if c.isdigit())
                            if len(cleaned_inn) in (10, 12):
                                logger.info(f"Custom creditor extraction for RTK non-tax document. Using INN: {cleaned_inn}")
                                web_search_text = await asyncio.wait_for(
                                    asyncio.to_thread(_search_by_inn, cleaned_inn),
                                    timeout=25,
                                ) or "Company name not found"
                                
                                prompt_tmpl = field_def.get("prompt_instruction_inn_web_search")
                                if prompt_tmpl:
                                    prompt = prompt_tmpl.format(inn=cleaned_inn, web_search_text=web_search_text)
                                else:
                                    prompt = (
                                        f"Выдели официальное наименование кредитора/организации по ИНН: {cleaned_inn} исключительно на основе результатов интернет-поиска.\n\n"
                                        f"Результаты поиска в интернете:\n{web_search_text}\n\n"
                                        f"ВНИМАНИЕ! Не используй текст самого судебного документа, извлеки имя строго по результатам поиска.\n"
                                        f"Примени стандартные правила сокращения организационно-правовой формы (например, ООО, ПАО, АО, ПКО и др.), но не сокращай само название.\n"
                                        f"Заполни поля схемы CompanyNameResult:\n"
                                        f"- company_name: итоговое официальное наименование с сокращенной организационно-правовой формой\n"
                                        f"- reasoning: краткое объяснение, из каких строк поиска взято наименование\n"
                                    )
                                try:
                                    result = await company_name_extraction_agent.run(prompt)
                                    data = result.data
                                    val = _clean_llm_string(data.company_name)
                                    confidence = 0.9 if val else 0.0
                                    reasoning = f"{data.reasoning} | Extracted directly from web search results by INN {cleaned_inn} without reading document text."
                                except Exception as e:
                                    logger.warning(f"Custom creditor extraction failed: {e}")
                                    val = None
                                    confidence = 0.0
                                    reasoning = f"Failed custom extraction: {e}"
                        
                        results[field_name] = {
                            "value": val,
                            "confidence": confidence,
                            "reasoning": reasoning,
                            "source": "inn_web_search_only",
                        }
                        continue

                    if is_tax_document:
                        prompt_instruction = """МЕТОДОЛОГИЯ АНАЛИЗА (SGR / Step-by-Step Reasoning):
      Сначала выполни пошаговые рассуждения в поле "reasoning" строго следуя шагам:
      1. Шаг 1: Найди полное наименование и номер межрайонной налоговой инспекции в шапке документа. Обрати внимание что при наличии номера инспекции обзательно нужно найти к какому городу или области относится эта инспекция (например "МЕЖРАЙОННАЯ ИНСПЕКЦИЯ ФЕДЕРАЛЬНОЙ НАЛОГОВОЙ СЛУЖБЫ № 8 ПО САРАТОВСКОЙ ОБЛАСТИ").
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
                                logger.warning(
                                    f"LLM extraction attempt {attempt} failed for tax creditor: {e}"
                                )
                                if attempt == max_attempts:
                                    raise e
                                if attempt == 2:
                                    await asyncio.sleep(15)

                        results[field_name] = {
                            "value": _clean_llm_string(val),
                            "confidence": confidence,
                            "reasoning": reasoning,
                            "source": "llm_tax",
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
                                logger.info(
                                    f"Creditor extraction retry {creditor_attempt}/{CREDITOR_RETRIES} due to null value with known INN {known_inn}"
                                )
                                current_prompt = (
                                    f"{base_prompt}\n\n"
                                    f"CRITICAL WARNING: You previously returned null for the creditor name. "
                                    f"Однако, ИНН кредитора ИЗВЕСТЕН: '{known_inn}'.\n"
                                    f"Ты ОБЯЗАН прямо сейчас использовать инструмент `search_creditor_name`, передав туда этот ИНН '{known_inn}'. "
                                    f"Дождись ответа от инструмента и запиши полученное официальное наименование в поле 'creditor_final'. "
                                    f"Ни в коем случае не возвращай null!"
                                )
                            else:
                                logger.info(
                                    f"Creditor extraction retry {creditor_attempt}/{CREDITOR_RETRIES} due to recognition error"
                                )

                        max_attempts = 3
                        for attempt in range(1, max_attempts + 1):
                            try:
                                result = await agent.run(current_prompt, deps=text)
                                break
                            except Exception as e:
                                import traceback
                                logger.warning(
                                    f"LLM call attempt {attempt} failed for field creditor: {e}\n{traceback.format_exc()}"
                                )
                                if attempt == max_attempts:
                                    raise e
                                if attempt == 2:
                                    logger.info(
                                        "Pausing for 15 seconds after the second failed attempt..."
                                    )
                                    await asyncio.sleep(15)

                        tool_called = False
                        try:
                            for msg in result.all_messages():
                                if hasattr(msg, "parts"):
                                    for part in msg.parts:
                                        if (
                                            hasattr(part, "tool_name")
                                            and getattr(part, "tool_name")
                                            == "search_creditor_name"
                                        ):
                                            tool_called = True
                        except Exception as e:
                            logger.error(f"Error checking tool calls: {e}")

                        if not is_tax_document and known_inn and not tool_called:
                            cleaned_inn = "".join(
                                c for c in str(known_inn) if c.isdigit()
                            )
                            if len(cleaned_inn) in (10, 12):
                                logger.info(
                                    f"LLM hallucinated and didn't call search_creditor_name. Forcing manual call for INN: {cleaned_inn}"
                                )
                                tool_result_text = (
                                    await asyncio.wait_for(
                                        asyncio.to_thread(_search_by_inn, cleaned_inn),
                                        timeout=25,
                                    )
                                    or "Company name not found"
                                )

                                forced_prompt = (
                                    f"{current_prompt}\n\n"
                                    f"ВНИМАНИЕ! Ты проигнорировал требование вызвать инструмент поиска имени по ИНН. Я вызвал его принудительно.\n"
                                    f"Результат поиска по ИНН {cleaned_inn}:\n{tool_result_text}\n"
                                    f"Учитывая эти данные поиска, извлеки корректное официальное наименование кредитора и верни JSON."
                                )
                                try:
                                    result = await agent.run(forced_prompt, deps=text)
                                except Exception as e:
                                    logger.warning(
                                        f"Forced LLM call failed for field creditor: {e}"
                                    )

                        data = result.data
                        val = _clean_llm_string(data.creditor_final)
                        confidence = data.confidence

                        sgr_reasoning_parts = []
                        if data.creditor_name:
                            sgr_reasoning_parts.append(
                                f"creditor_name: {data.creditor_name}"
                            )
                        if data.creditor_name_web:
                            sgr_reasoning_parts.append(
                                f"creditor_name_web: {data.creditor_name_web}"
                            )
                        reasoning = (
                            f"{data.reasoning} | SGR: {'; '.join(sgr_reasoning_parts)}"
                        )

                        # Сверка названия кредитора по ИНН через интернет
                        if val:
                            inn = results.get("creditor_inn", {}).get("value")
                            if inn:
                                cleaned_inn = "".join(
                                    c for c in str(inn) if c.isdigit()
                                )
                                if len(cleaned_inn) in (10, 12):
                                    web_search_text = await asyncio.wait_for(
                                        asyncio.to_thread(_search_by_inn, cleaned_inn),
                                        timeout=25,
                                    )
                                    if web_search_text:
                                        web_company_name = _clean_llm_string(
                                            await extract_company_name_from_search(
                                                cleaned_inn, web_search_text
                                            )
                                        )
                                        if web_company_name:
                                            comp_res = await compare_company_names(
                                                val, web_company_name
                                            )
                                            if comp_res.is_same:
                                                if comp_res.difference_type == "exact":
                                                    pass
                                                elif (
                                                    comp_res.difference_type == "minor"
                                                ):
                                                    logger.info(
                                                        f"Minor difference: doc='{val}', web='{web_company_name}'. Correcting to web name, setting confidence to 50%."
                                                    )
                                                    val = _clean_llm_string(
                                                        web_company_name
                                                    )
                                                    confidence = 0.5
                                                    reasoning = f"{reasoning} | Corrected from web registry by INN {cleaned_inn} (original: {val}, internet: {web_company_name}, confidence reduced to 50% due to minor mismatch)."
                                                else:
                                                    logger.warning(
                                                        f"Critical difference: doc='{val}', web='{web_company_name}'. Returning recognition error."
                                                    )
                                                    val = "Ошибка распознавания"
                                                    confidence = 0.0
                                                    reasoning = f"{reasoning} | CRITICAL MISMATCH with registry for INN {cleaned_inn} (internet: {web_company_name})."
                                            else:
                                                logger.warning(
                                                    f"Names do not match: doc='{val}', web='{web_company_name}'. Returning recognition error."
                                                )
                                                val = "Ошибка распознавания"
                                                confidence = 0.0
                                                reasoning = f"{reasoning} | CRITICAL MISMATCH with registry for INN {cleaned_inn} (internet: {web_company_name})."

                        if val != "Ошибка распознавания":
                            if not val and known_inn:
                                if creditor_attempt < CREDITOR_RETRIES:
                                    continue
                            break

                    if (not val or val == "Ошибка распознавания") and profile_id == "rtk" and not is_tax_document:
                        logger.info("Creditor not found or recognition error. Initiating fallback search for INN and creditor.")
                        inn_fallback_prompt = (
                            "Поищи актуальный ИНН в тексте документа еще раз. "
                            "Рекомендуется использовать инструмент search_creditor_inn передав в него имя кредитора/заявителя."
                        )
                        max_attempts = 3
                        new_inn_val = None
                        for attempt in range(1, max_attempts + 1):
                            try:
                                inn_result = await agent_creditor_inn.run(f"Instruction: {inn_fallback_prompt}\n\nDocument Text:\n{text[:10000]}", deps=text)
                                new_inn = inn_result.data.INN
                                if new_inn:
                                    inn_match = re.search(r"\d{10,12}", str(new_inn))
                                    new_inn_val = inn_match.group(0) if inn_match else new_inn
                                    if new_inn_val:
                                        logger.info(f"Fallback extracted new INN: {new_inn_val}")
                                        results["creditor_inn"] = {
                                            "value": new_inn_val,
                                            "confidence": inn_result.data.confidence,
                                            "reasoning": inn_result.data.reasoning + " | fallback search",
                                            "source": "rtk_fallback_inn"
                                        }
                                break
                            except Exception as e:
                                logger.warning(f"Fallback INN extraction attempt {attempt} failed: {e}")
                                if attempt == max_attempts:
                                    logger.error("Fallback INN extraction completely failed.")
                                elif attempt == 2:
                                    await asyncio.sleep(15)
                                    
                        if new_inn_val:
                            fallback_creditor_prompt = (
                                f"ВНИМАНИЕ! Найден уточненный ИНН кредитора: {new_inn_val}\n"
                                f"Используй этот ИНН для вызова инструмента search_creditor_name.\n\n"
                                f"Дождись ответа от инструмента и запиши полученное официальное наименование в поле 'creditor_final'.\n\n"
                                f"Ни в коем случае не возвращай null!\n\n"
                                f"Instruction: {field_def.get('prompt_instruction', '')}\n\nDocument Text:\n{text[:10000]}"
                            )
                            for attempt in range(1, max_attempts + 1):
                                try:
                                    cred_res = await agent_creditor.run(fallback_creditor_prompt, deps=text)
                                    data = cred_res.data
                                    
                                    tool_called = False
                                    try:
                                        for msg in cred_res.all_messages():
                                            if hasattr(msg, "parts"):
                                                for part in msg.parts:
                                                    if hasattr(part, "tool_name") and getattr(part, "tool_name") == "search_creditor_name":
                                                        tool_called = True
                                    except Exception:
                                        pass
                                        
                                    if not tool_called:
                                        cleaned_inn = "".join(c for c in str(new_inn_val) if c.isdigit())
                                        if len(cleaned_inn) in (10, 12):
                                            logger.info(f"Fallback LLM hallucinated. Forcing manual call for INN: {cleaned_inn}")
                                            tool_result_text = await asyncio.wait_for(asyncio.to_thread(_search_by_inn, cleaned_inn), timeout=25) or "Company name not found"
                                            forced_prompt = (
                                                f"{fallback_creditor_prompt}\n\n"
                                                f"ВНИМАНИЕ! Ты проигнорировал требование вызвать инструмент поиска имени по ИНН. Я вызвал его принудительно.\n"
                                                f"Результат поиска по ИНН {cleaned_inn}:\n{tool_result_text}\n"
                                                f"Учитывая эти данные поиска, извлеки корректное официальное наименование кредитора и верни JSON."
                                            )
                                            cred_res = await agent_creditor.run(forced_prompt, deps=text)
                                            data = cred_res.data
                                    
                                    val = _clean_llm_string(data.creditor_final)
                                    confidence = data.confidence
                                    reasoning = data.reasoning + " | fallback retry"
                                    
                                    if val:
                                        cleaned_inn = "".join(c for c in str(new_inn_val) if c.isdigit())
                                        if len(cleaned_inn) in (10, 12):
                                            web_search_text = await asyncio.wait_for(asyncio.to_thread(_search_by_inn, cleaned_inn), timeout=25)
                                            if web_search_text:
                                                web_company_name = await extract_company_name_from_search(cleaned_inn, web_search_text)
                                                if web_company_name:
                                                    web_company_name = _clean_llm_string(web_company_name)
                                                    comp_res = await compare_company_names(val, web_company_name)
                                                    if comp_res.is_same:
                                                        logger.info(f"Fallback match: '{val}' and '{web_company_name}'")
                                                    elif comp_res.difference_type == "minor":
                                                        logger.info(f"Fallback minor diff. Replacing '{val}' with '{web_company_name}'")
                                                        val = web_company_name
                                                    else:
                                                        logger.warning(f"Fallback critical mismatch: doc='{val}', web='{web_company_name}'.")
                                                        val = "Ошибка распознавания"
                                                        confidence = 0.0
                                                        reasoning = f"{reasoning} | CRITICAL MISMATCH with registry for INN {cleaned_inn} (internet: {web_company_name})."
                                    break
                                except Exception as e:
                                    logger.warning(f"Fallback creditor attempt {attempt} failed: {e}")
                                    if attempt == max_attempts:
                                        logger.error("Fallback creditor completely failed.")
                                    elif attempt == 2:
                                        await asyncio.sleep(15)

                elif field_name == "claims_amount":
                    agent = agent_claims_amount
                    max_attempts = 3
                    for attempt in range(1, max_attempts + 1):
                        try:
                            result = await agent.run(base_prompt, deps=text)
                            break
                        except Exception as e:
                            logger.warning(
                                f"LLM call attempt {attempt} failed for field claims_amount: {e}"
                            )
                            if attempt == max_attempts:
                                raise e
                            if attempt == 2:
                                await asyncio.sleep(15)

                    data = result.data
                    confidence = data.confidence
                    reasoning = data.reasoning

                    if (
                        data.commitments_count is not None
                        and isinstance(data.amounts, list)
                        and len(data.amounts) > 0
                    ):
                        total = sum(data.amounts)
                        val = f"{total:.2f}" if total > 0 else None
                    else:
                        val = None

                elif field_name == "grounds":
                    # Парсим допустимые значения из промпта
                    parsed_grounds = re.findall(r'-\s*"([^"]+)"', prompt_instruction)
                    VALID_GROUNDS = (
                        parsed_grounds
                        if parsed_grounds
                        else [
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
                            "административное правонарушение",
                        ]
                    )
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
                            logger.warning(
                                f"LLM call attempt {attempt} failed for field grounds: {e}"
                            )
                            if attempt == max_attempts:
                                raise e
                            await asyncio.sleep(5)
                            continue

                        data = result.data
                        confidence = data.confidence
                        reasoning = data.reasoning

                        clean_val_str = "null"
                        if data.grounds:
                            clean_val_str = (
                                str(data.grounds).strip().lower().strip("'.\",")
                            )

                        if clean_val_str in ("null", "none", ""):
                            val = None
                            break

                        # Проверяем регулярками (принудительно для РТК, но можно и для всех)
                        if profile_id == "rtk":
                            matched_ground = None
                            for vg in VALID_GROUNDS:
                                if re.search(
                                    r"\b" + re.escape(vg.lower()) + r"(?:$|\b)",
                                    clean_val_str,
                                    re.IGNORECASE | re.UNICODE,
                                ):
                                    matched_ground = vg
                                    break

                            if matched_ground:
                                val = matched_ground
                                break
                            else:
                                logger.warning(
                                    f"Grounds validation failed on attempt {attempt}: '{clean_val_str}' not in VALID_GROUNDS."
                                )
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
                    agent = (
                        agent_creditor_inn
                        if extraction_method == "llm_with_tools"
                        else agent_generic
                    )
                    max_attempts = 3
                    for attempt in range(1, max_attempts + 1):
                        try:
                            result = await agent.run(base_prompt, deps=text)
                            break
                        except Exception as e:
                            logger.warning(
                                f"LLM extraction attempt {attempt} failed for field {field_name}: {e}"
                            )
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
                            inn_match = re.search(r"\d{10,12}", str(data.INN))
                            val = inn_match.group(0) if inn_match else data.INN
                    else:
                        if data.value:
                            inn_match = re.search(r"\d{10,12}", str(data.value))
                            val = inn_match.group(0) if inn_match else data.value

                else:
                    agent = (
                        agent_generic_with_tools
                        if extraction_method == "llm_with_tools"
                        else agent_generic
                    )
                    max_attempts = 3
                    for attempt in range(1, max_attempts + 1):
                        try:
                            result = await agent.run(base_prompt, deps=text)
                            break
                        except Exception as e:
                            logger.warning(
                                f"LLM extraction attempt {attempt} failed for field {field_name}: {e}"
                            )
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
                    "source": extraction_method,
                }

            except Exception as e:
                import pydantic_ai

                raw_text = "Unknown"
                if isinstance(e, pydantic_ai.exceptions.UnexpectedModelBehavior):
                    if hasattr(e, "__cause__") and e.__cause__:
                        raw_text = str(e.__cause__)
                logger.error(
                    f"Failed to extract {field_name} with LLM: {e}. Raw cause: {raw_text}"
                )
                results[field_name] = {
                    "value": None,
                    "confidence": 0.0,
                    "reasoning": str(e),
                    "source": extraction_method,
                }

    # Final pass: if creditor_inn is null/missing, but creditor is found, force web search
    creditor_inn_info = results.get("creditor_inn")
    creditor_info = results.get("creditor")
    
    if creditor_info and creditor_info.get("value"):
        inn_val = creditor_inn_info.get("value") if creditor_inn_info else None
        if not inn_val:
            cred_name = creditor_info.get("value")
            logger.info(f"creditor_inn is missing/null, but creditor '{cred_name}' is found. Forcing search_creditor_inn tool call.")
            try:
                # search_creditor_inn is synchronous and ignores ctx
                found_inn = search_creditor_inn(None, cred_name)
                
                # Update the result if something was found, or even if not found (to record the attempt)
                if found_inn and "Not found" not in found_inn:
                    results["creditor_inn"] = {
                        "value": found_inn,
                        "confidence": 0.9,
                        "reasoning": f"Forced internet search by creditor name '{cred_name}' yielded INN {found_inn}.",
                        "source": "tool_fallback"
                    }
                    logger.info(f"Successfully found INN via fallback: {found_inn}")
                else:
                    results["creditor_inn"] = {
                        "value": None,
                        "confidence": 0.0,
                        "reasoning": f"Forced internet search by creditor name '{cred_name}' yielded no results.",
                        "source": "tool_fallback"
                    }
                    logger.info(f"Fallback search for INN returned no valid results.")
            except Exception as e:
                logger.warning(f"Forced search_creditor_inn failed: {e}")

    return results
