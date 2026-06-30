# Rule Pack Governance

## Ownership

The S1 regex rule pack is owned by the security team. All
changes require a PR review from a CODEOWNERS security reviewer.

## Versioning

- Rule pack is frozen at build time in `domain/regex_rules.py`.
- The SHA-256 checksum is computed and embedded in `scanner_version`.
- Changing any rule pattern, severity, or adding/removing rules
  requires a `scanner_version` bump.

## Review Process

1. Author opens a PR modifying `domain/regex_rules.py`.
2. PR must include updated tests in `tests/test_regex_scanner.py`.
3. Run the full injection seed set (50 patterns) to verify
   detection rate >= 95%.
4. Run the benign fixture set (20 responses) to verify
   0% false positive rate.
5. Security reviewer approves.
6. Merge bumps `scanner_version` in `service/filter_service.py`.

## Hot Reload

Not supported in v1. Rule pack is frozen at import time.
Hot reload with signed rule packs is a v2 topic.
