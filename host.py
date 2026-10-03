"""Container entry point: serves the agent over the Foundry Responses protocol.

Local run (no Docker):  python host.py   then POST http://127.0.0.1:8088/responses
"""
import os
import threading
import time

import requests
import uvicorn
from dotenv import load_dotenv

load_dotenv()


def start_mock_maintenance_api():
    """Run the mock maintenance system inside this container.

    Inside a container, 127.0.0.1 is the container itself, so the mock API has to live
    here for the demo. In a real deployment, set MAINTENANCE_API_URL to the client's
    maintenance system and this function is skipped.
    """
    server = uvicorn.Server(
        uvicorn.Config("api_server:app", host="127.0.0.1", port=8000, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            requests.get("http://127.0.0.1:8000/docs", timeout=0.5)
            return
        except requests.RequestException:
            time.sleep(0.1)
    raise RuntimeError("The bundled maintenance API did not start")


def main():
    if "MAINTENANCE_API_URL" not in os.environ:
        start_mock_maintenance_api()

    from langchain_azure_ai.agents.hosting import ResponsesHostServer
    from agent import graph

    port = int(os.environ.get("PORT", "8088"))
    ResponsesHostServer(graph).run(port=port)


if __name__ == "__main__":
    main()
