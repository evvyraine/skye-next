import asyncio
import os

from openai import AsyncOpenAI


async def main() -> None:
    client = AsyncOpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
    )
    try:
        page = await client.containers.files.list(container_id="gen_d508fc4dd54a", limit=100)
        print([item.model_dump() for item in page.data])
    finally:
        await client.close()


asyncio.run(main())
