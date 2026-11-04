"""Only the four M1 tools. SAM locate is deliberately absent until M2."""

from autoqa.config import Act, Finish, Issue, Note

MODELS = {"act": Act, "note": Note, "report_issue": Issue, "finish": Finish}
DESCRIPTIONS = {
    "act": "Execute 1 to 4 movement primitives in order, at most 70 tics in total. Ends the decision.",
    "note": "Append to or replace your private scratchpad (1500 characters maximum).",
    "report_issue": "Record a suspected gameplay issue with visible evidence; it is not verified as a defect.",
    "finish": "Stop playing because you give up or are blocked. The environment detects success.",
}


def parameters(model):
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            if "$ref" in value:
                return expand(definitions[value["$ref"].split("/")[-1]])
            return {key: expand(item) for key, item in value.items() if key != "title"}
        return value

    return expand(schema)


TOOLS = [
    dict(
        type="function",
        name=name,
        description=DESCRIPTIONS[name],
        strict=True,
        parameters=parameters(model),
    )
    for name, model in MODELS.items()
]
