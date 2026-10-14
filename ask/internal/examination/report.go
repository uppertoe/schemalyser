// Package examination reads the examiner's signed report of a question package: it verifies the signature with the
// examiner's public key over the report's canonical form, which is the form the examiner signed in Python, reads the
// level the report grants, and writes the bounded feedback that may return to the model.
package examination

import (
	"bytes"
	"crypto/ed25519"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

// Levels, from the lowest. A package is published only at LogicExamined or above.
const (
	NotWellFormed = "not well formed"
	WellFormed    = "well formed"
	LogicExamined = "logic examined"
	HeldOut       = "examined against held-out cases"
)

var order = map[string]int{NotWellFormed: 0, WellFormed: 1, LogicExamined: 2, HeldOut: 3}

// Publishable says whether a level lets a package be published.
func Publishable(level string) bool { return order[level] >= order[LogicExamined] }

// Report is what the service keeps of a verified report.
type Report struct {
	PackageSHA256 string
	Level         string
	Says          string
	ExaminedAt    string
	Feedback      string // what may return to the model: bounded, with no case detail
}

// Verify reads a report's bytes, checks that its signature is the examiner's over the canonical form, and gives the
// report. An unsigned report, one signed with another key, or one whose bytes were changed is refused.
func Verify(data []byte, publicKeyHex string) (*Report, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var held map[string]any
	if err := dec.Decode(&held); err != nil {
		return nil, fmt.Errorf("the report is not a JSON object")
	}
	signature, _ := held["signature"].(map[string]any)
	keyHex, _ := signature["public_key"].(string)
	sigHex, _ := signature["signature"].(string)
	if keyHex == "" || sigHex == "" {
		return nil, fmt.Errorf("the report carries no signature, so it grants nothing")
	}
	if !strings.EqualFold(keyHex, publicKeyHex) {
		return nil, fmt.Errorf("the report is signed with a key that is not the examiner's, so it grants nothing")
	}
	public, err := hex.DecodeString(publicKeyHex)
	sig, err2 := hex.DecodeString(sigHex)
	if err != nil || err2 != nil || len(public) != ed25519.PublicKeySize || len(sig) != ed25519.SignatureSize {
		return nil, fmt.Errorf("the report's signature is not one the examiner writes")
	}
	delete(held, "signature")
	if !ed25519.Verify(ed25519.PublicKey(public), Canonical(held), sig) {
		return nil, fmt.Errorf("the report's signature does not match its content, so it grants nothing")
	}
	pkg, _ := held["package"].(map[string]any)
	sha, _ := pkg["sha256"].(string)
	status, _ := held["status"].(map[string]any)
	level, _ := status["level"].(string)
	says, _ := status["says"].(string)
	at, _ := held["examined_at"].(string)
	if sha == "" || order[level] == 0 && level != NotWellFormed {
		return nil, fmt.Errorf("the report names no package or no level the service knows")
	}
	return &Report{PackageSHA256: sha, Level: level, Says: says, ExaminedAt: at, Feedback: feedback(held)}, nil
}

// Canonical writes a value as the examiner's Python writes the bytes it signs: keys sorted, no spaces, numbers as
// read, and nothing escaped for HTML.
func Canonical(value any) []byte {
	var buf bytes.Buffer
	canonical(&buf, value)
	return buf.Bytes()
}

func canonical(buf *bytes.Buffer, value any) {
	switch v := value.(type) {
	case map[string]any:
		keys := make([]string, 0, len(v))
		for k := range v {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		buf.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				buf.WriteByte(',')
			}
			writeString(buf, k)
			buf.WriteByte(':')
			canonical(buf, v[k])
		}
		buf.WriteByte('}')
	case []any:
		buf.WriteByte('[')
		for i, item := range v {
			if i > 0 {
				buf.WriteByte(',')
			}
			canonical(buf, item)
		}
		buf.WriteByte(']')
	case string:
		writeString(buf, v)
	case json.Number:
		buf.WriteString(v.String())
	case bool:
		if v {
			buf.WriteString("true")
		} else {
			buf.WriteString("false")
		}
	case nil:
		buf.WriteString("null")
	default:
		b, _ := json.Marshal(v)
		buf.Write(b)
	}
}

// writeString escapes as Python's json.dumps does with ensure_ascii off: the quote, the backslash and the control
// characters, with \n, \r, \t, \b and \f by name and the rest as \u00XX.
func writeString(buf *bytes.Buffer, s string) {
	buf.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			buf.WriteString(`\"`)
		case '\\':
			buf.WriteString(`\\`)
		case '\n':
			buf.WriteString(`\n`)
		case '\r':
			buf.WriteString(`\r`)
		case '\t':
			buf.WriteString(`\t`)
		case '\b':
			buf.WriteString(`\b`)
		case '\f':
			buf.WriteString(`\f`)
		default:
			if r < 0x20 {
				fmt.Fprintf(buf, `\u%04x`, r)
			} else {
				buf.WriteRune(r)
			}
		}
	}
	buf.WriteByte('"')
}

// feedback writes what may return to the model: every rule and fragment of the static stage, whether the question
// ran on the shadow and the engine's words, which metamorphic check failed, and of the planted cases and the oracle
// only how many disagreed, never which case, which row or which value.
func feedback(held map[string]any) string {
	var lines []string
	if rules, ok := held["rules"].([]any); ok {
		for _, r := range rules {
			rule, _ := r.(map[string]any)
			if passed, _ := rule["passed"].(bool); passed {
				continue
			}
			text, _ := rule["rule"].(string)
			var fragments []string
			if held, ok := rule["fragments"].([]any); ok {
				for _, f := range held {
					if s, ok := f.(string); ok {
						fragments = append(fragments, s)
					}
				}
			}
			lines = append(lines, "The package breaks the rule that "+strings.TrimSuffix(text, ".")+": "+strings.Join(fragments, "; ")+".")
		}
	}
	if form, ok := held["form"].([]any); ok && len(form) > 0 {
		lines = append(lines, "The question lacks the two-part form a hospital compiles.")
	}
	for _, stage := range []string{"shadow", "metamorphic"} {
		if report, ok := held[stage].(map[string]any); ok {
			if outcome, _ := report["outcome"].(string); outcome == "failed" {
				says, _ := report["says"].(string)
				lines = append(lines, says)
			}
		}
	}
	if report, ok := held["oracle"].(map[string]any); ok {
		if outcome, _ := report["outcome"].(string); outcome == "failed" {
			lines = append(lines, "The package's answer disagrees with the examiner's own on invented rows.")
		}
	}
	if cases, ok := held["planted"].([]any); ok && len(cases) > 0 {
		failed := 0
		for _, c := range cases {
			held, _ := c.(map[string]any)
			if outcome, _ := held["outcome"].(string); outcome != "passed" {
				failed++
			}
		}
		if failed > 0 {
			lines = append(lines, fmt.Sprintf("%d of %d planted cases disagree with the package's answer.", failed, len(cases)))
		}
	}
	return strings.Join(lines, " ")
}
