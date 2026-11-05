"""Mini serveur MCP (stdio) pour les tests."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Additionne deux entiers."""
    return a + b


@mcp.tool()
def danger(x: str) -> str:
    """Outil non listé dans allow : doit demander confirmation."""
    return x


if __name__ == "__main__":
    mcp.run()
