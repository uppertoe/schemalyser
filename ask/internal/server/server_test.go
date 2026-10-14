package server

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/uppertoe/schemalyser/ask/internal/config"
	"github.com/uppertoe/schemalyser/ask/internal/model"
	"github.com/uppertoe/schemalyser/ask/internal/store"
)

const settled = "The question is settled.\n\n```schemalyser-question\n{\"title\": \"Neonates with a mean pressure below 35\", \"specification\": {\"cohort\": {\"age_days_at_start\": {\"lt\": 28}}, \"outcome\": {\"part\": \"role_reading\", \"kind\": \"map_cuff\", \"below\": 35}, \"result\": {\"grain\": \"anaesthetic\", \"type\": \"count\"}}, \"decisions\": [\"Under 28 days at the start.\"], \"question_sql\": \"SELECT COUNT(*) AS anaesthetics FROM role_anaesthetic a JOIN role_patient p ON p.patient_key = a.patient_key WHERE p.is_test = 0 AND DATEDIFF(day, p.birth_date, a.start_time) < 28\", \"note\": \"A count.\", \"parts_used\": [\"role_anaesthetic\", \"role_patient\"], \"proposals\": []}\n```"

func testServer(t *testing.T, fake *model.Fake) (*Server, *httptest.Server) {
	t.Helper()
	st, err := store.Open(filepath.Join(t.TempDir(), "ask.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = st.Close() })
	cfg := &config.Config{PublicURL: "https://ask.example.test", Backend: "fake", BrandName: "Schemalyser", AdminEmails: []string{"owner@example.test"}}
	s, err := New(cfg, st, fake)
	if err != nil {
		t.Fatal(err)
	}
	s.now = func() time.Time { return time.Date(2026, 10, 14, 9, 0, 0, 0, time.UTC) }
	ts := httptest.NewServer(s.Handler())
	t.Cleanup(ts.Close)
	return s, ts
}

func as(email string, req *http.Request) *http.Request {
	req.Header.Set("Remote-Email", email)
	return req
}

func do(t *testing.T, req *http.Request) (*http.Response, string) {
	t.Helper()
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	return resp, string(body)
}

func TestAClinicianAsksTheAssistantClarifiesAndThePackageIsPublishedAtASecretAddress(t *testing.T) {
	fake := &model.Fake{Replies: []string{"Which age range do you mean by neonate, and over which years?", settled}}
	_, ts := testServer(t, fake)
	form := url.Values{"message": {"How many neonates had a mean pressure below 35?"}}
	req, _ := http.NewRequest(http.MethodPost, ts.URL+"/questions", strings.NewReader(form.Encode()))
	req.Header.Set("content-type", "application/x-www-form-urlencoded")
	resp, _ := do(t, as("dr@example.test", req))
	if resp.StatusCode != http.StatusSeeOther || !strings.HasPrefix(resp.Header.Get("Location"), "/questions/") {
		t.Fatalf("start: %d %s", resp.StatusCode, resp.Header.Get("Location"))
	}
	page := resp.Header.Get("Location")
	if !strings.Contains(fake.Systems[0], "role_reading") || !strings.Contains(fake.Systems[0], "schemalyser-question") {
		t.Fatal("the model did not read the contract and the protocol")
	}
	req, _ = http.NewRequest(http.MethodGet, ts.URL+page, nil)
	resp, body := do(t, as("dr@example.test", req))
	if resp.StatusCode != 200 || !strings.Contains(body, "Which age range") || strings.Contains(body, "The question, written") {
		t.Fatalf("page after the first turn: %d", resp.StatusCode)
	}
	form = url.Values{"message": {"Under 28 days, over 2024 and 2025."}}
	req, _ = http.NewRequest(http.MethodPost, ts.URL+page+"/messages", strings.NewReader(form.Encode()))
	req.Header.Set("content-type", "application/x-www-form-urlencoded")
	req.Header.Set("HX-Request", "true")
	resp, body = do(t, as("dr@example.test", req))
	if resp.StatusCode != 200 || !strings.Contains(body, "The question, written for the hospital") || strings.Contains(body, "```") {
		t.Fatalf("the package is not shown, or the block leaked: %d\n%s", resp.StatusCode, body)
	}
	if len(fake.Calls[1]) != 3 || fake.Calls[1][1].Role != "assistant" {
		t.Fatalf("the model did not see the whole conversation: %v", fake.Calls[1])
	}
	start := strings.Index(body, "https://ask.example.test/q/")
	if start < 0 {
		t.Fatal("no secret address on the page")
	}
	address := body[start : start+len("https://ask.example.test/q/")+48]
	req, _ = http.NewRequest(http.MethodGet, ts.URL+strings.TrimPrefix(address, "https://ask.example.test"), nil)
	resp, body = do(t, req)
	if resp.StatusCode != 200 || resp.Header.Get("content-type") != "application/json; charset=utf-8" {
		t.Fatalf("the secret address did not serve the package: %d", resp.StatusCode)
	}
	var pkg map[string]any
	if err := json.Unmarshal([]byte(body), &pkg); err != nil || pkg["format"] != "schemalyser-question/1" || pkg["hash"] != resp.Header.Get("x-schemalyser-question-hash") {
		t.Fatalf("package: %v %v", err, pkg["format"])
	}
	if pkg["status"] != "drafted" || pkg["contract_version"] != "1.1" {
		t.Fatalf("status %v version %v", pkg["status"], pkg["contract_version"])
	}
	// Another clinician cannot open the question by its number, and no one opens it without a login.
	req, _ = http.NewRequest(http.MethodGet, ts.URL+page, nil)
	if resp, _ := do(t, as("other@example.test", req)); resp.StatusCode != 404 {
		t.Fatalf("another clinician saw the question: %d", resp.StatusCode)
	}
	req, _ = http.NewRequest(http.MethodGet, ts.URL+page, nil)
	if resp, _ := do(t, req); resp.StatusCode != 401 {
		t.Fatalf("no login was accepted: %d", resp.StatusCode)
	}
	req, _ = http.NewRequest(http.MethodGet, ts.URL+"/", nil)
	if resp, body := do(t, as("owner@example.test", req)); resp.StatusCode != 200 || !strings.Contains(body, "dr@example.test") {
		t.Fatal("the admin does not see every question with who asked it")
	}
}

func TestABlockThatBreaksARuleIsShownAsAProblemAndNoPackageIsWritten(t *testing.T) {
	bad := strings.Replace(settled, "role_patient p", "role_person p", 1)
	fake := &model.Fake{Replies: []string{bad}}
	_, ts := testServer(t, fake)
	form := url.Values{"message": {"How many?"}}
	req, _ := http.NewRequest(http.MethodPost, ts.URL+"/questions", strings.NewReader(form.Encode()))
	req.Header.Set("content-type", "application/x-www-form-urlencoded")
	resp, _ := do(t, as("dr@example.test", req))
	req, _ = http.NewRequest(http.MethodGet, ts.URL+resp.Header.Get("Location"), nil)
	_, body := do(t, as("dr@example.test", req))
	if !strings.Contains(body, "role_person") || strings.Contains(body, "The question, written") {
		t.Fatalf("the problem was not shown, or a package was written:\n%s", body)
	}
}
