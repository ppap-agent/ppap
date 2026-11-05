"""Source-value policies for the existing full-functional measurement rules.

These functions do not locate records, read files, or resolve unknown evidence.
Callers must establish the record scope and read values from the original source.
NA means a known record does not trigger this particular check, not unknown data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

STRICT_OK_TOKENS = {"ok", "合格"}
INVALID_MEASUREMENT_TOKENS = {"/", "-", "—", "－", "na", "n/a", "none", "null", "／"}
FORBIDDEN_RESULT_EQUIVALENTS = {"pass", "正确"}
GEOMETRIC_STANDARD_PREFIXES = ("位置度", "同轴度", "圆柱度", "平面度", "垂直度", "平行度")
PURE_NOMINAL_STANDARD_RE = re.compile(r"(?:\d+\s*[xX×]\s*)?[Φφ]?-?\d+(?:\.\d+)?")
MEASUREMENT_NUMBER_WITH_UNIT_RE = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(?:[a-zA-ZμΩ℃°%·.]+)?\s*$")
RULE_7_ALLOWED_METHOD_KEYWORDS = ("通止规", "菲林尺", "卡板")


def split_tokens(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    text = str(value).replace("\n", " ").replace("\r", " ").strip().lower()
    if not text:
        return ()
    return tuple(token for token in re.split(r"[/,，;；\s]+", text) if token)


def normalize_quality_standard_text(value: Any) -> str:
    normalized = str(value or "").strip()
    replacements = {
        "（": "(",
        "）": ")",
        "，": ",",
        "＋": "+",
        "－": "-",
        "—": "-",
        "–": "-",
        "／": "/",
        "﹣": "-",
        "°": "",
    }
    for src, target in replacements.items():
        normalized = normalized.replace(src, target)
    return normalized


def parse_quality_standard_range(value: Any) -> tuple[float, float] | None:
    normalized = normalize_quality_standard_text(value)
    s = re.sub(r"^\d+\s*[xX×]\s*", "", normalized)
    s = re.sub(r"^[Φφ]", "", s)

    match = re.fullmatch(r"[rR]\s*(\d+\.?\d*)\s*max", s, flags=re.IGNORECASE)
    if match:
        return (0.0, float(match.group(1)))

    match = re.fullmatch(r"(-?\d+\.?\d*)\s*min", s, flags=re.IGNORECASE)
    if match:
        return (float(match.group(1)), float("inf"))

    match = re.fullmatch(r"(-?\d+\.?\d*)\s*±\s*(\d+\.?\d*)(?:\s*[a-zA-Z]*)?", s)
    if match:
        base, tolerance = map(float, match.groups())
        return (base - tolerance, base + tolerance)

    match = re.fullmatch(r"(-?\d+\.?\d*)\s*\+\s*(\d+\.?\d*)\s*/\s*-\s*(\d+\.?\d*)", s)
    if match:
        base, upper_tol, lower_tol = map(float, match.groups())
        return (base - lower_tol, base + upper_tol)

    match = re.fullmatch(r"(-?\d+\.?\d*)\s*\(\s*\+?(\d+\.?\d*)\s*[,/]\s*(-?\d+\.?\d*)\s*\)", s)
    if match:
        base = float(match.group(1))
        upper_tol = float(match.group(2))
        lower_tol = float(match.group(3))
        return (base + min(lower_tol, upper_tol), base + max(lower_tol, upper_tol))

    match = re.fullmatch(r"(-?\d+\.?\d*)\s*\+\s*(\d+\.?\d*)", s)
    if match:
        base, upper_tol = map(float, match.groups())
        return (base, base + upper_tol)

    match = re.fullmatch(r"(-?\d+\.?\d*)\s*/\s*-\s*(\d+\.?\d*)", s)
    if match:
        base, lower_tol = map(float, match.groups())
        return (base - lower_tol, base)

    match = re.fullmatch(r"(?:≤|<=)\s*(-?\d+\.?\d*)", s)
    if match:
        return (0.0, float(match.group(1)))

    match = re.fullmatch(r"(?:<|＜)\s*(-?\d+\.?\d*)", s)
    if match:
        return (0.0, float(match.group(1)))

    match = re.fullmatch(r"[rR][aA]?\s*(\d+\.?\d*)", s)
    if match:
        return (0.0, float(match.group(1)))

    return None


def is_strict_ok_text(value: Any) -> bool:
    tokens = split_tokens(value)
    if not tokens:
        return False
    return all(token in STRICT_OK_TOKENS for token in tokens)


def contains_strict_ok_token(value: Any) -> bool:
    return any(token in STRICT_OK_TOKENS for token in split_tokens(value))


def parse_measurement_numbers(value: Any) -> tuple[float, ...] | None:
    if value is None:
        return ()
    if isinstance(value, (int, float)):
        return (float(value),)

    text = str(value).strip()
    if not text:
        return ()
    compact_text = "".join(text.lower().split())
    if compact_text in INVALID_MEASUREMENT_TOKENS:
        return None

    numbers: list[float] = []
    for token in split_tokens(text):
        if token in INVALID_MEASUREMENT_TOKENS:
            return None
        if token in STRICT_OK_TOKENS:
            return None
        if token in FORBIDDEN_RESULT_EQUIVALENTS:
            return None
        match = MEASUREMENT_NUMBER_WITH_UNIT_RE.fullmatch(token)
        if match is None:
            return None
        numbers.append(float(match.group(1)))
    return tuple(numbers)


def inspect_measurements(
    raw_values: tuple[Any, ...],
) -> tuple[Literal["empty", "numeric_values", "strict_ok_values", "mixed_or_invalid"], tuple[tuple[float, ...], ...]]:
    if not raw_values:
        return "empty", ()

    parsed_groups: list[tuple[float, ...]] = []
    saw_strict_ok = False

    for value in raw_values:
        if is_strict_ok_text(value):
            saw_strict_ok = True
            parsed_groups.append(())
            continue

        numbers = parse_measurement_numbers(value)
        if numbers is None:
            return "mixed_or_invalid", ()
        parsed_groups.append(numbers)

    if saw_strict_ok:
        if all(is_strict_ok_text(value) for value in raw_values):
            return "strict_ok_values", ()
        return "mixed_or_invalid", ()

    return "numeric_values", tuple(parsed_groups)


def classify_quality_standard(value: Any) -> tuple[str, tuple[float, float] | None]:
    numeric_range = parse_quality_standard_range(value)
    if numeric_range is not None:
        return "numeric_range", numeric_range

    normalized = re.sub(r"\s+", "", normalize_quality_standard_text(value))
    if not normalized:
        return "unknown", None

    matched_prefix = next((prefix for prefix in GEOMETRIC_STANDARD_PREFIXES if normalized.startswith(prefix)), None)
    if matched_prefix is not None:
        suffix = normalized.removeprefix(matched_prefix)
        if suffix and re.fullmatch(r"-?\d+(?:\.\d+)?", suffix):
            return "numeric_range", (0.0, float(suffix))

    if PURE_NOMINAL_STANDARD_RE.fullmatch(normalized):
        return "recognized_non_range", None

    return "unknown", None


@dataclass(frozen=True)
class MeasurementCheck:
    status: Literal["PASS", "NG", "NA", "UNABLE"]
    reason: str = ""
    # Rule 3's existing aggregate message distinguishes numeric comparisons.
    numeric_checked: bool = False


_UNITS = {
    "s": ("time", "1"),
    "sec": ("time", "1"),
    "秒": ("time", "1"),
    "min": ("time", "60"),
    "分钟": ("time", "60"),
    "h": ("time", "3600"),
    "小时": ("time", "3600"),
    "mm": ("length", "0.001"),
    "毫米": ("length", "0.001"),
    "cm": ("length", "0.01"),
    "厘米": ("length", "0.01"),
    "m": ("length", "1"),
    "米": ("length", "1"),
    "μm": ("length", "0.000001"),
    "um": ("length", "0.000001"),
    "微米": ("length", "0.000001"),
    "g": ("mass", "0.001"),
    "克": ("mass", "0.001"),
    "kg": ("mass", "1"),
    "千克": ("mass", "1"),
    "°": ("angle", "1"),
    "度": ("angle", "1"),
    "%": ("ratio", "0.01"),
    "n": ("force", "1"),
    "kn": ("force", "1000"),
    "pa": ("pressure", "1"),
    "kpa": ("pressure", "1000"),
    "mpa": ("pressure", "1000000"),
}
_UNIT_SUFFIX = re.compile(r"(?<=[0-9)])\s*([a-zA-ZμµΩ℃°%\u4e00-\u9fff]+)\s*$")


def _quantity_unit(value: str, *, threshold: bool = False) -> tuple[str, str]:
    match = _UNIT_SUFFIX.search(value)
    if match is None or (threshold and match.group(1).lower() in {"min", "max"}):
        return value, ""
    return value[: match.start()].strip(), match.group(1).lower().replace("µ", "μ")


def check_tolerance(standard: Any, measurements: tuple[Any, ...], result: Any, method: Any, *, unit: str = "") -> MeasurementCheck:
    """Apply existing rule 3 to one known dimension record."""
    # Converted cells can contain several measurements separated by line breaks.
    # Keep those boundaries while accepting spaces between a number and its unit.
    measurements = tuple("" if value is None else ";".join("".join(line.split()) for line in str(value).splitlines()) for value in measurements)
    result_text = "" if result is None else str(result)
    method_text = "" if method is None else str(method)
    measurement_kind, parsed_groups = inspect_measurements(measurements)
    if measurement_kind == "empty":
        return MeasurementCheck("NA")
    if measurement_kind == "strict_ok_values":
        if not method_text.strip():
            return MeasurementCheck("NG", "检查数据为OK/合格但缺少检查方法，无法完成校验")
        if not is_strict_ok_text(result_text):
            return MeasurementCheck("NG", f"判定结果不规范: {result_text}")
        return MeasurementCheck("PASS")
    standard_text = "" if standard is None else str(standard)
    standard_text, standard_unit = _quantity_unit(standard_text, threshold=True)
    declared_unit = unit.strip().lower().replace("µ", "μ")
    target_unit = standard_unit or declared_unit
    quantities = [[_quantity_unit(token) for token in split_tokens(value)] for value in measurements]
    explicit_units = {item_unit for group in quantities for _, item_unit in group if item_unit}
    explicit_units.update(item_unit for item_unit in (standard_unit, declared_unit) if item_unit)
    if any(item_unit not in _UNITS for item_unit in explicit_units):
        return MeasurementCheck("UNABLE", "数值单位无法可靠解析，未执行公差比较")
    if len({_UNITS[item_unit][0] for item_unit in explicit_units}) > 1:
        return MeasurementCheck("NG", "检查数据与判定标准的单位维度不一致")
    if not target_unit and len(explicit_units) > 1:
        return MeasurementCheck("UNABLE", "判定标准缺少单位，无法确认不同单位数值的比较基准")
    target_unit = target_unit or next(iter(explicit_units), "")
    if explicit_units:
        normalized = []
        for group in quantities:
            values = []
            for number, item_unit in group:
                effective_unit = item_unit or declared_unit or target_unit
                if effective_unit != target_unit and re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", number):
                    number = format(Decimal(number) * Decimal(_UNITS[effective_unit][1]) / Decimal(_UNITS[target_unit][1]), "f")
                values.append(number)
            normalized.append(";".join(values))
        measurements = tuple(normalized)
    measurement_kind, parsed_groups = inspect_measurements(measurements)
    if measurement_kind == "mixed_or_invalid":
        invalid_value = next((value for value in measurements if parse_measurement_numbers(value) is None), measurements[0])
        return MeasurementCheck("NG", f"检查数据非数值或占位符: {invalid_value}")

    standard_kind, quality_range = classify_quality_standard(standard_text)
    if standard_kind == "recognized_non_range":
        return MeasurementCheck("NA")
    if standard_kind != "numeric_range" or quality_range is None:
        return MeasurementCheck("NG", f"判定标准无法解析: {standard_text}")
    if not is_strict_ok_text(result_text):
        return MeasurementCheck("NG", f"判定结果不规范: {result_text}")
    row_numbers = [number for numbers in parsed_groups for number in numbers]
    if not row_numbers:
        return MeasurementCheck("NA")
    lower, upper = quality_range
    for measured_number in row_numbers:
        if not (lower <= measured_number <= upper):
            return MeasurementCheck("NG", f"检查数据{measured_number}超出公差范围{(lower, upper)}", numeric_checked=True)
    return MeasurementCheck("PASS", numeric_checked=True)


def check_duplicates(measurements: tuple[Any, ...]) -> MeasurementCheck:
    """Apply existing rule 5 to one known dimension record."""
    measurement_kind, parsed_groups = inspect_measurements(measurements)
    if measurement_kind != "numeric_values":
        return MeasurementCheck("NA")
    row_values = [number for numbers in parsed_groups for number in numbers or ()]
    if len(row_values) < 2:
        return MeasurementCheck("NA")
    if len(set(row_values)) < len(row_values):
        return MeasurementCheck("NG", "同行检查数据存在重复值")
    return MeasurementCheck("PASS")


def check_qualitative_method(measurements: tuple[Any, ...], method: Any) -> MeasurementCheck:
    """Apply existing rule 7 only to records containing strict OK/合格 data."""
    measurement_kind, _ = inspect_measurements(measurements)
    if measurement_kind in {"empty", "numeric_values"}:
        return MeasurementCheck("NA")
    if measurement_kind == "mixed_or_invalid" and not any(contains_strict_ok_token(value) for value in measurements):
        return MeasurementCheck("NA")
    if measurement_kind != "strict_ok_values":
        invalid_value = next((value for value in measurements if not is_strict_ok_text(value)), measurements[0])
        return MeasurementCheck("NG", f"定性方法数据不合规: {invalid_value}")
    method_text = "" if method is None else str(method)
    if not any(keyword in "".join(method_text.split()) for keyword in RULE_7_ALLOWED_METHOD_KEYWORDS):
        return MeasurementCheck("NG", f"检查数据为OK/合格但方法不匹配: {method_text}")
    return MeasurementCheck("PASS")
