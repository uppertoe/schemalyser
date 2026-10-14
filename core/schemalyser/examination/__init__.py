"""The examination of a question package: the stages by which a package written by a person or a model is judged on
evidence the writer never saw, and the level each stage grants.

    python -m schemalyser.examination run PACKAGE.json [--admit draft] [--case NAME ...] [--seed N]
    python -m schemalyser.examination register NAME [--kind question|capability|audit]

The first stage, in static.py, reads the package as the import does and asks nothing of any hospital: the package's
own rules through question.py, the role-level policy with its dialect rule, the two-part form that a hospital compiles,
and the result columns the package declares against the columns its SQL returns. A package that keeps every rule is
well formed, which is the lowest level a package may hold and the only one this stage grants; the later stages, which
run the package on the role-level shadow, apply the metamorphic checks, compare it with the oracle and with planted
cases, grant the levels above. The examiner runs on the owner's own machine, with the private case root, and its
report is what the service shows beside a package.
"""
from . import metamorphic, planted, shadow, static  # noqa: F401

LEVELS = ("not well formed", "well formed")
