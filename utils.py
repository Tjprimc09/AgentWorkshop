async def stream_and_print(result):
    """Prints an agent's response as it streams in, then returns the final text."""
    async for event in result.stream_events():
        if event.type == "raw_response_event" and event.data.type == "response.output_text.delta":
            print(event.data.delta, end="", flush=True)
    print()
    return result.final_output
