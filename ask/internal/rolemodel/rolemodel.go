// Package rolemodel carries the public role contract and the question package's schema that the service is built
// with, and renders the contract as the text the model reads. Both files are copied in from the repository when the
// service is built, by go generate or by the Dockerfile, so that the service and the page always speak the same
// version; neither copy is committed here, and a test refuses a copy that has fallen behind.
package rolemodel

//go:generate cp ../../../core/schemalyser/rolemodel/contract.json contract.json
//go:generate cp ../../../core/schemalyser/rolemodel/question.schema.json question.schema.json

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

//go:embed contract.json
var contractJSON []byte

//go:embed question.schema.json
var schemaJSON []byte

// What a gate may do with a part for a question, in the contract's words.
const (
	Admitted = "admitted"
	Ceiling  = "admitted with a ceiling"
	Refused  = "refused"
)

// Column is one column of a part.
type Column struct {
	Name       string `json:"name"`
	Title      string `json:"title"`
	Type       string `json:"type"`
	Vocabulary string `json:"vocabulary"`
	Mapping    string `json:"mapping"`
	Meaning    string `json:"meaning"`
	Optional   bool   `json:"optional"`
}

// View is one part of the record, or one mapping view.
type View struct {
	Name        string   `json:"name"`
	Title       string   `json:"title"`
	Status      string   `json:"status"`
	Version     int      `json:"version"`
	Description string   `json:"description"`
	OneRowPer   string   `json:"one_row_per"`
	Key         []string `json:"key"`
	Links       []struct {
		Column string `json:"column"`
		To     string `json:"to"`
	} `json:"links"`
	Columns []Column `json:"columns"`
}

// Kind is one kind of a vocabulary.
type Kind struct {
	Kind    string `json:"kind"`
	Meaning string `json:"meaning"`
	Unit    string `json:"unit"`
}

// Parameter is one parameter of a capability.
type Parameter struct {
	Name    string `json:"name"`
	Type    string `json:"type"`
	Meaning string `json:"meaning"`
}

// Capability is one entry of the catalogue.
type Capability struct {
	Name       string      `json:"name"`
	Version    int         `json:"version"`
	Meaning    string      `json:"meaning"`
	Parameters []Parameter `json:"parameters"`
}

// Contract is the public contract as the service reads it.
type Contract struct {
	Version      json.Number       `json:"version"`
	Views        []View            `json:"views"`
	MappingViews []View            `json:"mapping_views"`
	Capabilities []Capability      `json:"capabilities"`
	Kinds        []Kind            `json:"kinds"`
	Vocabularies map[string][]Kind `json:"vocabularies"`
	Types        map[string]string `json:"types"`
	Rules        []string          `json:"rules"`
}

var loaded *Contract

// Load parses the embedded contract once.
func Load() (*Contract, error) {
	if loaded != nil {
		return loaded, nil
	}
	var c Contract
	if err := json.Unmarshal(contractJSON, &c); err != nil {
		return nil, fmt.Errorf("the embedded contract is not readable: %w", err)
	}
	loaded = &c
	return loaded, nil
}

// Raw gives the embedded contract's bytes, for the test that keeps the copy in step with the repository.
func Raw() []byte { return contractJSON }

// Schema gives the embedded question schema's bytes.
func Schema() []byte { return schemaJSON }

// View finds a part by name.
func (c *Contract) View(name string) *View {
	for i := range c.Views {
		if c.Views[i].Name == name {
			return &c.Views[i]
		}
	}
	return nil
}

// MappingView finds a mapping view by name.
func (c *Contract) MappingView(name string) *View {
	for i := range c.MappingViews {
		if c.MappingViews[i].Name == name {
			return &c.MappingViews[i]
		}
	}
	return nil
}

// Part finds a part or a mapping view by name: anything a question may read.
func (c *Contract) Part(name string) *View {
	if v := c.View(name); v != nil {
		return v
	}
	return c.MappingView(name)
}

// Admission says what the contract lets a question do with a part, as the core's admission function does: a contract
// part and a mapping view are admitted, a rehearsed part is admitted with a ceiling, and a draft is refused.
func (c *Contract) Admission(name string) string {
	if c.MappingView(name) != nil {
		return Admitted
	}
	v := c.View(name)
	switch {
	case v == nil:
		return Refused
	case v.Status == "contract":
		return Admitted
	case v.Status == "rehearsed":
		return Ceiling
	}
	return Refused
}

// Capability finds an entry of the catalogue by name.
func (c *Contract) Capability(name string) *Capability {
	for i := range c.Capabilities {
		if c.Capabilities[i].Name == name {
			return &c.Capabilities[i]
		}
	}
	return nil
}

// Render writes the contract as the compact text the model reads: every part with its status and columns, the
// vocabularies, and the rules of the record. Nothing of any hospital is in it.
func (c *Contract) Render() string {
	var b strings.Builder
	fmt.Fprintf(&b, "Role contract, version %s.\n\n", c.Version)
	b.WriteString("Rules of the record:\n")
	for _, r := range c.Rules {
		fmt.Fprintf(&b, "- %s\n", r)
	}
	b.WriteString("\nTypes: ")
	names := make([]string, 0, len(c.Types))
	for n := range c.Types {
		names = append(names, n)
	}
	sort.Strings(names)
	for i, n := range names {
		if i > 0 {
			b.WriteString("; ")
		}
		fmt.Fprintf(&b, "%s: %s", n, c.Types[n])
	}
	b.WriteString("\n\nParts. Each is a view named role_x, one row per what it says. A part whose status is draft may be used; the service marks it in the package so that the hospital knows it is not yet in the contract.\n")
	for _, v := range c.Views {
		fmt.Fprintf(&b, "\n%s (%s; status %s): one row per %s. %s\n", v.Name, v.Title, v.Status, v.OneRowPer, v.Description)
		fmt.Fprintf(&b, "  key: %s", strings.Join(v.Key, ", "))
		if len(v.Links) > 0 {
			parts := make([]string, 0, len(v.Links))
			for _, l := range v.Links {
				parts = append(parts, fmt.Sprintf("%s -> %s", l.Column, l.To))
			}
			fmt.Fprintf(&b, "; links: %s", strings.Join(parts, ", "))
		}
		b.WriteString("\n")
		for _, col := range v.Columns {
			t := col.Type
			if col.Vocabulary != "" {
				t += " of " + col.Vocabulary
			}
			if col.Mapping != "" {
				t += " of " + col.Mapping
			}
			opt := ""
			if col.Optional {
				opt = ", optional"
			}
			fmt.Fprintf(&b, "  - %s (%s%s): %s\n", col.Name, t, opt, col.Meaning)
		}
	}
	b.WriteString("\nKinds of reading (role_reading.kind), with the unit each is given in:\n")
	for _, k := range c.Kinds {
		fmt.Fprintf(&b, "- %s (%s): %s\n", k.Kind, k.Unit, k.Meaning)
	}
	b.WriteString("\nVocabularies, as the exact words a kind column holds:\n")
	vnames := make([]string, 0, len(c.Vocabularies))
	for n := range c.Vocabularies {
		vnames = append(vnames, n)
	}
	sort.Strings(vnames)
	for _, n := range vnames {
		kinds := make([]string, 0, len(c.Vocabularies[n]))
		for _, k := range c.Vocabularies[n] {
			kinds = append(kinds, k.Kind)
		}
		fmt.Fprintf(&b, "- %s: %s\n", n, strings.Join(kinds, ", "))
	}
	return b.String()
}
