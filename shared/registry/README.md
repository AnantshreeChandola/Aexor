# Plugin Registry

Logical `uses/call` → MCP tool binding and safety metadata.

- Fields per entry:
  - mcp_server, mcp_tool, operation, param map
  - previewability (true/false), scopes[], idempotency key strategy, compensation (op)
  - safety class, rate limits

- Example (conceptual):
```
uses: google.calendar
call: create_event
mcp_server: google-calendar
mcp_tool: create_event
params: { title, attendees, start, end, conferencing }
previewable: false
scopes: ["calendar.write"]
idempotency: plan_id:step:arg_hash
compensation: delete_event
```

Add/modify capabilities here; MCP tool invocations remain generic (Preview/Execute only).
