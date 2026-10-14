// Package question holds the question package: the clinician's question in the terms of the roles, as the model
// writes it at the end of the conversation and the service checks, saves and publishes. Its format is
// schemalyser-question/1, whose shape is the schema beside the contract, which the core's reader validates against as
// well. A package holds no SQL against any hospital's tables, only against the roles; it names the contract version
// it was written for and each part it reads at that part's version, so that the page that opens it knows what it rests
// on. Its status is not a field: what a package may claim is decided by the evidence recorded about it.
package question

import (
	"bytes"
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
	"strings"

	"github.com/uppertoe/schemalyser/ask/internal/rolemodel"
)

// Format is the package format's name.
const Format = "schemalyser-question/1"

// Statement is the clinician's words for the cohort, the outcome and the result.
type Statement struct {
	Cohort  string `json:"cohort"`
	Outcome string `json:"outcome"`
	Result  string `json:"result"`
}

// Decision is one choice made in the conversation, typed: what it is about, the choice, a value where one exists,
// the sentence, who made it and when.
type Decision struct {
	About    string          `json:"about"`
	Choice   string          `json:"choice"`
	Value    json.RawMessage `json:"value,omitempty"`
	Unit     *string         `json:"unit,omitempty"`
	Decision string          `json:"decision"`
	By       string          `json:"by"`
	Date     string          `json:"date"`
}

// Capability is one capability of the catalogue that the question composes.
type Capability struct {
	Name       string                     `json:"name"`
	Version    int                        `json:"version"`
	Parameters map[string]json.RawMessage `json:"parameters"`
}

// Part is one part the SQL reads, at its version in the contract and with the contract's admission for a question.
type Part struct {
	Name      string `json:"name"`
	Version   int    `json:"version"`
	Admission string `json:"admission"`
}

// Proposal is a part or a column the model found the contract lacks, in the contract's own shape, for the owner to
// run through the contract checker and the try command. It never appears in the SQL.
type Proposal struct {
	Kind    string          `json:"kind"`    // column or part
	Part    string          `json:"part"`    // the part a column would join, or the new part's name
	Because string          `json:"because"` // what the question needs it for
	Shape   json.RawMessage `json:"shape"`   // the candidate column or part, as contract.json writes one
}

// Produced says which model wrote the package, when, and for whom.
type Produced struct {
	Model string `json:"model"`
	At    string `json:"at"`
	By    string `json:"by,omitempty"`
}

// Approval is the reference of the audit, quality improvement, service evaluation or research a question serves,
// and its declared use, which is never the care of an individual patient.
type Approval struct {
	Reference string `json:"reference"`
	Use       string `json:"use"`
	Says      string `json:"says,omitempty"`
}

// Package is the question as the service saves and publishes it.
type Package struct {
	Format          string          `json:"format"`
	Title           string          `json:"title"`
	ContractVersion string          `json:"contract_version"`
	Statement       Statement       `json:"statement"`
	Decisions       []Decision      `json:"decisions"`
	Examinable      json.RawMessage `json:"examinable,omitempty"`
	Capabilities    []Capability    `json:"capabilities"`
	QuestionSQL     string          `json:"question_sql"`
	Parts           []Part          `json:"parts"`
	Proposals       []Proposal      `json:"proposals"`
	Approval        *Approval       `json:"approval,omitempty"`
	Note            string          `json:"note,omitempty"`
	Produced        Produced        `json:"produced"`
}

// Waits says whether the question waits for the contract: it holds a proposal.
func (p *Package) Waits() bool { return len(p.Proposals) > 0 }

// Drafts gives the parts the package reads that the contract does not admit for a question.
func (p *Package) Drafts() []string {
	var found []string
	for _, part := range p.Parts {
		if part.Admission == rolemodel.Refused {
			found = append(found, part.Name)
		}
	}
	return found
}

// The block the model writes at the end of its reply once the question is settled.
var block = regexp.MustCompile("(?s)```schemalyser-question\\s*\\n(.*?)\\n```")

