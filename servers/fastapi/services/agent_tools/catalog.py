"""One capability registry drives discovery and execution admission."""

from services.chat.schemas import NoArgsInput
from services.agent_tools.schemas import ConfirmOutline, SelectTemplate


READ_TOOLS = frozenset(
    {
        "getSlideAtIndex",
        "searchSlide",
        "getAvailableLayouts",
        "getContentSchemaFromLayoutId",
        "getAvailableBlocks",
    }
)
WRITE_RESULTS = {
    "addOutline": "saved",
    "updateOutline": "saved",
    "deleteOutline": "deleted",
    "saveSlide": "saved",
    "addNewSlide": "added",
    "deleteSlide": "deleted",
    "updateElement": "updated",
    "addElement": "added",
    "deleteElement": "deleted",
    "addComponent": "added",
    "createComponent": "added",
    "updateComponent": "updated",
    "deleteComponent": "deleted",
}
OUTLINE_TOOLS = frozenset({"addOutline", "updateOutline", "deleteOutline"})
LIFECYCLE_TOOLS = {
    "confirmOutline": (
        ConfirmOutline,
        "Record the user's confirmation of this outline revision.",
    ),
    "selectTemplate": (
        SelectTemplate,
        "Snapshot an accessible native V2 template after outline confirmation.",
    ),
    "completeDocument": (
        NoArgsInput,
        "Finish composing only when all outline pages are saved.",
    ),
}


def tool_catalog(tools):
    native = [
        {
            "name": tool.name,
            "description": tool.description.replace(
                "addNewSlideLayout or updateSlide", "saveSlide"
            ).replace(
                "a URL returned by generateAssets",
                "an existing owned asset URL from the native image upload API",
            ),
            "mutates": tool.name in WRITE_RESULTS,
            "inputSchema": tool.input_schema.model_json_schema(by_alias=True),
        }
        for tool in tools.get_tool_definitions()
        if tool.name in READ_TOOLS or tool.name in WRITE_RESULTS
    ]
    return native + [
        {
            "name": name,
            "description": description,
            "mutates": True,
            "inputSchema": schema.model_json_schema(by_alias=True),
        }
        for name, (schema, description) in LIFECYCLE_TOOLS.items()
    ]
