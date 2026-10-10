// POST /auth/logout — revokes the session of the Bearer token; later calls with it return 401.
package main

import (
	"context"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	if err := auth.SignOut(ctx, auth.FromContext(ctx)); err != nil {
		return 0, nil, err
	}
	return 200, map[string]any{"logged_out": true}, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }
