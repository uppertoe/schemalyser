package question

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/uppertoe/schemalyser/ask/internal/rolemodel"
)

// The reply the fake backend gives once a question is settled, in the shape the prompt asks for.
const reply = "Here is the question as agreed.\n\n```schemalyser-question\n" + Block + "\n```\n\nTell me if the age cut-off is right."

func contract(t *testing.T) *rolemodel.Contract {
	c, err := rolemodel.Load()
	if err != nil {
		t.Fatal(err)
	}
	return c
}

var produced = Produced{Model: "fake", At: "2026-10-14T09:00:00Z", By: "dr@example.test"}

func TestAReplysBlockBecomesAPackageInTheSchemasShapeAndTheProseStays(t *testing.T) {
	prose, raw := Split(reply)
	if raw == "" || strings.Contains(prose, "schemalyser-question") || !strings.Contains(prose, "age cut-off") {
		t.Fatalf("split went wrong: %q", prose)
	}
	pkg, data, err := Parse(raw, contract(t), produced, "2026-10-14")
	if err != nil {
		t.Fatal(err)
	}
	if pkg.Format != Format || pkg.ContractVersion != "1.1" || pkg.Waits() {
		t.Fatalf("package: %+v", pkg)
	}
	names := []string{}
	for _, p := range pkg.Parts {
		names = append(names, p.Name+" "+p.Admission)
	}
	if strings.Join(names, ", ") != "role_anaesthetic admitted, role_fluid refused, role_patient admitted" || strings.Join(pkg.Drafts(), ",") != "role_fluid" {
		t.Fatalf("parts: %v", names)
	}
	if pkg.Parts[0].Version < 1 {
		t.Fatal("a part has no version")
	}
	if pkg.Decisions[0].By != "dr@example.test" || pkg.Decisions[0].Date != "2026-10-14" || pkg.Decisions[1].Unit != nil {
		t.Fatalf("decisions: %+v", pkg.Decisions)
	}
	var held map[string]any
	if err := json.Unmarshal(data, &held); err != nil {
		t.Fatal(err)
	}
	for _, absent := range []string{"status", "hash", "parts_used", "draft_parts_used", "specification"} {
		if _, ok := held[absent]; ok {
			t.Errorf("the package carries %s, which the format does not have", absent)
		}
	}
	if problems, err := Validate(data, rolemodel.Schema()); err != nil || len(problems) > 0 {
		t.Fatalf("the saved bytes do not keep the schema: %v %v", err, problems)
	}
	if bytes.Contains(data, []byte("\\u003c")) || !bytes.Contains(data, []byte("< 28")) {
		t.Fatal("the SQL is escaped for HTML")
	}
}

