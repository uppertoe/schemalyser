package model

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// API reaches the Anthropic Messages API directly, with no SDK, so that the service stays small.
type API struct {
	Key    string
	Model  string
	Client *http.Client
	URL    string
}

// NewAPI builds the api backend.
func NewAPI(key, model string) *API {
	return &API{Key: key, Model: model, Client: &http.Client{Timeout: 180 * time.Second}, URL: "https://api.anthropic.com/v1/messages"}
}

func (a *API) Name() string { return a.Model }

type apiMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type apiRequest struct {
	Model     string       `json:"model"`
	MaxTokens int          `json:"max_tokens"`
	System    []apiSystem  `json:"system"`
	Messages  []apiMessage `json:"messages"`
}

type apiSystem struct {
	Type         string            `json:"type"`
	Text         string            `json:"text"`
	CacheControl map[string]string `json:"cache_control,omitempty"`
}

type apiResponse struct {
	Content []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	} `json:"content"`
	Error *struct {
		Message string `json:"message"`
	} `json:"error"`
}

// Reply sends the whole conversation and returns the model's text. The system prompt is marked for caching, because
// it is the same contract on every turn of every question.
func (a *API) Reply(ctx context.Context, system string, turns []Turn) (string, error) {
	req := apiRequest{Model: a.Model, MaxTokens: 8000,
		System: []apiSystem{{Type: "text", Text: system, CacheControl: map[string]string{"type": "ephemeral"}}}}
	for _, t := range turns {
		req.Messages = append(req.Messages, apiMessage{Role: t.Role, Content: t.Content})
	}
	body, err := json.Marshal(req)
	if err != nil {
		return "", err
	}
	r, err := http.NewRequestWithContext(ctx, http.MethodPost, a.URL, bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	r.Header.Set("content-type", "application/json")
	r.Header.Set("x-api-key", a.Key)
	r.Header.Set("anthropic-version", "2023-06-01")
	resp, err := a.Client.Do(r)
	if err != nil {
		return "", fmt.Errorf("the model could not be reached: %w", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	var out apiResponse
	if err := json.Unmarshal(raw, &out); err != nil {
		return "", fmt.Errorf("the model's reply was not readable (status %d)", resp.StatusCode)
	}
	if out.Error != nil {
		return "", fmt.Errorf("the model refused: %s", out.Error.Message)
	}
	var text bytes.Buffer
	for _, c := range out.Content {
		if c.Type == "text" {
			text.WriteString(c.Text)
		}
	}
	if text.Len() == 0 {
		return "", fmt.Errorf("the model gave no text (status %d)", resp.StatusCode)
	}
	return text.String(), nil
}
