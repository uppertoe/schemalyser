// Command ask is the clinician-facing service of Schemalyser: an anaesthetist writes a clinical question in their own
// words, the assistant pins it down and writes it in the terms of the public role contract, and the question is
// published at a secret address for the page in the hospital to open. The service sees the role contract and nothing
// of any hospital, and it runs behind the server's login wall.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/uppertoe/schemalyser/ask/internal/config"
	"github.com/uppertoe/schemalyser/ask/internal/model"
	"github.com/uppertoe/schemalyser/ask/internal/prompt"
	"github.com/uppertoe/schemalyser/ask/internal/rolemodel"
	"github.com/uppertoe/schemalyser/ask/internal/server"
	"github.com/uppertoe/schemalyser/ask/internal/store"
)

func main() {
	healthcheck := flag.Bool("healthcheck", false, "probe the local /healthz endpoint and exit")
	showPrompt := flag.Bool("prompt", false, "print the system prompt the model reads, and exit")
	flag.Parse()
	if *showPrompt {
		c, err := rolemodel.Load()
		if err != nil {
			log.Fatal(err)
		}
		fmt.Print(prompt.Build(c))
		return
	}
	if *healthcheck {
		os.Exit(probe())
	}
	if err := run(); err != nil {
		log.Fatalf("fatal: %v", err)
	}
}

func probe() int {
	addr := os.Getenv("ASK_LISTEN")
	if addr == "" {
		addr = ":8080"
	}
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://127.0.0.1" + addr + "/healthz")
	if err != nil || resp.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}

func backend(cfg *config.Config) (model.Model, error) {
	switch cfg.Backend {
	case "api":
		return model.NewAPI(cfg.APIKey, cfg.Model), nil
	case "cli":
		return model.NewCLI(cfg.Model), nil
	case "fake":
		return &model.Fake{}, nil
	}
	return nil, model.ErrNoBackend
}

func run() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	st, err := store.Open(cfg.SQLitePath)
	if err != nil {
		return err
	}
	defer st.Close()
	m, err := backend(cfg)
	if err != nil {
		return err
	}
	srv, err := server.New(cfg, st, m)
	if err != nil {
		return err
	}
	httpServer := &http.Server{Addr: cfg.ListenAddr, Handler: srv.Handler(), ReadHeaderTimeout: 10 * time.Second}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(shutdown)
	}()
	log.Printf("ask listens on %s with the %s backend", cfg.ListenAddr, cfg.Backend)
	if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}
