"""Classify rejected parameters without keeping their text or parser messages."""
import json
from dataclasses import dataclass

from ...core.serialization import (
    DuplicateJSONFieldError, NonFiniteJSONValueError, ObjectRootError, parse_json,
)


@dataclass(frozen=True, slots=True)
class ToolInputDiagnostic:
    error_category: str
    line: int | None = None
    column: int | None = None
    position: int | None = None

    def fields(self) -> dict:
        return {key: value for key, value in (
            ('error_category', self.error_category), ('line', self.line),
            ('column', self.column), ('position', self.position)) if value is not None}


def tool_input_diagnostic(value) -> ToolInputDiagnostic | None:
    if not isinstance(value, str):
        return ToolInputDiagnostic('TOOL_INPUT_TYPE_ERROR')
    try:
        parse_json(value)
    except json.JSONDecodeError as error:
        return ToolInputDiagnostic('JSON_SYNTAX_ERROR', error.lineno, error.colno, error.pos)
    except ObjectRootError:
        return ToolInputDiagnostic('JSON_ROOT_NOT_OBJECT')
    except DuplicateJSONFieldError:
        return ToolInputDiagnostic('JSON_DUPLICATE_KEY')
    except NonFiniteJSONValueError:
        return ToolInputDiagnostic('JSON_NON_FINITE')
    except RecursionError:
        return ToolInputDiagnostic('JSON_NESTING_LIMIT')
    except (ValueError, TypeError):
        return ToolInputDiagnostic('JSON_PARSE_ERROR')
    return None
