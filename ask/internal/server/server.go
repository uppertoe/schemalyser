// Package server is the service's pages: a list of the clinician's questions, a conversation for each, and the
// secret address at which a question's package is published. It trusts the Remote-Email header that the login wall
// in front of it sets, or ASK_DEV_USER when there is no wall.
package server

import (
	"context"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"html/template"
	"io/fs"
	"log"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/uppertoe/schemalyser/ask/internal/config"
	"github.com/uppertoe/schemalyser/ask/internal/model"
	"github.com/uppertoe/schemalyser/ask/internal/prompt"
	"github.com/uppertoe/schemalyser/ask/internal/question"
	"github.com/uppertoe/schemalyser/ask/internal/rolemodel"
	"github.com/uppertoe/schemalyser/ask/internal/store"
)

//go:embed static
var static embed.FS

// Server holds what every handler shares.
type Server struct {
	cfg      *config.Config
	store    *store.Store
	model    model.Model
	contract *rolemodel.Contract
	system   string
	index    *template.Template
	page     *template.Template
	turns    *template.Template
	handler  http.Handler
	now      func() time.Time
}

// New builds the server.
func New(cfg *config.Config, st *store.Store, m model.Model) (*Server, error) {
	contract, err := rolemodel.Load()
	if err != nil {
		return nil, err
	}
	s := &Server{cfg: cfg, store: st, model: m, contract: contract, system: prompt.Build(contract), now: time.Now}
	s.index = template.Must(template.New("layout").Parse(layoutHTML + indexHTML))
	s.page = template.Must(template.New("layout").Parse(layoutHTML + questionHTML + conversationHTML))
	s.turns = template.Must(template.New("conversation").Parse(conversationHTML))
	mux := http.NewServeMux()
	assets, _ := fs.Sub(static, "static")
	mux.Handle("GET /static/", http.StripPrefix("/static/", http.FileServer(http.FS(assets))))
	mux.HandleFunc("GET /static/ask.css", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("content-type", "text/css; charset=utf-8")
		_, _ = w.Write([]byte(styleCSS))
	})
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("ok")) })
	mux.HandleFunc("GET /q/{token}", s.published)
	mux.HandleFunc("GET /{$}", s.withUser(s.home))
	mux.HandleFunc("POST /questions", s.withUser(s.start))
	mux.HandleFunc("GET /questions/{id}", s.withUser(s.show))
	mux.HandleFunc("POST /questions/{id}/messages", s.withUser(s.reply))
	mux.HandleFunc("GET /questions/{id}/package.json", s.withUser(s.download))
	s.handler = secure(mux)
	return s, nil
}

// Handler is the service's one handler.
func (s *Server) Handler() http.Handler { return s.handler }

func secure(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("X-Frame-Options", "DENY")
		next.ServeHTTP(w, r)
	})
}

type userKey struct{}

func (s *Server) withUser(next func(http.ResponseWriter, *http.Request, string)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		email := strings.ToLower(strings.TrimSpace(r.Header.Get("Remote-Email")))
		if email == "" {
			email = s.cfg.DevUser
		}
		if email == "" {
			http.Error(w, "No one is signed in. The service runs behind the login wall, which says who you are.", http.StatusUnauthorized)
			return
		}
		if !s.cfg.MaySignIn(email) {
			http.Error(w, "This service is not yet open to you. The owner opens it to colleagues once the examination of questions exists.", http.StatusForbidden)
			return
		}
		next(w, r, email)
	}
}

type pageData struct {
	Brand, Title, User, Address, Error string
	Admin                              bool
	Questions                          []store.Question
	Question                           *store.Question
	Messages                           []store.Message
	Package                            *store.Package
	Pkg                                *question.Package
}

func (s *Server) home(w http.ResponseWriter, r *http.Request, user string) {
	qs, err := s.store.Questions(r.Context(), user, s.cfg.IsAdmin(user))
	if err != nil {
		s.fail(w, err)
		return
	}
	s.render(w, s.index, pageData{Brand: s.cfg.BrandName, Title: "Ask", User: user, Admin: s.cfg.IsAdmin(user), Questions: qs})
}

func (s *Server) start(w http.ResponseWriter, r *http.Request, user string) {
	first := strings.TrimSpace(r.FormValue("message"))
	if first == "" {
		http.Redirect(w, r, "/", http.StatusSeeOther)
		return
	}
	title := first
	if len(title) > 80 {
		title = title[:77] + "..."
	}
	q, err := s.store.NewQuestion(r.Context(), user, title, s.now())
	if err != nil {
		s.fail(w, err)
		return
	}
	if _, err := s.converse(r.Context(), q, user, first); err != nil {
		log.Printf("question %d: %v", q.ID, err)
	}
	http.Redirect(w, r, fmt.Sprintf("/questions/%d", q.ID), http.StatusSeeOther)
}

