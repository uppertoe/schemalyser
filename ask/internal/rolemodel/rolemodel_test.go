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
	for _, want := range []string{"role_patient", "role_technique (", "status draft", "- technique:", "map_cuff"} {
		if !strings.Contains(text, want) {
			t.Errorf("the rendered contract lacks %q", want)
		}
	}
	// A hospital's table is an upper-case name with an underscore; the contract's names are lower-case.
	if tableLike.MatchString(text) {
		t.Fatalf("the rendered contract names what looks like a hospital's table: %s", tableLike.FindString(text))
	}
	if c.View("role_patient").Version < 1 || c.MappingView("map_drug_concept") == nil || c.MappingView("map_drug_concept").Version < 1 {
		t.Fatal("a part or a mapping view has no version")
	}
	for name, want := range map[string]string{"role_patient": Admitted, "role_fluid": Refused, "map_drug_concept": Admitted, "role_nothing": Refused} {
		if got := c.Admission(name); got != want {
			t.Errorf("%s: admission %q, wanted %q", name, got, want)
		}
	}
	if c.Capability("minutes_beyond_threshold") == nil || c.Capability("minutes_beyond_threshold").Version != 1 {
		t.Fatal("the catalogue is not read")
	}
}
