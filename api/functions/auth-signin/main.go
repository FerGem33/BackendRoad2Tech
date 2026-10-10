// POST /auth/signin — ENDPOINTS.md §3.18 (formerly /auth/login). Public: returns a Bearer token.
package main

import (
	"context"
	"time"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

type body struct {
	Email    string `json:"email"`
	Password string `json:"password"`
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	var b body
	if err := httpx.Decode(r, &b); err != nil {
		return 0, nil, err
	}
	token, exp, u, err := auth.SignIn(ctx, b.Email, b.Password)
	if err != nil {
		return 0, nil, err
	}
	return 200, map[string]any{
		"token": token, "expires_at": exp.Format(time.RFC3339),
		"user": map[string]string{"id": u.UserID, "name": u.Name, "email": u.Email},
	}, nil
}

func main() { lambda.Start(httpx.Wrap(handle)) }
