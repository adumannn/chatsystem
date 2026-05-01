from openai import OpenAI

client = OpenAI(
    api_key="ollama",
    base_url="http://localhost:11434/v1"
)

MODEL_ID = "qwen3.5:4b"

def ask_llm(prompt: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL_ID,
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0.6,
    )

    return resp.choices[0].message.content or ""

if __name__ == "__main__":
    print(ask_llm("Who are you?"))