"""Rule engine — runs a ruleset over ETL metrics and returns ranked advice.

Importing this package registers every built-in rule (fault, contract,
device_choice, utilization). ``run_rules`` is the single entry point that
replaces the old ``anomalies.detect_all``.
"""

from .base import RuleContext, build_context, run_rules  # noqa: F401
from . import fault, contract, device_choice, utilization  # noqa: F401  (register rules)
