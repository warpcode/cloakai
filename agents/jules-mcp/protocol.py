"""Pydantic v2 protocol data models for JSON-RPC 2.0, MCP, and Jules tools.

Defines schemas for:
1. Standard JSON-RPC 2.0 requests, responses, and error codes.
2. Standard Model Context Protocol (MCP) lifecycle and tool definitions (2024-11-05).
3. Jules autonomous agent tool parameter models with robust alias support
   and input validation.
4. Schema translation helper `model_to_json_schema`.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

__all__ = [
    # JSON-RPC Error Codes
    "PARSE_ERROR",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "INTERNAL_ERROR",
    "JSONRPCErrorCode",
    # JSON-RPC Models
    "JSONRPCRequest",
    "JSONRPCResponse",
    "JSONRPCError",
    # MCP Protocol Models
    "ClientInfo",
    "ServerInfo",
    "ServerCapabilities",
    "InitializeParams",
    "InitializeResult",
    "ToolInputSchema",
    "Tool",
    "TextContent",
    "CallToolResult",
    "PromptArgument",
    "Prompt",
    "PromptMessage",
    "GetPromptResult",
    # Jules Tool Input Models
    "JulesStartParams",
    "JulesSendParams",
    "JulesStatusParams",
    "JulesApprovePlanParams",
    "JulesSourcesParams",
    "JulesGetSourceParams",
    "JulesListSessionsParams",
    "JulesGetSessionParams",
    "NudgeTemplate",
    "JulesNudgeParams",
    "JulesAuditParams",
    "JulesArchiveSessionParams",
    "JulesDeleteSessionParams",
    "JulesGetActivityParams",
    "JulesGetPatchParams",
    "JulesCallParams",
    # Schema Helpers
    "model_to_json_schema",
]


# ==============================================================================
# JSON-RPC 2.0 Specification Models and Standard Error Codes
# ==============================================================================

PARSE_ERROR: int = -32700
INVALID_REQUEST: int = -32600
METHOD_NOT_FOUND: int = -32601
INVALID_PARAMS: int = -32602
INTERNAL_ERROR: int = -32603


class JSONRPCErrorCode(IntEnum):
    """Standard JSON-RPC 2.0 error codes."""

    PARSE_ERROR = PARSE_ERROR
    INVALID_REQUEST = INVALID_REQUEST
    METHOD_NOT_FOUND = METHOD_NOT_FOUND
    INVALID_PARAMS = INVALID_PARAMS
    INTERNAL_ERROR = INTERNAL_ERROR


class JSONRPCError(BaseModel):
    """JSON-RPC 2.0 error object."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    code: int
    message: str
    data: Any | None = None


class JSONRPCRequest(BaseModel):
    """JSON-RPC 2.0 request or notification object."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] | None = None


class JSONRPCResponse(BaseModel):
    """JSON-RPC 2.0 response object."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    jsonrpc: str = "2.0"
    id: str | int | None = None
    result: Any | None = None
    error: JSONRPCError | None = None

    @classmethod
    def make_result(cls, req_id: str | int | None, result: Any) -> JSONRPCResponse:
        """Helper to construct a successful JSON-RPC 2.0 response."""
        return cls(id=req_id, result=result)

    @classmethod
    def make_error(
        cls,
        req_id: str | int | None,
        code: int,
        message: str,
        data: Any | None = None,
    ) -> JSONRPCResponse:
        """Helper to construct a JSON-RPC 2.0 error response."""
        return cls(id=req_id, error=JSONRPCError(code=code, message=message, data=data))


# ==============================================================================
# Model Context Protocol (MCP) Core Models (Protocol Version 2024-11-05)
# ==============================================================================


class ClientInfo(BaseModel):
    """Information about the MCP client implementation."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    version: str = "1.0.0"


class ServerInfo(BaseModel):
    """Information about the MCP server implementation."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    version: str = "1.0.0"


class ServerCapabilities(BaseModel):
    """Capabilities supported by the MCP server."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    tools: dict[str, Any] = Field(default_factory=dict)
    prompts: dict[str, Any] = Field(default_factory=dict)


class InitializeParams(BaseModel):
    """Parameters passed in the initialize request from client to server."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    protocolVersion: str = Field(
        default="2024-11-05",
        validation_alias=AliasChoices("protocolVersion", "protocol_version"),
    )
    capabilities: dict[str, Any] = Field(default_factory=dict)
    clientInfo: ClientInfo | None = Field(
        default=None,
        validation_alias=AliasChoices("clientInfo", "client_info"),
    )


class InitializeResult(BaseModel):
    """Result returned by the server for the initialize request."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    protocolVersion: str = Field(
        default="2024-11-05",
        validation_alias=AliasChoices("protocolVersion", "protocol_version"),
    )
    capabilities: ServerCapabilities = Field(default_factory=ServerCapabilities)
    serverInfo: ServerInfo = Field(
        validation_alias=AliasChoices("serverInfo", "server_info"),
    )


class ToolInputSchema(BaseModel):
    """JSON Schema describing the parameters accepted by an MCP tool."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: str = "object"
    properties: dict[str, Any] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)


