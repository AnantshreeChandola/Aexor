"""
S1 rule pack definitions -- LLD Section 6.1, FR-003.

Defines the frozen regex rule pack used by RegexScanner.
Rules are compiled once at init time, not per call.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, Field


class Rule(BaseModel):
    """A single regex detection rule."""

    rule_id: str
    pattern: str
    flags: int = 0
    severity: Literal["low", "med", "high"]
    category: str


class RulePack(BaseModel):
    """Frozen rule pack with integrity checksum."""

    rules: list[Rule] = Field(default_factory=list)
    version: str = "0.1.0"
    sha256: str = ""


def _build_default_rules() -> list[Rule]:
    """Build the shipped default S1 rule set."""
    rules: list[Rule] = []

    # -- HIGH: Role-switching phrases --
    rules.append(Rule(
        rule_id="ignore_previous_instructions",
        pattern=r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+"
                r"(instructions|prompts|rules|directives)",
        flags=re.IGNORECASE,
        severity="high",
        category="role_switching",
    ))
    rules.append(Rule(
        rule_id="you_are_now_x",
        pattern=r"(?i)you\s+are\s+now\s+(?:a|an|the)\s+\w+",
        flags=re.IGNORECASE,
        severity="high",
        category="role_switching",
    ))
    rules.append(Rule(
        rule_id="new_system_prompt",
        pattern=r"(?i)(new|updated|revised)\s+system\s+prompt",
        flags=re.IGNORECASE,
        severity="high",
        category="role_switching",
    ))
    rules.append(Rule(
        rule_id="disregard_instructions",
        pattern=r"(?i)disregard\s+(all\s+)?(previous|prior|your)"
                r"\s+(instructions|rules|guidelines)",
        flags=re.IGNORECASE,
        severity="high",
        category="role_switching",
    ))
    rules.append(Rule(
        rule_id="forget_instructions",
        pattern=r"(?i)forget\s+(all\s+)?(previous|prior|your)"
                r"\s+(instructions|rules|context)",
        flags=re.IGNORECASE,
        severity="high",
        category="role_switching",
    ))

    # -- HIGH: Instruction delimiters --
    rules.append(Rule(
        rule_id="instructions_tag",
        pattern=r"</?instructions>|</?system>|</?prompt>",
        flags=re.IGNORECASE,
        severity="high",
        category="instruction_delimiters",
    ))
    rules.append(Rule(
        rule_id="system_colon_prefix",
        pattern=r"(?m)^system\s*:",
        flags=re.IGNORECASE | re.MULTILINE,
        severity="high",
        category="instruction_delimiters",
    ))
    rules.append(Rule(
        rule_id="system_colon_midline",
        pattern=r"(?i)\bsystem\s*:\s*(?:new|override|you|ignore|"
                r"directive|role|switch|unrestricted)",
        flags=re.IGNORECASE,
        severity="high",
        category="instruction_delimiters",
    ))
    rules.append(Rule(
        rule_id="new_role_assignment",
        pattern=r"(?i)new\s+(?:role|directive|assignment|mode)",
        flags=re.IGNORECASE,
        severity="high",
        category="role_switching",
    ))
    rules.append(Rule(
        rule_id="jailbreak_phrases",
        pattern=r"(?i)(?:jailbreak|jailbroken|unrestricted"
                r"\s+(?:mode|ai)|no\s+safety\s+guidelines)",
        flags=re.IGNORECASE,
        severity="high",
        category="role_switching",
    ))
    rules.append(Rule(
        rule_id="assistant_colon_prefix",
        pattern=r"(?m)^assistant\s*:",
        flags=re.IGNORECASE | re.MULTILINE,
        severity="high",
        category="instruction_delimiters",
    ))

    # -- HIGH: Fake tool-call syntax --
    rules.append(Rule(
        rule_id="fake_tool_use_xml",
        pattern=r"<tool_use>|<function_call>|<tool_call>",
        flags=re.IGNORECASE,
        severity="high",
        category="fake_tool_calls",
    ))
    rules.append(Rule(
        rule_id="fake_function_call_json",
        pattern=r'"function_call"\s*:\s*\{',
        flags=0,
        severity="high",
        category="fake_tool_calls",
    ))

    # -- MED: Zero-width characters --
    rules.append(Rule(
        rule_id="zero_width_space",
        pattern=r"\u200b",
        flags=0,
        severity="med",
        category="zero_width",
    ))
    rules.append(Rule(
        rule_id="zero_width_joiner",
        pattern=r"\u200d",
        flags=0,
        severity="med",
        category="zero_width",
    ))
    rules.append(Rule(
        rule_id="byte_order_mark",
        pattern=r"\ufeff",
        flags=0,
        severity="med",
        category="zero_width",
    ))

    # -- MED: Homoglyphs / RTL overrides --
    rules.append(Rule(
        rule_id="rtl_override",
        pattern=r"[\u202e\u202d\u200f\u200e]",
        flags=0,
        severity="med",
        category="homoglyphs",
    ))
    rules.append(Rule(
        rule_id="cyrillic_lookalike_a_e_o",
        pattern=r"[\u0430\u0435\u043e]",
        flags=0,
        severity="med",
        category="homoglyphs",
    ))

    # -- MED: Base64/hex blobs above threshold --
    rules.append(Rule(
        rule_id="base64_blob_gt_256b",
        pattern=r"[A-Za-z0-9+/=]{256,}",
        flags=0,
        severity="med",
        category="encoded_blobs",
    ))
    rules.append(Rule(
        rule_id="hex_blob_gt_256b",
        pattern=r"(?:0x)?[0-9a-fA-F]{512,}",
        flags=0,
        severity="med",
        category="encoded_blobs",
    ))

    # -- LOW: Excessive markdown link density --
    rules.append(Rule(
        rule_id="md_link_density_gt_10pct",
        pattern=r"(?:\[.*?\]\(https?://.*?\).*?){5,}",
        flags=re.DOTALL,
        severity="low",
        category="link_density",
    ))

    # -- LOW: Suspicious URL in description --
    rules.append(Rule(
        rule_id="suspicious_url_in_description",
        pattern=r"https?://(?!(?:www\.)?(?:google|microsoft|apple|"
                r"zoom|teams)\.\w)[^\s]{30,}",
        flags=0,
        severity="low",
        category="suspicious_urls",
    ))

    return rules


def load_default_rule_pack() -> RulePack:
    """Load the default frozen rule pack."""
    rules = _build_default_rules()
    pack = RulePack(rules=rules, version="0.1.0")
    # Compute integrity checksum
    payload = json.dumps(
        [r.model_dump() for r in rules],
        sort_keys=True,
    )
    pack.sha256 = hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()[:16]
    return pack