// Split takes the package block out of a reply, giving the prose for the clinician and the block's text, which is
// empty where the reply holds none.
func Split(reply string) (prose, raw string) {
	m := block.FindStringSubmatchIndex(reply)
	if m == nil {
		return strings.TrimSpace(reply), ""
	}
	return strings.TrimSpace(reply[:m[0]] + reply[m[1]:]), reply[m[2]:m[3]]
}

// Bytes writes a package as the bytes that are saved and served: indented JSON with nothing escaped for HTML, so
// that the SQL reads as written.
func Bytes(p *Package) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", " ")
	if err := enc.Encode(p); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// Parse reads the block the model wrote, fills what the service fills (the format, the contract version, the parts
// the SQL reads with their versions and admissions, who made each decision and when, and who produced the package),
// checks the whole against the schema and the rules the service can apply without a parser, and gives the package
// with its bytes. A block that breaks a rule gives an error that names the rule, which the service hands back to the
// model.
func Parse(raw string, contract *rolemodel.Contract, produced Produced, date string) (*Package, []byte, error) {
	var in struct {
		Title        string          `json:"title"`
		Statement    Statement       `json:"statement"`
		Decisions    []Decision      `json:"decisions"`
		Examinable   json.RawMessage `json:"examinable"`
		Capabilities []Capability    `json:"capabilities"`
		QuestionSQL  string          `json:"question_sql"`
		Note         string          `json:"note"`
		Proposals    []Proposal      `json:"proposals"`
		Approval     *Approval       `json:"approval"`
	}
	if err := json.Unmarshal([]byte(raw), &in); err != nil {
		return nil, nil, fmt.Errorf("the question block is not valid JSON: %v", err)
	}
	sqlText := strings.TrimSpace(in.QuestionSQL)
	if sqlText == "" && len(in.Proposals) == 0 {
		return nil, nil, fmt.Errorf("the question block has neither question_sql nor a proposal")
	}
	if err := checkSQL(sqlText, contract); err != nil {
		return nil, nil, err
	}
	pkg := &Package{Format: Format, Title: strings.TrimSpace(in.Title), ContractVersion: contract.Version.String(),
		Statement: in.Statement, Decisions: in.Decisions, Examinable: in.Examinable, Capabilities: in.Capabilities,
		QuestionSQL: sqlText, Parts: partsIn(sqlText, contract, in.Capabilities), Proposals: in.Proposals, Approval: in.Approval,
		Note: strings.TrimSpace(in.Note), Produced: produced}
	if pkg.Decisions == nil {
		pkg.Decisions = []Decision{}
	}
	for i := range pkg.Decisions {
		if pkg.Decisions[i].By == "" {
			pkg.Decisions[i].By = produced.By
		}
		if pkg.Decisions[i].Date == "" {
			pkg.Decisions[i].Date = date
		}
	}
	if pkg.Capabilities == nil {
		pkg.Capabilities = []Capability{}
	}
	for i := range pkg.Capabilities {
		if pkg.Capabilities[i].Parameters == nil {
			pkg.Capabilities[i].Parameters = map[string]json.RawMessage{}
		}
	}
	if pkg.Proposals == nil {
		pkg.Proposals = []Proposal{}
	}
	if bytes.Equal(bytes.TrimSpace(pkg.Examinable), []byte("null")) {
		pkg.Examinable = nil
	}
	data, err := Bytes(pkg)
	if err != nil {
		return nil, nil, err
	}
	problems, err := Validate(data, rolemodel.Schema())
	if err != nil {
		return nil, nil, err
	}
	if len(problems) > 0 {
		return nil, nil, fmt.Errorf("the question block does not have the package's shape: %s", strings.Join(problems, "; "))
	}
	for i, p := range pkg.Proposals {
		if p.Kind == "column" && contract.View(p.Part) == nil {
			return nil, nil, fmt.Errorf("proposal %d would join %s, which the contract does not have", i+1, p.Part)
		}
		if p.Kind == "part" && contract.Part(p.Part) != nil {
			return nil, nil, fmt.Errorf("proposal %d proposes %s, which the contract already has", i+1, p.Part)
		}
	}
	for _, c := range pkg.Capabilities {
		entry := contract.Capability(c.Name)
		if entry == nil {
			return nil, nil, fmt.Errorf("capabilities names %s, which the catalogue does not have", c.Name)
		}
		if !entry.Composable() {
			return nil, nil, fmt.Errorf("capabilities names %s, which the catalogue declares without its SQL yet, so a question computes it over the parts instead", c.Name)
		}
	}
	return pkg, data, nil
}

