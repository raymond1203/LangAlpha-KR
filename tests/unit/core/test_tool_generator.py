"""Tests for ptc_agent.core.tool_generator module.

Covers ToolFunctionGenerator: type mapping, function generation,
docstring creation, and return type extraction.
"""

from ptc_agent.core.mcp_registry import MCPToolInfo
from ptc_agent.core.tool_generator import ToolFunctionGenerator


def _make_tool(
    name: str = "get-data",
    description: str = "Fetch data",
    input_schema: dict | None = None,
    server_name: str = "test_server",
) -> MCPToolInfo:
    schema = input_schema or {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Ticker symbol",
            },
        },
        "required": ["symbol"],
    }
    return MCPToolInfo(
        name=name,
        description=description,
        input_schema=schema,
        server_name=server_name,
    )


class TestMapJsonTypeToPython:
    """Tests for _map_json_type_to_python."""

    def test_known_types(self):
        gen = ToolFunctionGenerator()
        assert gen._map_json_type_to_python("string") == "str"
        assert gen._map_json_type_to_python("number") == "float"
        assert gen._map_json_type_to_python("integer") == "int"
        assert gen._map_json_type_to_python("boolean") == "bool"
        assert gen._map_json_type_to_python("array") == "List"
        assert gen._map_json_type_to_python("object") == "Dict"
        assert gen._map_json_type_to_python("null") == "None"

    def test_unknown_type_returns_any(self):
        gen = ToolFunctionGenerator()
        assert gen._map_json_type_to_python("custom_type") == "Any"


class TestGenerateExampleValue:
    """Tests for _generate_example_value."""

    def test_known_examples(self):
        gen = ToolFunctionGenerator()
        assert gen._generate_example_value("string") == '"example"'
        assert gen._generate_example_value("number") == "42.0"
        assert gen._generate_example_value("integer") == "42"
        assert gen._generate_example_value("boolean") == "True"
        assert gen._generate_example_value("array") == "[]"
        assert gen._generate_example_value("object") == "{}"

    def test_unknown_type_returns_empty_string(self):
        gen = ToolFunctionGenerator()
        assert gen._generate_example_value("foo") == '""'


class TestExtractReturnInfo:
    """Tests for _extract_return_info."""

    def test_no_returns_section(self):
        gen = ToolFunctionGenerator()
        rtype, rdesc = gen._extract_return_info("Just a description")
        assert rtype == "Any"
        assert rdesc == "Tool execution result"

    def test_empty_description(self):
        gen = ToolFunctionGenerator()
        rtype, rdesc = gen._extract_return_info("")
        assert rtype == "Any"

    def test_none_description(self):
        gen = ToolFunctionGenerator()
        rtype, rdesc = gen._extract_return_info(None)
        assert rtype == "Any"

    def test_dict_return_type(self):
        gen = ToolFunctionGenerator()
        desc = "Does something.\n\nReturns:\n    dict: A mapping of values"
        rtype, rdesc = gen._extract_return_info(desc)
        assert rtype == "dict"

    def test_list_of_dict_return_type(self):
        gen = ToolFunctionGenerator()
        desc = "Fetches data.\n\nReturns:\n    list[dict] with results"
        rtype, rdesc = gen._extract_return_info(desc)
        assert rtype == "list[dict]"


class TestGenerateFunction:
    """Tests for _generate_function."""

    def test_function_name_sanitization(self):
        gen = ToolFunctionGenerator()
        tool = _make_tool(name="get-stock.data")
        code = gen._generate_function(tool, "server")
        assert "def get_stock_data(" in code

    def test_required_params_before_optional(self):
        gen = ToolFunctionGenerator()
        tool = _make_tool(
            input_schema={
                "type": "object",
                "properties": {
                    "optional_param": {"type": "string", "description": "opt"},
                    "required_param": {"type": "integer", "description": "req"},
                },
                "required": ["required_param"],
            }
        )
        code = gen._generate_function(tool, "server")
        # required_param (int) should appear before optional_param
        req_pos = code.find("required_param: int")
        opt_pos = code.find("optional_param: str | None = None")
        assert req_pos < opt_pos


class TestGenerateToolModule:
    """Tests for generate_tool_module."""

    def test_module_contains_header_and_functions(self):
        gen = ToolFunctionGenerator()
        tools = [
            _make_tool(name="tool-a", description="Tool A"),
            _make_tool(name="tool-b", description="Tool B"),
        ]
        code = gen.generate_tool_module("my_server", tools)
        assert "my_server" in code
        assert "def tool_a(" in code
        assert "def tool_b(" in code
        assert "from typing import Any" in code


class TestGenerateToolDocumentation:
    """Tests for generate_tool_documentation."""

    def test_documentation_contains_sections(self):
        gen = ToolFunctionGenerator()
        tool = _make_tool(name="fetch-prices", description="Fetches prices.", server_name="market")
        doc = gen.generate_tool_documentation(tool)
        assert "# fetch_prices(" in doc
        assert "## Parameters" in doc
        assert "## Returns" in doc
        assert "## Example" in doc
        assert "from tools.market import fetch_prices" in doc
