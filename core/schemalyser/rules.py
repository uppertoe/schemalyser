"""The site rules file: everything the tool is told about one site's naming."""
import json
from dataclasses import dataclass, field

KNOWN_KEYS = {"definitionKeys", "personTables", "localTablePatterns", "sentinelValues",
              "neverListColumns", "personKeyColumns", "roles", "tuning"}


@dataclass
class SiteRules:
    definition_keys: list = field(default_factory=list)
    person_tables: list = field(default_factory=list)
    local_table_patterns: list = field(default_factory=list)
    sentinel_values: list = field(default_factory=list)
    never_list_columns: list = field(default_factory=list)
    person_key_columns: list = field(default_factory=list)
    roles: list = field(default_factory=list)
    tuning: dict = field(default_factory=dict)     # overrides of the tunable parameters, checked in tuning.py

    @classmethod
    def from_json(cls, text):
        if not text or not text.strip():
            return cls()
        data = json.loads(text)
        unknown = set(data) - KNOWN_KEYS
        if unknown:
            raise ValueError(f"The site rules file has unknown keys: {sorted(unknown)}")
        return cls(
            definition_keys=data.get("definitionKeys", []),
            person_tables=data.get("personTables", []),
            local_table_patterns=data.get("localTablePatterns", []),
            sentinel_values=data.get("sentinelValues", []),
            never_list_columns=data.get("neverListColumns", []),
            person_key_columns=data.get("personKeyColumns", []),
            roles=data.get("roles", []),
            tuning=data.get("tuning", {}),
        )
