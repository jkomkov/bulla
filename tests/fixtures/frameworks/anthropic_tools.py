"""Sample Anthropic Messages tool list as a Python literal."""

tools = [
    {
        "name": "fetch_url",
        "description": "Fetch a URL and return text content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "timeout_seconds": {"type": "integer"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "list_files",
        "description": "List files in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
]
