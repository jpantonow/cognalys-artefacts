#!/usr/bin/env python3
"""
Parsing helpers for free-text LLM responses.

Extracted from generate_error_fixes.py (Phase 1 of the
modularization plan).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional


class ResponseParser:
    """Extracts structured data from model responses.

    Shared by every provider, OpenAI included -- naming one model here was
    wrong rather than merely stale.
    """

    @staticmethod
    def parse_json_object(response_text: str) -> Optional[Dict]:
        """Parse a JSON object, accepting an optional Markdown code fence."""
        cleaned = response_text.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
        if fence:
            cleaned = fence.group(1)
        else:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                cleaned = cleaned[start:end + 1]
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def parse_response(response_text: str, logger: Optional[logging.Logger] = None) -> Dict:
        """
        Parse a model response into structured format.

        Args:
            response_text: Raw response from the model
            logger: Optional logger

        Returns:
            Dict with bug_root_cause, error_explanation, etc.
        """
        result = {
            "bug_root_cause": "Not extracted",
            "error_explanation": "Not extracted",
            "fix_summary": "Model analysis applied",
            "key_changes": [],
            "confidence_percent": 50,
            "analysis_notes": "Extracted from the model response"
        }

        # Extract ROOT CAUSE section
        root_cause_match = re.search(
            r'^\s*ROOT\s+CAUSE\s*:\s*(.+?)'
            r'(?=^\s*(?:EXPLANATION|FIX|CONFIDENCE)\s*:|\Z)',
            response_text,
            re.IGNORECASE | re.DOTALL | re.MULTILINE
        )
        if root_cause_match:
            result["bug_root_cause"] = root_cause_match.group(1).strip()

        # Extract EXPLANATION section
        explanation_match = re.search(
            r'^\s*EXPLANATION\s*:\s*(.+?)'
            r'(?=^\s*(?:FIX|CONFIDENCE|ROOT\s+CAUSE)\s*:|\Z)',
            response_text,
            re.IGNORECASE | re.DOTALL | re.MULTILINE
        )
        if explanation_match:
            result["error_explanation"] = explanation_match.group(1).strip()

        # Extract CONFIDENCE
        confidence_match = re.search(
            r'CONFIDENCE[:\s]+(\w+)',
            response_text,
            re.IGNORECASE
        )
        if confidence_match:
            conf_text = confidence_match.group(1).lower()
            if "high" in conf_text:
                result["confidence_percent"] = 85
            elif "medium" in conf_text:
                result["confidence_percent"] = 60
            elif "low" in conf_text:
                result["confidence_percent"] = 30

        # Extract code block (Java/Kotlin)
        code_match = re.search(
            r'```(?:java|kotlin)?\s*\n(.*?)\n```',
            response_text,
            re.DOTALL
        )

        code_block = None
        if code_match:
            code_block = code_match.group(1).strip()
        else:
            # Fallback: find largest brace-balanced Java code
            braced_blocks = re.findall(
                r'((?:public|private|protected)?\s+\w+\s+\w+\s*\([^)]*\)\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})',
                response_text,
                re.DOTALL
            )
            if braced_blocks:
                code_block = max(braced_blocks, key=len)

        return result, code_block

    @staticmethod
    def extract_key_changes(response_text: str) -> List[str]:
        """Extract key changes from response."""
        changes = []

        # Look for bulleted changes
        bullet_matches = re.findall(
            r'[-*]\s+(.+?)(?:\n|$)',
            response_text
        )
        changes.extend(bullet_matches[:5])  # Max 5 changes

        # Look for numbered changes
        numbered_matches = re.findall(
            r'\d+\.\s+(.+?)(?:\n|$)',
            response_text
        )
        changes.extend(numbered_matches[:3])

        return changes[:5] if changes else ["Fix applied based on model analysis"]
