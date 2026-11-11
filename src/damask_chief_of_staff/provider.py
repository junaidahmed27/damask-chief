from __future__ import annotations
import json
import os
import shutil
import subprocess
from urllib.parse import urlparse
import httpx
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from .adapters import claude_version


class FindingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=100)
    kind: Literal["drift", "blocker", "evidence", "decision"]
    title: str = Field(min_length=1, max_length=160)
    explanation: str = Field(min_length=1, max_length=1600)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)
    proposed: str = Field(min_length=1, max_length=2000)
    uncertainty: str = Field(max_length=500)
    constraint_id: str | None = None
    check_id: str | None = None


class ReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=600)
    goal_source_ids: list[str] = Field(min_length=1, max_length=20)
    summary: str = Field(min_length=1, max_length=2500)
    summary_source_ids: list[str] = Field(min_length=1, max_length=100)
    ambiguities: list[str] = Field(max_length=8)
    findings: list[FindingOutput] = Field(max_length=6)


from pathlib import Path
SYSTEM = (Path(__file__).parent / "prompts/chief-v1.txt").read_text(encoding="utf-8")

PROMPT_VERSION = "chief-v1"


def validate_route(model):
    provider = model.get("provider", "disabled")
    if provider not in ("disabled", "anthropic", "claude-cli"):
        raise ValueError("Supported routes are disabled, anthropic or claude-cli")
    if provider == "disabled":
        return
    if not model.get("approved"):
        raise ValueError("Explicit approval is required before sending selected engineering context to a model route")
    if not model.get("model") or len(model["model"]) > 200:
        raise ValueError("Specify an approved model or deployment ID")
    if provider == "anthropic":
        url = urlparse(model.get("endpoint", ""))
        if url.username or url.password or url.fragment or not url.hostname:
            raise ValueError("Use a credential-free Messages endpoint URL")
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")):
            raise ValueError("Model endpoints require HTTPS, except loopback development gateways")
        if not model.get("key_env", "ANTHROPIC_API_KEY").isidentifier():
            raise ValueError("Specify an environment-variable name for the credential")
    else:
        claude_version()


def call(config, payload):
    model = dict(config.data["model"])
    validate_route(model)
    if model["provider"] == "disabled":
        raise ValueError("Semantic review is unavailable until an approved model route is configured")
    schema = ReviewOutput.model_json_schema()
    body = json.dumps({"schema": schema, "evidence": payload}, ensure_ascii=False)
    if len(body) > 32000:
        raise ValueError("Review context exceeded the bounded input window")
    if model["provider"] == "anthropic":
        key = os.environ.get(model.get("key_env", "ANTHROPIC_API_KEY"))
        if not key:
            raise ValueError("Configured model credential is missing from the service environment")
        with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
            response = client.post(model["endpoint"], headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                json={"model": model["model"], "max_tokens": 1500, "system": SYSTEM,
                    "messages": [{"role": "user", "content": body}]})
        if response.status_code != 200:
            raise ValueError("Model route returned HTTP " + str(response.status_code) + "; response body withheld")
        data = response.json()
        result = "".join(x.get("text", "") for x in data.get("content", []) if x.get("type") == "text")
        usage = data.get("usage", {})
    else:
        env = {**os.environ, "DAMASK_SUPERVISOR": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
        argv = [shutil.which("claude"), "--print", "--safe-mode", "--tools", "", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--disable-slash-commands", "--setting-sources", "",
            "--no-session-persistence", "--output-format", "json", "--json-schema", json.dumps(schema),
            "--model", model["model"], "--system-prompt", SYSTEM]
        from .process import bounded_run
        data = bounded_run(argv, input_text=body, cwd=config.home / "model-work", env=env, timeout=90, output_limit=100_000)
        if data["returncode"] != 0:
            raise ValueError("Claude reviewer could not complete; verify the selected authentication/model route")
        obj = json.loads(data["stdout"])
        if obj.get("is_error"):
            raise ValueError("Claude reviewer returned an error; observation continues")
        result = json.dumps(obj["structured_output"]) if obj.get("structured_output") else obj.get("result", "")
        usage = obj.get("usage", {})
    if result.strip().startswith("```json"):
        result = result.strip()[7:].removesuffix("```").strip()
    output = ReviewOutput.model_validate_json(result)
    # Never persist a model's arbitrary usage fields or raw diagnostics.
    usage = {k: v for k, v in usage.items() if k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens") and isinstance(v, int)}
    return output, usage