func (s *Server) owned(r *http.Request, user string) (*store.Question, error) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		return nil, fmt.Errorf("no such question")
	}
	q, err := s.store.Question(r.Context(), id)
	if err != nil {
		return nil, fmt.Errorf("no such question")
	}
	if q.Owner != user && !s.cfg.IsAdmin(user) {
		return nil, fmt.Errorf("no such question")
	}
	return q, nil
}

func (s *Server) show(w http.ResponseWriter, r *http.Request, user string) {
	q, err := s.owned(r, user)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	data, err := s.pageOf(r.Context(), q, user, "")
	if err != nil {
		s.fail(w, err)
		return
	}
	s.render(w, s.page, data)
}

func (s *Server) reply(w http.ResponseWriter, r *http.Request, user string) {
	q, err := s.owned(r, user)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	text := strings.TrimSpace(r.FormValue("message"))
	problem := ""
	if text != "" {
		if _, err := s.converse(r.Context(), q, user, text); err != nil && !errors.Is(err, ErrHeldBack) {
			problem = "The assistant could not reply: " + err.Error()
		}
	}
	if r.Header.Get("HX-Request") == "" {
		http.Redirect(w, r, fmt.Sprintf("/questions/%d", q.ID), http.StatusSeeOther)
		return
	}
	data, err := s.pageOf(r.Context(), q, user, problem)
	if err != nil {
		s.fail(w, err)
		return
	}
	s.render(w, s.turns, data)
}

// Patterns that a message about a group of patients never needs: a run of digits long enough to be a record number, a
// date written as a person's date of birth, and an upper-case name with an underscore, which is how a hospital's table
// is named. A message that matches is held back before it reaches the model, and the clinician is told why.
var (
	recordNumberLike = regexp.MustCompile(`\b\d{6,}\b`)
	birthDateLike    = regexp.MustCompile(`(?i)\b(born|dob|date of birth|d\.o\.b)\b.{0,20}\d|\b\d{1,2}[/.-]\d{1,2}[/.-](19|20)\d{2}\b`)
	tableLike        = regexp.MustCompile(`\b[A-Z][A-Z0-9]+_[A-Z0-9_]+\b`)
	heldBack         = "(held back, not sent) "
)

// screen says why a message is held back, or gives an empty string where it may go to the model.
func screen(text string) string {
	switch {
	case recordNumberLike.MatchString(text):
		return "The message holds a run of digits that could be a record number, so it has not been sent. A question is about groups of patients; please write it without any patient's identifiers."
	case birthDateLike.MatchString(text):
		return "The message holds what looks like a date of birth or another date of a patient's, so it has not been sent. Give ages or age ranges instead of dates."
	case tableLike.MatchString(text):
		return "The message holds what looks like the name of a table in the hospital's database, so it has not been sent. The assistant works from the public description of the record and never from the hospital's tables; describe what the table records instead."
	}
	return ""
}

// ErrHeldBack says that a message was not sent to the model.
var ErrHeldBack = errors.New("held back")

