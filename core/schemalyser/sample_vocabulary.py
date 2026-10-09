"""A few concepts from the public OMOP vocabulary, so that the derived mappings can be exercised without a download.

The testbed writes them for its fast profile, and the SQL Server harness in tools/sqlserver writes the same concepts
when it is given --sample-vocabulary, so that both engines see one vocabulary.
"""

CONCEPT = [
    "concept_id\tconcept_name\tdomain_id\tvocabulary_id\tconcept_class_id\tstandard_concept\tconcept_code\tvalid_start_date\tvalid_end_date\tinvalid_reason",
    "753626\tpropofol\tDrug\tRxNorm\tIngredient\tS\t8782\t19700101\t20991231\t",
    "1125315\tacetaminophen\tDrug\tRxNorm\tIngredient\tS\t161\t19700101\t20991231\t",
    "4070719\tTonsillectomy\tProcedure\tSNOMED\tProcedure\tS\t173422009\t19700101\t20991231\t",
    "45542411\tUmbilical hernia without obstruction or gangrene\tCondition\tICD10\tICD10 code\t\tK42.9\t19700101\t20991231\t",
    "4245842\tUmbilical hernia\tCondition\tSNOMED\tDisorder\tS\t396347007\t19700101\t20991231\t",
]
CONCEPT_RELATIONSHIP = [
    "concept_id_1\tconcept_id_2\trelationship_id\tvalid_start_date\tvalid_end_date\tinvalid_reason",
    "45542411\t4245842\tMaps to\t19700101\t20991231\t",
]
CONCEPT_SYNONYM = ["concept_id\tconcept_synonym_name\tlanguage_concept_id", "1125315\tparacetamol\t4180186"]


def write(folder):
    """Writes CONCEPT.csv, CONCEPT_RELATIONSHIP.csv and CONCEPT_SYNONYM.csv into folder, and returns the folder."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "CONCEPT.csv").write_text("\n".join(CONCEPT) + "\n")
    (folder / "CONCEPT_RELATIONSHIP.csv").write_text("\n".join(CONCEPT_RELATIONSHIP) + "\n")
    (folder / "CONCEPT_SYNONYM.csv").write_text("\n".join(CONCEPT_SYNONYM) + "\n")
    return folder
