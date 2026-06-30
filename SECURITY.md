# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| latest  | Yes                |

## Reporting a Vulnerability

If you discover a security vulnerability in Aexor, please report it responsibly.

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, please email **[anantshreechandola@gmail.com]** with:

- A description of the vulnerability
- Steps to reproduce the issue
- Any relevant logs or screenshots (redact sensitive data)

You can expect an initial response within 72 hours. We will work with you to understand the issue and coordinate a fix before any public disclosure.

## Security Practices

- **No secrets in source control.** All credentials, API keys, and encryption keys must be set via environment variables (`.env`). The `.env` file is gitignored and has been purged from history.
- **Encryption at rest.** Sensitive user preferences are encrypted with AES-256 via `ENCRYPTION_KEY`. Credential vault uses AES-256-GCM via `CREDENTIAL_MASTER_KEY`.
- **JWT authentication.** API endpoints are protected by signed JWTs (`JWT_SECRET`). Approval gates use a separate signing secret (`APPROVAL_TOKEN_SECRET`).
- **Trust boundary pipeline.** Untrusted LLM outputs pass through a prompt-injection defense pipeline before execution (SPEC 037).
- **Least-privilege tool execution.** The ExecuteOrchestrator scopes MCP tool calls to user-authorized integrations only.

## Key Rotation

If you suspect any key has been compromised, rotate immediately:

```bash
# Encryption key (base64, 32 bytes)
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# JWT / approval token secret (min 32 chars)
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Credential vault master key (hex, 64 hex chars)
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Update the values in your `.env` file and restart the application.
