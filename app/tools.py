import logging
import os

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

logger = logging.getLogger("tools")


def get_mcp_tools():
    """Constructs and returns the McpToolset connecting to our custom mcp_server.py."""
    # Find absolute paths
    app_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(app_dir)
    mcp_server_path = os.path.join(project_root, "mcp_server.py")

    # Determine the python executable to use (ensuring virtual environment is used if available)
    venv_python = os.path.join(project_root, ".venv", "bin", "python")
    if os.path.exists(venv_python):
        python_executable = venv_python
    else:
        python_executable = "python"

    logger.info(
        f"Configuring McpToolset using interpreter: {python_executable} with script: {mcp_server_path}"
    )

    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=python_executable,
                args=[mcp_server_path],
            )
        )
    )
