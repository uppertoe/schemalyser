// Package rolemodel carries the public role contract that the service is built with, and renders it as the text the
// model reads. The contract is copied in from core/schemalyser/rolemodel/contract.json when the service is built, so
// that the service and the page always speak the same version; a test refuses a copy that has fallen behind.
package rolemodel

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

//go:embed contract.json
var contractJSON []byte

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

// View is one part of the record.
type View struct {
	Name        string   `json:"name"`
	Title       string   `json:"title"`
	Status      string   `json:"status"`
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

// Contract is the public contract as the service reads it.
type Contract struct {
	Version      json.Number       `json:"version"`
	Views        []View            `json:"views"`
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

// View finds a part by name.
func (c *Contract) View(name string) *View {
	for i := range c.Views {
		if c.Views[i].Name == name {
			return &c.Views[i]
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
	b.WriteString("\n\nParts. Each is a view named role_x, one row per what it says. A part whose status is draft may be used, and the package must list it so that the hospital knows it is not yet in the contract.\n")
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
