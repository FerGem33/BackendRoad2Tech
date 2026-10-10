// Package auth issues and checks the Bearer tokens of ENDPOINTS.md §1 / §3.18.
//
// Tokens are HS256 JWTs signed with JWT_SECRET. Each token has a server-side session
// (PK=SESSION#<jti>) so /auth/logout can revoke it before it expires.
package auth

import (
	"context"
	"crypto/hmac"
	"crypto/pbkdf2"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

const TokenTTL = 12 * time.Hour

type Claims struct {
	Sub   string `json:"sub"`
	Email string `json:"email"`
	Name  string `json:"name"`
	JTI   string `json:"jti"`
	IAT   int64  `json:"iat"`
	EXP   int64  `json:"exp"`
}

type User struct {
	UserID       string `dynamodbav:"user_id"`
	Name         string `dynamodbav:"name"`
	Email        string `dynamodbav:"email"`
	PasswordHash string `dynamodbav:"password_hash"`
}

type Session struct {
	PK         string `dynamodbav:"PK"`
	SK         string `dynamodbav:"SK"`
	EntityType string `dynamodbav:"entity_type"`
	UserID     string `dynamodbav:"user_id"`
	Email      string `dynamodbav:"email"`
	CreatedAt  string `dynamodbav:"created_at"`
	ExpiresAt  string `dynamodbav:"expires_at"`
	TTL        int64  `dynamodbav:"ttl"`
}

var (
	ErrUnauthorized = httpx.Errorf(401, "unauthorized", "Se requiere iniciar sesión.")
	ErrExpired      = httpx.Errorf(401, "token_expired", "La sesión expiró; inicia sesión de nuevo.")
)

func secret() []byte {
	s := os.Getenv("JWT_SECRET")
	if s == "" {
		panic("JWT_SECRET is not set")
	}
	return []byte(s)
}

var b64 = base64.RawURLEncoding

func sign(claims Claims) (string, error) {
	header := b64.EncodeToString([]byte(`{"alg":"HS256","typ":"JWT"}`))
	payload, err := json.Marshal(claims)
	if err != nil {
		return "", err
	}
	unsigned := header + "." + b64.EncodeToString(payload)
	mac := hmac.New(sha256.New, secret())
	mac.Write([]byte(unsigned))
	return unsigned + "." + b64.EncodeToString(mac.Sum(nil)), nil
}

func parse(token string) (*Claims, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return nil, ErrUnauthorized
	}
	mac := hmac.New(sha256.New, secret())
	mac.Write([]byte(parts[0] + "." + parts[1]))
	sig, err := b64.DecodeString(parts[2])
	if err != nil || !hmac.Equal(sig, mac.Sum(nil)) {
		return nil, ErrUnauthorized
	}
	raw, err := b64.DecodeString(parts[1])
	if err != nil {
		return nil, ErrUnauthorized
	}
	var c Claims
	if err := json.Unmarshal(raw, &c); err != nil || c.JTI == "" {
		return nil, ErrUnauthorized
	}
	if time.Now().Unix() >= c.EXP {
		return nil, ErrExpired
	}
	return &c, nil
}

func NewID(prefix string) string {
	b := make([]byte, 10)
	_, _ = rand.Read(b)
	return prefix + strconv.FormatInt(time.Now().UnixMilli(), 36) + hex.EncodeToString(b)
}

// VerifyPassword checks a "pbkdf2_sha256$<iter>$<salt b64>$<hash b64>" hash.
func VerifyPassword(encoded, password string) bool {
	parts := strings.Split(encoded, "$")
	if len(parts) != 4 || parts[0] != "pbkdf2_sha256" {
		return false
	}
	iter, err := strconv.Atoi(parts[1])
	if err != nil {
		return false
	}
	salt, err1 := base64.StdEncoding.DecodeString(parts[2])
	want, err2 := base64.StdEncoding.DecodeString(parts[3])
	if err1 != nil || err2 != nil {
		return false
	}
	got, err := pbkdf2.Key(sha256.New, password, salt, iter, len(want))
	return err == nil && subtle.ConstantTimeCompare(got, want) == 1
}

// SignIn validates credentials and opens a session. Returns the token and its expiry.
func SignIn(ctx context.Context, email, password string) (string, time.Time, *User, error) {
	email = strings.ToLower(strings.TrimSpace(email))
	invalid := httpx.Errorf(401, "invalid_credentials", "Correo o contraseña incorrectos.")
	if email == "" || password == "" {
		return "", time.Time{}, nil, invalid
	}
	var u User
	ok, err := db.Get(ctx, "USER#"+email, "#META", &u)
	if err != nil {
		return "", time.Time{}, nil, err
	}
	if !ok || !VerifyPassword(u.PasswordHash, password) {
		return "", time.Time{}, nil, invalid
	}
	now := time.Now().UTC()
	exp := now.Add(TokenTTL)
	c := Claims{Sub: u.UserID, Email: u.Email, Name: u.Name, JTI: NewID("ses_"), IAT: now.Unix(), EXP: exp.Unix()}
	token, err := sign(c)
	if err != nil {
		return "", time.Time{}, nil, err
	}
	err = db.Put(ctx, Session{PK: "SESSION#" + c.JTI, SK: "#META", EntityType: "SESSION", UserID: u.UserID,
		Email: u.Email, CreatedAt: now.Format(time.RFC3339), ExpiresAt: exp.Format(time.RFC3339), TTL: exp.Unix()}, "")
	if err != nil {
		return "", time.Time{}, nil, err
	}
	return token, exp, &u, nil
}

// SignOut revokes the session of the token.
func SignOut(ctx context.Context, c *Claims) error {
	return db.Delete(ctx, "SESSION#"+c.JTI, "#META")
}

// Authenticate validates the Bearer token and its session.
func Authenticate(ctx context.Context, r *httpx.Request) (*Claims, error) {
	h := httpx.Header(r, "Authorization")
	token, ok := strings.CutPrefix(h, "Bearer ")
	if !ok || token == "" {
		return nil, ErrUnauthorized
	}
	c, err := parse(strings.TrimSpace(token))
	if err != nil {
		return nil, err
	}
	var s Session
	found, err := db.Get(ctx, "SESSION#"+c.JTI, "#META", &s)
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, ErrUnauthorized
	}
	return c, nil
}

type ctxKey struct{}

// Required is an httpx middleware that rejects requests without a valid session.
func Required(next httpx.Handler) httpx.Handler {
	return func(ctx context.Context, r *httpx.Request) (int, any, error) {
		c, err := Authenticate(ctx, r)
		if err != nil {
			var e *httpx.Error
			if errors.As(err, &e) {
				return 0, nil, e
			}
			return 0, nil, err
		}
		return next(context.WithValue(ctx, ctxKey{}, c), r)
	}
}

func FromContext(ctx context.Context) *Claims {
	c, _ := ctx.Value(ctxKey{}).(*Claims)
	return c
}
