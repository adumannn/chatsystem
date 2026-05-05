import os
import re

from ollama import Client as OllamaClient

MODEL_ID = os.environ.get("SUMMARY_MODEL", "qwen3.5:4b")
OLLAMA_HOST = os.environ.get("SUMMARY_OLLAMA_HOST", "http://localhost:11434")
REQUEST_TIMEOUT = float(os.environ.get("SUMMARY_LLM_TIMEOUT", "30"))
DEFAULT_MAX_TOKENS = int(os.environ.get("SUMMARY_MAX_TOKENS", "220"))

client = OllamaClient(host=OLLAMA_HOST, timeout=REQUEST_TIMEOUT)


def _strip_think_blocks(text: str) -> str:
    # Strip explicit <think>...</think> blocks if the model emits them.
    return re.sub(r"<think>[\s\S]*?</think>", "", text).strip()


def _response_content(response) -> str:
    try:
        return response["message"]["content"]
    except (TypeError, KeyError):
        return response.message.content


def ask_llm(
    prompt: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.2,
    think: bool = False,
) -> str:
    """
    Ask the local Ollama endpoint for a response.

    For Qwen reasoning models, we default to think=False and cap max tokens so
    command handlers (/summary) return quickly and reliably.
    """
    response = client.chat(
        model=MODEL_ID,
        messages=[{"role": "user", "content": prompt}],
        think=think,
        options={
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    )

    raw = _response_content(response) or ""
    raw = _strip_think_blocks(raw)

    if not raw:
        raise ValueError("LLM returned empty content")

    return raw


if __name__ == "__main__":
    print(ask_llm("Who are you?"))
