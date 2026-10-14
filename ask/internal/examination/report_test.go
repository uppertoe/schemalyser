package examination

import (
	"crypto/ed25519"
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/uppertoe/schemalyser/ask/internal/rolemodel"
)

// A report the examiner wrote and signed in Python, verified here with the repository's public key, which holds the
// two implementations of the signature and of the canonical form to one another.
func TestAReportSignedByTheExaminerInPythonVerifiesHere(t *testing.T) {
	data, err := os.ReadFile("../../../fixtures/packages/ask-fake-backend.examination.json")
	if err != nil {
		t.Fatalf("the signed report fixture is not beside the service: %v", err)
	}
	report, err := Verify(data, rolemodel.ExaminerPublicKey())
	if err != nil {
		t.Fatal(err)
	}
	if report.Level != LogicExamined || !Publishable(report.Level) || report.PackageSHA256 == "" || report.Feedback != "" {
		t.Fatalf("report: %+v", report)
	}
	if _, err := Verify(data, strings.Repeat("00", 32)); err == nil || !strings.Contains(err.Error(), "not the examiner's") {
		t.Fatalf("another key's verification did not refuse: %v", err)
	}
	changed := []byte(strings.Replace(string(data), "logic examined", "examined against held-out cases", 1))
	if _, err := Verify(changed, rolemodel.ExaminerPublicKey()); err == nil || !strings.Contains(err.Error(), "does not match its content") {
		t.Fatalf("a changed report was not refused: %v", err)
	}
}

// Sign writes a report as the examiner would, for the tests: the canonical form under a fresh key.
func Sign(t *testing.T, report map[string]any) ([]byte, string) {
	t.Helper()
	public, private, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	signature := ed25519.Sign(private, Canonical(report))
	report["signature"] = map[string]any{"public_key": hex.EncodeToString(public), "signature": hex.EncodeToString(signature)}
	data, _ := json.Marshal(report)
	return data, hex.EncodeToString(public)
}

func TestTheFeedbackToTheModelHoldsNoCaseDetail(t *testing.T) {
	report := map[string]any{
		"package":     map[string]any{"sha256": "abc", "title": "A question"},
		"status":      map[string]any{"level": WellFormed, "says": "The package is well formed; its answer disagrees with the oracle's."},
		"rules":       []any{map[string]any{"id": "dialect", "rule": "The question keeps the portable dialect.", "passed": true}},
		"shadow":      map[string]any{"outcome": "passed", "says": "ran"},
		"metamorphic": map[string]any{"outcome": "failed", "says": "The question fails the metamorphic check moved: a row of role_fluid moved to another anaesthetic gives a different answer."},
		"oracle":      map[string]any{"outcome": "failed", "runs": []any{map[string]any{"seed": json.Number("1"), "expected": json.Number("3")}}},
		"planted": []any{
			map[string]any{"name": "transfusion_neonates", "outcome": "failed", "says": "row 990002400 gives 12.5 and the question 11.0", "compared": map[string]any{"rows_a": json.Number("4")}},
			map[string]any{"name": "transfusion_edge", "outcome": "passed", "says": "fine"},
		},
	}
	data, public := Sign(t, report)
	verified, err := Verify(data, public)
	if err != nil {
		t.Fatal(err)
	}
	for _, leak := range []string{"990002400", "12.5", "11.0", "transfusion_neonates", "transfusion_edge", "rows_a"} {
		if strings.Contains(verified.Feedback, leak) {
			t.Fatalf("the feedback holds case detail %q: %s", leak, verified.Feedback)
		}
	}
	for _, want := range []string{"1 of 2 planted cases disagree", "disagrees with the examiner's own", "metamorphic check moved"} {
		if !strings.Contains(verified.Feedback, want) {
			t.Fatalf("the feedback lacks %q: %s", want, verified.Feedback)
		}
	}
	if _, err := Verify([]byte(`{"package": {"sha256": "abc"}, "status": {"level": "logic examined"}}`), public); err == nil || !strings.Contains(err.Error(), "no signature") {
		t.Fatalf("an unsigned report was accepted: %v", err)
	}
}
