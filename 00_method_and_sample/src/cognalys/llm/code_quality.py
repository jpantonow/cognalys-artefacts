#!/usr/bin/env python3
"""
Syntax-level quality signal for generated code.

Extracted from generate_error_fixes.py (Passo 5 of the
modularization plan). This is deliberately independent of the model's
self-reported confidence: it inspects the generated text itself for
hallucination markers and structural damage.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple


class CodeQualityValidator:
    """Validates generated code and detects hallucinations."""

    # Hallucination patterns specific to common LLM errors
    HALLUCINATION_PATTERNS = [
        (r"\bObject_MonitorOwner\b", "Hallucinated class: Object_MonitorOwner"),
        (r"ownsObjectMonitor", "Non-existent method: ownsObjectMonitor"),
        (r"\balogs\b", "Corrupted text: alogs"),
        (r"s\s+monitor", "Corrupted phrase: s monitor"),
        (r"Check if the current thread owns", "Template text leaked"),
        (r"Call the.*method on the object's monitor", "Template text leaked"),
        (r"Install\s+\w+\s+\w+", "Hallucinated Install pattern"),
    ]

    # Valid Android/Java APIs (whitelist for common methods)
    VALID_ANDROID_APIS = {
        "android.app.Notification",
        "android.app.NotificationManager",
        "androidx.core.app.NotificationCompat",
        "android.content.Context",
        "android.content.Intent",
        "android.app.PendingIntent",
        "java.lang.Thread",
        "java.lang.Exception",
        "java.lang.IllegalStateException",
        "java.lang.IllegalArgumentException",
    }

    @staticmethod
    def validate_code(
        code: str,
        original_code: str = None,
        spec: str = "",
        method_name: str = "",
    ) -> Tuple[bool, float, List[str]]:
        """
        Validate generated code quality.

        Args:
            code: Generated Java/Kotlin code
            original_code: Original extracted method for comparison
            spec: Dynamic-analysis specification associated with the error
            method_name: Target method whose signature must be preserved

        Returns:
            (is_valid, quality_score[0-1], issues_list)
        """
        if not code or len(code) < 10:
            return False, 0.0, ["Code too short"]

        quality_score = 100.0
        issues = []

        original_code = original_code or ""

        # Check for hallucinated keywords/classes
        for pattern, issue_desc in CodeQualityValidator.HALLUCINATION_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                issues.append(issue_desc)
                quality_score -= 25

        # Check brace matching
        brace_count = code.count('{') - code.count('}')
        paren_count = code.count('(') - code.count(')')

        if brace_count != 0:
            issues.append(f"Unmatched braces (delta: {brace_count})")
            quality_score -= 15

        if paren_count != 0:
            issues.append(f"Unmatched parentheses (delta: {paren_count})")
            quality_score -= 15

        # Check for unclosed strings
        double_quotes = code.count('"') % 2
        if double_quotes != 0:
            issues.append("Unclosed string literals")
            quality_score -= 10

        # Check method signature preserved
        if original_code and "public" in code:
            # Basic check: method should start with access modifier
            if not re.search(r'\b(public|private|protected)\s+', code):
                issues.append("Missing access modifier")
                quality_score -= 10

        # Check for missing semicolons only in Java. Kotlin statements do not
        # require them, so this heuristic is actively misleading there.
        lines = code.split('\n')
        suspect_lines = 0
        # Check both sides: original_code alone misses the common case of a
        # `declaration` edit replacing a non-function anchor (an import, a
        # val, an existing class) with new Kotlin code.
        is_kotlin = bool(re.search(r"\bfun\s+\w+\s*\(", f"{original_code}\n{code}"))
        if not is_kotlin:
            for line in lines:
                line = line.strip()
                if line and not line.endswith(('{', '}', ';', '/*', '//', '*/', ',', '(')):
                    # Could be missing semicolon
                    if any(kw in line for kw in ['=', 'new', 'return', 'throw', 'break']):
                        suspect_lines += 1

        if lines and suspect_lines > len(lines) * 0.3:
            issues.append(f"Many lines without semicolons ({suspect_lines}/{len(lines)})")
            quality_score -= 10

        quality_score = max(0, quality_score)
        is_valid = quality_score > 50

        return is_valid, quality_score / 100.0, issues


def coordinated_quality_metrics(
    edits: List[Dict], spec: str = "", method_name: str = "",
) -> Dict:
    """Deterministic, syntax-level quality signal for a coordinated edit set.

    Independent of the model's self-reported confidence: runs
    CodeQualityValidator against every non-delete edit's corrected_code and
    reduces by the weakest edit, so one malformed file in a multi-file patch
    is not hidden by the other edits looking fine. `confidence_percent` (the
    model's own report) must stay a separate field from this.
    """
    results = [
        CodeQualityValidator.validate_code(
            str(item.get("corrected_code", "")),
            str(item.get("original_code", "")),
            spec, method_name,
        )
        for item in edits
        if isinstance(item, dict) and str(item.get("edit_kind", "")) != "delete"
    ]
    if not results:
        return {
            "is_syntactically_valid": True, "quality_score": 100, "issues": [],
        }
    return {
        "is_syntactically_valid": all(result[0] for result in results),
        "quality_score": round(min(result[1] for result in results) * 100),
        "issues": sorted({issue for result in results for issue in result[2]}),
    }
