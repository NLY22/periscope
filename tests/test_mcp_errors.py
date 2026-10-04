from src.mcp.errors import McpError


def test_periscope_mcp_error_string_representation() -> None:
    err = McpError(code="E_TEST", message="boom", details={"k": "v"})

    assert str(err) == "E_TEST: boom"
    assert err.details == {"k": "v"}
