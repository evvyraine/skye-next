import asyncio
import os

from openai import AsyncOpenAI


async def main() -> None:
    client = AsyncOpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
        max_retries=0,
    )
    try:
        response = await client.responses.create(
            model="openai/gpt-5.6-luna",
            input=(
                "Use shell exactly once. Run curl --max-time 10 -sS -o /dev/null "
                "-w '%{http_code}' https://api.github.com/ and report stdout."
            ),
            tools=[
                {
                    "type": "openrouter:shell",
                    "parameters": {
                        "engine": "openrouter",
                        "environment": {"type": "container_auto"},
                    },
                }
            ],
        )
        for item in response.output:
            value = item.model_dump()
            print(
                {
                    "type": value.get("type"),
                    "output": value.get("output"),
                    "content": value.get("content"),
                }
            )
    finally:
        await client.close()


asyncio.run(main())