class Tool(BaseModel):
    """Definition of an MCP tool exposed to the client."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    description: str = ""
    inputSchema: ToolInputSchema = Field(
        default_factory=ToolInputSchema,
        validation_alias=AliasChoices("inputSchema", "input_schema"),
    )


class TextContent(BaseModel):
    """Text content item returned within an MCP tool call result."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: str = "text"
    text: str


class CallToolResult(BaseModel):
    """Result returned by an MCP server tool execution."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    content: list[TextContent] = Field(default_factory=list)
    isError: bool = Field(
        default=False,
        validation_alias=AliasChoices("isError", "is_error"),
    )

    @classmethod
    def text(cls, text: str, is_error: bool = False) -> CallToolResult:
        """Convenience factory creating a result with a single text item."""
        return cls(content=[TextContent(type="text", text=text)], isError=is_error)

    @classmethod
    def error(cls, message: str) -> CallToolResult:
        """Convenience factory creating an error result with message text."""
        return cls(content=[TextContent(type="text", text=message)], isError=True)


class PromptArgument(BaseModel):
    """Argument accepted by an MCP prompt template."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    description: str = ""
    required: bool = False


class Prompt(BaseModel):
    """Definition of an MCP prompt template exposed to clients."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    description: str = ""
    arguments: list[PromptArgument] = Field(default_factory=list)


class PromptMessage(BaseModel):
    """Individual message returned in a prompt response."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    role: Literal["user", "assistant"] = "user"
    content: TextContent


class GetPromptResult(BaseModel):
    """Result returned for prompts/get request."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    description: str = ""
    messages: list[PromptMessage] = Field(default_factory=list)


# ==============================================================================
# Jules Tool Input Parameter Models
# ==============================================================================


class JulesStartParams(BaseModel):
    """Input parameters for `jules_start` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    prompt: str = Field(
        ...,
        description="Task instruction or goal.",
    )
    source: str | None = Field(
        default=None,
        description="Repo slug ('owner/repo') or omit for sandbox.",
    )
    branch: str = Field(
        default="main",
        description="Starting branch (default: main).",
    )
    title: str | None = Field(
        default=None,
        description="Optional session title.",
    )
    require_plan_approval: bool = Field(
        default=False,
        description="Require approval before applying plan.",
        validation_alias=AliasChoices("require_plan_approval", "requirePlanApproval"),
    )
    automation_mode: str = Field(
        default="AUTO_CREATE_PR",
        description="AUTO_CREATE_PR or MANUAL.",
        validation_alias=AliasChoices("automation_mode", "automationMode"),
    )


class JulesSendParams(BaseModel):
    """Input parameters for `jules_send` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    session_id: str = Field(
        ...,
        description="Jules session ID.",
        validation_alias=AliasChoices("session_id", "sessionId"),
    )
    prompt: str = Field(
        default="",
        description="Instruction or message.",
        validation_alias=AliasChoices("prompt", "message"),
    )
    message: str | None = Field(
        default=None,
        description="Alias for prompt.",
    )

    @model_validator(mode="before")
    @classmethod
    def _unify_prompt_and_message(cls, data: Any) -> Any:
        if isinstance(data, dict):
            prompt = data.get("prompt")
            message = data.get("message")
            if not prompt and message:
                data["prompt"] = message
            elif prompt and not message:
                data["message"] = prompt
        return data

    @model_validator(mode="after")
    def _ensure_content(self) -> JulesSendParams:
        if not self.prompt.strip():
            raise ValueError("Either 'prompt' or 'message' must be provided and non-empty.")
        if not self.message:
            self.message = self.prompt
        return self


class JulesStatusParams(BaseModel):
    """Input parameters for `jules_status` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    session_id: str = Field(
        ...,
        description="Jules session ID.",
        validation_alias=AliasChoices("session_id", "sessionId"),
    )
    activity_limit: int = Field(
        default=10,
        description="Max activities to return (default: 10).",
        validation_alias=AliasChoices("activity_limit", "activityLimit"),
    )


class JulesApprovePlanParams(BaseModel):
    """Input parameters for `jules_approve_plan` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    session_id: str = Field(
        ...,
        description="Jules session ID.",
        validation_alias=AliasChoices("session_id", "sessionId"),
    )
    plan_id: str | None = Field(
        default=None,
        description="Optional plan ID to confirm.",
        validation_alias=AliasChoices("plan_id", "planId"),
    )


class JulesSourcesParams(BaseModel):
    """Input parameters for `jules_sources` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    page_size: int = Field(
        default=20,
        description="Max items per page (default: 20).",
        validation_alias=AliasChoices("page_size", "pageSize"),
    )
    page_token: str | None = Field(
        default=None,
        description="Pagination token.",
        validation_alias=AliasChoices("page_token", "pageToken"),
    )
    filter_expr: str | None = Field(
        default=None,
        description="Query filter expression.",
        validation_alias=AliasChoices("filter_expr", "filterExpr", "filter"),
    )