// converse records the clinician's message, asks the model, records its prose, and keeps any package it wrote. A
// message the screen holds back is recorded with the reason and never reaches the model.
func (s *Server) converse(ctx context.Context, q *store.Question, user, text string) (*question.Package, error) {
	now := s.now()
	if why := screen(text); why != "" {
		if err := s.store.AddMessage(ctx, q.ID, "user", heldBack+text, now); err != nil {
			return nil, err
		}
		if err := s.store.AddMessage(ctx, q.ID, "assistant", why, s.now()); err != nil {
			return nil, err
		}
		return nil, fmt.Errorf("%w: %s", ErrHeldBack, why)
	}
	if err := s.store.AddMessage(ctx, q.ID, "user", text, now); err != nil {
		return nil, err
	}
	history, err := s.store.Messages(ctx, q.ID)
	if err != nil {
		return nil, err
	}
	// A held-back message and the sentence that answered it stay in the record but are not shown to the model.
	turns := make([]model.Turn, 0, len(history))
	skipAnswer := false
	for _, m := range history {
		if strings.HasPrefix(m.Content, heldBack) {
			skipAnswer = true
			continue
		}
		if skipAnswer && m.Role == "assistant" {
			skipAnswer = false
			continue
		}
		turns = append(turns, model.Turn{Role: m.Role, Content: m.Content})
	}
	reply, err := s.model.Reply(ctx, s.system, turns)
	if err != nil {
		return nil, err
	}
	prose, raw := question.Split(reply)
	var pkg *question.Package
	if raw != "" {
		pkg, err = question.Parse(raw, s.contract, question.Produced{Model: s.model.Name(), At: now.UTC().Format(time.RFC3339), By: user})
		if err != nil {
			// The model's block broke a rule: the clinician sees the prose, and the rule goes back to the model as the
			// next turn so that it writes the block again.
			prose = strings.TrimSpace(prose + "\n\n(The question could not be written yet: " + err.Error() + ". The assistant will try again when you reply.)")
		}
	}
	if prose == "" {
		prose = "(The assistant wrote the question and nothing else.)"
	}
	if err := s.store.AddMessage(ctx, q.ID, "assistant", prose+rememberBlock(raw), s.now()); err != nil {
		return nil, err
	}
	if pkg != nil {
		encoded, _ := json.MarshalIndent(pkg, "", " ")
		if _, err := s.store.AddPackage(ctx, q.ID, string(encoded), pkg.Hash, s.now()); err != nil {
			return nil, err
		}
		if err := s.store.SetTitleAndStatus(ctx, q.ID, pkg.Title, pkg.Status, s.now()); err != nil {
			return nil, err
		}
	}
	return pkg, nil
}

// rememberBlock keeps the model's block in the stored turn, so that the model sees its own earlier package on the
// next turn; the page hides it when it shows the conversation.
func rememberBlock(raw string) string {
	if raw == "" {
		return ""
	}
	return "\n\n```schemalyser-question\n" + raw + "\n```"
}

func (s *Server) pageOf(ctx context.Context, q *store.Question, user, problem string) (pageData, error) {
	q, err := s.store.Question(ctx, q.ID)
	if err != nil {
		return pageData{}, err
	}
	messages, err := s.store.Messages(ctx, q.ID)
	if err != nil {
		return pageData{}, err
	}
	for i := range messages {
		messages[i].Content, _ = question.Split(messages[i].Content)
	}
	latest, err := s.store.LatestPackage(ctx, q.ID)
	if err != nil {
		return pageData{}, err
	}
	data := pageData{Brand: s.cfg.BrandName, Title: q.Title, User: user, Admin: s.cfg.IsAdmin(user), Question: q,
		Messages: messages, Package: latest, Error: problem, Address: s.cfg.PublicURL + "/q/" + q.Token}
	if latest != nil {
		var pkg question.Package
		if err := json.Unmarshal([]byte(latest.JSON), &pkg); err == nil {
			data.Pkg = &pkg
		}
	}
	return data, nil
}

func (s *Server) download(w http.ResponseWriter, r *http.Request, user string) {
	q, err := s.owned(r, user)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	s.servePackage(w, r, q)
}

// published serves a question's latest package at its secret address, with no login, so that the page in a hospital
// can fetch it. The address is a 48-character secret, and the package holds nothing of any hospital.
func (s *Server) published(w http.ResponseWriter, r *http.Request) {
	q, err := s.store.QuestionByToken(r.Context(), r.PathValue("token"))
	if err != nil {
		http.NotFound(w, r)
		return
	}
	s.servePackage(w, r, q)
}

func (s *Server) servePackage(w http.ResponseWriter, r *http.Request, q *store.Question) {
	latest, err := s.store.LatestPackage(r.Context(), q.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	if latest == nil {
		http.Error(w, "This question has no package yet: the conversation is still settling it.", http.StatusNotFound)
		return
	}
	w.Header().Set("content-type", "application/json; charset=utf-8")
	w.Header().Set("cache-control", "no-store")
	w.Header().Set("access-control-allow-origin", "*")
	w.Header().Set("x-schemalyser-question-hash", latest.Hash)
	_, _ = w.Write([]byte(latest.JSON))
}

func (s *Server) render(w http.ResponseWriter, t *template.Template, data pageData) {
	w.Header().Set("content-type", "text/html; charset=utf-8")
	if err := t.Execute(w, data); err != nil {
		log.Printf("render: %v", err)
	}
}

func (s *Server) fail(w http.ResponseWriter, err error) {
	log.Printf("error: %v", err)
	http.Error(w, "Something went wrong on the service's side. Try again, and tell the owner if it keeps happening.", http.StatusInternalServerError)
}
