import asyncio
from azure.identity import DefaultAzureCredential
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

PROJECT = "https://manufacturing-agent-resource.services.ai.azure.com/api/projects/manufacturing-agent"
TOOLBOX = "manufacturing-sop-kb"

url = f"{PROJECT}/toolboxes/{TOOLBOX}/mcp?api-version=v1"
token = DefaultAzureCredential().get_token("https://ai.azure.com/.default").token
headers = {"Authorization": f"Bearer {token}", "Foundry-Features": "Toolboxes=V1Preview"}

async def main():
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Tools found:", len(tools.tools))
            for t in tools.tools:
                print(" -", t.name, "|", (t.description or "")[:80])
            if not tools.tools:
                return
            name = tools.tools[0].name
            print("\nSearching the SOPs with:", name)
            try:
                result = await session.call_tool(name, {"queries": ["coolant system service interval"]})
                print(str(result.content)[:1500])
            except Exception as e:
                print("SEARCH FAILED:", type(e).__name__, str(e)[:600])

asyncio.run(main())