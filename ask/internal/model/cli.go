package model

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// CLI reaches the model through the headless Claude Code command line, which bills a subscription rather than the
// metered API. It is for development on a machine that has the command line and no key; the conversation so far is
// folded into one prompt, because each call is a fresh process.
type CLI struct {
	Model   string
	Command string
	Timeout time.Duration
}

// NewCLI builds the cli backend.
func NewCLI(model string) *CLI {
	return &CLI{Model: model, Command: "claude", Timeout: 10 * time.Minute}
}

func (c *CLI) Name() string { return c.Model + " (cli)" }

// Arguments are the command line's flags for one headless completion with nothing but the prompt: no tools, no
// external servers, no skills, no settings files, no session kept, one turn. Minimal mode is not used, because it
// drops the subscription login.
func Arguments(system, model string) []string {
	return []string{"-p", "--system-prompt", system, "--model", model, "--output-format", "json", "--max-turns", "1",
		"--tools", "", "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", "",
		"--exclude-dynamic-system-prompt-sections", "--no-session-persistence"}
}

// Reply runs one headless completion.
func (c *CLI) Reply(ctx context.Context, system string, turns []Turn) (string, error) {
	var prompt strings.Builder
	prompt.WriteString("The conversation so far, oldest first. Reply as the assistant to the last message only.\n\n")
	for _, t := range turns {
		who := "Clinician"
		if t.Role == "assistant" {
			who = "Assistant"
		}
		fmt.Fprintf(&prompt, "%s:\n%s\n\n", who, t.Content)
	}
	ctx, cancel := context.WithTimeout(ctx, c.Timeout)
	defer cancel()
	// The command line runs with no tools, no external servers, no skills, none of the developer's settings or memory,
	// and in an empty folder of its own, so that the model can only read the prompt and write its reply.
	dir, err := os.MkdirTemp("", "schemalyser-ask-")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(dir)
	cmd := exec.CommandContext(ctx, c.Command, Arguments(system, c.Model)...)
	cmd.Dir = dir
	// The key is emptied so that the command line can only bill the subscription, never the metered API.
	cmd.Env = append(os.Environ(), "ANTHROPIC_API_KEY=")
	cmd.Stdin = strings.NewReader(prompt.String())
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("the command line failed: %v: %s", err, strings.TrimSpace(errb.String()))
	}
	var envelope struct {
		Result  string `json:"result"`
		IsError bool   `json:"is_error"`
	}
	if err := json.Unmarshal(out.Bytes(), &envelope); err != nil {
		return "", fmt.Errorf("the command line's reply was not readable: %w", err)
	}
	if envelope.IsError {
		return "", fmt.Errorf("the command line reported an error: %s", envelope.Result)
	}
	return envelope.Result, nil
}
