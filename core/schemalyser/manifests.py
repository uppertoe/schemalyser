"""The primitives of an execution package's manifest, which an audit's package (audit.py) and a release's (release.py)
both write and which audit.status and audit.approve read back: the tool's name and version, the hash of a file or a
text, the hashes of the files beside a script, the record of the contract's parts that a script reads, the policy's
version, and the approval as it stands before anyone records one. They sit in layer 3 so that the release, which is
layer 3, may use them without importing the orchestration that assembles an audit's package."""
import hashlib
import re
from pathlib import Path

from . import policy, rolemap

TOOL = "schemalyser"
NOT_APPROVED = "not approved"
APPROVAL_STATES = (NOT_APPROVED, "approved", "refused")
NOT_RECORDED = "not recorded"
APPROVE = "python -m schemalyser.audit approve {folder} --by NAME [--refuse] [--note TEXT] [--date YYYY-MM-DD]"
APPROVAL_SAYS = "No one has yet recorded approval or refusal. The database analyst records either with the command below."


def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def tool_version():
    try:
        from importlib.metadata import version
        return version("schemalyser")
    except Exception:  # noqa: BLE001 - the package may run from its folder without being installed
        text = (Path(__file__).resolve().parents[1] / "pyproject.toml")
        found = re.search(r'^version = "([^"]+)"', text.read_text(encoding="utf-8"), re.M) if text.exists() else None
        return found.group(1) if found else "unknown"


def policy_version():
    """The version of the policy that derives the class, or "not recorded" where the policy states none."""
    return str(getattr(policy, "POLICY_VERSION", NOT_RECORDED))


def file_hashes(folder, names):
    """The hash of each named file beside a script, or None where the file is absent."""
    found = {}
    for name in names:
        path = Path(folder) / name
        found[name] = sha256(path.read_bytes()) if path.is_file() else None
    return found


def contract_record(views):
    """The contract's version and the hash of each of the given parts, which a change to the contract is checked against."""
    model = rolemap.contract()
    hashes = rolemap.part_hashes(model)
    return {"version": model.get("version"), "part_hashes": {v: hashes.get(v) for v in sorted(views)}}


def approval(folder_name):
    """The approval of a package before anyone has recorded one, with the command that records it."""
    return {"state": NOT_APPROVED, "by": None, "date": None, "note": None, "inputs_sha256": None,
            "says": APPROVAL_SAYS, "command": APPROVE.format(folder=folder_name)}
