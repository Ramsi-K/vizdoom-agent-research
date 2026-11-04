"""The five Pilot tools; only act can advance the game."""

from autoqa.config import Act, CombatAct, Finish, Issue, Locate, Note

MODELS = {"act": Act, "note": Note, "report_issue": Issue, "finish": Finish, "locate": Locate}
DESCRIPTIONS = {
    "act": "Execute 1 to 4 movement primitives in order, at most 70 tics in total. Ends the decision.",
    "note": "Update your private landmark/outcome ledger (1500 characters total). Record visual appearance, attempted action with decision, observed result, and uncertainty/next hypothesis. Use replace to compact old entries. Never invent an outcome before observing it.",
    "report_issue": "Record a suspected gameplay issue with visible evidence; it is not verified as a defect.",
    "finish": "Stop playing because you give up or are blocked. The environment detects success.",
    "locate": "Look for one 1-4 word noun phrase in the current frame. Returns optical candidates or found=false; it does not advance the game or verify identity.",
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


def models_for_combat(combat=False):
    return {**MODELS, "act": CombatAct} if combat else MODELS


def tools_for_level(level, *, combat=False):
    if combat:
        return [
            {
                **tool,
                "parameters": parameters(CombatAct),
                "description": tool["description"]
                + " Combat controls enabled: every primitive has a required fire boolean to hold ATTACK simultaneously. See the supplied control units.",
            }
            if tool["name"] == "act"
            else tool
            for tool in tools_for_level(level)
        ]
    if level == 1:
        return TOOLS
    return [
        {
            **tool,
            "description": tool["description"]
            + (
                " Include the NAV JSON annotation described in the instructions in your existing note text."
                if tool["name"] == "note"
                else " A current model-written NAV entry is required before act."
                if tool["name"] == "act"
                else ""
            ),
        }
        for tool in TOOLS
    ]
