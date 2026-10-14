package server

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/uppertoe/schemalyser/ask/internal/config"
	"github.com/uppertoe/schemalyser/ask/internal/examination"
	"github.com/uppertoe/schemalyser/ask/internal/model"
	"github.com/uppertoe/schemalyser/ask/internal/question"
	"github.com/uppertoe/schemalyser/ask/internal/rolemodel"
	"github.com/uppertoe/schemalyser/ask/internal/store"
)

const settled = "The question is settled.\n\n```schemalyser-question\n" + question.Block + "\n```"

func testServer(t *testing.T, fake *model.Fake) (*Server, *httptest.Server) {
	t.Helper()
	st, err := store.Open(filepath.Join(t.TempDir(), "ask.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = st.Close() })
	cfg := &config.Config{PublicURL: "https://ask.example.test", Backend: "fake", BrandName: "Schemalyser",
		AdminEmails: []string{"owner@example.test"}, AllowedEmails: []string{"dr@example.test", "other@example.test"}}
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
	s, ts := testServer(t, fake)
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
	// Before the examiner's report, the page says the question is not examined and the address serves nothing.
	if !strings.Contains(body, "stands at <strong>drafted</strong>") || strings.Contains(body, "/q/") {
		t.Fatalf("an unexamined package shows an address:\n%s", body)
	}
	hash := s.latestHash(t, page)
	upload(t, ts, page, s.signed(t, hash, "logic examined", "The package's logic is examined."))
	req, _ = http.NewRequest(http.MethodGet, ts.URL+page, nil)
	_, body = do(t, as("owner@example.test", req))
	if !strings.Contains(body, "stands at <strong>logic examined</strong>") {
		t.Fatalf("the level is not shown:\n%s", body)
	}
	// The address the asker copies names version 1 and carries the hash of its bytes.
	address := regexp.MustCompile(`https://ask\.example\.test/q/[0-9a-f]{48}/1#sha256=[0-9a-f]{64}`).FindString(body)
	if address == "" {
		t.Fatalf("no versioned secret address with its hash on the page:\n%s", body)
	}
	path := strings.TrimPrefix(address, "https://ask.example.test")
	path, fragment, _ := strings.Cut(path, "#sha256=")
	req, _ = http.NewRequest(http.MethodGet, ts.URL+path, nil)
	resp, body = do(t, req)
	if resp.StatusCode != 200 || resp.Header.Get("content-type") != "application/json; charset=utf-8" || resp.Header.Get("x-schemalyser-question-level") != "logic examined" {
		t.Fatalf("the secret address did not serve the package: %d", resp.StatusCode)
	}
	sum := sha256.Sum256([]byte(body))
	if hex.EncodeToString(sum[:]) != fragment || resp.Header.Get("x-schemalyser-question-hash") != fragment || resp.Header.Get("x-schemalyser-question-version") != "1" {
		t.Fatal("the served bytes do not hash to the address's fragment")
	}
	var pkg map[string]any
	if err := json.Unmarshal([]byte(body), &pkg); err != nil || pkg["format"] != "schemalyser-question/1" || pkg["contract_version"] != "1.1" {
		t.Fatalf("package: %v %v", err, pkg["format"])
	}
	if _, held := pkg["status"]; held {
		t.Fatal("the package carries a status")
	}
	if problems, err := question.Validate([]byte(body), rolemodel.Schema()); err != nil || len(problems) > 0 {
		t.Fatalf("the served package does not keep the schema: %v %v", err, problems)
	}
	// The latest version is also at the plain address, and a version never written is not found.
	req, _ = http.NewRequest(http.MethodGet, ts.URL+strings.TrimSuffix(path, "/1"), nil)
	if resp, again := do(t, req); resp.StatusCode != 200 || again != body {
		t.Fatalf("the plain address did not serve the latest version: %d", resp.StatusCode)
	}
	req, _ = http.NewRequest(http.MethodGet, ts.URL+strings.TrimSuffix(path, "/1")+"/2", nil)
	if resp, _ := do(t, req); resp.StatusCode != 404 {
		t.Fatalf("a version never written was served: %d", resp.StatusCode)
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

func TestAMessageWithAPatientsIdentifierIsHeldBackAndNeverReachesTheModel(t *testing.T) {
	fake := &model.Fake{Replies: []string{"Which years?"}}
	_, ts := testServer(t, fake)
	form := url.Values{"message": {"How many anaesthetics did patient 12345678 have?"}}
	req, _ := http.NewRequest(http.MethodPost, ts.URL+"/questions", strings.NewReader(form.Encode()))
	req.Header.Set("content-type", "application/x-www-form-urlencoded")
	resp, _ := do(t, as("dr@example.test", req))
	req, _ = http.NewRequest(http.MethodGet, ts.URL+resp.Header.Get("Location"), nil)
	_, body := do(t, as("dr@example.test", req))
	if len(fake.Calls) != 0 {
		t.Fatal("the model received a held-back message")
	}
	if !strings.Contains(body, "could be a record number") {
		t.Fatalf("the clinician was not told why:\n%s", body)
	}
	// The next message, clean, goes to the model without the held-back one.
	form = url.Values{"message": {"How many anaesthetics did children under one year have in 2025?"}}
	req, _ = http.NewRequest(http.MethodPost, ts.URL+resp.Header.Get("Location")+"/messages", strings.NewReader(form.Encode()))
	req.Header.Set("content-type", "application/x-www-form-urlencoded")
	do(t, as("dr@example.test", req))
	if len(fake.Calls) != 1 || len(fake.Calls[0]) != 1 || strings.Contains(fake.Calls[0][0].Content, "12345678") {
		t.Fatalf("the model saw the wrong turns: %v", fake.Calls)
	}
	for _, held := range []string{"the child born 3/4/2019", "look in SOME_TABLE for it"} {
		if screen(held) == "" {
			t.Errorf("%q was not held back", held)
		}
	}
	if screen("children under 28 days in 2024 and 2025") != "" {
		t.Error("a clean message was held back")
	}
}

func TestOnlyTheAllowedAddressesMaySignIn(t *testing.T) {
	fake := &model.Fake{}
	s, ts := testServer(t, fake)
	s.cfg.AllowedEmails = []string{"dr@example.test"}
	// Only the allowed addresses and the admins; the development user is also admitted where one is set.
	for email, want := range map[string]int{"dr@example.test": 200, "owner@example.test": 200, "other@example.test": 403} {
		req, _ := http.NewRequest(http.MethodGet, ts.URL+"/", nil)
		if resp, _ := do(t, as(email, req)); resp.StatusCode != want {
			t.Errorf("%s: %d, wanted %d", email, resp.StatusCode, want)
		}
	}
}

// The examiner of the tests: a fresh key, whose public key the server is configured with.
type examiner struct {
	public  ed25519.PublicKey
	private ed25519.PrivateKey
}

func (s *Server) examiner(t *testing.T) *examiner {
	t.Helper()
	public, private, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	s.cfg.ExaminerPublicKey = hex.EncodeToString(public)
	return &examiner{public, private}
}

var examiners = map[*Server]*examiner{}

// signed writes a report over the package with the given hash at a level, signed by the test's examiner.
func (s *Server) signed(t *testing.T, hash, level, says string) []byte {
	t.Helper()
	e, ok := examiners[s]
	if !ok {
		e = s.examiner(t)
		examiners[s] = e
	}
	report := map[string]any{
		"package":     map[string]any{"path": "package.json", "sha256": hash, "title": "A question"},
		"status":      map[string]any{"level": level, "says": says},
		"rules":       []any{map[string]any{"id": "schema", "rule": "The package has the fields of the format.", "passed": true, "fragments": []any{}}},
		"planted":     []any{map[string]any{"name": "secret_case", "outcome": "failed", "says": "row 990002400 differs"}, map[string]any{"name": "other_case", "outcome": "passed", "says": "fine"}},
		"examined_at": "2026-10-15T09:00:00+11:00",
		"versions":    map[string]any{"contract": "1.1", "policy": "1", "dialect": json.Number("1"), "examiner": "1"},
	}
	signature := ed25519.Sign(e.private, examination.Canonical(report))
	report["signature"] = map[string]any{"public_key": hex.EncodeToString(e.public), "signature": hex.EncodeToString(signature)}
	data, _ := json.Marshal(report)
	return data
}

func (s *Server) latestHash(t *testing.T, page string) string {
	t.Helper()
	id, _ := strconv.ParseInt(strings.TrimPrefix(page, "/questions/"), 10, 64)
	latest, err := s.store.LatestPackage(context.Background(), id)
	if err != nil || latest == nil {
		t.Fatalf("no package: %v", err)
	}
	return latest.Hash
}

func upload(t *testing.T, ts *httptest.Server, page string, report []byte) (*http.Response, string) {
	t.Helper()
	var buf bytes.Buffer
	writer := multipart.NewWriter(&buf)
	part, _ := writer.CreateFormFile("report", "report.json")
	part.Write(report)
	writer.Close()
	req, _ := http.NewRequest(http.MethodPost, ts.URL+page+"/report", &buf)
	req.Header.Set("content-type", writer.FormDataContentType())
	req.Header.Set("HX-Request", "true")
	return do(t, as("owner@example.test", req))
}

func TestAnUnsignedOrWronglySignedReportIsRefusedAndAPackageBelowLogicExaminedIsNotPublished(t *testing.T) {
	fake := &model.Fake{Replies: []string{settled}}
	s, ts := testServer(t, fake)
	form := url.Values{"message": {"How much red cell volume do neonates get?"}}
	req, _ := http.NewRequest(http.MethodPost, ts.URL+"/questions", strings.NewReader(form.Encode()))
	req.Header.Set("content-type", "application/x-www-form-urlencoded")
	resp, _ := do(t, as("owner@example.test", req))
	page := resp.Header.Get("Location")
	hash := s.latestHash(t, page)
	good := s.signed(t, hash, "well formed", "The package is well formed.")
	// Unsigned, signed by another key, and changed after signing: each refused, and nothing recorded.
	var held map[string]any
	json.Unmarshal(good, &held)
	delete(held, "signature")
	unsigned, _ := json.Marshal(held)
	_, body := upload(t, ts, page, unsigned)
	if !strings.Contains(body, "carries no signature") {
		t.Fatalf("an unsigned report was not refused:\n%s", body)
	}
	other, _, _ := ed25519.GenerateKey(nil)
	json.Unmarshal(good, &held)
	held["signature"].(map[string]any)["public_key"] = hex.EncodeToString(other)
	wrong, _ := json.Marshal(held)
	if _, body := upload(t, ts, page, wrong); !strings.Contains(body, "not the examiner") {
		t.Fatalf("another key's report was not refused:\n%s", body)
	}
	changed := []byte(strings.Replace(string(good), "well formed", "logic examined", 1))
	if _, body := upload(t, ts, page, changed); !strings.Contains(body, "does not match its content") {
		t.Fatalf("a changed report was not refused:\n%s", body)
	}
	if n, _ := s.store.Examinations(context.Background(), 1); n != 0 {
		t.Fatalf("a refused report was recorded: %d", n)
	}
	// A verified report at well formed is recorded, the model hears only the bounded feedback, and nothing is published.
	_, body = upload(t, ts, page, good)
	if !strings.Contains(body, "The examiner") || strings.Contains(body, "secret_case") || strings.Contains(body, "990002400") {
		t.Fatalf("the feedback is missing or holds case detail:\n%s", body)
	}
	if !strings.Contains(body, "1 of 2 planted cases disagree") {
		t.Fatalf("the count of disagreeing cases is not given:\n%s", body)
	}
	form = url.Values{"message": {"Please fix it."}}
	req, _ = http.NewRequest(http.MethodPost, ts.URL+page+"/messages", strings.NewReader(form.Encode()))
	req.Header.Set("content-type", "application/x-www-form-urlencoded")
	do(t, as("owner@example.test", req))
	turns := fake.Calls[len(fake.Calls)-1]
	heard := ""
	for _, turn := range turns {
		heard += turn.Content + "\n"
	}
	if !strings.Contains(heard, "The examiner reports: ") || strings.Contains(heard, "secret_case") || strings.Contains(heard, "990002400") {
		t.Fatalf("the model heard the wrong thing:\n%s", heard)
	}
	q, _ := s.store.Question(context.Background(), 1)
	req, _ = http.NewRequest(http.MethodGet, ts.URL+"/q/"+q.Token+"/1", nil)
	if resp, _ := do(t, req); resp.StatusCode != 404 {
		t.Fatalf("a package at well formed was published: %d", resp.StatusCode)
	}
	req, _ = http.NewRequest(http.MethodGet, ts.URL+"/q/"+q.Token, nil)
	if resp, _ := do(t, req); resp.StatusCode != 404 {
		t.Fatalf("a package at well formed was published at the plain address: %d", resp.StatusCode)
	}
}

func TestAFourthExaminationIsRefused(t *testing.T) {
	fake := &model.Fake{Replies: []string{settled}}
	s, ts := testServer(t, fake)
	form := url.Values{"message": {"How much red cell volume do neonates get?"}}
	req, _ := http.NewRequest(http.MethodPost, ts.URL+"/questions", strings.NewReader(form.Encode()))
	req.Header.Set("content-type", "application/x-www-form-urlencoded")
	resp, _ := do(t, as("owner@example.test", req))
	page := resp.Header.Get("Location")
	hash := s.latestHash(t, page)
	for n := 1; n <= 3; n++ {
		if _, body := upload(t, ts, page, s.signed(t, hash, "well formed", "Not yet.")); strings.Contains(body, "as many as it may have") {
			t.Fatalf("examination %d was refused:\n%s", n, body)
		}
	}
	_, body := upload(t, ts, page, s.signed(t, hash, "logic examined", "Now."))
	if !strings.Contains(body, "has had 3 examinations, which is as many as it may have") {
		t.Fatalf("a fourth examination was not refused:\n%s", body)
	}
	q, _ := s.store.Question(context.Background(), 1)
	req, _ = http.NewRequest(http.MethodGet, ts.URL+"/q/"+q.Token+"/1", nil)
	if resp, _ := do(t, req); resp.StatusCode != 404 {
		t.Fatalf("the fourth report published the package: %d", resp.StatusCode)
	}
}

func TestASecondVersionLeavesTheFirstsBytesAndAddressUnchanged(t *testing.T) {
	second := strings.Replace(settled, "under 28 days at the start", "under 29 days at the start", 1)
	fake := &model.Fake{Replies: []string{settled, second}}
	s, ts := testServer(t, fake)
	form := url.Values{"message": {"How much red cell volume do neonates get?"}}
	req, _ := http.NewRequest(http.MethodPost, ts.URL+"/questions", strings.NewReader(form.Encode()))
	req.Header.Set("content-type", "application/x-www-form-urlencoded")
	resp, _ := do(t, as("dr@example.test", req))
	page := resp.Header.Get("Location")
	req, _ = http.NewRequest(http.MethodGet, ts.URL+page, nil)
	_, body := do(t, as("dr@example.test", req))
	upload(t, ts, page, s.signed(t, s.latestHash(t, page), "logic examined", "Examined."))
	req, _ = http.NewRequest(http.MethodGet, ts.URL+page, nil)
	_, body = do(t, as("dr@example.test", req))
	first := regexp.MustCompile(`/q/[0-9a-f]{48}/1#sha256=[0-9a-f]{64}`).FindString(body)
	if first == "" {
		t.Fatal("no address for version 1")
	}
	req, _ = http.NewRequest(http.MethodGet, ts.URL+strings.Split(first, "#")[0], nil)
	_, firstBytes := do(t, req)
	form = url.Values{"message": {"Make it 29 days."}}
	req, _ = http.NewRequest(http.MethodPost, ts.URL+page+"/messages", strings.NewReader(form.Encode()))
	req.Header.Set("content-type", "application/x-www-form-urlencoded")
	do(t, as("dr@example.test", req))
	upload(t, ts, page, s.signed(t, s.latestHash(t, page), "logic examined", "Examined again."))
	req, _ = http.NewRequest(http.MethodGet, ts.URL+page, nil)
	_, body = do(t, as("dr@example.test", req))
	second2 := regexp.MustCompile(`/q/[0-9a-f]{48}/2#sha256=[0-9a-f]{64}`).FindString(body)
	if second2 == "" || !strings.Contains(body, "version 2 of the question package") {
		t.Fatalf("no address for version 2:\n%s", body)
	}
	req, _ = http.NewRequest(http.MethodGet, ts.URL+strings.Split(first, "#")[0], nil)
	resp, again := do(t, req)
	if resp.StatusCode != 200 || again != firstBytes || !strings.Contains(again, "28 days") {
		t.Fatal("version 1 changed under the person who received it")
	}
	req, _ = http.NewRequest(http.MethodGet, ts.URL+strings.Split(second2, "#")[0], nil)
	if _, latest := do(t, req); !strings.Contains(latest, "29 days") || latest == firstBytes {
		t.Fatal("version 2 is not its own bytes")
	}
}
