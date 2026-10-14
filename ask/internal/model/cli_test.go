package model

import (
	"strings"
	"testing"
)

func TestTheCommandLineRunsWithNothingButThePrompt(t *testing.T) {
	args := strings.Join(Arguments("the system prompt", "claude-fable-5-1"), " ")
	for _, want := range []string{"--tools  ", "--strict-mcp-config", "--disable-slash-commands", "--setting-sources  ", "--max-turns 1", "--no-session-persistence", "--output-format json"} {
		if !strings.Contains(args, want) {
			t.Errorf("the command line lacks %q: %s", want, args)
		}
	}
}