class JulesGetSourceParams(BaseModel):
    """Input parameters for `jules_get_source` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    source_id: str = Field(
        ...,
        description="Source ID (e.g. 'github/owner/repo').",
        validation_alias=AliasChoices("source_id", "sourceId", "name", "id"),
    )


class JulesListSessionsParams(BaseModel):
    """Input parameters for `jules_list_sessions` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    page_size: int = Field(
        default=20,
        description="Max items per page (default: 20).",
        validation_alias=AliasChoices("page_size", "pageSize"),
    )
    page_token: str | None = Field(
        default=None,
        description="Pagination token.",
        validation_alias=AliasChoices("page_token", "pageToken"),
    )
    filter_expr: str | None = Field(
        default=None,
        description="Query filter expression.",
        validation_alias=AliasChoices("filter_expr", "filterExpr", "filter"),
    )
    repo: str | None = Field(
        default=None,
        description="Filter by repo slug ('owner/repo').",
    )
    max_age_days: int | None = Field(
        default=None,
        description="Filter by max age in days.",
        validation_alias=AliasChoices("max_age_days", "maxAgeDays"),
    )


class JulesGetSessionParams(BaseModel):
    """Input parameters for `jules_get_session` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    session_id: str = Field(
        ...,
        description="Jules session ID.",
        validation_alias=AliasChoices("session_id", "sessionId", "id"),
    )


NudgeTemplate = Literal["progress_check", "plan_stalled", "pr_reminder"]


class JulesNudgeParams(BaseModel):
    """Input parameters for `jules_nudge` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    session_id: str = Field(
        ...,
        description="Jules session ID.",
        validation_alias=AliasChoices("session_id", "sessionId"),
    )
    template: NudgeTemplate = Field(
        default="progress_check",
        description="Template ('progress_check', 'plan_stalled', 'pr_reminder').",
    )


class JulesAuditParams(BaseModel):
    """Input parameters for `jules_audit` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    max_age_days: int = Field(
        default=30,
        description="Audit window in days (default: 30).",
        validation_alias=AliasChoices("max_age_days", "maxAgeDays"),
    )
    stale_threshold_mins: int = Field(
        default=60,
        description="Threshold in mins for stale idle runners (default: 60).",
        validation_alias=AliasChoices("stale_threshold_mins", "staleThresholdMins"),
    )
    repo: str | None = Field(
        default=None,
        description="Filter by repo slug ('owner/repo').",
    )
    all_pages: bool = Field(
        default=False,
        description="Whether to paginate all sessions.",
        validation_alias=AliasChoices("all_pages", "allPages"),
    )


class JulesArchiveSessionParams(BaseModel):
    """Input parameters for `jules_archive_session` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    session_id: str = Field(
        ...,
        description="Jules session ID.",
        validation_alias=AliasChoices("session_id", "sessionId"),
    )
    unarchive: bool = Field(
        default=False,
        description="True to restore; False to archive.",
    )


class JulesDeleteSessionParams(BaseModel):
    """Input parameters for `jules_delete_session` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    session_id: str = Field(
        ...,
        description="Jules session ID.",
        validation_alias=AliasChoices("session_id", "sessionId"),
    )
    confirm: bool = Field(
        default=False,
        description="Explicit confirmation flag for permanent deletion.",
    )


class JulesGetActivityParams(BaseModel):
    """Input parameters for `jules_get_activity` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    session_id: str = Field(
        ...,
        description="Jules session ID.",
        validation_alias=AliasChoices("session_id", "sessionId"),
    )
    activity_id: str = Field(
        ...,
        description="Activity event ID.",
        validation_alias=AliasChoices("activity_id", "activityId"),
    )


class JulesGetPatchParams(BaseModel):
    """Input parameters for `jules_get_patch` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    session_id: str = Field(
        ...,
        description="Jules session ID.",
        validation_alias=AliasChoices("session_id", "sessionId"),
    )


class JulesCallParams(BaseModel):
    """Input parameters for raw `jules_call` tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    endpoint: str = Field(
        ...,
        description="Relative API endpoint path.",
    )


# ==============================================================================
# Schema Extraction Helper
# ==============================================================================


def model_to_json_schema(model_cls: type[BaseModel]) -> ToolInputSchema:
    """Extract a token-efficient JSON Schema compatible with MCP Tool inputSchema."""
    raw_schema = model_cls.model_json_schema(mode="validation")
    schema_type = raw_schema.get("type", "object")
    properties = raw_schema.get("properties", {})
    required = raw_schema.get("required", [])

    clean_properties: dict[str, Any] = {}
    for prop_name, prop_data in properties.items():
        if isinstance(prop_data, dict):
            # Omit redundant 'title' field to save tokens
            cleaned = {k: v for k, v in prop_data.items() if k != "title"}
            clean_properties[prop_name] = cleaned
        else:
            clean_properties[prop_name] = prop_data

    extra: dict[str, Any] = {}
    if "$defs" in raw_schema:
        extra["$defs"] = raw_schema["$defs"]

    return ToolInputSchema(
        type=schema_type,
        properties=clean_properties,
        required=required,
        **extra,
    )
