// Package store keeps the questions, their conversations and their packages in one SQLite file.
package store

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"fmt"
	"time"

	_ "modernc.org/sqlite"
)

// Store is the one file.
type Store struct{ db *sql.DB }

// Question is one clinician's question.
type Question struct {
	ID      int64
	Token   string // the secret of its address
	Owner   string
	Title   string
	Status  string
	Created time.Time
	Updated time.Time
}

// Message is one turn of a question's conversation.
type Message struct {
	ID      int64
	Role    string
	Content string
	Created time.Time
}

// Package is one version of the question's package, as JSON.
type Package struct {
	ID      int64
	Version int
	JSON    string
	Hash    string
	Created time.Time
}

// Open opens or creates the file.
func Open(path string) (*Store, error) {
	db, err := sql.Open("sqlite", path+"?_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)&_pragma=foreign_keys(1)")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	for _, stmt := range []string{
		`CREATE TABLE IF NOT EXISTS questions (id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE, owner TEXT NOT NULL,
		   title TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL REFERENCES questions(id),
		   role TEXT NOT NULL, content TEXT NOT NULL, created TEXT NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS packages (id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL REFERENCES questions(id),
		   version INTEGER NOT NULL, json TEXT NOT NULL, hash TEXT NOT NULL, created TEXT NOT NULL)`,
		`CREATE INDEX IF NOT EXISTS messages_by_question ON messages(question_id, id)`,
		`CREATE INDEX IF NOT EXISTS packages_by_question ON packages(question_id, version)`,
	} {
		if _, err := db.Exec(stmt); err != nil {
			return nil, fmt.Errorf("the store could not be prepared: %w", err)
		}
	}
	return &Store{db: db}, nil
}

// Close closes the file.
func (s *Store) Close() error { return s.db.Close() }

func stamp(t time.Time) string { return t.UTC().Format(time.RFC3339) }

func parse(s string) time.Time {
	t, _ := time.Parse(time.RFC3339, s)
	return t
}

func token() (string, error) {
	b := make([]byte, 24)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// NewQuestion starts a question for an owner.
func (s *Store) NewQuestion(ctx context.Context, owner, title string, now time.Time) (*Question, error) {
	tok, err := token()
	if err != nil {
		return nil, err
	}
	res, err := s.db.ExecContext(ctx, `INSERT INTO questions (token, owner, title, status, created, updated) VALUES (?, ?, ?, ?, ?, ?)`,
		tok, owner, title, "in conversation", stamp(now), stamp(now))
	if err != nil {
		return nil, err
	}
	id, _ := res.LastInsertId()
	return &Question{ID: id, Token: tok, Owner: owner, Title: title, Status: "in conversation", Created: now, Updated: now}, nil
}

func (s *Store) question(ctx context.Context, where string, arg any) (*Question, error) {
	var q Question
	var created, updated string
	err := s.db.QueryRowContext(ctx, `SELECT id, token, owner, title, status, created, updated FROM questions WHERE `+where, arg).
		Scan(&q.ID, &q.Token, &q.Owner, &q.Title, &q.Status, &created, &updated)
	if err != nil {
		return nil, err
	}
	q.Created, q.Updated = parse(created), parse(updated)
	return &q, nil
}

// Question finds a question by id.
func (s *Store) Question(ctx context.Context, id int64) (*Question, error) {
	return s.question(ctx, "id = ?", id)
}

// QuestionByToken finds a question by the secret of its address.
func (s *Store) QuestionByToken(ctx context.Context, tok string) (*Question, error) {
	return s.question(ctx, "token = ?", tok)
}

// Questions lists an owner's questions, or everyone's for an admin, newest first.
func (s *Store) Questions(ctx context.Context, owner string, all bool) ([]Question, error) {
	query := `SELECT id, token, owner, title, status, created, updated FROM questions WHERE owner = ? ORDER BY updated DESC`
	args := []any{owner}
	if all {
		query, args = `SELECT id, token, owner, title, status, created, updated FROM questions ORDER BY updated DESC`, nil
	}
	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Question
	for rows.Next() {
		var q Question
		var created, updated string
		if err := rows.Scan(&q.ID, &q.Token, &q.Owner, &q.Title, &q.Status, &created, &updated); err != nil {
			return nil, err
		}
		q.Created, q.Updated = parse(created), parse(updated)
		out = append(out, q)
	}
	return out, rows.Err()
}

// SetTitleAndStatus records what the latest package says of the question.
func (s *Store) SetTitleAndStatus(ctx context.Context, id int64, title, status string, now time.Time) error {
	_, err := s.db.ExecContext(ctx, `UPDATE questions SET title = ?, status = ?, updated = ? WHERE id = ?`, title, status, stamp(now), id)
	return err
}

// AddMessage appends a turn.
func (s *Store) AddMessage(ctx context.Context, id int64, role, content string, now time.Time) error {
	if _, err := s.db.ExecContext(ctx, `INSERT INTO messages (question_id, role, content, created) VALUES (?, ?, ?, ?)`,
		id, role, content, stamp(now)); err != nil {
		return err
	}
	_, err := s.db.ExecContext(ctx, `UPDATE questions SET updated = ? WHERE id = ?`, stamp(now), id)
	return err
}

// Messages gives a question's conversation in order.
func (s *Store) Messages(ctx context.Context, id int64) ([]Message, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id, role, content, created FROM messages WHERE question_id = ? ORDER BY id`, id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Message
	for rows.Next() {
		var m Message
		var created string
		if err := rows.Scan(&m.ID, &m.Role, &m.Content, &created); err != nil {
			return nil, err
		}
		m.Created = parse(created)
		out = append(out, m)
	}
	return out, rows.Err()
}

// AddPackage records a new version of the question's package.
func (s *Store) AddPackage(ctx context.Context, id int64, jsonText, hash string, now time.Time) (int, error) {
	var version int
	if err := s.db.QueryRowContext(ctx, `SELECT COALESCE(MAX(version), 0) + 1 FROM packages WHERE question_id = ?`, id).Scan(&version); err != nil {
		return 0, err
	}
	_, err := s.db.ExecContext(ctx, `INSERT INTO packages (question_id, version, json, hash, created) VALUES (?, ?, ?, ?, ?)`,
		id, version, jsonText, hash, stamp(now))
	return version, err
}

// LatestPackage gives the question's latest package, or nil where none has been written.
func (s *Store) LatestPackage(ctx context.Context, id int64) (*Package, error) {
	return s.package_(ctx, `SELECT id, version, json, hash, created FROM packages WHERE question_id = ? ORDER BY version DESC LIMIT 1`, id)
}

// PackageVersion gives one version of the question's package, or nil where it was never written. A version is a row
// that nothing updates, so its bytes are the bytes that were saved.
func (s *Store) PackageVersion(ctx context.Context, id int64, version int) (*Package, error) {
	return s.package_(ctx, `SELECT id, version, json, hash, created FROM packages WHERE question_id = ? AND version = ?`, id, version)
}

func (s *Store) package_(ctx context.Context, query string, args ...any) (*Package, error) {
	var p Package
	var created string
	err := s.db.QueryRowContext(ctx, query, args...).Scan(&p.ID, &p.Version, &p.JSON, &p.Hash, &created)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	p.Created = parse(created)
	return &p, nil
}
