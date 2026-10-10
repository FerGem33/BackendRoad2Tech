// Package httpx implements the response conventions of ENDPOINTS.md §1 for API Gateway HTTP API (payload v2):
// envelope {request_id, data_as_of, data}, errors {code, message, request_id}, pagination and CORS.
package httpx

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math"
	"os"
	"strconv"
	"strings"

	"github.com/aws/aws-lambda-go/events"
)

type Request = events.APIGatewayV2HTTPRequest
type Response = events.APIGatewayV2HTTPResponse

// Error is an API error rendered as {code, message, request_id} with the given HTTP status.
type Error struct {
	Status  int
	Code    string
	Message string
	Details any
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }

func Errorf(status int, code, format string, args ...any) *Error {
	return &Error{Status: status, Code: code, Message: fmt.Sprintf(format, args...)}
}

func BadRequest(format string, args ...any) *Error {
	return Errorf(400, "invalid_request", format, args...)
}

func NotFound(code, format string, args ...any) *Error { return Errorf(404, code, format, args...) }

// DataAsOf is the data cut date: "today" for every calculation (never the wall clock).
func DataAsOf() string {
	if v := os.Getenv("DATA_AS_OF"); v != "" {
		return v
	}
	return "2026-09-30"
}

func RequestID(r *Request) string {
	if id := r.RequestContext.RequestID; id != "" {
		return "req_" + strings.ReplaceAll(id, "=", "")
	}
	return "req_local"
}

func headers() map[string]string {
	origin := os.Getenv("CORS_ALLOW_ORIGIN")
	if origin == "" {
		origin = "*"
	}
	return map[string]string{
		"Content-Type":                 "application/json; charset=utf-8",
		"Access-Control-Allow-Origin":  origin,
		"Access-Control-Allow-Headers": "Authorization,Content-Type,Idempotency-Key",
		"Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
	}
}

func JSON(r *Request, status int, data any) Response {
	body, err := json.Marshal(map[string]any{"request_id": RequestID(r), "data_as_of": DataAsOf(), "data": data})
	if err != nil {
		return Fail(r, Errorf(500, "internal_error", "No se pudo serializar la respuesta."))
	}
	return Response{StatusCode: status, Headers: headers(), Body: string(body)}
}

func Fail(r *Request, err error) Response {
	var e *Error
	if !errors.As(err, &e) {
		slog.Error("unhandled error", "err", err, "path", r.RawPath)
		e = Errorf(500, "internal_error", "Ocurrió un error interno.")
	}
	payload := map[string]any{"code": e.Code, "message": e.Message, "request_id": RequestID(r)}
	if e.Details != nil {
		payload["details"] = e.Details
	}
	body, _ := json.Marshal(payload)
	return Response{StatusCode: e.Status, Headers: headers(), Body: string(body)}
}

// Handler returns (status, data, error). A nil error renders the envelope with data.
type Handler func(ctx context.Context, r *Request) (int, any, error)

// Wrap turns a Handler into a Lambda handler with CORS preflight, panic recovery and the envelope.
func Wrap(h Handler, middlewares ...func(Handler) Handler) func(context.Context, Request) (Response, error) {
	for i := len(middlewares) - 1; i >= 0; i-- {
		h = middlewares[i](h)
	}
	return func(ctx context.Context, req Request) (resp Response, _ error) {
		r := &req
		if r.RequestContext.HTTP.Method == "OPTIONS" {
			return Response{StatusCode: 204, Headers: headers()}, nil
		}
		defer func() {
			if p := recover(); p != nil {
				slog.Error("panic", "panic", p, "path", r.RawPath)
				resp = Fail(r, Errorf(500, "internal_error", "Ocurrió un error interno."))
			}
		}()
		status, data, err := h(ctx, r)
		if err != nil {
			return Fail(r, err), nil
		}
		return JSON(r, status, data), nil
	}
}

// Decode parses a JSON body into v. An empty body leaves v untouched.
func Decode(r *Request, v any) error {
	body := r.Body
	if r.IsBase64Encoded {
		raw, err := base64.StdEncoding.DecodeString(body)
		if err != nil {
			return BadRequest("Cuerpo base64 inválido.")
		}
		body = string(raw)
	}
	if strings.TrimSpace(body) == "" {
		return nil
	}
	dec := json.NewDecoder(strings.NewReader(body))
	if err := dec.Decode(v); err != nil {
		return BadRequest("JSON inválido: %v", err)
	}
	return nil
}

func Query(r *Request, key string) string { return strings.TrimSpace(r.QueryStringParameters[key]) }

func Path(r *Request, key string) string { return r.PathParameters[key] }

func Header(r *Request, key string) string {
	for k, v := range r.Headers {
		if strings.EqualFold(k, key) {
			return v
		}
	}
	return ""
}

func QueryBool(r *Request, key string) (bool, error) {
	v := Query(r, key)
	if v == "" {
		return false, nil
	}
	b, err := strconv.ParseBool(v)
	if err != nil {
		return false, BadRequest("%s debe ser true o false.", key)
	}
	return b, nil
}

// Page is the pagination of ENDPOINTS.md §1: page from 1, page_size max 100.
type Page struct {
	Page     int `json:"page"`
	PageSize int `json:"page_size"`
	Total    int `json:"total"`
}

func ParsePage(r *Request, defaultSize int) (Page, error) {
	p := Page{Page: 1, PageSize: defaultSize}
	if v := Query(r, "page"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 {
			return p, BadRequest("page debe ser un entero >= 1.")
		}
		p.Page = n
	}
	if v := Query(r, "page_size"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 100 {
			return p, BadRequest("page_size debe estar entre 1 y 100.")
		}
		p.PageSize = n
	}
	return p, nil
}

// Paginate slices items for the page and fills Total.
func Paginate[T any](items []T, p *Page) []T {
	p.Total = len(items)
	start := (p.Page - 1) * p.PageSize
	if start >= len(items) {
		return []T{}
	}
	end := min(start+p.PageSize, len(items))
	return items[start:end]
}

// Money rounds to 2 decimals.
func Money(v float64) float64 { return math.Round(v*100) / 100 }

func Round(v float64, decimals int) float64 {
	f := math.Pow(10, float64(decimals))
	return math.Round(v*f) / f
}
