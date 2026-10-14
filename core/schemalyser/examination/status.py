"""The level a package holds, derived from the stages its examination passed, and the signed report that records it.

A package asserts no status. The report names the package's hash, the contract's version, the policy's and the
dialect's versions, the examiner's version and the register's hash, and the examiner signs it with a key kept outside
any repository; the service and the hospital side verify the signature with the public key, and a change of any of
those versions voids every level above well formed that was granted under the old one.

    well formed                       the static stage passed
    logic examined                    with synthetic execution, the three metamorphic checks, and the oracle's
                                      differential run where the oracle covers the question
    examined against held-out cases   with the planted cases passed as well, every one registered before the
                                      examination ran
"""
import datetime as dt
import hashlib
import json
from pathlib import Path

from . import planted
from .. import manifests, policy, rolemap, rolepolicy, signing

EXAMINER_VERSION = "1"
LEVELS = ("not well formed", "well formed", "logic examined", "examined against held-out cases")
# The examiner's public key ships with the role model, so that the page in a hospital and the service verify the same
# signatures; the private key lives in the examiner's private root alone.
PUBLIC_KEY = rolemap.MODEL / "examiner.pub"


def versions():
    """The versions a level rests on, as the report records them."""
    register = planted.REGISTER
    return {"contract": rolemap.contract().get("version"), "policy": manifests.policy_version(),
            "dialect": rolepolicy.DIALECT_VERSION, "examiner": EXAMINER_VERSION,
            "register_sha256": hashlib.sha256(register.read_bytes()).hexdigest() if register.is_file() else None}


def derive(report, examined_at=None):
    """The level the report's stages grant, with the reason, as {"level", "says"}."""
    if report.get("level") != "well formed":
        return {"level": "not well formed", "says": report.get("says") or "The static stage did not pass."}
    shadow = (report.get("shadow") or {}).get("outcome")
    metamorphic = (report.get("metamorphic") or {}).get("outcome")
    oracle = (report.get("oracle") or {}).get("outcome")
    if shadow != "passed" or metamorphic != "passed":
        return {"level": "well formed", "says": "The package is well formed; synthetic execution or the metamorphic checks did not pass, so its logic is not examined."}
    if oracle == "failed":
        return {"level": "well formed", "says": "The package is well formed; its answer disagrees with the oracle's, so its logic is not examined."}
    level = "logic examined"
    says = "The package's logic is examined: it runs on the shadow, passes the three metamorphic checks" + \
        (" and agrees with the oracle." if oracle == "passed" else ", and the oracle does not cover it, so its logic rests on cases written by hand.")
    cases = report.get("planted") or []
    if cases and all(c.get("outcome") == "passed" for c in cases):
        held = planted.read_register()["cases"]
        when = examined_at or report.get("examined_at") or dt.date.today().isoformat()
        early = all(held.get(c["name"], {}).get("date", "9999") < when[:10] for c in cases)
        if early:
            level = "examined against held-out cases"
            says += f" It gives the held-out answer of {len(cases)} planted {'case' if len(cases) == 1 else 'cases'}, each registered before this examination."
        else:
            says += " Its planted cases were registered on the day of the examination or later, so they do not yet count as held out."
    return {"level": level, "says": says}


def canonical(report):
    """The bytes a signature covers: the report without its signature, as canonical JSON."""
    held = {k: v for k, v in report.items() if k != "signature"}
    return json.dumps(held, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def finish(report, key=None, examined_at=None, examinable=None):
    """The report completed: the versions, the time, the worked examples where an examinable is given, the derived
    level, and, with a key, the examiner's signature."""
    report = dict(report)
    report["examined_at"] = examined_at or dt.datetime.now().astimezone().isoformat(timespec="seconds")
    report["versions"] = versions()
    if examinable is not None and "examples" not in report:
        from . import examples
        report["examples"] = examples.public(examples.generate(examinable))
    derived = derive(report, report["examined_at"])
    report["status"] = derived
    if key is not None:
        report["signature"] = {"public_key": signing.public_key(key).hex(), "signature": signing.sign(key, canonical(report)).hex()}
    return report


def verify(report, public=None):
    """Whether a report is the examiner's, its versions current and its level standing: {"verified", "level",
    "says"}. A report with no signature, a wrong one or another key's is refused; a report signed under other
    versions than the current keeps well formed at most."""
    signature = report.get("signature") or {}
    public = public if public is not None else (bytes.fromhex(PUBLIC_KEY.read_text().strip()) if PUBLIC_KEY.is_file() else None)
    if public is None:
        return {"verified": False, "level": "not well formed", "says": "No public key of the examiner is held, so the report cannot be verified."}
    try:
        held_key, held = bytes.fromhex(signature.get("public_key", "")), bytes.fromhex(signature.get("signature", ""))
    except ValueError:
        return {"verified": False, "level": "not well formed", "says": "The report's signature is not one the examiner writes."}
    if held_key != public or not signing.verify(public, canonical(report), held):
        return {"verified": False, "level": "not well formed", "says": "The report's signature is not the examiner's, so the report grants nothing."}
    level = (report.get("status") or {}).get("level", "not well formed")
    now = versions()
    changed = [k for k, v in (report.get("versions") or {}).items() if now.get(k) != v]
    if changed and level in LEVELS[2:]:
        return {"verified": True, "level": "well formed",
                "says": f"The report is the examiner's, but the {', '.join(changed)} changed since it was written, which voids every level above well formed."}
    return {"verified": True, "level": level, "says": (report.get("status") or {}).get("says", "")}


def read_key(path):
    return bytes.fromhex(Path(path).read_text().strip())
