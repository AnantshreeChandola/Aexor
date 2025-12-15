# Plugin Registry

Logical `uses/call` → n8n node binding and safety metadata.

- Fields per entry:
  - node, operation, param map
  - previewability (true/false), scopes[], idempotency key strategy, compensation (op)
  - safety class, rate limits

- Example (conceptual):
```
uses: google.calendar
call: create_event
node: Google Calendar
operation: create
params: { title, attendees, start, end, conferencing }
previewable: false
scopes: ["calendar.write"]
idempotency: plan_id:step:arg_hash
compensation: delete_event
```

Add/modify capabilities here; n8n flows remain generic (Preview/Execute only).