var partName = regexp.MustCompile(`\b(role|map)_[a-z][a-z0-9_]*\b`)

// partsIn gives the parts and mapping views the SQL names, with the parts each composed capability reads, each at
// its version with its admission, in name order.
func partsIn(sqlText string, contract *rolemodel.Contract, capabilities []Capability) []Part {
	names := partName.FindAllString(stripCommentsAndStrings(sqlText), -1)
	for _, c := range capabilities {
		if entry := contract.Capability(c.Name); entry != nil {
			names = append(names, entry.Requires.Parts...)
		}
	}
	found := map[string]Part{}
	for _, name := range names {
		if v := contract.Part(name); v != nil {
			found[name] = Part{Name: name, Version: v.Version, Admission: contract.Admission(name)}
		}
	}
	ordered := make([]string, 0, len(found))
	for name := range found {
		ordered = append(ordered, name)
	}
	sort.Strings(ordered)
	parts := make([]Part, 0, len(ordered))
	for _, name := range ordered {
		parts = append(parts, found[name])
	}
	return parts
}

var forbidden = regexp.MustCompile(`(?i)\b(exec|execute|openquery|openrowset|insert|update|delete|drop|alter|create|merge|truncate|grant|revoke|xp_cmdshell|sp_executesql)\b`)

// checkSQL applies the rules the service can apply without a parser, over the SQL with its comments and string
// literals stripped: it reads the roles and nothing else, it is a SELECT, it is one statement, and it does nothing a
// question never does. The policy of the core applies its full rules later.
func checkSQL(sqlText string, contract *rolemodel.Contract) error {
	if sqlText == "" {
		return nil
	}
	body := stripCommentsAndStrings(sqlText)
	if m := forbidden.FindString(body); m != "" {
		return fmt.Errorf("question_sql uses %s, which a question never does", strings.ToUpper(m))
	}
	for _, name := range partName.FindAllString(body, -1) {
		if contract.Part(name) == nil {
			return fmt.Errorf("question_sql reads %s, which the contract does not have; propose it instead of inventing it", name)
		}
	}
	upper := strings.ToUpper(strings.TrimSpace(body))
	if !strings.HasPrefix(upper, "SELECT") && !strings.HasPrefix(upper, "WITH") {
		return fmt.Errorf("question_sql must be one SELECT, which may begin with WITH")
	}
	if strings.Count(strings.TrimRight(body, "; \n\t"), ";") > 0 {
		return fmt.Errorf("question_sql must be one statement")
	}
	return nil
}

// stripCommentsAndStrings takes line comments, block comments and string literals out of SQL, leaving an empty
// literal where each string was, so that a word inside a comment or a literal is not read as a statement.
func stripCommentsAndStrings(s string) string {
	var out strings.Builder
	for i := 0; i < len(s); {
		switch {
		case strings.HasPrefix(s[i:], "--"):
			end := strings.Index(s[i:], "\n")
			if end < 0 {
				i = len(s)
			} else {
				i += end
			}
		case strings.HasPrefix(s[i:], "/*"):
			end := strings.Index(s[i+2:], "*/")
			if end < 0 {
				i = len(s)
			} else {
				i += end + 4
			}
			out.WriteByte(' ')
		case s[i] == '\'':
			j := i + 1
			for j < len(s) {
				if s[j] == '\'' {
					if j+1 < len(s) && s[j+1] == '\'' {
						j += 2
						continue
					}
					break
				}
				j++
			}
			out.WriteString("''")
			if j < len(s) {
				i = j + 1
			} else {
				i = len(s)
			}
		default:
			out.WriteByte(s[i])
			i++
		}
	}
	return out.String()
}
