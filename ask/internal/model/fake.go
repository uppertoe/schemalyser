package model

import (
	"context"
	"fmt"
)

// Fake replies from a script, for the tests and for trying the pages with no model at all.
type Fake struct {
	Replies []string
	Calls   [][]Turn
	Systems []string
}

func (f *Fake) Name() string { return "fake" }

// Reply gives the next scripted reply, or a plain line once the script runs out.
func (f *Fake) Reply(_ context.Context, system string, turns []Turn) (string, error) {
	f.Calls = append(f.Calls, append([]Turn(nil), turns...))
	f.Systems = append(f.Systems, system)
	if n := len(f.Calls) - 1; n < len(f.Replies) {
		return f.Replies[n], nil
	}
	return fmt.Sprintf("I have read your message (%d turns so far). Tell me more about the cohort.", len(turns)), nil
}
