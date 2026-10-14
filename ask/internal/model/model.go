// Package model reaches the language model through one of three backends: the Anthropic API, the headless Claude
// Code command line on a subscription, for development, or a scripted fake for the tests. Each takes the system
// prompt and the conversation so far and gives the model's next reply as text.
package model

import (
	"context"
	"errors"
)

// Turn is one message of the conversation, by the clinician ("user") or the model ("assistant").
type Turn struct {
	Role    string
	Content string
}

// Model gives the next reply.
type Model interface {
	Reply(ctx context.Context, system string, turns []Turn) (string, error)
	Name() string
}

// ErrNoBackend is returned when a backend cannot be built from its settings.
var ErrNoBackend = errors.New("the model's backend is not configured")
