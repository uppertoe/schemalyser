package question

import (
	"strings"
	"testing"

	"github.com/uppertoe/schemalyser/ask/internal/rolemodel"
)

const reply = "Here is the question as agreed.\n\n```schemalyser-question\n{\"title\": \"Median transfusion in neonates\", \"specification\": {\"cohort\": {\"age_days_at_start\": {\"lt\": 28}}, \"outcome\": {\"part\": \"role_fluid\", \"kind\": \"red_cells\"}, \"result\": {\"grain\": \"anaesthetic\"}}, \"decisions\": [\"A neonate is under 28 days at the start of the anaesthetic.\"], \"question_sql\": \"WITH neonatal AS (SELECT a.anaesthetic_key FROM role_anaesthetic a JOIN role_patient p ON p.patient_key = a.patient_key WHERE p.is_test = 0 AND DATEDIFF(day, p.birth_date, a.start_time) < 28)\\nSELECT COUNT(*) AS anaesthetics, SUM(f.volume_ml) AS red_cells_ml FROM neonatal n JOIN role_fluid f ON f.anaesthetic_key = n.anaesthetic_key WHERE f.kind = 'red_cells'\", \"note\": \"Counts and volumes only.\", \"parts_used\": [\"role_patient\", \"role_anaesthetic\", \"role_fluid\"], \"proposals\": []}\n```\n\nTell me if the age cut-off is right."

func contract(t *testing.T) *rolemodel.Contract {
	c, err := rolemodel.Load()
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestAReplysBlockBecomesAPackageAndTheProseStays(t *testing.T) {
	prose, raw := Split(reply)
	if raw == "" || strings.Contains(prose, "schemalyser-question") || !strings.Contains(prose, "age cut-off") {
		t.Fatalf("split went wrong: %q", prose)
	}
	pkg, err := Parse(raw, contract(t), Produced{Model: "fake", At: "2026-10-14", By: "a@b"})
	if err != nil {
		t.Fatal(err)
	}
	if pkg.Format != Format || pkg.ContractVersion != "1.1" || pkg.Status != "drafted" || pkg.Hash == "" {
		t.Fatalf("package: %+v", pkg)
	}
	if strings.Join(pkg.PartsUsed, ",") != "role_anaesthetic,role_fluid,role_patient" || strings.Join(pkg.DraftPartsUsed, ",") != "role_fluid" {
		t.Fatalf("parts: %v drafts %v", pkg.PartsUsed, pkg.DraftPartsUsed)
	}
	again, _ := Parse(raw, contract(t), Produced{Model: "other", At: "later"})
	if again.Hash != pkg.Hash {
		t.Fatal("the hash depends on who produced the package")
	}
}

func TestAnInventedRoleAndAForbiddenStatementAreRefused(t *testing.T) {
	bad := strings.Replace(reply, "role_fluid f", "role_blood f", 1)
	_, raw := Split(bad)
	if _, err := Parse(raw, contract(t), Produced{}); err == nil || !strings.Contains(err.Error(), "role_blood") {
		t.Fatalf("an invented role was not refused: %v", err)
	}
	bad = strings.Replace(reply, "SELECT COUNT(*)", "DELETE FROM x; SELECT COUNT(*)", 1)
	_, raw = Split(bad)
	if _, err := Parse(raw, contract(t), Produced{}); err == nil || !strings.Contains(err.Error(), "DELETE") {
		t.Fatalf("a forbidden statement was not refused: %v", err)
	}
}

func TestAProposalMakesTheQuestionWaitForTheContract(t *testing.T) {
	raw := `{"title": "Blocks with a catheter", "specification": {"cohort": {}}, "question_sql": "", "proposals": [{"kind": "column", "part": "role_technique", "because": "the question needs the block's depth", "shape": {"name": "depth_cm", "type": "number"}}]}`
	pkg, err := Parse(raw, contract(t), Produced{})
	if err != nil {
		t.Fatal(err)
	}
	if pkg.Status != "waits for the contract" || len(pkg.Proposals) != 1 {
		t.Fatalf("package: %+v", pkg)
	}
}
