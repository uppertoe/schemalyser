package rolemodel

import (
	"bytes"
	"os"
	"regexp"
	"strings"
	"testing"
)

var tableLike = regexp.MustCompile(`\b[A-Z][A-Z0-9]+_[A-Z0-9_]+\b`)

func TestTheEmbeddedContractAndSchemaAreTheRepositorysOwn(t *testing.T) {
	for _, held := range []struct {
		name string
		got  []byte
	}{{"contract.json", Raw()}, {"question.schema.json", Schema()}} {
		own, err := os.ReadFile("../../../core/schemalyser/rolemodel/" + held.name)
		if err != nil {
			t.Fatalf("the repository's %s is not beside the service: the service is built from the repository's root, and the tests run there", held.name)
		}
		if !bytes.Equal(own, held.got) {
			t.Fatalf("the embedded %s has fallen behind core/schemalyser/rolemodel/%s; run go generate ./...", held.name, held.name)
		}
	}
}

func TestTheContractRendersEveryPartAndVocabularyAndGivesEachPartItsVersionAndAdmission(t *testing.T) {
	c, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	text := c.Render()
	for _, want := range []string{"role_patient", "role_technique (", "a draft, over which a question waits", "in the contract, version 1", "- technique:", "map_cuff",
		"Mapping views.", "The capability catalogue."} {
		if !strings.Contains(text, want) {
			t.Errorf("the rendered contract lacks %q", want)
		}
	}
	// Every mapping view and every capability of the embedded contract is named, with its parameters and purpose.
	for _, m := range c.MappingViews {
		if !strings.Contains(text, "- "+m.Name+" (") || !strings.Contains(text, "domain "+m.Domain) {
			t.Errorf("the rendered contract lacks the mapping view %s", m.Name)
		}
	}
	for _, cap := range c.Capabilities {
		if !strings.Contains(text, "- "+cap.Name+" (version ") || !strings.Contains(text, cap.Meaning) {
			t.Errorf("the rendered contract lacks the capability %s", cap.Name)
		}
		for _, p := range cap.Parameters {
			if !strings.Contains(text, p.Name+" ("+p.Type) {
				t.Errorf("the capability %s lacks its parameter %s", cap.Name, p.Name)
			}
		}
	}
	// A hospital's table is an upper-case name with an underscore; the contract's names are lower-case.
	if tableLike.MatchString(text) {
		t.Fatalf("the rendered contract names what looks like a hospital's table: %s", tableLike.FindString(text))
	}
	if c.View("role_patient").Version < 1 || c.MappingView("map_drug_concept") == nil || c.MappingView("map_drug_concept").Version < 1 {
		t.Fatal("a part or a mapping view has no version")
	}
	for name, want := range map[string]string{"role_patient": Admitted, "role_fluid": Ceiling, "role_lab": Refused, "map_drug_concept": Admitted, "role_nothing": Refused} {
		if got := c.Admission(name); got != want {
			t.Errorf("%s: admission %q, wanted %q", name, got, want)
		}
	}
	if c.Capability("minutes_beyond_threshold") == nil || c.Capability("minutes_beyond_threshold").Version != 1 {
		t.Fatal("the catalogue is not read")
	}
}
