"""Shared helpers for reading Claude API responses."""


def extract_text(message) -> str:
    """
    Return the first text block from a Claude message response.

    Indexing `message.content[0].text` is unsafe: models with extended thinking
    return a ThinkingBlock as the first content block, which has no `.text`
    attribute. That is what broke all three Claude stages when the pipeline
    moved from claude-sonnet-4 to claude-sonnet-5-5.

    Scanning for the first block whose type is "text" works whether or not the
    model returns thinking, so this keeps working across model changes.
    """
    for block in message.content:
        if getattr(block, "type", None) == "text":
            return block.text

    # Fallback: any block exposing a string `.text`, in case a future block
    # type carries the answer under a different type name.
    for block in message.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text

    kinds = [getattr(b, "type", type(b).__name__) for b in message.content]
    raise ValueError(
        f"No text block found in the model response. Blocks returned: {kinds}"
    )
