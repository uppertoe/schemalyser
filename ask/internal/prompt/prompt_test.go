package prompt

import (
	"strings"
	"testing"

	"github.com/uppertoe/schemalyser/ask/internal/rolemodel"
)

func TestThePromptStatesTheTwoPartFormTheMedianTheStandingOfPartsAndNamesEveryMappingViewAndCapability(t *testing.T) {
	c, err := rolemodel.Load()
	if err != nil {
		t.Fatal(err)
	}
	text := Build(c)
	for _, want := range []string{
		"one step, usually the first common table expression, chooses its anaesthetics from role_patient and role_anaesthetic alone",
		"reached from that step by a key the step carries",
		"no row of a patient",
		"PERCENTILE_CONT(0.5) WITHIN GROUP",
		"A question over a draft part waits until the part joins the contract",
		"rehearsed part runs with a ceiling",
		"Mapping views.",
		"The capability catalogue.",
		"The service works out which parts the SQL reads",
		"read it in question_sql as a table named after it",
		"write examinable, the structured form of the decisions",
		"write them as approval, an object with reference and use",
		"{\"oracle\": \"none\"",
		"composable, giving anaesthetic_key, age_days, threshold, readings, minutes_beyond",
	} {
		if !strings.Contains(text, want) {
			t.Errorf("the prompt lacks %q", want)
		}
	}
	for _, m := range c.MappingViews {
		if !strings.Contains(text, m.Name) {
			t.Errorf("the prompt lacks the mapping view %s", m.Name)
		}
	}
	for _, cap := range c.Capabilities {
		if !strings.Contains(text, "- "+cap.Name+" (version ") {
			t.Errorf("the prompt lacks the capability %s", cap.Name)
		}
	}
	if strings.Contains(text, "parts_used") {
		t.Error("the prompt still asks for parts_used")
	}
}
