// Package config reads the service's settings from the environment, as the other apps of the server do.
package config

import (
	"fmt"
	"os"
	"strings"
)

// Config is everything the service is told from outside.
type Config struct {
	ListenAddr  string   // where the service listens; Caddy is in front of it
	SQLitePath  string   // the one file that holds the questions
	PublicURL   string   // the address the service is reached at, for the secret addresses it hands out
	Backend     string   // api, cli or fake: how the model is reached
	Model       string   // the model's name
	APIKey      string   // the key for the api backend, never written anywhere
	DevUser     string   // an email to act as when no login wall is in front, for development only
	AdminEmails []string // who may see every question rather than their own
	BrandName   string
}

func getenv(key, fallback string) string {
	if v, ok := os.LookupEnv(key); ok && strings.TrimSpace(v) != "" {
		return strings.TrimSpace(v)
	}
	return fallback
}

// Load reads the configuration and refuses a combination the service cannot run with.
func Load() (*Config, error) {
	c := &Config{
		ListenAddr: getenv("ASK_LISTEN", ":8080"),
		SQLitePath: getenv("ASK_SQLITE", "/data/ask.db"),
		PublicURL:  strings.TrimRight(getenv("ASK_PUBLIC_URL", "http://localhost:8080"), "/"),
		Backend:    strings.ToLower(getenv("ASK_MODEL_BACKEND", "api")),
		Model:      getenv("ASK_MODEL", "claude-fable-5-1"),
		APIKey:     getenv("ANTHROPIC_API_KEY", ""),
		DevUser:    strings.ToLower(getenv("ASK_DEV_USER", "")),
		BrandName:  getenv("BRAND_NAME", "Schemalyser"),
	}
	for _, e := range strings.Split(getenv("ASK_ADMIN_EMAILS", ""), ",") {
		if e = strings.ToLower(strings.TrimSpace(e)); e != "" {
			c.AdminEmails = append(c.AdminEmails, e)
		}
	}
	switch c.Backend {
	case "api":
		if c.APIKey == "" {
			return nil, fmt.Errorf("ASK_MODEL_BACKEND is api, so ANTHROPIC_API_KEY must be set")
		}
	case "cli", "fake":
	default:
		return nil, fmt.Errorf("ASK_MODEL_BACKEND must be api, cli or fake, not %q", c.Backend)
	}
	return c, nil
}

// IsAdmin says whether an email may see every question.
func (c *Config) IsAdmin(email string) bool {
	for _, e := range c.AdminEmails {
		if e == strings.ToLower(email) {
			return true
		}
	}
	return false
}
