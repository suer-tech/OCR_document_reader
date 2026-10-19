# Extraction request limits and queue ownership

Each PydanticAI extraction operation uses `BoundedAgent`: at most three model
requests with tools, including schema corrections. If that budget is exhausted
after receiving tool results, continue the same task with exactly one additional
request without any tools, including the `final_result` tool. Keep the original
document, prior model answers, tool arguments and results. Render historical
tool-call/return blocks as ordinary text so Anthropic can accept the history
without tool definitions. Explicitly tell the model to use the already found INN,
not search again, preserve each party's identity and return raw JSON in the
original result schema. Validate the JSON and existing result validators before
returning the usual typed result and continuing the pipeline. Usage includes all
four requests. Unavailable nullable values remain null, never fabricated.

Explicit stricter caller limits and token budgets are preserved. A budget error
without tool evidence, or an invalid final answer, remains terminal: fallbacks
and worker retries must not restart the loop. Other transient failures retain
the existing retry policy.

All former Grok entries in pipeline configuration use
`anthropic/claude-sonnet-5.5` through RouterAI. Passport Gemini profiles remain
unchanged. Confirm exact model availability through the RouterAI models endpoint.
The PydanticAI 0.0.18 adapter overrides `agent_model` and constructs an
`OpenAIAgentModel` subclass (not a request method on the parent model).
Anthropic requests use `tool_choice=auto` and omit `parallel_tool_calls`:
forced tools were rejected by the RouterAI/Bedrock route in the live canary.

PostgreSQL session advisory locks serialize `process_pipeline_run` by run ID.
The lock is held for the entire processing operation and automatically released
when the database connection/process dies. SQLite development mode only provides
in-process exclusion and is not supported for multiple worker processes.

A delivery for an already active run raises `PipelineRunBusy`; the worker does
not change its status or schedule an application retry. RabbitMQ nacks/requeues
the delivery and waits five seconds to avoid a tight redelivery loop. A completed
run retains the existing `done` check and is acknowledged without processing.
An active delivery must never be acknowledged simply because another worker owns it.

RabbitMQ consumer acknowledgement timeout is 60 minutes, longer than the existing
30-minute pipeline timeout and cleanup. This is supplementary to locking and
does not replace the model request budget. On deployment verify the effective
broker timeout, empty ready/unacknowledged queue before container replacement,
the expected worker count, and model selection in running containers.

Regression checks must exercise an endlessly repeating tool followed by a valid
typed answer without tools, preserved evidence in the actual HTTP request,
no further requests on an invalid final answer, successful ordinary completion,
propagation through extraction/worker retries, nack on
busy delivery, and PostgreSQL exclusion across separate connections/processes.
