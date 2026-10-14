package rolemodel

import (
	"bytes"
	"os"
	"regexp"
	"strings"
	"testing"
)

var tableLike = regexp.MustCompile(`\b[A-Z][A-Z0-9]+_[A-Z0-9_]+\b`)

func TestTheEmbeddedContractIsTheRepositorysOwn(t *testing.T) {
	own, err := os.ReadFile("../../../core/schemalyser/rolemodel/contract.json")
	if err != nil {
		t.Skip("the repository's contract is not beside the service here")
	}
	if !bytes.Equal(own, Raw()) {
		t.Fatal("the embedded contract has fallen behind core/schemalyser/rolemodel/contract.json; copy it in again")
	}
}

func TestTheContractRendersEveryPartAndVocabulary(t *testing.T) {
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
}