func TestTheCommittedFixtureIsWhatTheServiceWrites(t *testing.T) {
	const path = "../../../fixtures/packages/ask-fake-backend.question.json"
	_, raw := Split(reply)
	_, data, err := Parse(raw, contract(t), produced, "2026-10-14")
	if err != nil {
		t.Fatal(err)
	}
	if os.Getenv("ASK_WRITE_FIXTURE") == "1" {
		if err := os.WriteFile(path, data, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	held, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("the fixture is not beside the service: %v", err)
	}
	if !bytes.Equal(held, data) {
		t.Fatal("the fixture has fallen behind what the service writes; run the test with ASK_WRITE_FIXTURE=1")
	}
}

func TestABlockThatBreaksTheShapeIsRefusedWithThePath(t *testing.T) {
	for _, held := range []struct{ change, want string }{
		{`"title": "Red cells given to neonates under anaesthesia"`, `"title": ""`},
		{`"about": "who is a neonate", `, ``},
		{`"outcome": "The volume`, `"outcomes": "The volume`},
		{`"proposals": []`, `"proposals": [], "examinable": {"oracle": "none", "because": "words", "weight": 3}`},
	} {
		raw := strings.Replace(Block, held.change, held.want, 1)
		if raw == Block {
			t.Fatalf("the change %q did not apply", held.change)
		}
		_, _, err := Parse(raw, contract(t), produced, "2026-10-14")
		if err == nil || !strings.Contains(err.Error(), "does not have the package's shape") {
			t.Errorf("%q was not refused by the schema: %v", held.want, err)
		}
	}
	// The service reads the model's block into the package's own shape, so a misnamed field of the statement is read
	// as a missing one and the schema names the path of what is missing.
	_, _, err := Parse(strings.Replace(Block, `"outcome"`, `"outcomes"`, 1), contract(t), produced, "2026-10-14")
	if !strings.Contains(err.Error(), "statement.outcome: is empty") {
		t.Fatalf("the path is not named: %v", err)
	}
	_, _, err = Parse(strings.Replace(Block, `"proposals": []`, `"proposals": [], "examinable": {"oracle": "none", "because": "words", "weight": 3}`, 1), contract(t), produced, "2026-10-14")
	if !strings.Contains(err.Error(), "examinable.weight: is not a field it may have") {
		t.Fatalf("the path is not named: %v", err)
	}
}

func TestAnInventedRoleAndAForbiddenStatementAreRefusedButNotInsideACommentOrAString(t *testing.T) {
	bad := strings.Replace(Block, "role_fluid f", "role_blood f", 1)
	if _, _, err := Parse(bad, contract(t), produced, "2026-10-14"); err == nil || !strings.Contains(err.Error(), "role_blood") {
		t.Fatalf("an invented role was not refused: %v", err)
	}
	bad = strings.Replace(Block, "SELECT COUNT(*)", "DELETE FROM x; SELECT COUNT(*)", 1)
	if _, _, err := Parse(bad, contract(t), produced, "2026-10-14"); err == nil || !strings.Contains(err.Error(), "DELETE") {
		t.Fatalf("a forbidden statement was not refused: %v", err)
	}
	fine := strings.Replace(Block, "WITH neonatal AS", "/* CREATE nothing; see role_nowhere */ -- drop? no\\nWITH neonatal AS", 1)
	fine = strings.Replace(fine, "f.kind = 'red_cells'", "f.kind = 'red_cells' AND f.kind <> 'update; drop role_x'", 1)
	pkg, _, err := Parse(fine, contract(t), produced, "2026-10-14")
	if err != nil {
		t.Fatalf("a word inside a comment or a string was refused: %v", err)
	}
	if len(pkg.Parts) != 3 {
		t.Fatalf("a name inside a comment was read as a part: %+v", pkg.Parts)
	}
	if got := stripCommentsAndStrings("a 'it''s' b -- c\n/* d */ e"); got != "a '' b \n  e" {
		t.Fatalf("stripped: %q", got)
	}
}

func TestAProposalMakesTheQuestionWaitForTheContract(t *testing.T) {
	raw := `{"title": "Blocks with a catheter", "statement": {"cohort": "Blocks.", "outcome": "The depth.", "result": "Rows."}, "decisions": [], "question_sql": "", "proposals": [{"kind": "column", "part": "role_technique", "because": "the question needs the block's depth", "shape": {"name": "depth_cm", "type": "number"}}]}`
	pkg, _, err := Parse(raw, contract(t), produced, "2026-10-14")
	if err != nil {
		t.Fatal(err)
	}
	if !pkg.Waits() || len(pkg.Proposals) != 1 || len(pkg.Parts) != 0 {
		t.Fatalf("package: %+v", pkg)
	}
	raw = strings.Replace(raw, `"part": "role_technique"`, `"part": "role_nowhere"`, 1)
	if _, _, err := Parse(raw, contract(t), produced, "2026-10-14"); err == nil || !strings.Contains(err.Error(), "role_nowhere") {
		t.Fatalf("a proposal to join a part the contract lacks was accepted: %v", err)
	}
}

func TestACapabilityMustBeInTheCatalogue(t *testing.T) {
	raw := strings.Replace(Block, `"proposals": []`, `"proposals": [], "capabilities": [{"name": "transfusion", "version": 1, "parameters": {}}]`, 1)
	if _, _, err := Parse(raw, contract(t), produced, "2026-10-14"); err != nil {
		t.Fatal(err)
	}
	raw = strings.Replace(raw, `"name": "transfusion"`, `"name": "transfusions"`, 1)
	if _, _, err := Parse(raw, contract(t), produced, "2026-10-14"); err == nil || !strings.Contains(err.Error(), "transfusions") {
		t.Fatalf("an unknown capability was accepted: %v", err)
	}
}
