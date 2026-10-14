// Package question holds the question package: the clinician's question in the terms of the roles, as the model
// writes it at the end of the conversation and the service checks and hands on. Its format is
// schemalyser-question/1. It holds no SQL against any hospital's tables, only against the roles, and it names the
// contract version it was written for, so that the page that opens it knows what it rests on.
package question

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
	"strings"

	"github.com/uppertoe/schemalyser/ask/internal/rolemodel"
)

// Format is the package format's name.
const Format = "schemalyser-question/1"

// Proposal is a part or a column the model found the contract lacks, in the contract's own shape, for the owner to
// run through the contract checker and the try command. It never appears in the SQL.
type Proposal struct {
	Kind    string          `json:"kind"`    // column or part
	Part    string          `json:"part"`    // the part a column would join, or the new part's name
	Because string          `json:"because"` // what the question needs it for
	Shape   json.RawMessage `json:"shape"`   // the candidate column or part, as contract.json writes one
}

// Package is the question as the service stores and publishes it.
type Package struct {
	Format          string          `json:"format"`
	Title           string          `json:"title"`
	ContractVersion string          `json:"contract_version"`
	Specification   json.RawMessage `json:"specification"`
	Decisions       []string        `json:"decisions"`
	QuestionSQL     string          `json:"question_sql"`
	Note            string          `json:"note"`
	PartsUsed       []string        `json:"parts_used"`
	DraftPartsUsed  []string        `json:"draft_parts_used"`
	Proposals       []Proposal      `json:"proposals"`
	Status          string          `json:"status"`
	Produced        Produced        `json:"produced"`
	Hash            string          `json:"hash"`
}

// Produced says which model wrote the package and when.
type Produced struct {
	Model string `json:"model"`
	At    string `json:"at"`
	By    string `json:"by"`
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

// Parse reads the block the model wrote, checks it against the contract, and gives the package with its status and
// hash. A block that breaks a rule gives an error that names the rule, which the service hands back to the model.
func Parse(raw string, contract *rolemodel.Contract, produced Produced) (*Package, error) {
	var in struct {
		Title         string          `json:"title"`
		Specification json.RawMessage `json:"specification"`
		Decisions     []string        `json:"decisions"`
		QuestionSQL   string          `json:"question_sql"`
		Note          string          `json:"note"`
		PartsUsed     []string        `json:"parts_used"`
		Proposals     []Proposal      `json:"proposals"`
	}
	if err := json.Unmarshal([]byte(raw), &in); err != nil {
		return nil, fmt.Errorf("the question block is not valid JSON: %v", err)
	}
	if strings.TrimSpace(in.Title) == "" {
		return nil, fmt.Errorf("the question block has no title")
	}
	if len(in.Specification) == 0 || string(in.Specification) == "null" {
		return nil, fmt.Errorf("the question block has no specification")
	}
	sqlText := strings.TrimSpace(in.QuestionSQL)
	if sqlText == "" && len(in.Proposals) == 0 {
		return nil, fmt.Errorf("the question block has neither question_sql nor a proposal")
	}
	used := partsIn(sqlText, contract)
	for _, p := range in.PartsUsed {
		if contract.View(p) == nil {
			return nil, fmt.Errorf("parts_used names %s, which the contract does not have", p)
		}
		if !contains(used, p) {
			used = append(used, p)
		}
	}
	sort.Strings(used)
	var drafts []string
	for _, p := range used {
		if contract.View(p).Status != "contract" {
			drafts = append(drafts, p)
		}
	}
	if err := checkSQL(sqlText, contract); err != nil {
		return nil, err
	}
	for i, p := range in.Proposals {
		if p.Kind != "column" && p.Kind != "part" {
			return nil, fmt.Errorf("proposal %d has a kind that is not column or part", i+1)
		}
		if p.Kind == "column" && contract.View(p.Part) == nil {
			return nil, fmt.Errorf("proposal %d would join %s, which the contract does not have", i+1, p.Part)
		}
		if len(p.Shape) == 0 || string(p.Shape) == "null" {
			return nil, fmt.Errorf("proposal %d gives no shape", i+1)
		}
	}
	pkg := &Package{Format: Format, Title: strings.TrimSpace(in.Title), ContractVersion: contract.Version.String(),
		Specification: in.Specification, Decisions: in.Decisions, QuestionSQL: sqlText, Note: strings.TrimSpace(in.Note),
		PartsUsed: used, DraftPartsUsed: drafts, Proposals: in.Proposals, Produced: produced}
	if pkg.Decisions == nil {
		pkg.Decisions = []string{}
	}
	if pkg.Proposals == nil {
		pkg.Proposals = []Proposal{}
	}
	if drafts == nil {
		pkg.DraftPartsUsed = []string{}
	}
	switch {
	case len(pkg.Proposals) > 0:
		pkg.Status = "waits for the contract"
	default:
		pkg.Status = "drafted"
	}
	pkg.Hash = hashOf(pkg)
	return pkg, nil
}

var roleName = regexp.MustCompile(`\brole_[a-z][a-z0-9_]*\b`)

func partsIn(sqlText string, contract *rolemodel.Contract) []string {
	var found []string
	for _, name := range roleName.FindAllString(sqlText, -1) {
		if contract.View(name) != nil && !contains(found, name) {
			found = append(found, name)
		}
	}
	return found
}

var forbidden = regexp.MustCompile(`(?i)\b(exec|execute|openquery|openrowset|insert|update|delete|drop|alter|create|merge|truncate|grant|revoke|xp_cmdshell|sp_executesql)\b`)

// checkSQL applies the rules the service can apply without a parser: the SQL reads the roles and nothing else, it is
// a SELECT, and it does nothing a question never does. The policy of the core applies its full rules later.
func checkSQL(sqlText string, contract *rolemodel.Contract) error {
	if sqlText == "" {
		return nil
	}
	if m := forbidden.FindString(sqlText); m != "" {
		return fmt.Errorf("question_sql uses %s, which a question never does", strings.ToUpper(m))
	}
	for _, name := range roleName.FindAllString(sqlText, -1) {
		if contract.View(name) == nil {
			return fmt.Errorf("question_sql reads %s, which the contract does not have; propose it instead of inventing it", name)
		}
	}
	body := stripComments(sqlText)
	upper := strings.ToUpper(strings.TrimSpace(body))
	if !strings.HasPrefix(upper, "SELECT") && !strings.HasPrefix(upper, "WITH") {
		return fmt.Errorf("question_sql must be one SELECT, which may begin with WITH")
	}
	if strings.Count(strings.TrimRight(body, "; \n\t"), ";") > 0 {
		return fmt.Errorf("question_sql must be one statement")
	}
	return nil
}

func stripComments(s string) string {
	var out []string
	for _, line := range strings.Split(s, "\n") {
		if i := strings.Index(line, "--"); i >= 0 {
			line = line[:i]
		}
		out = append(out, line)
	}
	return strings.Join(out, "\n")
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// hashOf hashes what the package says, and not when or by whom it was produced, so that the same question has the
// same hash wherever it is written.
func hashOf(p *Package) string {
	stable := struct {
		Format, Title, ContractVersion string
		Specification                  json.RawMessage
		Decisions                      []string
		QuestionSQL, Note              string
		PartsUsed                      []string
		Proposals                      []Proposal
	}{p.Format, p.Title, p.ContractVersion, p.Specification, p.Decisions, p.QuestionSQL, p.Note, p.PartsUsed, p.Proposals}
	b, _ := json.Marshal(stable)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}
